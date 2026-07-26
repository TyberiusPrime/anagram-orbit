#!/usr/bin/env python3
"""Stage 1 of the German kids pack build: pick the words.

Reduces dewiktionary_words.json down to words a 4th grader would plausibly
know, using three signals:

  1. length          - short words only (the game shows every letter as a tile)
  2. corpus frequency - the actual reading-level proxy, from an OpenSubtitles
                        frequency list (hermitdave/FrequencyWords, MIT)
  3. a blocklist      - subtitle corpora rank profanity and violence *high*,
                        so frequency alone happily hands you ARSCH and KOKAIN

Definitions are deliberately NOT filtered here. Wiktionary defines AFFE as
"Saeugetier aus der Unterordnung Anthropoidea" - rejecting prose like that
throws away the best kid words in the list. Stage 2 (rewrite_defs.py) hands
the survivors to an LLM to rewrite instead.

Usage:
    python3 tools/build_kids_pack.py            # writes the candidate list
    python3 tools/build_kids_pack.py --stats    # ...and prints the funnel
"""

import argparse
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "dewiktionary_words.json")
CANDIDATES = os.path.join(ROOT, "tools", "de_kids_candidates.json")
FREQ_CACHE = os.path.join(ROOT, "tools", ".cache", "de_50k.txt")
FREQ_URL = (
    "https://raw.githubusercontent.com/hermitdave/FrequencyWords"
    "/master/content/2018/de/de_50k.txt"
)

MIN_LEN, MAX_LEN = 4, 8
MAX_RANK = 8000

# Words that clear the frequency bar but have no business in a kids' game.
# Subtitle frequency is dialogue frequency, and film dialogue swears a lot.
BLOCKED_WORDS = {
    "ARSCH", "ARSCHLOCH", "BESOFFEN", "BORDELL", "BUSEN", "DROGE", "DROGEN",
    "EREKTION", "FICKEN", "FOTZE", "GEIL", "HEROIN", "HURE", "KIFFEN",
    "KOKAIN", "KOTZEN", "LEICHE", "MORD", "MOERDER", "MÖRDER", "MUSCHI",
    "NACKT", "NUTTE", "ORGASMUS", "PENIS", "PISTOLE", "PUFF", "SAUFEN",
    "SCHEISSE", "SCHWANZ", "SELBSTMORD", "SEX", "TITTE", "TITTEN", "VAGINA",
    "VERGEWALTIGUNG", "WHISKY", "ZUHAELTER", "ZUHÄLTER",
    # Not about the word's own meaning - both have perfectly innocent
    # definitions ("Reisefuehrer", "Ureinwohner Amerikas"). FUEHRER carries
    # obvious historical baggage as a bare word in a German kids' game, and
    # INDIANER is a term contemporary German children's publishing has largely
    # moved away from. Drop either line if you disagree.
    "FUEHRER", "FÜHRER", "INDIANER",
}

# Applied to the *definition*, to catch adult words the list above misses.
# Kept deliberately narrow: broad terms like "Waffe" or "Geschlecht" also
# flag HERDE, KUSS and MAERCHEN, so those are left to the LLM's judgement
# call in stage 2 rather than being auto-rejected here.
BLOCKED_DEF = re.compile(
    r"sexuell|Geschlechtsorgan|Geschlechtsverkehr|Prostitu|Rauschgift|"
    r"Rauschmittel|vorsätzliche Tötung|Schimpfwort|vulgär|derb\b",
    re.IGNORECASE,
)


def load_freq():
    """word -> 1-based rank in the OpenSubtitles German frequency list."""
    if not os.path.exists(FREQ_CACHE):
        os.makedirs(os.path.dirname(FREQ_CACHE), exist_ok=True)
        sys.stderr.write(f"fetching {FREQ_URL}\n")
        urllib.request.urlretrieve(FREQ_URL, FREQ_CACHE)
    freq = {}
    with open(FREQ_CACHE, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            parts = line.split()
            if len(parts) == 2 and parts[0] not in freq:
                freq[parts[0]] = i + 1
    return freq


def rank_of(word, freq):
    """Rank lookup. The source data folds ss/ß together, the corpus doesn't."""
    lower = word.lower()
    keys = {lower}
    if "ss" in lower:
        keys.add(lower.replace("ss", "ß"))
    return min((freq[k] for k in keys if k in freq), default=None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="print the filter funnel")
    ap.add_argument("--max-rank", type=int, default=MAX_RANK)
    args = ap.parse_args()

    freq = load_freq()
    with open(SOURCE, encoding="utf-8") as fh:
        words = json.load(fh)

    funnel = [("source", len(words))]

    words = [w for w in words if MIN_LEN <= len(w["word"]) <= MAX_LEN]
    funnel.append((f"length {MIN_LEN}-{MAX_LEN}", len(words)))

    ranked = []
    for w in words:
        r = rank_of(w["word"], freq)
        if r is not None and r <= args.max_rank:
            ranked.append(dict(w, rank=r))
    funnel.append((f"freq rank <= {args.max_rank}", len(ranked)))

    ranked = [w for w in ranked if w["word"] not in BLOCKED_WORDS]
    funnel.append(("word blocklist", len(ranked)))

    ranked = [w for w in ranked if not BLOCKED_DEF.search(w["def"])]
    funnel.append(("definition blocklist", len(ranked)))

    ranked.sort(key=lambda w: w["rank"])
    with open(CANDIDATES, "w", encoding="utf-8") as fh:
        json.dump(ranked, fh, ensure_ascii=False, indent=1)

    if args.stats:
        prev = None
        for name, n in funnel:
            drop = "" if prev is None else f"  (-{prev - n})"
            print(f"{name:28} {n:>6}{drop}")
            prev = n
        from collections import Counter
        print("\nby word class:", dict(Counter(w["category"] for w in ranked)))
        print(f"\nwrote {CANDIDATES}")


if __name__ == "__main__":
    main()
