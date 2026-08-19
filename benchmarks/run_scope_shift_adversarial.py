from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json

from csv_consistency_repair import RepairConfig, repair


def run() -> dict:
    with TemporaryDirectory() as td:
        root = Path(td)
        src = root / "input.csv"
        out = root / "output.csv"
        rep = root / "report.json"
        lines = ["id,key,value"]
        for i in range(50):
            lines.append(f"{i},X,A")
        for i in range(50, 100):
            lines.append(f"{i},Y,{'B' if i < 90 else 'C'}")
        src.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cfg = RepairConfig(
            discover_relationships=True,
            repair_discovered_relationships=True,
            discovery_confidence=0.85,
            discovery_min_rows=12,
            discovery_min_group_support=8,
            discovery_stress_tolerance=0.02,
        )
        result = repair(src, out, rep, cfg)
        relation = next(
            r for r in result.report["relationship_discovery"]["relationships"]
            if r["determinant"] == ["key"] and r["dependent"] == "value"
        )
        repaired_relation_edits = [
            e for e in result.report["committed_edits"]
            if e.get("metadata", {}).get("rule_type") == "discovered_functional_dependency"
        ]
        return {
            "base_relation_pass": relation["stability"]["base_pass"],
            "stability_pass": relation["stability"]["pass"],
            "stability_reasons": relation["stability"]["reasons"],
            "discovered_relation_repairs_committed": len(repaired_relation_edits),
            "row_scope_sensitive_detected": "row_scope_sensitive" in relation["stability"]["reasons"],
            "final_status": result.final_status,
            "remaining_issues": result.remaining_issues,
            "forward_replay_pass": result.report["closure"]["forward_replay_pass"],
            "inverse_roundtrip_pass": result.report["closure"]["inverse_roundtrip_pass"],
        }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
