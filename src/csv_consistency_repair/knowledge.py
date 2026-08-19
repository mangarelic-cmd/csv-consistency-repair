from __future__ import annotations

from copy import deepcopy
from typing import Any


_FAMILIES = ("relationship", "numeric", "temporal", "scoped", "sequential")


def empty_knowledge_registry() -> dict[str, Any]:
    """Create an in-memory convergence knowledge registry.

    The registry is evidence memory, not repair authority.  A historical relation may
    influence ordering/explanation only when a current analyzer independently proposes
    a concrete edit.  Existing shadow validation and objective-improvement gates remain
    authoritative for every material change.
    """
    return {
        "enabled": True,
        "relations": {},
        "timeline": [],
        "summary": {},
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _relation_observation(family: str, rel: dict[str, Any]) -> dict[str, Any]:
    stability = rel.get("stability") or {}
    raw = stability.get("raw") or {}
    base = stability.get("base") or {}

    if "stable" in rel:
        stable = bool(rel.get("stable"))
    elif family == "scoped":
        stable = bool(stability.get("pass", True))
    else:
        stable = bool(stability.get("pass", False))

    confidence = _safe_float(
        rel.get("confidence",
            raw.get("confidence",
                base.get("confidence",
                    min(
                        _safe_float((rel.get("left_stability") or {}).get("base", {}).get("confidence"), 1.0),
                        _safe_float((rel.get("right_stability") or {}).get("base", {}).get("confidence"), 1.0),
                    ) if rel.get("left_stability") or rel.get("right_stability") else 0.0
                )
            )
        )
    )
    exact_confidence = _safe_float(rel.get("exact_confidence"), confidence)
    support = int(
        rel.get("eligible_rows")
        or rel.get("row_count")
        or raw.get("considered_rows")
        or base.get("eligible")
        or 0
    )
    coverage = _safe_float(raw.get("coverage"), 1.0 if support else 0.0)

    identity_keys = (
        "kind", "target", "sources", "operation", "determinant", "dependent",
        "scope_column", "scope_value", "source", "start", "end", "duration",
        "unit", "balance", "inflow", "outflow", "split_row_index",
    )
    definition = {k: deepcopy(rel[k]) for k in identity_keys if k in rel}
    return {
        "relation_id": str(rel.get("relation_id", "")),
        "family": family,
        "stable": stable,
        "confidence": confidence,
        "exact_confidence": exact_confidence,
        "support": support,
        "coverage": coverage,
        "definition": definition,
    }


def iter_registry_relations(registries: dict[str, dict[str, Any]]):
    for family in _FAMILIES:
        registry = registries.get(family) or {}
        rels = registry.get("relationships", []) if family == "relationship" else registry.get("relations", [])
        for rel in rels or []:
            rid = rel.get("relation_id")
            if rid:
                yield _relation_observation(family, rel)
        if family == "scoped":
            for rel in registry.get("row_segment_relations", []) or []:
                rid = rel.get("relation_id")
                if rid:
                    obs = _relation_observation(family, rel)
                    obs["definition"]["subkind"] = "row_segment"
                    obs["stable"] = True
                    yield obs


def _edit_ids(edits: list[dict[str, Any]] | None) -> list[str]:
    return [str(e.get("candidate_id")) for e in (edits or []) if e.get("candidate_id")]


def update_knowledge_registry(
    state: dict[str, Any],
    registries: dict[str, dict[str, Any]],
    *,
    cycle: int,
    state_digest: str,
    edits_since_previous: list[dict[str, Any]] | None = None,
) -> None:
    """Merge one observed table state into cumulative relation knowledge."""
    edit_ids = _edit_ids(edits_since_previous)
    changed = bool(edit_ids)
    observed = {o["relation_id"]: o for o in iter_registry_relations(registries)}
    entries: dict[str, Any] = state["relations"]
    transition_counts: dict[str, int] = {}

    for rid, obs in observed.items():
        previous = entries.get(rid)
        if previous is None:
            provenance = "revealed_after_repair" if changed else "initial_discovery"
            entry = {
                "relation_id": rid,
                "family": obs["family"],
                "definition": obs["definition"],
                "first_seen_cycle": cycle,
                "last_seen_cycle": cycle,
                "certified_cycle": cycle if obs["stable"] else None,
                "current_status": "certified" if obs["stable"] else "weak",
                "max_confidence": obs["confidence"],
                "max_support": obs["support"],
                "ever_strengthened_after_repair": False,
                "ever_revealed_after_repair": bool(changed),
                "observations": [],
            }
            entries[rid] = entry
        else:
            entry = previous
            prev_obs = next((x for x in reversed(entry["observations"]) if x.get("observed", True)), None)
            prev_conf = _safe_float(prev_obs.get("confidence")) if prev_obs else 0.0
            prev_stable = bool(prev_obs.get("stable")) if prev_obs else False
            delta = obs["confidence"] - prev_conf
            if obs["stable"] and not prev_stable:
                provenance = "certified_after_repair" if changed else "certified_on_recheck"
                if changed and delta > 1e-12:
                    entry["ever_strengthened_after_repair"] = True
                if entry.get("certified_cycle") is None:
                    entry["certified_cycle"] = cycle
            elif changed and delta > 1e-12:
                provenance = "strengthened_after_repair"
                entry["ever_strengthened_after_repair"] = True
            elif changed and delta < -1e-12:
                provenance = "weakened_after_repair"
            elif obs["stable"]:
                provenance = "revalidated_certified"
            else:
                provenance = "rediscovered_weak"
            entry["last_seen_cycle"] = cycle
            entry["definition"] = obs["definition"] or entry.get("definition", {})
            entry["max_confidence"] = max(_safe_float(entry.get("max_confidence")), obs["confidence"])
            entry["max_support"] = max(int(entry.get("max_support", 0)), obs["support"])

        entry["current_status"] = "certified" if obs["stable"] else "weak"
        entry["current_observed"] = True
        entry["observations"].append({
            "cycle": cycle,
            "state_digest": state_digest,
            "observed": True,
            "stable": obs["stable"],
            "confidence": obs["confidence"],
            "exact_confidence": obs["exact_confidence"],
            "support": obs["support"],
            "coverage": obs["coverage"],
            "provenance": provenance,
            "caused_by_edit_ids": edit_ids,
        })
        transition_counts[provenance] = transition_counts.get(provenance, 0) + 1

    # Preserve knowledge when a relation is temporarily not observable.  It is explicitly
    # marked historical and never treated as current repair authority.
    for rid, entry in entries.items():
        if rid in observed:
            continue
        prev_obs = entry["observations"][-1] if entry.get("observations") else None
        if prev_obs and prev_obs.get("cycle") == cycle:
            continue
        entry["current_observed"] = False
        entry["current_status"] = "historical_certified" if entry.get("certified_cycle") is not None else "historical_weak"
        provenance = "not_observed_after_repair" if changed else "not_observed_on_recheck"
        entry["observations"].append({
            "cycle": cycle,
            "state_digest": state_digest,
            "observed": False,
            "stable": False,
            "confidence": None,
            "support": 0,
            "coverage": 0.0,
            "provenance": provenance,
            "caused_by_edit_ids": edit_ids,
        })
        transition_counts[provenance] = transition_counts.get(provenance, 0) + 1

    state["timeline"].append({
        "cycle": cycle,
        "state_digest": state_digest,
        "edits_since_previous": edit_ids,
        "observed_relations": len(observed),
        "transition_counts": transition_counts,
    })
    _refresh_summary(state)


def _refresh_summary(state: dict[str, Any]) -> None:
    entries = list(state.get("relations", {}).values())
    state["summary"] = {
        "relation_count": len(entries),
        "currently_certified": sum(e.get("current_status") == "certified" for e in entries),
        "currently_weak": sum(e.get("current_status") == "weak" for e in entries),
        "historical_only": sum(str(e.get("current_status", "")).startswith("historical_") for e in entries),
        "ever_certified": sum(e.get("certified_cycle") is not None for e in entries),
        "revealed_after_repair": sum(bool(e.get("ever_revealed_after_repair")) for e in entries),
        "strengthened_after_repair": sum(bool(e.get("ever_strengthened_after_repair")) for e in entries),
        "cycles_observed": len(state.get("timeline", [])),
    }
    state["views"] = {
        "initial_relations": sorted(e["relation_id"] for e in entries if e.get("first_seen_cycle") == 0),
        "newly_certified_relations": sorted(e["relation_id"] for e in entries if isinstance(e.get("certified_cycle"), int) and e.get("certified_cycle", 0) > 0),
        "revealed_by_repair_relations": sorted(e["relation_id"] for e in entries if e.get("ever_revealed_after_repair")),
        "strengthened_by_repair_relations": sorted(e["relation_id"] for e in entries if e.get("ever_strengthened_after_repair")),
        "historical_relations": sorted(e["relation_id"] for e in entries if str(e.get("current_status", "")).startswith("historical_")),
    }


def relation_priority_map(state: dict[str, Any]) -> dict[str, int]:
    """Return non-authoritative ordering hints for currently proposed candidates."""
    out: dict[str, int] = {}
    for rid, entry in state.get("relations", {}).items():
        status = entry.get("current_status")
        if status == "certified":
            # A relation that was strengthened/revealed through convergence gets the
            # strongest ordering hint, but all edits still pass the normal shadow gates.
            if entry.get("ever_strengthened_after_repair") or entry.get("ever_revealed_after_repair"):
                out[rid] = 0
            else:
                out[rid] = 1
        elif status == "weak":
            out[rid] = 2
        else:
            out[rid] = 3
        if (entry.get("definition") or {}).get("subkind") == "row_segment":
            out[rid + "L"] = out[rid]
            out[rid + "R"] = out[rid]
    return out


def candidate_relation_ids(metadata: dict[str, Any] | None) -> list[str]:
    meta = metadata or {}
    ids: list[str] = []
    if meta.get("relation_id"):
        ids.append(str(meta["relation_id"]))
    for key in ("relation_ids", "supporting_relations"):
        val = meta.get(key) or []
        if isinstance(val, (list, tuple, set)):
            ids.extend(str(x) for x in val)
    return sorted(set(ids))


def candidate_knowledge_rank(metadata: dict[str, Any] | None, priorities: dict[str, int] | None) -> int:
    ids = candidate_relation_ids(metadata)
    if not ids or not priorities:
        return 4
    return min(priorities.get(rid, 4) for rid in ids)


def public_knowledge_registry(state: dict[str, Any]) -> dict[str, Any]:
    # Deep copy because report consumers may retain/mutate the returned structure.
    return deepcopy(state)
