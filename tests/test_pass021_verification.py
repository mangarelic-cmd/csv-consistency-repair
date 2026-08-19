from __future__ import annotations

import csv
import json
import tomllib
from pathlib import Path

import csv_consistency_repair as ccr
from csv_consistency_repair import RepairConfig, repair
from csv_consistency_repair.bundle import repair_bundle
from csv_consistency_repair.streaming import StreamRepairConfig, stream_repair
from csv_consistency_repair.cli import main as cli_main


def test_package_version_matches_project_metadata():
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    version = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    assert ccr.__version__ == version


def test_regular_report_version_matches_package(tmp_path: Path):
    src=tmp_path/"in.csv"; out=tmp_path/"out.csv"; rep=tmp_path/"rep.json"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    rr=repair(src,out,rep,RepairConfig(auto_mode=False))
    assert rr.report["version"] == ccr.__version__


def test_stream_report_version_and_generic_cli_undo_autodetect(tmp_path: Path):
    src=tmp_path/"in.csv"; out=tmp_path/"out.csv"; rep=tmp_path/"rep.json"; restored=tmp_path/"restored.csv"
    with src.open("w", newline="", encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["id","flag","text"])
        for i in range(1,101):
            w.writerow([i, " YES " if i%2 else " no ", f" text {i} "])
    sr=stream_repair(src,out,rep,StreamRepairConfig(normalize_booleans=True,journal_edits=True,verify_replay=True))
    assert sr["version"] == ccr.__version__
    # Deliberately omit --stream: the report should route undo automatically.
    rc=cli_main([str(out),"--undo","--report",str(rep),"-o",str(restored)])
    assert rc == 0
    assert restored.read_bytes() == src.read_bytes()


def test_bundle_report_version_matches_package(tmp_path: Path):
    a=tmp_path/"a.csv"; b=tmp_path/"b.csv"; manifest=tmp_path/"bundle.json"; out=tmp_path/"out"
    a.write_text("id,value\nA,1\nB,2\n", encoding="utf-8")
    b.write_text("id,value\nA,1\nB,2\n", encoding="utf-8")
    manifest.write_text(json.dumps({"datasets":[{"name":"a","input":"a.csv"},{"name":"b","input":"b.csv"}]}), encoding="utf-8")
    summary=repair_bundle(manifest,out,out/"bundle.report.json")
    assert summary["version"] == ccr.__version__
