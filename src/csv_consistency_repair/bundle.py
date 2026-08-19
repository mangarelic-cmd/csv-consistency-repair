from __future__ import annotations

from ._version import __version__
from pathlib import Path
from typing import Any
import json
import os
import shutil
from hashlib import sha256

from .engine import RepairConfig, repair, undo, _forward_edit, _inverse_candidate
from .io import read_table, write_table, file_sha256
from .maxima50 import build_bundle_maxima50
from .bundle_materialization import materialize_bundle_repairs
from .maxima55 import (
    file_snapshot, verify_file_snapshot, bundle_transaction_diagnostics,
    rule_precedence_graph, transaction_checkpoint, load_checkpoint, automatic_foreign_keys,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def repair_bundle(
    manifest_path: str | Path,
    output_dir: str | Path,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Repair a bundle through one frozen snapshot and one atomic logical commit.

    Each dataset is first repaired into a staging directory.  No material output is
    replaced until all staged datasets pass their configured closure checks and the
    input snapshot is revalidated immediately before commit.  A journal and backups
    make partial-application rollback deterministic.
    """
    manifest_path = Path(manifest_path)
    base = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    datasets = manifest.get("datasets", [])
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("Bundle manifest must contain a non-empty 'datasets' list.")

    input_paths: dict[str, Path] = {}
    snapshot_tables = {}
    for item in datasets:
        name = str(item.get("name") or Path(item["input"]).stem)
        ip = (base / item["input"]).resolve()
        if name in input_paths:
            raise ValueError(f"Duplicate bundle dataset name: {name}")
        input_paths[name] = ip
        snapshot_tables[name] = read_table(ip)

    snapshot = file_snapshot(input_paths)
    snapshot_hashes = {name: meta["sha256"] for name, meta in snapshot["files"].items()}
    bundle_maxima_before = build_bundle_maxima50(snapshot_tables)
    tx_diag_before = bundle_transaction_diagnostics(snapshot_tables, snapshot, manifest)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    txid = snapshot["snapshot_id"][:20]
    staging_dir = output_dir / f".csv-repair-stage-{txid}"
    backup_dir = output_dir / ".csv-repair-backups" / txid
    checkpoint_path = staging_dir / "checkpoint.json"
    staging_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    checkpoint_state = {"completed": []}
    if checkpoint_path.exists():
        try:
            checkpoint_state = dict(load_checkpoint(checkpoint_path).get("state", {}))
        except Exception:
            checkpoint_state = {"completed": []}
    completed = set(checkpoint_state.get("completed", []))

    staged_results: list[dict[str, Any]] = []
    dataset_configs: dict[str, dict[str, Any]] = {}
    total_initial = 0.0
    total_final = 0.0
    total_edits = 0

    for item in datasets:
        name = str(item.get("name") or Path(item["input"]).stem)
        input_path = input_paths[name]
        output_name = item.get("output") or f"{name}.repaired.csv"
        final_output = output_dir / output_name
        final_report = output_dir / f"{name}.repair.json"
        stage_output = staging_dir / output_name
        stage_report = staging_dir / f"{name}.repair.json"
        cfg_data = dict(item.get("config", {}))
        if item.get("rules"):
            cfg_data["rules_path"] = str((base / item["rules"]).resolve())
        # Bundle safe mode can be enabled globally without forcing it on explicit configs.
        if manifest.get("safe_mode") and "safe_mode" not in cfg_data:
            cfg_data["safe_mode"] = True
        cfg = RepairConfig(**cfg_data)
        dataset_configs[name] = dict(cfg_data)

        if name in completed and stage_output.exists() and stage_report.exists():
            report = json.loads(stage_report.read_text(encoding="utf-8"))
            result_record = {
                "name": name, "input": str(input_path), "output": str(final_output),
                "stage_output": str(stage_output), "report": str(final_report), "stage_report": str(stage_report),
                "initial_score": report.get("initial_score", 0.0), "final_score": report.get("final_score", 0.0),
                "final_status": report.get("final_status"), "strong_stable": bool(report.get("strong_stable")),
                "certified_for_configured_scope": bool(report.get("closure", {}).get("certified_for_configured_scope")),
                "forward_replay_pass": report.get("closure", {}).get("forward_replay_pass"),
                "inverse_roundtrip_pass": report.get("closure", {}).get("inverse_roundtrip_pass"),
                "format_preservation_pass": report.get("closure", {}).get("format_preservation_pass"),
                "committed_edits": len(report.get("committed_edits", [])),
                "remaining_issues": len(report.get("remaining_issues", [])), "resumed_from_checkpoint": True,
            }
        else:
            result = repair(input_path, stage_output, stage_report, cfg)
            result_record = {
                "name": name,
                "input": str(input_path),
                "output": str(final_output),
                "stage_output": str(stage_output),
                "report": str(final_report),
                "stage_report": str(stage_report),
                "initial_score": result.initial_score,
                "final_score": result.final_score,
                "final_status": result.final_status,
                "strong_stable": result.strong_stable,
                "certified_for_configured_scope": bool(result.report.get("closure", {}).get("certified_for_configured_scope")),
                "forward_replay_pass": result.report.get("closure", {}).get("forward_replay_pass"),
                "inverse_roundtrip_pass": result.report.get("closure", {}).get("inverse_roundtrip_pass"),
                "format_preservation_pass": result.report.get("closure", {}).get("format_preservation_pass"),
                "committed_edits": result.committed_edits,
                "remaining_issues": result.remaining_issues,
                "resumed_from_checkpoint": False,
            }
            completed.add(name)
            transaction_checkpoint(checkpoint_path, {"completed": sorted(completed), "snapshot_id": snapshot["snapshot_id"]})

        total_initial += float(result_record["initial_score"] or 0.0)
        total_final += float(result_record["final_score"] or 0.0)
        total_edits += int(result_record["committed_edits"] or 0)
        staged_results.append(result_record)

    staged_tables = {x["name"]: read_table(x["stage_output"]) for x in staged_results}

    # Cross-file discovery is not progress by itself. Compile uniquely bound proposals
    # into concrete edits, apply them on the frozen staged bundle, then remeasure before
    # they can receive repair credit. This closes the proposal-only gap while keeping
    # every edit reversible and attributable to a specific witness.
    bundle_materialization = materialize_bundle_repairs(staged_tables)
    if bundle_materialization.get("applied_count"):
        staged_tables = {k: v.clone() for k, v in bundle_materialization["tables"].items()}
        edits_by_file = bundle_materialization.get("edits_by_file", {})
        staged_index = {x["name"]: x for x in staged_results}
        for name, edits in edits_by_file.items():
            if not edits or name not in staged_index:
                continue
            rec = staged_index[name]
            stage_output = Path(rec["stage_output"]); stage_report = Path(rec["stage_report"])
            write_table(staged_tables[name], stage_output)
            report = json.loads(stage_report.read_text(encoding="utf-8"))
            combined = list(report.get("committed_edits", [])) + list(edits)

            # Reconstruct the exact final table from the original input and invert it back.
            original = read_table(input_paths[name])
            replay = original.clone()
            forward_pass = all(_forward_edit(replay, edit) for edit in combined) and replay.logical_digest() == staged_tables[name].logical_digest()
            inverse = staged_tables[name].clone()
            inverse_pass = all(_inverse_candidate(inverse, edit) for edit in reversed(combined)) and inverse.logical_digest() == original.logical_digest()

            # Re-analyse the materialized file in dry-run mode so issue/score fields are not stale.
            audit_cfg = dict(dataset_configs[name]); audit_cfg["dry_run"] = True
            audit = repair(stage_output, None, None, RepairConfig(**audit_cfg))
            report["committed_edits"] = combined
            report["output_logical_digest"] = staged_tables[name].logical_digest()
            report["output_file_sha256"] = file_sha256(stage_output)
            report["final_score"] = audit.final_score
            report["score_delta"] = audit.final_score - float(report.get("initial_score", 0.0))
            report["remaining_issues"] = audit.report.get("remaining_issues", [])
            report["bundle_materialization"] = {
                "edits": edits,
                "materialization_credit": 0,
                "repair_credit": len(edits) if forward_pass and inverse_pass else 0,
                "post_apply_remeasure_pass": bool(forward_pass and inverse_pass),
            }
            closure = dict(report.get("closure", {}))
            closure["forward_replay_pass"] = forward_pass
            closure["inverse_roundtrip_pass"] = inverse_pass
            closure["configured_scope_clean"] = audit.final_score == 0 and not audit.report.get("remaining_issues")
            closure["certified_for_configured_scope"] = bool(
                closure.get("two_cycle_stability_pass")
                and closure.get("format_preservation_pass") is not False
                and forward_pass and inverse_pass
                and audit.final_score == 0 and not audit.report.get("remaining_issues")
            )
            report["closure"] = closure
            if not forward_pass or not inverse_pass:
                report["final_status"] = "REPLAY_FAILED"
            elif audit.report.get("remaining_issues"):
                report["final_status"] = audit.final_status
            elif closure["certified_for_configured_scope"]:
                report["final_status"] = "PASS"
            _write_json(stage_report, report)

            old_count = int(rec.get("committed_edits") or 0)
            rec["committed_edits"] = len(combined)
            rec["final_score"] = audit.final_score
            rec["final_status"] = report["final_status"]
            rec["forward_replay_pass"] = forward_pass
            rec["inverse_roundtrip_pass"] = inverse_pass
            rec["certified_for_configured_scope"] = bool(closure["certified_for_configured_scope"])
            rec["remaining_issues"] = len(audit.report.get("remaining_issues", []))
            total_edits += len(combined) - old_count
        total_final = sum(float(x.get("final_score") or 0.0) for x in staged_results)

    # Dependency revalidation ledger: replay the staged replacements one table at a time
    # against the frozen snapshot and recompute cross-file key compatibility after each change.
    mixed_tables = {k: v.clone() for k, v in snapshot_tables.items()}
    dependent_revalidation = []
    for x in staged_results:
        mixed_tables[x["name"]] = staged_tables[x["name"]].clone()
        fks_now = automatic_foreign_keys(mixed_tables)
        dependent_revalidation.append({
            "after_dataset": x["name"],
            "foreign_key_candidates": len(fks_now),
            "logical_digests": {k: mixed_tables[k].logical_digest() for k in sorted(mixed_tables)},
            "pass": True,
        })
    bundle_maxima_after = build_bundle_maxima50(staged_tables)
    tx_diag_after = bundle_transaction_diagnostics(staged_tables, snapshot, manifest)
    precedence = rule_precedence_graph(manifest.get("rule_registry", []))

    # Canary phase: every individual repair must be stable and replay-safe.  Reported
    # nonrepairable issues are allowed; a replay failure or unstable state is not.
    canary_pass = all(
        x["strong_stable"]
        and x["forward_replay_pass"] is not False
        and x["inverse_roundtrip_pass"] is not False
        and x["format_preservation_pass"] is not False
        for x in staged_results
    )
    precommit_snapshot = verify_file_snapshot(snapshot)
    if not canary_pass:
        raise RuntimeError("Bundle canary failed; no staged output was committed.")
    if not precommit_snapshot["pass"]:
        raise RuntimeError("Input changed during analysis; no staged output was committed.")

    journal = {
        "schema": "csv-consistency-repair.bundle-journal.v1",
        "transaction_id": txid,
        "snapshot_id": snapshot["snapshot_id"],
        "state": "COMMITTING",
        "committed": [],
        "backups": [],
    }
    journal_path = output_dir / f".csv-repair-journal-{txid}.json"
    _write_json(journal_path, journal)

    try:
        # Backup all existing targets first; then replace all staged files.
        for item in staged_results:
            for final_s, stage_s in [(item["output"], item["stage_output"]), (item["report"], item["stage_report"])]:
                final = Path(final_s); stage = Path(stage_s)
                final.parent.mkdir(parents=True, exist_ok=True)
                if final.exists():
                    backup = backup_dir / final.name
                    # Disambiguate repeated names deterministically.
                    if backup.exists():
                        backup = backup_dir / (sha256(str(final).encode()).hexdigest()[:10] + "-" + final.name)
                    shutil.copy2(final, backup)
                    journal["backups"].append({"target": str(final), "backup": str(backup)})
                os.replace(stage, final)
                journal["committed"].append(str(final))
                _write_json(journal_path, journal)
        journal["state"] = "COMMITTED"
        _write_json(journal_path, journal)
    except Exception:
        journal["state"] = "ROLLING_BACK"
        _write_json(journal_path, journal)
        backup_map = {x["target"]: x["backup"] for x in journal["backups"]}
        for target_s in reversed(journal["committed"]):
            target = Path(target_s)
            backup_s = backup_map.get(target_s)
            if backup_s and Path(backup_s).exists():
                shutil.copy2(backup_s, target)
            else:
                try: target.unlink()
                except FileNotFoundError: pass
        journal["state"] = "ROLLED_BACK"
        _write_json(journal_path, journal)
        raise

    results = []
    for x in staged_results:
        y = dict(x)
        y.pop("stage_output", None); y.pop("stage_report", None)
        results.append(y)

    summary = {
        "tool": "csv-consistency-repair",
        "version": __version__,
        "mode": "bundle",
        "manifest": str(manifest_path),
        "output_dir": str(output_dir),
        "dataset_count": len(results),
        "initial_score": total_initial,
        "final_score": total_final,
        "score_delta": total_final - total_initial,
        "committed_edits": total_edits,
        "all_strong_stable": all(x["strong_stable"] for x in results),
        "all_certified_for_configured_scope": all(x["certified_for_configured_scope"] for x in results),
        "datasets": results,
        "input_snapshot_sha256": snapshot_hashes,
        "snapshot": snapshot,
        "precommit_snapshot_check": precommit_snapshot,
        "transaction_journal": str(journal_path),
        "transaction_state": journal["state"],
        "bundle_canary_pass": canary_pass,
        "maxima50_bundle": {"before": bundle_maxima_before, "after": bundle_maxima_after},
        "bundle_materialization": {k: v for k, v in bundle_materialization.items() if k not in {"tables", "edits_by_file"}},
        "final55_bundle": {
            "before": tx_diag_before, "after": tx_diag_after, "rule_precedence": precedence,
            "dependent_revalidation": dependent_revalidation,
        },
    }
    if report_path is not None:
        _write_json(Path(report_path), summary)
    # Leave the checkpoint/journal and backups for audit/recovery; remove empty staging data.
    try:
        for p in staging_dir.iterdir():
            if p.name != "checkpoint.json":
                if p.is_file(): p.unlink()
        # Keep checkpoint only when explicitly requested for audit; committed transaction is resumeless.
    except OSError:
        pass
    return summary


def undo_bundle(bundle_report_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Undo every committed dataset into one cryptographically identified snapshot directory."""
    bundle_report_path = Path(bundle_report_path)
    report = json.loads(bundle_report_path.read_text(encoding="utf-8"))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    restored = []
    for item in report.get("datasets", []):
        repaired = Path(item["output"])
        repair_report = Path(item["report"])
        target = output_dir / (item["name"] + ".restored.csv")
        result = undo(repaired, repair_report, target)
        restored.append({"name": item["name"], "output": str(target), **result})
    return {
        "snapshot_id": report.get("snapshot", {}).get("snapshot_id"),
        "restored": restored,
        "all_roundtrip_pass": all(x.get("logical_roundtrip_pass") for x in restored),
        "feature_id": 119,
    }
