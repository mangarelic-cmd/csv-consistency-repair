from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable
import hashlib

from .models import AnalysisResult, Candidate, Issue, Table


def _candidate_id(*parts: Any) -> str:
    return hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()[:16]


def _norm(value: str) -> str:
    return value.strip().casefold()


@dataclass(frozen=True)
class RelationStats:
    eligible_rows: int
    considered_rows: int
    repeated_groups: int
    confidence: float
    coverage: float
    null_rate: float
    violations: tuple[tuple[int, str, str, int, float], ...]
    missing: tuple[tuple[int, str, int, float], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible_rows": self.eligible_rows,
            "considered_rows": self.considered_rows,
            "repeated_groups": self.repeated_groups,
            "confidence": self.confidence,
            "coverage": self.coverage,
            "null_rate": self.null_rate,
            "violations": [
                {
                    "row": row,
                    "observed": observed,
                    "expected": expected,
                    "group_support": support,
                    "group_confidence": confidence,
                }
                for row, observed, expected, support, confidence in self.violations
            ],
            "missing": [
                {
                    "row": row,
                    "expected": expected,
                    "group_support": support,
                    "group_confidence": confidence,
                }
                for row, expected, support, confidence in self.missing
            ],
        }


def _relation_stats(
    table: Table,
    determinants: tuple[int, ...],
    dependent: int,
    row_indexes: Iterable[int] | None = None,
    *,
    normalize: bool = False,
) -> RelationStats:
    indexes = list(range(len(table.rows))) if row_indexes is None else list(row_indexes)
    groups: dict[tuple[str, ...], list[tuple[int, str]]] = defaultdict(list)
    missing_by_group: dict[tuple[str, ...], list[int]] = defaultdict(list)
    eligible = 0
    nulls = 0

    for r in indexes:
        row = table.rows[r]
        if dependent >= len(row) or any(c >= len(row) for c in determinants):
            continue
        key_parts = [row[c] for c in determinants]
        value = row[dependent]
        if normalize:
            key_parts = [_norm(v) for v in key_parts]
            value = _norm(value)
        if any(v == "" for v in key_parts):
            continue
        key = tuple(key_parts)
        eligible += 1
        if value == "":
            nulls += 1
            missing_by_group[key].append(r)
            continue
        groups[key].append((r, value))

    repeated = {k: items for k, items in groups.items() if len(items) >= 2}
    considered = sum(len(items) for items in repeated.values())
    if considered == 0:
        return RelationStats(
            eligible_rows=eligible,
            considered_rows=0,
            repeated_groups=0,
            confidence=0.0,
            coverage=0.0,
            null_rate=(nulls / eligible if eligible else 0.0),
            violations=(),
            missing=(),
        )

    matched = 0
    violations: list[tuple[int, str, str, int, float]] = []
    missing: list[tuple[int, str, int, float]] = []
    for key, items in repeated.items():
        counts = Counter(v for _, v in items)
        winner, winner_count = counts.most_common(1)[0]
        local_confidence = winner_count / len(items)
        matched += winner_count
        for r, value in items:
            if value != winner:
                violations.append((r, value, winner, len(items), local_confidence))
        for r in missing_by_group.get(key, []):
            missing.append((r, winner, len(items), local_confidence))

    return RelationStats(
        eligible_rows=eligible,
        considered_rows=considered,
        repeated_groups=len(repeated),
        confidence=matched / considered,
        coverage=(considered / eligible if eligible else 0.0),
        null_rate=(nulls / eligible if eligible else 0.0),
        violations=tuple(violations),
        missing=tuple(missing),
    )


def _partitions(n: int) -> list[tuple[str, list[int]]]:
    if n <= 0:
        return []
    half = n // 2
    return [
        ("even_rows", list(range(0, n, 2))),
        ("odd_rows", list(range(1, n, 2))),
        ("first_half", list(range(0, half))),
        ("second_half", list(range(half, n))),
    ]


def _stability_contract(table: Table, determinants: tuple[int, ...], dependent: int, config) -> dict[str, Any]:
    threshold = float(config.discovery_confidence)
    tolerance = max(0.0, float(config.discovery_stress_tolerance))
    min_rows = max(4, int(config.discovery_min_rows))
    min_coverage = min(1.0, max(0.0, float(config.discovery_min_coverage)))

    raw = _relation_stats(table, determinants, dependent)
    normalized = _relation_stats(table, determinants, dependent, normalize=True)
    base_pass = (
        raw.considered_rows >= min_rows
        and raw.repeated_groups >= 2
        and raw.confidence >= threshold
        and raw.coverage >= min_coverage
    )

    stress_rows_floor = max(4, min_rows // 3)
    partition_checks = []
    applicable = 0
    partition_pass = True
    for name, indexes in _partitions(len(table.rows)):
        stats = _relation_stats(table, determinants, dependent, indexes)
        is_applicable = stats.considered_rows >= stress_rows_floor and stats.repeated_groups >= 1
        passed = None
        if is_applicable:
            applicable += 1
            passed = stats.confidence >= max(0.0, threshold - tolerance)
            partition_pass = partition_pass and bool(passed)
        partition_checks.append({
            "name": name,
            "applicable": is_applicable,
            "pass": passed,
            "confidence": stats.confidence,
            "coverage": stats.coverage,
            "considered_rows": stats.considered_rows,
            "repeated_groups": stats.repeated_groups,
        })

    enough_partition_evidence = applicable >= 2
    threshold_probe = min(0.999999, threshold + min(0.02, max(0.005, tolerance / 2 if tolerance else 0.01)))
    threshold_margin_pass = raw.confidence >= threshold_probe
    normalization_delta = abs(raw.confidence - normalized.confidence)
    normalization_pass = normalization_delta <= max(0.01, tolerance)
    # Missing values are allowed when projection is enabled, but a relation dominated by missing data is not trusted.
    null_scope_pass = raw.null_rate <= 0.25

    stable = bool(
        base_pass
        and enough_partition_evidence
        and partition_pass
        and threshold_margin_pass
        and normalization_pass
        and null_scope_pass
    )
    reasons = []
    if not base_pass:
        reasons.append("base_relation_not_strong_enough")
    if not enough_partition_evidence:
        reasons.append("insufficient_partition_evidence")
    elif not partition_pass:
        reasons.append("row_scope_sensitive")
    if not threshold_margin_pass:
        reasons.append("confidence_threshold_sensitive")
    if not normalization_pass:
        reasons.append("normalization_sensitive")
    if not null_scope_pass:
        reasons.append("missing_value_scope_sensitive")

    return {
        "pass": stable,
        "base_pass": base_pass,
        "reasons": reasons,
        "configured_confidence": threshold,
        "stricter_confidence_probe": threshold_probe,
        "normalization_confidence_delta": normalization_delta,
        "raw": raw.to_dict(),
        "normalized": normalized.to_dict(),
        "partition_checks": partition_checks,
    }


def discover_relationships(table: Table, config) -> dict[str, Any]:
    width = len(table.header)
    relations: list[dict[str, Any]] = []
    stable_count = 0
    unstable_count = 0
    max_det = max(1, min(2, int(getattr(config, "discovery_max_determinant_columns", 1))))

    columns = list(range(width))
    for det_size in range(1, max_det + 1):
        for determinants in combinations(columns, det_size):
            for dep in range(width):
                if dep in determinants:
                    continue
                contract = _stability_contract(table, determinants, dep, config)
                raw = contract["raw"]
                if raw["considered_rows"] < max(4, int(config.discovery_min_rows) // 2):
                    continue
                if raw["repeated_groups"] < 1:
                    continue
                det_names = [table.header[c] for c in determinants]
                relation_id = _candidate_id("fd", tuple(det_names), table.header[dep])
                item = {
                    "relation_id": relation_id,
                    "kind": "functional_dependency",
                    "determinant": det_names,
                    "dependent": table.header[dep],
                    "stability": contract,
                }
                relations.append(item)
                if contract["pass"]:
                    stable_count += 1
                else:
                    unstable_count += 1

    unique_candidates = []
    max_unique = max_det
    for size in range(1, max_unique + 1):
        for cols in combinations(columns, size):
            values = []
            for row in table.rows:
                if any(c >= len(row) or row[c] == "" for c in cols):
                    continue
                values.append(tuple(row[c] for c in cols))
            if len(values) < max(4, int(config.discovery_min_rows)):
                continue
            distinct = len(set(values))
            ratio = distinct / len(values)
            if ratio == 1.0:
                unique_candidates.append({
                    "kind": "unique_key_candidate",
                    "columns": [table.header[c] for c in cols],
                    "nonempty_rows": len(values),
                    "uniqueness_ratio": ratio,
                    "status": "stable_observation",
                })

    return {
        "enabled": True,
        "relationship_count": len(relations),
        "stable_relationships": stable_count,
        "unstable_relationships": unstable_count,
        "relationships": relations,
        "unique_key_candidates": unique_candidates,
        "max_determinant_columns": max_det,
    }


class DiscoveryAnalyzer:
    name = "relationship_discovery"

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        registry = discover_relationships(table, config)
        out.evidence["relationship"] = registry
        repair_enabled = bool(config.repair_discovered_relationships)
        missing_enabled = bool(getattr(config, "repair_missing_values", False))
        min_group_support = max(3, int(config.discovery_min_group_support))
        threshold = float(config.discovery_confidence)

        for relation in registry["relationships"]:
            contract = relation["stability"]
            if not contract["base_pass"]:
                continue
            det_names = relation["determinant"]
            dep_name = relation["dependent"]
            dep_idx = table.header.index(dep_name)
            stable = bool(contract["pass"])
            for violation in contract["raw"]["violations"]:
                r = int(violation["row"])
                old = str(violation["observed"])
                new = str(violation["expected"])
                group_support = int(violation["group_support"])
                group_confidence = float(violation["group_confidence"])
                repairable = bool(
                    repair_enabled
                    and stable
                    and group_support >= min_group_support
                    and group_confidence >= threshold
                )
                out.issues.append(Issue(
                    self.name,
                    "discovered_functional_dependency_violation",
                    f"Observed {dep_name}={old!r} conflicts with a stable repeated mapping from {det_names}; expected {new!r}.",
                    "warning",
                    row=r,
                    column=dep_idx,
                    value=old,
                    repairable=repairable,
                    metadata={
                        "relation_id": relation["relation_id"],
                        "determinant": det_names,
                        "dependent": dep_name,
                        "expected": new,
                        "group_support": group_support,
                        "group_confidence": group_confidence,
                        "stability_pass": stable,
                        "stability_reasons": contract["reasons"],
                    },
                ))
                if repairable:
                    out.candidates.append(Candidate(
                        candidate_id=_candidate_id("discovered_fd", relation["relation_id"], r, dep_idx, old, new),
                        analyzer=self.name,
                        operation="set_cell",
                        reason="Repair an outlier only after the inferred mapping remains stable across scope and threshold stress checks.",
                        cost=1,
                        confidence=min(group_confidence, float(contract["raw"]["confidence"])),
                        row=r,
                        column=dep_idx,
                        old_value=old,
                        new_value=new,
                        reversible=True,
                        metadata={
                            "relation_id": relation["relation_id"],
                            "rule_type": "discovered_functional_dependency",
                            "stability_pass": True,
                            "stability_contract": contract,
                        },
                    ))

            for projection in contract["raw"].get("missing", []):
                r = int(projection["row"])
                new = str(projection["expected"])
                group_support = int(projection["group_support"])
                group_confidence = float(projection["group_confidence"])
                repairable = bool(
                    missing_enabled
                    and stable
                    and group_support >= min_group_support
                    and group_confidence >= max(threshold, 0.98)
                )
                out.issues.append(Issue(
                    self.name,
                    "missing_value_reconstructible_from_mapping",
                    f"Missing {dep_name} can be reconstructed from stable repeated mapping {det_names} -> {dep_name}.",
                    "warning",
                    row=r,
                    column=dep_idx,
                    value="",
                    repairable=repairable,
                    metadata={
                        "relation_id": relation["relation_id"],
                        "determinant": det_names,
                        "dependent": dep_name,
                        "expected": new,
                        "group_support": group_support,
                        "group_confidence": group_confidence,
                        "stability_pass": stable,
                    },
                ))
                if repairable:
                    out.candidates.append(Candidate(
                        candidate_id=_candidate_id("discovered_fd_missing", relation["relation_id"], r, dep_idx, new),
                        analyzer=self.name,
                        operation="set_cell",
                        reason="Project a missing value from a stable repeated mapping with strong local support.",
                        cost=1,
                        confidence=min(group_confidence, float(contract["raw"]["confidence"])),
                        row=r,
                        column=dep_idx,
                        old_value="",
                        new_value=new,
                        reversible=True,
                        metadata={
                            "relation_id": relation["relation_id"],
                            "rule_type": "discovered_mapping_missing_projection",
                            "stability_pass": True,
                            "projection": True,
                        },
                    ))
        return out
