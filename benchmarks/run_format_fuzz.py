from __future__ import annotations

import csv
import io
import json
import random
import tempfile
import time
from pathlib import Path

from csv_consistency_repair import RepairConfig, repair, undo
from csv_consistency_repair.io import read_table


def write_case(path: Path, header, rows, delimiter, lineterminator, bom):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=delimiter, quotechar='"', lineterminator=lineterminator)
    writer.writerow(header)
    writer.writerows(rows)
    raw = buf.getvalue().encode("utf-8")
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    path.write_bytes(raw)


def main() -> None:
    rng = random.Random(20260818)
    delimiters = [",", ";", "\t", "|"]
    tokens = [
        "alpha", " beta ", "Québec", "東京", 'say "hi"', "line1\nline2",
        "", "=1+1", "@SUM(A1:A2)", "NA", "null", "true", "FALSE",
    ]
    cases = 256
    started = time.perf_counter()
    failures = []
    committed = 0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for case in range(cases):
            delimiter = delimiters[case % 4]
            line = "\r\n" if case % 2 else "\n"
            bom = case % 3 == 0
            src = root / f"case-{case}.csv"
            out = root / f"case-{case}.out.csv"
            rep = root / f"case-{case}.json"
            restored = root / f"case-{case}.restored.csv"
            rows = []
            for r in range(4 + case % 13):
                value = rng.choice(tokens)
                note = rng.choice(tokens)
                if r == 0:
                    value = f"inside{delimiter}field"
                rows.append([str(r), value, note])
            write_case(src, ["id", "value", "note"], rows, delimiter, line, bom)
            original = read_table(src)
            try:
                result = repair(
                    src,
                    out,
                    rep,
                    RepairConfig(normalize_null_markers=True, normalize_booleans=True),
                )
                committed += result.committed_edits
                repaired = read_table(out)
                receipt = undo(out, rep, restored)
                ok = all([
                    repaired.dialect.delimiter == delimiter,
                    repaired.dialect.lineterminator == line,
                    repaired.utf8_bom == bom,
                    result.report["format_contract"]["preservation_pass"] is True,
                    result.report["closure"]["forward_replay_pass"] is True,
                    result.report["closure"]["inverse_roundtrip_pass"] is True,
                    receipt["logical_roundtrip_pass"] is True,
                    read_table(restored).logical_digest() == original.logical_digest(),
                ])
                if not ok:
                    failures.append({"case": case, "reason": "property_failure"})
            except Exception as exc:
                failures.append({"case": case, "reason": type(exc).__name__, "detail": str(exc)})

        malformed = root / "malformed.csv"
        malformed.write_text('a,b\n1,"unterminated\n', encoding="utf-8")
        malformed_rejected = False
        try:
            repair(malformed)
        except ValueError:
            malformed_rejected = True

        latin1 = root / "latin1.csv"
        latin1.write_bytes(b"name\nAndr\xe9\n")
        non_utf8_rejected = False
        try:
            repair(latin1)
        except ValueError:
            non_utf8_rejected = True

    report = {
        "version": "0.4.0",
        "seed": 20260818,
        "cases": cases,
        "passed": cases - len(failures),
        "failed": len(failures),
        "committed_edits_exercised": committed,
        "delimiters": ["comma", "semicolon", "tab", "pipe"],
        "line_terminators": ["LF", "CRLF"],
        "utf8_bom_variation": True,
        "embedded_delimiter_variation": True,
        "embedded_quote_variation": True,
        "embedded_newline_variation": True,
        "unicode_variation": True,
        "formula_like_text_variation": True,
        "malformed_csv_rejected": malformed_rejected,
        "non_utf8_rejected": non_utf8_rejected,
        "runtime_seconds": round(time.perf_counter() - started, 4),
        "failures": failures[:20],
        "pass": not failures and malformed_rejected and non_utf8_rejected,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
