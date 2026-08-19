from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def build_constraint_graph(table, relationship_registry: dict[str, Any], numeric_registry: dict[str, Any], scoped_registry: dict[str, Any] | None = None, sequential_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    nodes = [{"column": name, "index": i} for i, name in enumerate(table.header)]
    relations: list[dict[str, Any]] = []
    degree = Counter()
    violation_support = defaultdict(list)

    for rel in relationship_registry.get("relationships", []):
        if not rel.get("stability", {}).get("pass"):
            continue
        rid = rel["relation_id"]
        det = list(rel["determinant"])
        dep = rel["dependent"]
        relations.append({
            "relation_id": rid,
            "type": "mapping",
            "inputs": det,
            "output": dep,
            "confidence": rel["stability"]["raw"]["confidence"],
        })
        for c in det + [dep]:
            degree[c] += 1
        dep_idx = table.header.index(dep)
        for v in rel["stability"]["raw"].get("violations", []):
            violation_support[(int(v["row"]), dep_idx)].append(rid)
        for v in rel["stability"]["raw"].get("missing", []):
            violation_support[(int(v["row"]), dep_idx)].append(rid)

    for rel in numeric_registry.get("relations", []):
        if not rel.get("stable"):
            continue
        rid = rel["relation_id"]
        src = list(rel["sources"])
        tgt = rel["target"]
        relations.append({
            "relation_id": rid,
            "type": "numeric_formula",
            "operation": rel["operation"],
            "inputs": src,
            "output": tgt,
            "confidence": rel["confidence"],
            "exact_confidence": rel.get("exact_confidence"),
        })
        for c in src + [tgt]:
            degree[c] += 1
        tgt_idx = int(rel.get("target_index", table.header.index(tgt)))
        for v in rel.get("violations", []):
            violation_support[(int(v["row"]), tgt_idx)].append(rid)

    scoped_registry = scoped_registry or {}
    sequential_registry = sequential_registry or {}

    for rel in scoped_registry.get("relations", []):
        rid = rel["relation_id"]
        src = rel["source"]
        tgt = rel["target"]
        scope = rel["scope_column"]
        relations.append({
            "relation_id": rid,
            "type": "scoped_formula",
            "scope_column": scope,
            "scope_value": rel["scope_value"],
            "inputs": [scope, src],
            "output": tgt,
            "confidence": rel["stability"]["base"]["confidence"],
        })
        for c in (scope, src, tgt):
            degree[c] += 1

    for rel in scoped_registry.get("row_segment_relations", []):
        rid = rel["relation_id"]
        src = rel["source"]
        tgt = rel["target"]
        relations.append({
            "relation_id": rid,
            "type": "row_segment_formula",
            "split_row_index": rel["split_row_index"],
            "inputs": [src],
            "output": tgt,
            "confidence": rel["confidence"],
        })
        degree[src] += 1
        degree[tgt] += 1

    for rel in sequential_registry.get("relations", []):
        rid = rel["relation_id"]
        inputs = [rel["balance"], rel["inflow"]]
        if rel.get("outflow"):
            inputs.append(rel["outflow"])
        tgt = rel["balance"]
        relations.append({
            "relation_id": rid,
            "type": "running_balance",
            "inputs": inputs,
            "output": tgt,
            "confidence": rel["confidence"],
            "uses_previous_row": True,
        })
        for c in set(inputs + [tgt]):
            degree[c] += 1

    cells = []
    for (r, c), rels in sorted(violation_support.items()):
        cells.append({
            "row": r,
            "column": c,
            "column_name": table.header[c],
            "relation_support": len(set(rels)),
            "relation_ids": sorted(set(rels)),
        })

    return {
        "node_count": len(nodes),
        "relation_count": len(relations),
        "nodes": [n | {"constraint_degree": degree[n["column"]]} for n in nodes],
        "relations": relations,
        "cells_with_structural_evidence": cells,
        "multi_relation_cells": sum(1 for c in cells if c["relation_support"] >= 2),
        "description": "Column-level dependency graph used to explain which stable relations support each detected inconsistency or projection.",
    }
