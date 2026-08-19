from __future__ import annotations

"""Conservative cross-file repair materialization.

Discovery alone never changes a table.  A proposal becomes an executable edit only
when its witness is bound to one frozen bundle snapshot, the target still has the
expected old value, competing proposals agree, and a post-application remeasurement
confirms the cross-file relation.
"""

from collections import defaultdict
from copy import deepcopy
from hashlib import sha1
from typing import Any

from .models import Table
from .maxima50 import build_bundle_maxima50
from .maxima55 import automatic_foreign_keys


def _norm(value: str) -> str:
    return " ".join(str(value).strip().split()).casefold()


def _proposal_id(*parts: Any) -> str:
    raw = "|".join(repr(x) for x in parts)
    return sha1(raw.encode("utf-8")).hexdigest()[:20]


def _unique_parent_index(table: Table, key_col: int) -> dict[str, int] | None:
    index: dict[str, int] = {}
    for r, row in enumerate(table.rows):
        if key_col >= len(row):
            continue
        key = row[key_col]
        if not key:
            continue
        if key in index:
            return None
        index[key] = r
    return index


def discover_bundle_repair_proposals(
    tables: dict[str, Table], *, min_support: int = 8, min_agreement: float = 0.95
) -> list[dict[str, Any]]:
    """Return bound cross-file proposals without mutating any input table."""
    proposals: list[dict[str, Any]] = []

    # Exact missing-cell reconstruction already discovered from bidirectional unique joins.
    diag50 = build_bundle_maxima50(tables)
    for p in diag50.get("cross_file_reconstruction", []):
        file_name = p["file"]
        table = tables[file_name]
        r = int(p["row"]); c = int(p["column"])
        old = table.rows[r][c] if r < len(table.rows) and c < len(table.rows[r]) else None
        if old is None or old != "" or p.get("value") in {None, ""}:
            continue
        proposals.append({
            "proposal_id": _proposal_id("unique_cross_file_missing", file_name, r, c, p.get("value"), p.get("witness_file"), p.get("key")),
            "kind": "cross_file_missing_reconstruction",
            "file": file_name,
            "row": r,
            "column": c,
            "old_value": old,
            "new_value": str(p["value"]),
            "witness_file": p.get("witness_file"),
            "key": p.get("key"),
            "basis": "unique_bidirectional_join_same_attribute",
            "support": None,
            "agreement": 1.0,
        })

    # Parent/child relations: materialize a child attribute only if a unique parent key
    # plus strong repeated agreement identifies the field relation in the frozen bundle.
    for fk in automatic_foreign_keys(tables):
        child_name = fk["child_file"]; parent_name = fk["parent_file"]
        cc = int(fk["child_column"]); pc = int(fk["parent_column"])
        child = tables[child_name]; parent = tables[parent_name]
        parent_index = _unique_parent_index(parent, pc)
        if not parent_index:
            continue

        # First, exact key repairs already surfaced by the FK detector.
        key_suggestions = {x["value"]: x["suggested"] for x in fk.get("repair_proposals", []) if x.get("suggested")}
        for r, row in enumerate(child.rows):
            if cc >= len(row):
                continue
            old = row[cc]
            new = key_suggestions.get(old)
            if new and new != old:
                proposals.append({
                    "proposal_id": _proposal_id("fk_key", child_name, r, cc, old, new, parent_name),
                    "kind": "foreign_key_repair",
                    "file": child_name,
                    "row": r,
                    "column": cc,
                    "old_value": old,
                    "new_value": new,
                    "witness_file": parent_name,
                    "parent_key_column": pc,
                    "child_key_column": cc,
                    "key": new,
                    "basis": "unique_parent_key_match",
                    "support": len(parent_index),
                    "agreement": 1.0,
                })

        common_headers = sorted(set(child.header) & set(parent.header))
        for header in common_headers:
            dc = child.header.index(header); dp = parent.header.index(header)
            if dc == cc or dp == pc:
                continue
            support = 0; agree = 0
            for row in child.rows:
                if cc >= len(row) or dc >= len(row):
                    continue
                key = row[cc]
                pr = parent_index.get(key)
                if pr is None or dp >= len(parent.rows[pr]):
                    continue
                cv = row[dc]; pv = parent.rows[pr][dp]
                if not cv or not pv:
                    continue
                support += 1
                if _norm(cv) == _norm(pv):
                    agree += 1
            if support < min_support:
                continue
            ratio = agree / support if support else 0.0
            if ratio < min_agreement:
                continue

            for r, row in enumerate(child.rows):
                if cc >= len(row) or dc >= len(row):
                    continue
                key = row[cc]
                pr = parent_index.get(key)
                if pr is None or dp >= len(parent.rows[pr]):
                    continue
                expected = parent.rows[pr][dp]
                if expected == "":
                    continue
                old = row[dc]
                if _norm(old) == _norm(expected):
                    continue
                proposals.append({
                    "proposal_id": _proposal_id("parent_attr", child_name, r, dc, old, expected, parent_name, key, header),
                    "kind": "cross_file_attribute_repair",
                    "file": child_name,
                    "row": r,
                    "column": dc,
                    "old_value": old,
                    "new_value": expected,
                    "witness_file": parent_name,
                    "parent_key_column": pc,
                    "child_key_column": cc,
                    "parent_value_column": dp,
                    "attribute": header,
                    "key": key,
                    "basis": "unique_parent_key_plus_repeated_attribute_agreement",
                    "support": support,
                    "agreement": ratio,
                })

    return sorted(proposals, key=lambda x: (x["file"], x["row"], x["column"], x["proposal_id"]))


def _proposal_still_bound(tables: dict[str, Table], p: dict[str, Any]) -> bool:
    file_name = p["file"]
    if file_name not in tables:
        return False
    table = tables[file_name]
    r = int(p["row"]); c = int(p["column"])
    if r >= len(table.rows) or c >= len(table.rows[r]):
        return False
    if table.rows[r][c] != p["new_value"]:
        return False
    parent_name = p.get("witness_file")
    if parent_name and p.get("parent_key_column") is not None:
        parent = tables.get(parent_name)
        if parent is None:
            return False
        pc = int(p["parent_key_column"])
        idx = _unique_parent_index(parent, pc)
        if not idx:
            return False
        pr = idx.get(p.get("key"))
        if pr is None:
            return False
        if p.get("parent_value_column") is not None:
            dp = int(p["parent_value_column"])
            if dp >= len(parent.rows[pr]) or _norm(parent.rows[pr][dp]) != _norm(p["new_value"]):
                return False
    return True


def materialize_bundle_repairs(tables: dict[str, Table]) -> dict[str, Any]:
    """Compile proposals to edits, apply them on clones, then remeasure.

    `materialization_credit` is always zero: merely creating an executable object is not
    repair progress. `repair_credit` is granted only after the applied value is re-bound
    to the same frozen witness relation.
    """
    frozen = {k: v.clone() for k, v in tables.items()}
    proposals = discover_bundle_repair_proposals(frozen)

    by_target: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for p in proposals:
        by_target[(p["file"], int(p["row"]), int(p["column"]))].append(p)

    compiled: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for target, group in sorted(by_target.items()):
        values = sorted(set(str(x["new_value"]) for x in group))
        if len(values) != 1:
            conflicts.append({"target": list(target), "proposal_ids": [x["proposal_id"] for x in group], "candidate_values": values})
            continue
        # Merge witness aliases for the same concrete edit.
        p = deepcopy(group[0])
        p["witness_count"] = len({(x.get("witness_file"), x.get("basis"), x.get("key")) for x in group})
        p["source_proposal_ids"] = [x["proposal_id"] for x in group]
        compiled.append(p)

    trial = {k: v.clone() for k, v in frozen.items()}
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for p in compiled:
        table = trial[p["file"]]; r = int(p["row"]); c = int(p["column"])
        if r >= len(table.rows) or c >= len(table.rows[r]) or table.rows[r][c] != p["old_value"]:
            rejected.append({**p, "status": "STALE_TARGET", "materialization_credit": 0, "repair_credit": 0})
            continue
        table.rows[r][c] = p["new_value"]
        if not _proposal_still_bound(trial, p):
            table.rows[r][c] = p["old_value"]
            rejected.append({**p, "status": "REMEASURE_FAILED", "materialization_credit": 0, "repair_credit": 0})
            continue
        edit = {
            "candidate_id": "bundle-mat-" + p["proposal_id"],
            "analyzer": "bundle_relationship_repair",
            "operation": "set_cell",
            "reason": "Repair a cross-file inconsistency from a frozen, uniquely bound relationship witness.",
            "cost": 1.0,
            "confidence": 1.0 if (p.get("agreement") or 0) >= 0.999999 else float(p.get("agreement") or 0.0),
            "row": r,
            "column": c,
            "old_value": p["old_value"],
            "new_value": p["new_value"],
            "old_row": None,
            "new_row": None,
            "reversible": True,
            "metadata": {
                "kind": p["kind"], "witness_file": p.get("witness_file"), "key": p.get("key"),
                "basis": p.get("basis"), "support": p.get("support"), "agreement": p.get("agreement"),
                "materialization_status": "OUTPUT_RUNTIME_OBJECT_MATERIALIZED",
                "materialization_credit": 0,
                "post_apply_remeasure_pass": True,
            },
            "decision": "committed_bundle_materialization",
        }
        applied.append({**p, "status": "APPLIED_REMEASURED", "materialization_credit": 0, "repair_credit": 1, "edit": edit})

    # Final all-at-once remeasurement. If any applied edit lost its witness binding after
    # other edits, reject the whole materialized bundle rather than partially trust it.
    all_bound = all(_proposal_still_bound(trial, p) for p in applied)
    if not all_bound:
        trial = frozen
        rejected.extend({**p, "status": "BUNDLE_REMEASURE_FAILED", "materialization_credit": 0, "repair_credit": 0} for p in applied)
        applied = []

    edits_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in applied:
        edits_by_file[p["file"]].append(p["edit"])

    return {
        "schema": "csv-consistency-repair.bundle-materialization.v1",
        "proposals_seen": len(proposals),
        "compiled_objects": len(compiled),
        "conflicts": conflicts,
        "applied_count": len(applied),
        "rejected_count": len(rejected),
        "applied": applied,
        "rejected": rejected,
        "edits_by_file": dict(edits_by_file),
        "tables": trial,
        "materialization_credit": 0,
        "repair_credit": len(applied),
        "post_apply_remeasure_pass": all_bound,
    }
