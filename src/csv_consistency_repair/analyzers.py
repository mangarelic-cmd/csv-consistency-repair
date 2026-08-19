from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
import hashlib

from .models import AnalysisResult, Candidate, Issue, Table


def _candidate_id(analyzer: str, operation: str, row, column, old, new) -> str:
    payload = repr((analyzer, operation, row, column, old, new)).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def _cand(*, analyzer: str, operation: str, reason: str, row=None, column=None,
          old_value=None, new_value=None, old_row=None, cost=1, confidence=1.0,
          metadata=None) -> Candidate:
    return Candidate(
        candidate_id=_candidate_id(analyzer, operation, row, column, old_value, new_value),
        analyzer=analyzer,
        operation=operation,
        reason=reason,
        row=row,
        column=column,
        old_value=old_value,
        new_value=new_value,
        old_row=old_row,
        cost=cost,
        confidence=confidence,
        metadata=metadata or {},
    )


class Analyzer:
    name = "analyzer"

    def analyze(self, table: Table, config) -> AnalysisResult:
        raise NotImplementedError


class ShapeAnalyzer(Analyzer):
    name = "shape"

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        width = len(table.header)
        if not table.header:
            out.issues.append(Issue(self.name, "missing_header", "CSV has no header row.", "error"))
            return out
        seen = defaultdict(list)
        for i, name in enumerate(table.header):
            seen[name].append(i)
        for name, cols in seen.items():
            if len(cols) > 1:
                out.issues.append(Issue(
                    self.name, "duplicate_column_name",
                    f"Column name {name!r} occurs {len(cols)} times.",
                    "error", column=cols[1], value=name, repairable=False,
                    metadata={"columns": cols},
                ))
        for r, row in enumerate(table.rows):
            if len(row) != width:
                out.issues.append(Issue(
                    self.name, "row_width_mismatch",
                    f"Row {r + 2} has {len(row)} fields; expected {width}.",
                    "error", row=r, repairable=False,
                    metadata={"actual": len(row), "expected": width},
                ))
        return out


class WhitespaceAnalyzer(Analyzer):
    name = "whitespace"

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        trimmed_headers = [h.strip() for h in table.header]
        resulting_counts = Counter(trimmed_headers)
        for c, value in enumerate(table.header):
            new = value.strip()
            if new != value:
                safe = bool(new) and resulting_counts[new] == 1
                out.issues.append(Issue(
                    self.name, "header_outer_whitespace",
                    f"Column {c + 1} has leading or trailing whitespace.",
                    "warning", column=c, value=value, repairable=safe,
                    metadata={"suggested": new, "collision_free": safe},
                ))
                if safe:
                    out.candidates.append(_cand(
                        analyzer=self.name, operation="rename_column",
                        reason="Trim outer whitespace from a column name.",
                        column=c, old_value=value, new_value=new,
                    ))
        for r, row in enumerate(table.rows):
            for c, value in enumerate(row):
                new = value.strip()
                if new != value:
                    out.issues.append(Issue(
                        self.name, "cell_outer_whitespace",
                        f"Cell R{r + 2}C{c + 1} has leading or trailing whitespace.",
                        "warning", row=r, column=c, value=value, repairable=True,
                        metadata={"suggested": new},
                    ))
                    out.candidates.append(_cand(
                        analyzer=self.name, operation="set_cell",
                        reason="Trim outer whitespace from a field.",
                        row=r, column=c, old_value=value, new_value=new,
                    ))
        return out


class DuplicateRowAnalyzer(Analyzer):
    name = "duplicates"

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        first_seen: dict[tuple[str, ...], int] = {}
        for r, row in enumerate(table.rows):
            key = tuple(row)
            if key in first_seen:
                first = first_seen[key]
                repairable = bool(config.remove_exact_duplicates)
                out.issues.append(Issue(
                    self.name, "exact_duplicate_row",
                    f"Row {r + 2} exactly duplicates row {first + 2}.",
                    "warning", row=r, repairable=repairable,
                    metadata={"first_row": first, "duplicate_row": r},
                ))
                if repairable:
                    out.candidates.append(_cand(
                        analyzer=self.name, operation="delete_row",
                        reason="Remove an exact duplicate row under explicit deduplication policy.",
                        row=r, old_row=list(row), cost=max(1, len(row)), confidence=1.0,
                        metadata={"first_row": first},
                    ))
            else:
                first_seen[key] = r
        return out


NULL_MARKERS = {"na", "n/a", "null", "none", "nil", "missing"}


class NullMarkerAnalyzer(Analyzer):
    name = "null_markers"

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        contextual_ok: dict[int, bool] = {}
        if bool(getattr(config, "auto_contextual_nulls", False)):
            for c in range(len(table.header)):
                vals=[row[c] for row in table.rows if c < len(row)]
                empties=sum(not v.strip() for v in vals)
                markers=sum(v.strip().casefold() in NULL_MARKERS for v in vals)
                observed=sum(bool(v.strip()) and v.strip().casefold() not in NULL_MARKERS for v in vals)
                # A marker is auto-normalized only when this exact column already has an
                # empty-field missing convention and the marker is a minority residue.
                contextual_ok[c] = bool(empties >= 1 and observed >= 3 and markers <= max(2, int(.20 * max(1, observed))))
        for r, row in enumerate(table.rows):
            for c, value in enumerate(row):
                normalized = value.strip().casefold()
                if normalized in NULL_MARKERS:
                    repairable = bool(config.normalize_null_markers) and (not bool(getattr(config, "auto_contextual_nulls", False)) or contextual_ok.get(c, False))
                    out.issues.append(Issue(
                        self.name, "noncanonical_null_marker",
                        f"Cell R{r + 2}C{c + 1} uses null marker {value!r}.",
                        "info", row=r, column=c, value=value, repairable=repairable,
                        metadata={"suggested": ""},
                    ))
                    if repairable:
                        out.candidates.append(_cand(
                            analyzer=self.name, operation="set_cell",
                            reason="Normalize a common null marker to an empty field under explicit policy.",
                            row=r, column=c, old_value=value, new_value="",
                        ))
        return out


TRUE_WORDS = {"true", "yes", "y", "t"}
FALSE_WORDS = {"false", "no", "n", "f"}


class BooleanAnalyzer(Analyzer):
    name = "boolean_format"

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        width = len(table.header)
        for c in range(width):
            values = [row[c] for row in table.rows if c < len(row) and row[c] != ""]
            if len(values) < 2:
                continue
            folded = [v.strip().casefold() for v in values]
            boolish = [v for v in folded if v in TRUE_WORDS or v in FALSE_WORDS]
            if len(boolish) / len(values) < 0.9:
                continue
            for r, row in enumerate(table.rows):
                if c >= len(row):
                    continue
                raw = row[c]
                folded_value = raw.strip().casefold()
                if folded_value in TRUE_WORDS:
                    new = "true"
                elif folded_value in FALSE_WORDS:
                    new = "false"
                else:
                    continue
                if raw == new:
                    continue
                repairable = bool(config.normalize_booleans)
                out.issues.append(Issue(
                    self.name, "mixed_boolean_spelling",
                    f"Column {c + 1} mixes boolean spellings; {raw!r} can be canonicalized to {new!r}.",
                    "info", row=r, column=c, value=raw, repairable=repairable,
                    metadata={"suggested": new},
                ))
                if repairable:
                    out.candidates.append(_cand(
                        analyzer=self.name, operation="set_cell",
                        reason="Canonicalize a boolean spelling under explicit policy.",
                        row=r, column=c, old_value=raw, new_value=new,
                    ))
        return out


def _value_type(value: str) -> str:
    v = value.strip()
    if not v:
        return "empty"
    low = v.casefold()
    if low in TRUE_WORDS or low in FALSE_WORDS:
        return "bool"
    try:
        int(v)
        return "int"
    except ValueError:
        pass
    try:
        float(v)
        return "float"
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            datetime.strptime(v, fmt)
            return "date"
        except ValueError:
            pass
    return "string"


class TypeConsistencyAnalyzer(Analyzer):
    name = "type_consistency"

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        width = len(table.header)
        for c in range(width):
            typed = []
            for r, row in enumerate(table.rows):
                if c >= len(row):
                    continue
                t = _value_type(row[c])
                if t != "empty":
                    typed.append((r, row[c], t))
            if len(typed) < 3:
                continue
            counts = Counter(t for _, _, t in typed)
            # int is compatible with float in a numeric column.
            numeric = counts.get("int", 0) + counts.get("float", 0)
            dominant, dominant_count = counts.most_common(1)[0]
            if numeric > dominant_count:
                dominant = "number"
                dominant_count = numeric
            if dominant_count / len(typed) < 0.8:
                continue
            for r, value, t in typed:
                compatible = (dominant == "number" and t in {"int", "float"}) or t == dominant
                if not compatible:
                    out.issues.append(Issue(
                        self.name, "dominant_type_violation",
                        f"Cell R{r + 2}C{c + 1} has type {t}; dominant column type is {dominant}.",
                        "warning", row=r, column=c, value=value, repairable=False,
                        metadata={"dominant_type": dominant, "observed_type": t},
                    ))
        return out


DEFAULT_ANALYZERS: tuple[Analyzer, ...] = (
    ShapeAnalyzer(),
    WhitespaceAnalyzer(),
    DuplicateRowAnalyzer(),
    NullMarkerAnalyzer(),
    BooleanAnalyzer(),
    TypeConsistencyAnalyzer(),
)


def analyze_all(table: Table, config, analyzers: Iterable[Analyzer] = DEFAULT_ANALYZERS) -> AnalysisResult:
    frozen = table.clone()
    combined = AnalysisResult()
    for analyzer in analyzers:
        result = analyzer.analyze(frozen, config)
        combined.issues.extend(result.issues)
        combined.candidates.extend(result.candidates)
        combined.evidence.update(result.evidence)
    return combined
