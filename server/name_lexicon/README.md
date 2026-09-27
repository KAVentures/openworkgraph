# Name lexicon

Used only by `server/contextual_redaction.py`, locally, to recognise people in window titles and UI labels for **Redacted** AI context. Only names are stored: no counts or ranks.

| File | Entries | Sources |
|---|---|---|
| `first_names.txt` | ~4,400 | US Census Bureau 1990 (top 1,000 female + 800 male); Statistics Sweden *tilltalsnamn* with ≥ 200 bearers |
| `surnames.txt` | ~4,200 | US Census Bureau 1990 (top 2,500); Statistics Sweden *efternamn* with ≥ 500 bearers |

## Sources and licenses

- **US Census Bureau, 1990 Census name files** (`dist.female.first`, `dist.male.first`, `dist.all.last`), <https://www2.census.gov/topics/genealogy/1990surnames/>. Public domain (work of the US federal government, 17 U.S.C. § 105).
- **Statistics Sweden (SCB), "Namn med minst två bärare, 31 december 2022"**, <https://www.scb.se/hitta-statistik/statistik-efter-amne/befolkning-och-levnadsforhallanden/ovrigt/namnstatistik/>. SCB open data is published under **CC0 1.0**; attribution is not required but is given here.

## Rebuilding

```bash
python scripts/build_name_lexicon.py --sources /path/to/downloads --extract-scb   # needs openpyxl
```

The script documents the download URLs and thresholds. OpenWorkGraph never downloads anything at runtime.

## Ambiguous names

Some names are also everyday words, places or products (May, Bill, Grace, Paris, Claude …). They are listed in `AMBIGUOUS_NAMES` in `server/contextual_redaction.py`. The list is hand-curated and part of the code, not the data. Such a name only counts with an adjacent listed surname or a strong person cue.
