from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .bundle import repair_bundle, undo_bundle
from .engine import RepairConfig, repair, undo
from .maxima55 import export_learned_rules, drift_report
from .streaming import StreamRepairConfig, stream_repair, stream_undo


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="csv-consistency-repair",
        description="Conservative automatic consistency repair for CSV datasets.",
    )
    p.add_argument("input", nargs="?", help="Input CSV path, repaired CSV path with --undo, or bundle manifest with --bundle.")
    p.add_argument("-o", "--output", required=True, help="Output CSV path, restored CSV path, or bundle output directory.")
    p.add_argument("--report", required=True, help="Repair report JSON path.")
    p.add_argument("--rules", help="Optional JSON rules file for declared table relationships.")
    p.add_argument("--discover-relationships", action="store_true", help="Discover repeated column mappings and stress-test them across row scopes before reporting them.")
    p.add_argument("--repair-discovered-relationships", action="store_true", help="Allow repairs from discovered mappings only when stability checks pass.")
    p.add_argument("--discovery-confidence", type=float, default=0.95, help="Minimum confidence for discovered repeated mappings.")
    p.add_argument("--discovery-min-rows", type=int, default=12, help="Minimum repeated-row evidence for relationship discovery.")
    p.add_argument("--discover-numeric-constraints", action="store_true", help="Discover stable numeric equations between columns.")
    p.add_argument("--repair-numeric-constraints", action="store_true", help="Repair only when multiple independent stable equations reconstruct the same cell value.")
    p.add_argument("--numeric-min-independent-constraints", type=int, default=2, help="Minimum independent equations required for an automatic numeric repair.")
    p.add_argument("--numeric-max-formula-terms", type=int, choices=[2,3], default=2, help="Maximum number of source columns in automatically discovered numeric formulas.")
    p.add_argument("--repair-missing-values", action="store_true", help="Project missing cells only from stable repeated mappings or exact numeric relations.")
    p.add_argument("--project-missing", action="store_true", help="Enable conservative missing-cell projection from stable mappings, numeric formulas, and elapsed-time relations.")
    p.add_argument("--max-determinant-columns", type=int, choices=[1,2], default=1, help="Maximum number of determinant columns used for discovered repeated mappings.")
    p.add_argument("--discover-temporal-constraints", action="store_true", help="Discover stable start/end/duration relationships.")
    p.add_argument("--repair-temporal-missing", action="store_true", help="Compute one missing start/end/duration value from an exact stable temporal relation.")
    p.add_argument("--discover-scoped-relations", action="store_true", help="Discover stable formulas that are valid only within a categorical group or row segment.")
    p.add_argument("--repair-scoped-missing", action="store_true", help="Compute missing values only inside the learned scope and source range of a stable scoped formula.")
    p.add_argument("--repair-scoped-values", action="store_true", help="Repair existing scoped-formula violations only when independent scoped formulas agree.")
    p.add_argument("--discover-sequential-constraints", action="store_true", help="Discover stable running-balance relations across adjacent rows.")
    p.add_argument("--repair-sequential-missing", action="store_true", help="Compute missing values from exact stable running-balance relations.")
    p.add_argument("--repair-sequential-values", action="store_true", help="Repair sequence values only when forward and backward checks agree.")
    p.add_argument("--bundle", action="store_true", help="Treat input as a multi-file bundle manifest.")
    p.add_argument("--deduplicate", action="store_true", help="Remove exact duplicate rows when globally improving.")
    p.add_argument("--normalize-null-markers", action="store_true", help="Normalize common NA/null markers to empty fields.")
    p.add_argument("--normalize-booleans", action="store_true", help="Normalize strongly inferred boolean columns to true/false.")
    p.add_argument("--max-cycles", type=int, default=8)
    p.add_argument("--maxima", action="store_true", help="Enable the 50-feature advanced CSV diagnostics layer (no extra edits by itself).")
    p.add_argument("--maxima-final", action="store_true", help="Enable the final semantic, transaction, streaming, scale, and audit diagnostics layer.")
    p.add_argument("--safe", action="store_true", help="One-command conservative mode: broad discovery, certified repairs only, abstain when ambiguous.")
    p.add_argument("--no-auto", action="store_true", help="Disable zero-configuration automatic repair discovery and use only explicitly requested operations.")
    p.add_argument("--stream", action="store_true", help="Use the bounded-memory fast path for local cleanup/exact deduplication (no global formula discovery).")
    p.add_argument("--export-rules", help="Write learned relationship/constraint registries to a reusable JSON file.")
    p.add_argument("--drift-against", help="Compare learned constraints with a previous repair report and add a drift report.")
    p.add_argument("--maxima-repair", action="store_true", help="Enable conservative material repairs from explicitly safe Maxima features.")
    p.add_argument("--maxima-repair-headers", action="store_true", help="Repair uniquely canonicalizable headers.")
    p.add_argument("--maxima-repair-row-alignment", action="store_true", help="Repair only uniquely type-compatible short/long rows.")
    p.add_argument("--maxima-repair-locale-numbers", action="store_true", help="Canonicalize only decisively inferred locale-formatted numeric columns.")
    p.add_argument("--maxima-repair-low-rank-missing", action="store_true", help="Project missing cells only from multiple agreeing exact rank-1 witnesses.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--undo", action="store_true", help="Reverse committed edits using --report.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input:
        print("error: input path is required", file=sys.stderr)
        return 1
    try:
        if args.bundle and args.undo:
            result = undo_bundle(args.input, args.output)
            print(json.dumps(result, indent=2))
            return 0 if result.get("all_roundtrip_pass") else 2
        if args.bundle:
            result = repair_bundle(args.input, args.output, args.report)
            print(json.dumps(result, indent=2))
            return 0
        if args.stream and args.undo:
            result = stream_undo(args.input, args.report, args.output)
            print(json.dumps(result, indent=2))
            return 0 if result.get("logical_roundtrip_pass") else 2
        if args.undo:
            # Auto-dispatch undo from the report type so an operator does not
            # need to remember which repair path originally created it.
            report_mode = None
            try:
                report_mode = json.loads(Path(args.report).read_text(encoding="utf-8")).get("mode")
            except (OSError, json.JSONDecodeError, AttributeError):
                report_mode = None
            if report_mode == "bounded_memory_stream_repair":
                result = stream_undo(args.input, args.report, args.output)
                print(json.dumps(result, indent=2))
                return 0 if result.get("logical_roundtrip_pass") else 2
            result = undo(args.input, args.report, args.output)
            print(json.dumps(result, indent=2))
            return 0 if result["logical_roundtrip_pass"] else 2
        temp_input = None
        temp_output = None
        effective_input = args.input
        effective_output = args.output
        pipeline_stdout = args.output == "-"
        if args.input == "-":
            fd, temp_input = tempfile.mkstemp(prefix="csv-repair-stdin-", suffix=".csv")
            Path(temp_input).write_bytes(sys.stdin.buffer.read())
            effective_input = temp_input
            try:
                import os; os.close(fd)
            except OSError:
                pass
        if pipeline_stdout:
            fd, temp_output = tempfile.mkstemp(prefix="csv-repair-stdout-", suffix=".csv")
            try:
                import os; os.close(fd)
            except OSError:
                pass
            effective_output = temp_output
        if args.stream:
            complex_flags = [
                args.rules, args.discover_relationships, args.repair_discovered_relationships,
                args.discover_numeric_constraints, args.repair_numeric_constraints, args.project_missing,
                args.discover_temporal_constraints, args.repair_temporal_missing,
                args.discover_scoped_relations, args.repair_scoped_missing, args.repair_scoped_values,
                args.discover_sequential_constraints, args.repair_sequential_missing, args.repair_sequential_values,
                args.maxima, args.maxima_final, args.maxima_repair, args.safe,
            ]
            if any(complex_flags):
                raise ValueError("--stream is the bounded-memory local-repair path and cannot be combined with global discovery/Maxima/safe-mode options.")
            sr = stream_repair(
                effective_input, effective_output, args.report,
                StreamRepairConfig(
                    trim_outer_whitespace=True,
                    normalize_null_markers=args.normalize_null_markers,
                    normalize_booleans=args.normalize_booleans,
                    remove_exact_duplicates=args.deduplicate,
                    verify_replay=True,
                ),
            )
            if pipeline_stdout and temp_output:
                sys.stdout.buffer.write(Path(temp_output).read_bytes()); sys.stdout.buffer.flush()
            print(json.dumps(sr, indent=2), file=sys.stderr if pipeline_stdout else sys.stdout)
            for tmp in (temp_input, temp_output):
                if tmp:
                    try: Path(tmp).unlink()
                    except OSError: pass
            return 0 if sr.get("replay_pass") is not False else 2
        cfg = RepairConfig(
            remove_exact_duplicates=args.deduplicate,
            normalize_null_markers=args.normalize_null_markers,
            normalize_booleans=args.normalize_booleans,
            max_cycles=max(1, args.max_cycles),
            dry_run=args.dry_run,
            rules_path=args.rules,
            discover_relationships=args.discover_relationships or args.repair_discovered_relationships or args.project_missing,
            repair_discovered_relationships=args.repair_discovered_relationships,
            discovery_confidence=min(1.0, max(0.5, args.discovery_confidence)),
            discovery_min_rows=max(4, args.discovery_min_rows),
            discover_numeric_constraints=args.discover_numeric_constraints or args.repair_numeric_constraints or args.project_missing,
            repair_numeric_constraints=args.repair_numeric_constraints,
            numeric_min_independent_constraints=max(2, args.numeric_min_independent_constraints),
            numeric_max_formula_terms=args.numeric_max_formula_terms,
            repair_missing_values=args.repair_missing_values or args.project_missing,
            discovery_max_determinant_columns=args.max_determinant_columns,
            discover_temporal_constraints=args.discover_temporal_constraints or args.repair_temporal_missing or args.project_missing,
            repair_temporal_missing=args.repair_temporal_missing or args.project_missing,
            discover_scoped_relations=args.discover_scoped_relations or args.repair_scoped_missing or args.repair_scoped_values or args.project_missing,
            repair_scoped_missing=args.repair_scoped_missing or args.project_missing,
            repair_scoped_values=args.repair_scoped_values,
            discover_sequential_constraints=args.discover_sequential_constraints or args.repair_sequential_missing or args.repair_sequential_values or args.project_missing,
            repair_sequential_missing=args.repair_sequential_missing or args.project_missing,
            repair_sequential_values=args.repair_sequential_values,
            maxima50=args.maxima or args.maxima_repair or args.maxima_repair_headers or args.maxima_repair_row_alignment or args.maxima_repair_locale_numbers or args.maxima_repair_low_rank_missing,
            maxima_repair=args.maxima_repair or args.maxima_repair_headers or args.maxima_repair_row_alignment or args.maxima_repair_locale_numbers or args.maxima_repair_low_rank_missing,
            maxima_repair_headers=args.maxima_repair_headers,
            maxima_repair_row_alignment=args.maxima_repair_row_alignment,
            maxima_repair_locale_numbers=args.maxima_repair_locale_numbers,
            maxima_repair_low_rank_missing=args.maxima_repair_low_rank_missing,
            maxima55=args.maxima_final or args.safe,
            safe_mode=args.safe,
            auto_mode=not args.no_auto,
        )
        result = repair(effective_input, effective_output, args.report, cfg)
        if args.export_rules:
            export_learned_rules(result.report, args.export_rules)
        if args.drift_against:
            old = json.loads(Path(args.drift_against).read_text(encoding="utf-8"))
            dr = drift_report(old, result.report)
            result.report["drift_report"] = dr
            Path(args.report).write_text(json.dumps(result.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if pipeline_stdout and temp_output:
            sys.stdout.buffer.write(Path(temp_output).read_bytes())
            sys.stdout.buffer.flush()
        summary = {
            "input": result.input_path,
            "output": result.output_path,
            "report": result.report_path,
            "initial_score": result.initial_score,
            "final_score": result.final_score,
            "final_status": result.final_status,
            "strong_stable": result.strong_stable,
            "cycles": result.cycles,
            "committed_edits": result.committed_edits,
            "remaining_issues": result.remaining_issues,
        }
        print(json.dumps(summary, indent=2), file=sys.stderr if pipeline_stdout else sys.stdout)
        for tmp in (temp_input, temp_output):
            if tmp:
                try: Path(tmp).unlink()
                except OSError: pass
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
