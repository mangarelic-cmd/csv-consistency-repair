import hashlib
import json
from pathlib import Path

import csv_consistency_repair

ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_single_and_current():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "1.3.5"' in pyproject
    assert csv_consistency_repair.__version__ == "1.3.5"


def test_locked_benchmark_manifest_is_portable_and_hashes_match():
    bench = ROOT / "benchmark_evidence"
    lock = json.loads((bench / "FUNCTIONAL_MANIFEST_LOCK.json").read_text(encoding="utf-8"))
    for case in lock["cases"]:
        for field, hash_field in (("dirty", "dirty_sha256"), ("clean", "clean_sha256")):
            rel = Path(case[field])
            assert not rel.is_absolute()
            path = bench / rel
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == case[hash_field]


def test_public_repository_has_no_development_machine_paths():
    roots = [ROOT / "benchmark_harness", ROOT / "examples"]
    for base in roots:
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".json", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "/mnt/data/" not in text
            assert "/home/oai/" not in text


def test_guides_and_author_note_are_packaged_as_package_data_sources():
    pkg = ROOT / "src" / "csv_consistency_repair"
    for name in ("GUIDE_LLM.md", "GUIDE_HUMAN.md", "AUTHOR.md"):
        assert (pkg / name).is_file()
