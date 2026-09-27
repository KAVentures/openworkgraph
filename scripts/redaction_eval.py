"""Measure AI-context name redaction on tests/fixtures/redaction_eval.jsonl.

    python scripts/redaction_eval.py            # summary
    python scripts/redaction_eval.py --verbose  # plus every miss / over-redaction

Metrics (computed on the real Redacted AI-context pipeline):

* name recall: share of labeled PERSON spans whose letters were all replaced;
* over-redaction: share of non-sensitive words (outside every labeled span)
  that were replaced.

Replaced regions are found by aligning input and output characters, so the
metric does not depend on token formats.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "redaction_eval.jsonl"
_WORD = re.compile(r"[^\W_][\w'’.-]*", re.UNICODE)


_PIECE = re.compile(r"[^\W_][\w'’.-]*|\s+|.", re.UNICODE)


def replaced_mask(before: str, after: str) -> list[bool]:
    """Mark input characters that did not survive into the output.

    Alignment is done on word/space/punctuation pieces, not characters, so a
    replacement such as "Anna Svensson" -> "PERSON_1A2B3C" is not credited with
    the letters the two strings happen to share.
    """
    a = [(m.start(), m.end(), m.group(0)) for m in _PIECE.finditer(before)]
    b = [m.group(0) for m in _PIECE.finditer(after)]
    mask = [False] * len(before)
    matcher = difflib.SequenceMatcher(None, [p[2] for p in a], b, autojunk=False)
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            for start, end, _text in a[i1:i2]:
                for k in range(start, end):
                    mask[k] = True
    return mask


def evaluate(rows: list[dict], redact) -> dict:
    names = names_hit = 0
    words = words_hit = 0
    misses: list[str] = []
    over: list[str] = []
    for row in rows:
        text = row["text"]
        out = redact(text)
        mask = replaced_mask(text, out)
        sensitive = [False] * len(text)
        for span in row["spans"]:
            for k in range(span["start"], span["end"]):
                sensitive[k] = True
            if span["kind"] != "PERSON":
                continue
            names += 1
            letters = [k for k in range(span["start"], span["end"]) if text[k].isalpha()]
            if letters and all(mask[k] for k in letters):
                names_hit += 1
            else:
                misses.append(f"#{row['id']} {span['text']!r}: {text!r} -> {out!r}")
        for m in _WORD.finditer(text):
            if any(sensitive[k] for k in range(m.start(), m.end())):
                continue
            words += 1
            if any(mask[k] for k in range(m.start(), m.end())):
                words_hit += 1
                over.append(f"#{row['id']} {m.group(0)!r}: {text!r} -> {out!r}")
    return {
        "cases": len(rows),
        "names": names,
        "name_recall": names_hit / names if names else 1.0,
        "non_sensitive_words": words,
        "over_redaction": words_hit / words if words else 0.0,
        "misses": misses,
        "over_redacted": over,
    }


def load_rows() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def run() -> dict:
    # Isolated install state: fresh local key, no learned aliases, no config.
    tmp = tempfile.mkdtemp(prefix="owg-redaction-eval-")
    os.environ["WORKFLOW_OBSERVER_DATA"] = tmp
    os.environ["WORKFLOW_OBSERVER_CONFIG"] = str(Path(tmp) / "config.json")
    sys.path.insert(0, str(ROOT))
    from server.ai_context import redact_contextually

    return evaluate(load_rows(), lambda text: redact_contextually({"window_title": text})["window_title"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    result = run()
    print(
        f"cases={result['cases']} names={result['names']} "
        f"name_recall={result['name_recall']:.3f} "
        f"over_redaction={result['over_redaction']:.3f} "
        f"(non-sensitive words={result['non_sensitive_words']})"
    )
    if args.verbose:
        for line in result["misses"]:
            print("MISS ", line)
        for line in result["over_redacted"]:
            print("OVER ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
