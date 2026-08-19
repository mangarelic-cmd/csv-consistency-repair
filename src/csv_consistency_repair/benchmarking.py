from __future__ import annotations

from ._version import __version__
"""Reproducible benchmarking harness for csv-consistency-repair.

The harness is corpus-agnostic.  It can consume an external corpus with ground truth,
but does not label a corpus external merely because it was passed to this function.
The manifest must declare origin and immutable hashes are locked before execution.
"""

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any
import json
import os
import resource
import tempfile

from .engine import RepairConfig, repair
from .io import read_table, file_sha256


def _json_hash(obj: Any) -> str:
    return sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def _manifest_files(manifest_path: Path, manifest: dict[str, Any]) -> dict[str, str]:
    base = manifest_path.parent
    out = {}
    for case in manifest.get("cases", []):
        for key in ("dirty", "clean", "rules"):
            if case.get(key):
                p = (base / case[key]).resolve()
                out[str(p)] = file_sha256(p)
        for rel in (case.get("baseline_outputs") or {}).values():
            p = (base / rel).resolve()
            out[str(p)] = file_sha256(p)
    return out


def lock_benchmark_protocol(manifest_path: str | Path, lock_path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = _manifest_files(manifest_path, manifest)
    protocol = {
        "schema": "csv-consistency-repair.benchmark-lock.v1",
        "manifest_sha256": file_sha256(manifest_path),
        "files": files,
        "metrics": ["precision", "recall", "false_mutation_rate", "exact_dataset_recovery", "abstention_quality", "throughput_rows_per_s", "peak_rss_kib"],
        "modes": ["zero_config", "configured"],
        "baselines": ["noop", "trim_outer_whitespace", "pandas_trim_if_available", "manifest_baseline_outputs"],
        "corruption_families": sorted({str(c.get("family", "unspecified")) for c in manifest.get("cases", [])}),
        "corpus_origin": manifest.get("corpus_origin", "unspecified"),
        "tuning_on_locked_cases_forbidden": True,
    }
    protocol["protocol_sha256"] = _json_hash(protocol)
    if lock_path:
        Path(lock_path).write_text(json.dumps(protocol, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return protocol


def verify_benchmark_lock(manifest_path: str | Path, lock: dict[str, Any]) -> bool:
    current = lock_benchmark_protocol(manifest_path)
    return current.get("protocol_sha256") == lock.get("protocol_sha256")


def _cell_map(table) -> dict[tuple[int, int], str]:
    out = {}
    for c, v in enumerate(table.header): out[(-1, c)] = v
    for r, row in enumerate(table.rows):
        for c, v in enumerate(row): out[(r, c)] = v
    return out


def compare_to_truth(dirty, repaired, clean) -> dict[str, Any]:
    d = _cell_map(dirty); r = _cell_map(repaired); c = _cell_map(clean)
    keys = set(d) | set(r) | set(c)
    truth_bad = {k for k in keys if d.get(k) != c.get(k)}
    touched = {k for k in keys if d.get(k) != r.get(k)}
    correct_touches = {k for k in touched if r.get(k) == c.get(k) and k in truth_bad}
    false_mutations = {k for k in touched if k not in truth_bad or r.get(k) != c.get(k)}
    repaired_truth = {k for k in truth_bad if r.get(k) == c.get(k)}
    precision = len(correct_touches) / len(touched) if touched else 1.0
    recall = len(repaired_truth) / len(truth_bad) if truth_bad else 1.0
    clean_cells = max(1, len(keys) - len(truth_bad))
    false_mutation_rate = len({k for k in touched if k not in truth_bad}) / clean_cells
    return {
        "truth_corrupt_cells": len(truth_bad),
        "touched_cells": len(touched),
        "correct_repaired_cells": len(repaired_truth),
        "false_mutations": len(false_mutations),
        "precision": precision,
        "recall": recall,
        "false_mutation_rate": false_mutation_rate,
        "exact_dataset_recovery": repaired.logical_digest() == clean.logical_digest(),
        "clean_file_mutation_pass": (not truth_bad and not touched),
        "abstained_corrupt_cells": len(truth_bad - repaired_truth),
    }


def _trim_baseline(table):
    x = table.clone()
    x.header = [v.strip() for v in x.header]
    x.rows = [[v.strip() for v in row] for row in x.rows]
    return x


def _pandas_trim_baseline(path: Path):
    try:
        import pandas as pd  # type: ignore
    except Exception:
        return None
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception:
        return None
    df.columns = [str(c).strip() for c in df.columns]
    for c in df.columns:
        df[c] = df[c].map(lambda x: x.strip() if isinstance(x, str) else x)
    from .models import Table
    return Table(header=[str(c) for c in df.columns], rows=[[str(x) for x in row] for row in df.astype(str).itertuples(index=False, name=None)])


def _run_one(case: dict[str, Any], base: Path, mode: str, work: Path) -> dict[str, Any]:
    dirty_path = (base / case["dirty"]).resolve(); clean_path = (base / case["clean"]).resolve()
    dirty = read_table(dirty_path); clean = read_table(clean_path)
    out = work / f"{case.get('id','case')}-{mode}.csv"; rep = work / f"{case.get('id','case')}-{mode}.json"
    cfg_data = {} if mode == "zero_config" else dict(case.get("config", {}))
    if mode == "configured" and case.get("rules"):
        cfg_data["rules_path"] = str((base / case["rules"]).resolve())
    if mode == "configured" and not cfg_data:
        # Safe mode is the generic configured comparator when no per-case config exists.
        cfg_data["safe_mode"] = True
    cfg = RepairConfig(**cfg_data)
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = perf_counter(); result = repair(dirty_path, out, rep, cfg); dt = perf_counter() - t0
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    repaired = read_table(out)
    metrics = compare_to_truth(dirty, repaired, clean)
    metrics.update({
        "seconds": dt,
        "rows": len(dirty.rows),
        "throughput_rows_per_s": len(dirty.rows) / dt if dt else float("inf"),
        "peak_rss_kib": max(rss_before, rss_after),
        "final_status": result.final_status,
        "committed_edits": result.committed_edits,
    })
    return metrics


def run_benchmark(manifest_path: str | Path, lock: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if lock is not None and not verify_benchmark_lock(manifest_path, lock):
        raise ValueError("Benchmark manifest/files changed after protocol lock.")
    protocol = lock or lock_benchmark_protocol(manifest_path)
    base = manifest_path.parent
    rows = []
    with tempfile.TemporaryDirectory(prefix="csv-repair-benchmark-") as td:
        work = Path(td)
        for case in manifest.get("cases", []):
            dirty = read_table((base / case["dirty"]).resolve()); clean = read_table((base / case["clean"]).resolve())
            # Ordinary baselines are evaluated on the exact same truth.
            baseline_noop = compare_to_truth(dirty, dirty, clean)
            baseline_trim = compare_to_truth(dirty, _trim_baseline(dirty), clean)
            baselines = {"noop": baseline_noop, "trim_outer_whitespace": baseline_trim}
            pd_table = _pandas_trim_baseline((base / case["dirty"]).resolve())
            if pd_table is not None:
                baselines["pandas_trim"] = compare_to_truth(dirty, pd_table, clean)
            # External/industry comparator outputs can be locked into the same manifest.
            for name, rel in sorted((case.get("baseline_outputs") or {}).items()):
                bp = (base / rel).resolve()
                baselines[str(name)] = compare_to_truth(dirty, read_table(bp), clean)
            modes = {}
            for mode in ("zero_config", "configured"):
                modes[mode] = _run_one(case, base, mode, work)
            rows.append({
                "id": case.get("id"), "family": case.get("family", "unspecified"), "rows": len(dirty.rows),
                "modes": modes,
                "baselines": baselines,
            })
    def avg(path: tuple[str, ...]) -> float:
        vals = []
        for row in rows:
            x: Any = row
            for p in path: x = x[p]
            if isinstance(x, bool): x = float(x)
            vals.append(float(x))
        return sum(vals) / len(vals) if vals else 0.0
    summary = {
        "cases": len(rows),
        "zero_config": {
            "precision": avg(("modes","zero_config","precision")), "recall": avg(("modes","zero_config","recall")),
            "false_mutation_rate": avg(("modes","zero_config","false_mutation_rate")),
            "exact_dataset_recovery_rate": avg(("modes","zero_config","exact_dataset_recovery")),
            "throughput_rows_per_s_mean": avg(("modes","zero_config","throughput_rows_per_s")),
        },
        "configured": {
            "precision": avg(("modes","configured","precision")), "recall": avg(("modes","configured","recall")),
            "false_mutation_rate": avg(("modes","configured","false_mutation_rate")),
            "exact_dataset_recovery_rate": avg(("modes","configured","exact_dataset_recovery")),
            "throughput_rows_per_s_mean": avg(("modes","configured","throughput_rows_per_s")),
        },
        "clean_file_cases": sum(1 for x in rows if x["modes"]["configured"]["truth_corrupt_cells"] == 0),
        "clean_file_mutation_passes": sum(1 for x in rows if x["modes"]["configured"]["clean_file_mutation_pass"]),
        "families": sorted({x["family"] for x in rows}),
    }
    return {
        "tool": "csv-consistency-repair", "version": __version__,
        "protocol": protocol,
        "corpus_origin": manifest.get("corpus_origin", "unspecified"),
        "external_corpus_claim_allowed": manifest.get("corpus_origin") == "external" and bool(manifest.get("provenance")),
        "summary": summary,
        "cases": rows,
        "feature_ids": list(range(123, 131)),
    }


def scaling_benchmark(paths: list[str | Path], config: RepairConfig | None = None) -> dict[str, Any]:
    config = config or RepairConfig()
    results = []
    with tempfile.TemporaryDirectory(prefix="csv-repair-scale-") as td:
        td = Path(td)
        for i, p in enumerate(paths):
            p = Path(p); table = read_table(p)
            out = td / f"out-{i}.csv"; rep = td / f"out-{i}.json"
            rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            t0 = perf_counter(); repair(p, out, rep, config); dt = perf_counter() - t0
            rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            results.append({"path": str(p), "rows": len(table.rows), "seconds": dt,
                            "throughput_rows_per_s": len(table.rows)/dt if dt else float("inf"),
                            "peak_rss_kib": max(rss_before, rss_after)})
    return {"results": results, "feature_id": 125}
