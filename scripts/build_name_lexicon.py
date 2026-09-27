"""Rebuild server/name_lexicon/*.txt from public-domain / CC0 name statistics.

This is a maintainer tool; OpenWorkGraph never downloads anything at runtime.
Sources (download them into one folder and pass it as --sources):

* US Census Bureau, 1990 Census name files (public domain, US federal work):
  https://www2.census.gov/topics/genealogy/1990surnames/dist.female.first
  https://www2.census.gov/topics/genealogy/1990surnames/dist.male.first
  https://www2.census.gov/topics/genealogy/1990surnames/dist.all.last
* Statistics Sweden (SCB), "Namn med minst två bärare, 31 december 2022"
  (open data, CC0 1.0):
  https://www.scb.se/contentassets/9fe7dbb460994c72b835163dbc491ef9/namn-med-minst-tva-barare-31-december-2022.xlsx
  Extract it first with ``--extract-scb`` (needs ``openpyxl``).

Only names are shipped: no counts, no ranks. Thresholds keep the bundle small.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "server" / "name_lexicon"

US_FEMALE_FIRST = 1000
US_MALE_FIRST = 800
US_SURNAMES = 2500
SE_FIRST_MIN_BEARERS = 200  # tilltalsnamn (the given name actually used)
SE_SURNAME_MIN_BEARERS = 500

_VALID = re.compile(r"^[a-zà-öø-ÿ][a-zà-öø-ÿ'’-]*[a-zà-öø-ÿ]$")


def _clean(name: str) -> str:
    value = str(name or "").strip().casefold()
    return value if _VALID.fullmatch(value) and len(value) >= 2 else ""


def _census(path: Path, limit: int) -> list[str]:
    names = []
    for line in path.read_text(encoding="latin-1").splitlines()[:limit]:
        parts = line.split()
        if parts:
            names.append(_clean(parts[0]))
    return [n for n in names if n]


def _extract_scb(xlsx: Path, out: Path) -> None:
    import openpyxl  # maintainer-only dependency

    wb = openpyxl.load_workbook(xlsx, read_only=True)
    data = {}
    for sheet in ("Efternamn", "Tilltalsnamn kvinnor", "Tilltalsnamn män"):
        values = {}
        for row in wb[sheet].iter_rows(min_row=6, values_only=True):
            if row[0] and isinstance(row[1], (int, float)):
                values[str(row[0]).strip()] = int(row[1])
        data[sheet] = values
    out.write_text(json.dumps(data), encoding="utf-8")


def _write(path: Path, header: str, names: set[str]) -> None:
    lines = [f"# {line}" for line in header.strip().splitlines()]
    path.write_text("\n".join(lines + sorted(names)) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--extract-scb", action="store_true")
    args = parser.parse_args()
    src = args.sources
    scb_json = src / "scb_extract.json"
    if args.extract_scb:
        _extract_scb(src / "namn-med-minst-tva-barare-31-december-2022.xlsx", scb_json)
    scb = json.loads(scb_json.read_text(encoding="utf-8"))

    first: set[str] = set()
    first.update(_census(src / "dist.female.first", US_FEMALE_FIRST))
    first.update(_census(src / "dist.male.first", US_MALE_FIRST))
    for sheet in ("Tilltalsnamn kvinnor", "Tilltalsnamn män"):
        first.update(n for n in (_clean(k) for k, v in scb[sheet].items() if v >= SE_FIRST_MIN_BEARERS) if n)

    surnames: set[str] = set(_census(src / "dist.all.last", US_SURNAMES))
    surnames.update(
        n for n in (_clean(k) for k, v in scb["Efternamn"].items() if v >= SE_SURNAME_MIN_BEARERS) if n
    )

    OUT.mkdir(parents=True, exist_ok=True)
    common = """
Built by scripts/build_name_lexicon.py. Names only (no counts). Sources:
US Census Bureau 1990 name files (public domain, US federal government work).
Statistics Sweden (SCB) name statistics 31 Dec 2022 (open data, CC0 1.0).
"""
    _write(OUT / "first_names.txt", common + f"First names: US top {US_FEMALE_FIRST} female + {US_MALE_FIRST} male; "
           f"SCB tilltalsnamn with >= {SE_FIRST_MIN_BEARERS} bearers.", first)
    _write(OUT / "surnames.txt", common + f"Surnames: US top {US_SURNAMES}; SCB efternamn with >= "
           f"{SE_SURNAME_MIN_BEARERS} bearers.", surnames)
    print(f"first_names={len(first)} surnames={len(surnames)} -> {OUT}")


if __name__ == "__main__":
    main()
