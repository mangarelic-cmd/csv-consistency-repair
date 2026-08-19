from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import time

from csv_consistency_repair import RepairConfig, repair
from csv_consistency_repair.io import read_table


def run() -> dict:
    with TemporaryDirectory() as td:
        root = Path(td)
        src = root / "input.csv"
        out = root / "output.csv"
        rep = root / "report.json"
        countries = ["US", "CA", "FR", "DE", "JP", "BR", "AU", "MX"]
        lines = ["row_id,customer_id,country,amount"]
        defects = 0
        n = 5000
        for i in range(n):
            customer = f"C{i % 80:03d}"
            country = countries[(i % 80) % len(countries)]
            if i % 211 == 0:
                country = "ZZ"
                defects += 1
            amount = f"{10 + (i % 91)}"
            lines.append(f"{i},{customer},{country},{amount}")
        src.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cfg = RepairConfig(
            discover_relationships=True,
            repair_discovered_relationships=True,
            discovery_confidence=0.95,
            discovery_min_rows=30,
            discovery_min_group_support=20,
            discovery_stress_tolerance=0.04,
        )
        start = time.perf_counter()
        result = repair(src, out, rep, cfg)
        elapsed = time.perf_counter() - start
        table = read_table(out)
        residual_zz = sum(1 for row in table.rows if row[2] == "ZZ")
        relation = next(
            r for r in result.report["relationship_discovery"]["relationships"]
            if r["determinant"] == ["customer_id"] and r["dependent"] == "country"
        )
        return {
            "rows": n,
            "injected_mapping_outliers": defects,
            "residual_mapping_outliers": residual_zz,
            "committed_edits": result.committed_edits,
            "initial_score": result.initial_score,
            "final_score": result.final_score,
            "final_status": result.final_status,
            "relationship_stability_pass": relation["stability"]["pass"],
            "forward_replay_pass": result.report["closure"]["forward_replay_pass"],
            "inverse_roundtrip_pass": result.report["closure"]["inverse_roundtrip_pass"],
            "elapsed_seconds": round(elapsed, 6),
        }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
