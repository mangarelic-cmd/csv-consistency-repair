from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from csv_consistency_repair import RepairConfig, repair


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "input.csv"
        out = td / "output.csv"
        rep = td / "report.json"
        with src.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([" id ", "active", "value"])
            for i in range(100):
                w.writerow([f" R{i:04d} ", "YES" if i % 2 else "false", str(i)])
            w.writerow([" R0001 ", "YES", "1"])
        result = repair(
            src, out, rep,
            RepairConfig(remove_exact_duplicates=True, normalize_booleans=True),
        )
        print({
            "rows": 101,
            "initial_score": result.initial_score,
            "final_score": result.final_score,
            "committed_edits": result.committed_edits,
            "cycles": result.cycles,
            "strong_stable": result.strong_stable,
            "status": result.final_status,
        })


if __name__ == "__main__":
    main()
