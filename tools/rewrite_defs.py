#!/usr/bin/env python3
"""Stage 2 of the German kids pack build: rewrite the definitions.

The Wiktionary definitions that survive stage 1 are written for adults. They
lean on taxonomy ("Saeugetier aus der Unterordnung Anthropoidea"), they run
long, and - because the source carries one sense per word - they routinely
pick the sense a child has never met: POOL is defined as a financial holding
structure, BAHN as a physics trajectory, ORDNUNG as "Synonym fuer Organisation".

So each candidate goes to an LLM (via `claude -p`) which rewrites the clue in
plain 4th-grade German, picks the sense a child actually knows, and votes on
whether the word belongs in a kids' game at all.

The one hard constraint is that a clue must not give the answer away: this is
an anagram game, so the definition may not contain the word or anything built
on its stem. That is checked here rather than trusted - violations go back for
a repair round, and anything still leaking after that is dropped.

Results are cached per batch under tools/.cache/rewrites/, so a re-run only
pays for what changed.

Usage:
    python3 tools/rewrite_defs.py                 # rewrite, then write the pack
    python3 tools/rewrite_defs.py --limit 50      # smoke-test on 50 words
    python3 tools/rewrite_defs.py --stats
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "dewiktionary_words.json")
CANDIDATES = os.path.join(ROOT, "tools", "de_kids_candidates.json")
OUTPUT = os.path.join(ROOT, "de_kids_words.json")
REJECTS = os.path.join(ROOT, "tools", "de_kids_rejected.json")
CACHE_DIR = os.path.join(ROOT, "tools", ".cache", "rewrites")

MODEL = "claude-sonnet-5"
BATCH_SIZE = 25
WORKERS = 8
MAX_DEF_LEN = 95

PROMPT = """Du schreibst Worterklaerungen fuer ein Anagramm-Ratespiel fuer \
deutsche Grundschulkinder der 4. Klasse (9-10 Jahre).

Fuer jedes Wort unten bekommst du die Wiktionary-Definition fuer Erwachsene. \
Schreibe sie in einfaches Deutsch um, das ein Kind der 4. Klasse versteht.

Regeln:
1. WICHTIG: Die Erklaerung darf das gesuchte Wort NICHT enthalten - auch nicht \
als Wortstamm, Zusammensetzung oder gebeugte Form. Das Kind muss das Wort \
erraten. Bei "BESITZEN" ist "etwas als Besitz haben" also falsch.
2. Hoechstens 90 Zeichen. Ein kurzer Satz oder eine Wortgruppe, kein Punkt am Ende.
3. Waehle die Bedeutung, die ein Kind kennt, nicht die fachliche. Bei "POOL" \
also das Schwimmbecken, nicht die Finanzbeteiligung.
4. Keine Klammern, keine Fachbegriffe, keine lateinischen Namen. Nur Woerter, \
die ein Kind der 4. Klasse kennt.
5. Setze "ok": false, wenn das Wort selbst fuer Kinder ungeeignet ist: zu \
abstrakt, nur in Erwachsenensprache gebraeuchlich (Buerokratie, Finanzen, \
Recht), oder thematisch unpassend (Gewalt, Sexualitaet, Drogen, Alkohol). \
Setze "ok": true, wenn ein Kind der 4. Klasse das Wort kennen oder gut \
erraten koennte. Auch bei "ok": false bitte trotzdem eine Erklaerung schreiben.

Antworte NUR mit einem JSON-Array, ohne Markdown-Codeblock, in dieser Form:
[{"w": "WORT", "def": "einfache Erklaerung", "ok": true}]

Die Woerter:
"""

REPAIR_NOTE = """
ACHTUNG: Bei diesen Woertern hat ein frueherer Versuch das gesuchte Wort in \
der Erklaerung verraten oder die Laengengrenze gerissen. Formuliere komplett \
neu, ohne den Wortstamm zu verwenden, und bleibe unter 90 Zeichen.
"""


def lowercaseable():
    """Words that are safe to de-capitalize at the start of a clue.

    The model capitalizes the first word about half the time, and the existing
    banks don't - but German capitalizes nouns, so a blind .lower() would turn
    "Faehigkeit, etwas zu tun" into "faehigkeit, ...". Instead: any word the
    Wiktionary definitions themselves use lowercase mid-sentence is not a noun,
    and can safely be lowered.
    """
    seen = set()
    with open(SOURCE, encoding="utf-8") as fh:
        for entry in json.load(fh):
            for token in re.findall(r"[a-zäöüß][a-zäöüß]+", entry["def"]):
                seen.add(token)
    return seen


LOWERABLE = None


def normalize(text):
    """Trim, drop a trailing period, and align the leading capital."""
    text = re.sub(r"\s+", " ", text).strip().rstrip(".").strip()
    head = text.split(" ", 1)[0]
    if head.lower() in LOWERABLE and head[:1].isupper():
        text = head[0].lower() + text[1:]
    return text


def stem(word):
    """Leading chunk of the word that a clue must not contain."""
    return word[:4] if len(word) <= 5 else word[:5]


def leaks(word, text):
    """True if the clue gives the answer away."""
    hay = text.upper().replace("SS", "S").replace("ß".upper(), "S")
    for form in (word, stem(word)):
        needle = form.upper().replace("SS", "S")
        if needle and needle in hay:
            return True
    return False


def valid(word, entry):
    d = (entry.get("def") or "").strip()
    if not (8 <= len(d) <= MAX_DEF_LEN):
        return False
    if "(" in d or ")" in d:
        return False
    return not leaks(word, d)


def run_batch(batch, repair=False):
    """One `claude -p` call over a list of candidate dicts."""
    key = hashlib.sha256(
        (MODEL + ("R" if repair else "") + "|".join(w["word"] for w in batch)
         + "|".join(w["def"] for w in batch)).encode()
    ).hexdigest()[:16]
    cache_path = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)

    listing = "\n".join(
        f'{w["word"]} [{w["category"]}]: {w["def"]}' for w in batch
    )
    prompt = PROMPT + (REPAIR_NOTE if repair else "") + "\n" + listing
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", MODEL],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"batch failed: {proc.stderr[:300]}\n")
        return []
    match = re.search(r"\[.*\]", proc.stdout, re.S)
    if not match:
        sys.stderr.write(f"no JSON in reply: {proc.stdout[:200]}\n")
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as err:
        sys.stderr.write(f"bad JSON: {err}\n")
        return []

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(parsed, fh, ensure_ascii=False)
    return parsed


def rewrite(candidates, repair=False):
    batches = [candidates[i:i + BATCH_SIZE]
               for i in range(0, len(candidates), BATCH_SIZE)]
    out = {}
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(run_batch, b, repair): b for b in batches}
        for fut in concurrent.futures.as_completed(futures):
            for entry in fut.result():
                if isinstance(entry, dict) and entry.get("w"):
                    out[entry["w"].strip().upper()] = entry
            done += 1
            sys.stderr.write(f"\r  batch {done}/{len(batches)}")
            sys.stderr.flush()
    sys.stderr.write("\n")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only process the first N candidates")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    global LOWERABLE
    LOWERABLE = lowercaseable()

    with open(CANDIDATES, encoding="utf-8") as fh:
        candidates = json.load(fh)
    if args.limit:
        candidates = candidates[:args.limit]

    sys.stderr.write(f"rewriting {len(candidates)} definitions\n")
    results = rewrite(candidates)

    # Anything the model leaked, over-ran, or simply skipped gets one more go.
    needs_repair = [
        c for c in candidates
        if c["word"] not in results or not valid(c["word"], results[c["word"]])
    ]
    if needs_repair:
        sys.stderr.write(f"repair round for {len(needs_repair)} words\n")
        for word, entry in rewrite(needs_repair, repair=True).items():
            results[word] = entry

    kept, rejected = [], []
    for cand in candidates:
        word = cand["word"]
        entry = results.get(word)
        if not entry:
            rejected.append({**cand, "why": "no rewrite"})
            continue
        text = normalize(entry.get("def") or "")
        if not entry.get("ok", True):
            rejected.append({**cand, "why": "model: not kid-appropriate"})
            continue
        if not valid(word, {"def": text}):
            why = "leaks answer" if leaks(word, text) else "malformed/too long"
            rejected.append({**cand, "why": why})
            continue
        kept.append({"word": word, "def": text, "category": cand["category"]})

    kept.sort(key=lambda w: w["word"])
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(kept, fh, ensure_ascii=False, indent=1)
    with open(REJECTS, "w", encoding="utf-8") as fh:
        json.dump(rejected, fh, ensure_ascii=False, indent=1)

    print(f"kept {len(kept)} / {len(candidates)}  -> {OUTPUT}")
    if args.stats:
        from collections import Counter
        print("rejected:", dict(Counter(r["why"] for r in rejected)))
        print("by class:", dict(Counter(w["category"] for w in kept)))


if __name__ == "__main__":
    main()
