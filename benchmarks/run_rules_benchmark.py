from __future__ import annotations

import json
import random
import tempfile
import time
from pathlib import Path

from csv_consistency_repair import RepairConfig, repair
from csv_consistency_repair.io import read_table


def main() -> int:
    rng = random.Random(20260818)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        clean = root / "clean.csv"
        dirty = root / "dirty.csv"
        fixed = root / "fixed.csv"
        report = root / "report.json"
        rules = root / "rules.json"

        countries = ["CA", "US", "FR", "DE", "JP"]
        rows = []
        for i in range(2000):
            customer_num = i % 100
            customer = f"C{customer_num:03d}"
            country = countries[customer_num % len(countries)]
            subtotal = (i % 50) + 1
            tax = (i % 7) + 1
            total = subtotal + tax
            length = (i % 9) + 1
            rows.append([str(i + 1), customer, country, str(subtotal), str(tax), str(total), f"{length} m"])

        header = "row_id,customer_id,country,subtotal,tax,total,length\n"
        clean.write_text(header + "\n".join(",".join(r) for r in rows) + "\n", encoding="utf-8")

        dirty_rows = [list(r) for r in rows]
        fd_corrupt = rng.sample(range(2000), 120)
        formula_corrupt = rng.sample(range(2000), 180)
        unit_corrupt = rng.sample(range(2000), 220)
        whitespace_corrupt = rng.sample(range(2000), 160)

        for i in fd_corrupt:
            current = dirty_rows[i][2]
            dirty_rows[i][2] = "US" if current != "US" else "CA"
        for i in formula_corrupt:
            dirty_rows[i][5] = str(int(dirty_rows[i][5]) + 9)
        for i in unit_corrupt:
            metres = int(dirty_rows[i][6].split()[0])
            dirty_rows[i][6] = f"{metres * 100} cm"
        for i in whitespace_corrupt:
            dirty_rows[i][1] = f" {dirty_rows[i][1]} "

        dirty.write_text(header + "\n".join(",".join(r) for r in dirty_rows) + "\n", encoding="utf-8")
        rules.write_text(json.dumps({
            "functional_dependencies": [{
                "determinant": ["customer_id"],
                "dependent": "country",
                "min_support": 10,
                "min_confidence": 0.75,
                "repair": True
            }],
            "row_formulas": [{
                "target": "total",
                "expression": {"op": "sum", "columns": ["subtotal", "tax"]},
                "tolerance": 0,
                "repair": True
            }],
            "units": [{
                "column": "length",
                "canonical": "m",
                "aliases": {"cm": 0.01},
                "repair": True
            }],
            "unique": ["row_id"]
        }, indent=2), encoding="utf-8")

        start = time.perf_counter()
        result = repair(dirty, fixed, report, RepairConfig(rules_path=str(rules), max_cycles=12))
        elapsed = time.perf_counter() - start
        exact_recovery = read_table(fixed).logical_digest() == read_table(clean).logical_digest()

        receipt = {
            "rows": 2000,
            "injected_defects": {
                "functional_dependency": len(fd_corrupt),
                "row_formula": len(formula_corrupt),
                "unit": len(unit_corrupt),
                "whitespace": len(whitespace_corrupt),
                "total_injections": len(fd_corrupt) + len(formula_corrupt) + len(unit_corrupt) + len(whitespace_corrupt),
            },
            "initial_score": result.initial_score,
            "final_score": result.final_score,
            "final_status": result.final_status,
            "strong_stable": result.strong_stable,
            "cycles": result.cycles,
            "committed_edits": result.committed_edits,
            "exact_ground_truth_recovery": exact_recovery,
            "elapsed_seconds": round(elapsed, 6),
        }
        print(json.dumps(receipt, indent=2))
        return 0 if exact_recovery and result.final_score == 0 and result.strong_stable else 1


if __name__ == "__main__":
    raise SystemExit(main())
