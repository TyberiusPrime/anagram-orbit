A silly little anagram word game.

Dictionaries: 

English, WordNet, see LICENSE.WordNet.txt
German: German Wiktionary (via kaikki.org/wiktextract), see LICENSE.Wiktionary.txt
German for kids: derived from the above, see below and LICENSE.FrequencyWords.txt

## The German kids pack

`de_kids_words.json` is a 4th-grade-reading-level cut of the German Wiktionary
list, built by the two scripts in `tools/`. It is checked in, so the game needs
nothing extra to run — rebuild it only when you want to change the thresholds.

```
python3 tools/build_kids_pack.py --stats   # pick the words
python3 tools/rewrite_defs.py --stats      # rewrite the definitions
```

**Stage 1 — `build_kids_pack.py`** narrows 6,339 words down to candidates using
word length (4–8 letters), corpus frequency rank, and a blocklist. Frequency is
the signal that actually tracks reading level: length alone leaves you with
`DISTAL`, `GALLIUM` and `OBIG`, which are short but not remotely simple. The
ranking comes from an OpenSubtitles frequency list
([hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords), MIT,
cached under `tools/.cache/`). Because that corpus is film dialogue, it also
ranks profanity and violence *high* — hence the blocklist, which is not optional.

**Stage 2 — `rewrite_defs.py`** sends the survivors to an LLM via `claude -p`,
because the Wiktionary definitions are written for adults (`AFFE` is "Säugetier
aus der Unterordnung Anthropoidea") and the source carries only one sense per
word, often not the one a child has met (`POOL` is defined as a financial
holding structure). The model rewrites each clue in plain German, picks the
child-facing sense, and votes on whether the word belongs in a kids' game at
all. Since this is an anagram game, clues may not give the answer away, so
definitions containing the word or its stem are rejected and re-requested.
Results are cached per batch, so re-runs only pay for what changed.

Rejected words and the reason for each land in `tools/de_kids_rejected.json`.
