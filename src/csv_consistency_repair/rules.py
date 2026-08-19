from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from .analyzers import Analyzer
from .io import read_table
from .models import AnalysisResult, Candidate, Issue, Table


_NUMBER_UNIT_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\d\s].*?)\s*$")


def _candidate_id(analyzer: str, operation: str, row, column, old, new, tag: str = "") -> str:
    payload = repr((analyzer, operation, row, column, old, new, tag)).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def _set_cell_candidate(*, analyzer: str, row: int, column: int, old: str, new: str,
                        reason: str, confidence: float = 1.0, metadata: dict[str, Any] | None = None,
                        tag: str = "") -> Candidate:
    return Candidate(
        candidate_id=_candidate_id(analyzer, "set_cell", row, column, old, new, tag),
        analyzer=analyzer,
        operation="set_cell",
        reason=reason,
        row=row,
        column=column,
        old_value=old,
        new_value=new,
        cost=1,
        confidence=confidence,
        metadata=metadata or {},
    )


def _column_index(table: Table, name: str) -> int | None:
    try:
        return table.header.index(name)
    except ValueError:
        return None


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


_RULE_KEYS = {
    "unique", "functional_dependencies", "row_formulas", "units",
    "foreign_keys", "allowed_values", "ranges",
}


def _require_rule_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"Rules field {key!r} must be a JSON list.")
    return value


def validate_rules(data: dict[str, Any]) -> None:
    unknown = sorted(set(data) - _RULE_KEYS)
    if unknown:
        raise ValueError(f"Unknown rules field(s): {', '.join(unknown)}")

    unique = _require_rule_list(data, "unique")
    for entry in unique:
        if isinstance(entry, str):
            if not entry:
                raise ValueError("Unique-key column names cannot be empty.")
        elif isinstance(entry, list) and entry and all(isinstance(x, str) and x for x in entry):
            pass
        else:
            raise ValueError("Each unique rule must be a column name or a non-empty list of column names.")

    for key in ("functional_dependencies", "row_formulas", "units", "foreign_keys", "allowed_values", "ranges"):
        for i, rule in enumerate(_require_rule_list(data, key)):
            if not isinstance(rule, dict):
                raise ValueError(f"{key}[{i}] must be a JSON object.")

    for i, rule in enumerate(data.get("functional_dependencies", [])):
        determinant = rule.get("determinant")
        dependent = rule.get("dependent")
        det = [determinant] if isinstance(determinant, str) else determinant
        if not isinstance(det, list) or not det or not all(isinstance(x, str) and x for x in det):
            raise ValueError(f"functional_dependencies[{i}].determinant must name one or more columns.")
        if not isinstance(dependent, str) or not dependent:
            raise ValueError(f"functional_dependencies[{i}].dependent must name a column.")
        confidence = float(rule.get("min_confidence", 0.75))
        if not 0.5 <= confidence <= 1.0:
            raise ValueError(f"functional_dependencies[{i}].min_confidence must be between 0.5 and 1.0.")
        if int(rule.get("min_support", 3)) < 2:
            raise ValueError(f"functional_dependencies[{i}].min_support must be at least 2.")

    for i, rule in enumerate(data.get("row_formulas", [])):
        target = rule.get("target")
        expression = rule.get("expression")
        if not isinstance(target, str) or not target:
            raise ValueError(f"row_formulas[{i}].target must name a column.")
        if not isinstance(expression, dict):
            raise ValueError(f"row_formulas[{i}].expression must be an object.")
        op = expression.get("op", "sum")
        if op not in {"sum", "product", "difference", "ratio"}:
            raise ValueError(f"row_formulas[{i}].expression.op is unsupported: {op!r}.")
        cols = expression.get("columns")
        cols = [cols] if isinstance(cols, str) else cols
        if not isinstance(cols, list) or not cols or not all(isinstance(x, str) and x for x in cols):
            raise ValueError(f"row_formulas[{i}].expression.columns must name one or more columns.")
        if op in {"difference", "ratio"} and len(cols) != 2:
            raise ValueError(f"row_formulas[{i}] operation {op!r} requires exactly two columns.")
        tolerance = Decimal(str(rule.get("tolerance", 0)))
        if tolerance < 0:
            raise ValueError(f"row_formulas[{i}].tolerance cannot be negative.")

    for i, rule in enumerate(data.get("units", [])):
        if not isinstance(rule.get("column"), str) or not rule.get("column"):
            raise ValueError(f"units[{i}].column must name a column.")
        if not isinstance(rule.get("canonical"), str) or not rule.get("canonical"):
            raise ValueError(f"units[{i}].canonical must be a non-empty unit label.")
        aliases = rule.get("aliases", {})
        if not isinstance(aliases, dict):
            raise ValueError(f"units[{i}].aliases must be an object mapping unit labels to factors.")
        for unit, factor in aliases.items():
            if not str(unit):
                raise ValueError(f"units[{i}] contains an empty alias.")
            try:
                Decimal(str(factor))
            except InvalidOperation as exc:
                raise ValueError(f"units[{i}] factor for {unit!r} is not numeric.") from exc

    for i, rule in enumerate(data.get("foreign_keys", [])):
        for field in ("column", "reference_file", "reference_column"):
            if not isinstance(rule.get(field), str) or not rule.get(field):
                raise ValueError(f"foreign_keys[{i}].{field} must be a non-empty string.")

    for i, rule in enumerate(data.get("allowed_values", [])):
        if not isinstance(rule.get("column"), str) or not rule.get("column"):
            raise ValueError(f"allowed_values[{i}].column must name a column.")
        if not isinstance(rule.get("values", []), list):
            raise ValueError(f"allowed_values[{i}].values must be a list.")

    for i, rule in enumerate(data.get("ranges", [])):
        if not isinstance(rule.get("column"), str) or not rule.get("column"):
            raise ValueError(f"ranges[{i}].column must name a column.")
        if "min" not in rule and "max" not in rule:
            raise ValueError(f"ranges[{i}] must declare min, max, or both.")
        minimum = Decimal(str(rule["min"])) if "min" in rule else None
        maximum = Decimal(str(rule["max"])) if "max" in rule else None
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"ranges[{i}] has min greater than max.")


def load_rules(path: str | Path) -> tuple[dict[str, Any], Path]:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Rules file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Rules file must contain a JSON object.")
    validate_rules(data)
    return data, p.parent


@dataclass
class RuleAnalyzer(Analyzer):
    rules: dict[str, Any]
    base_dir: Path
    name: str = "declared_rules"

    @classmethod
    def from_path(cls, path: str | Path) -> "RuleAnalyzer":
        rules, base = load_rules(path)
        return cls(rules=rules, base_dir=base)

    def analyze(self, table: Table, config) -> AnalysisResult:
        out = AnalysisResult()
        self._unique(table, out)
        self._functional_dependencies(table, out)
        self._row_formulas(table, out)
        self._units(table, out)
        self._foreign_keys(table, out)
        self._allowed_values(table, out)
        self._ranges(table, out)
        return out

    def _missing_columns(self, table: Table, names: list[str], rule_type: str, out: AnalysisResult) -> bool:
        missing = [name for name in names if name not in table.header]
        if not missing:
            return False
        out.issues.append(Issue(
            self.name,
            "rule_column_missing",
            f"{rule_type} rule refers to missing columns: {', '.join(missing)}.",
            "error",
            repairable=False,
            metadata={"rule_type": rule_type, "missing_columns": missing},
        ))
        return True

    def _unique(self, table: Table, out: AnalysisResult) -> None:
        entries = self.rules.get("unique", [])
        for entry in entries:
            columns = [entry] if isinstance(entry, str) else list(entry)
            if not columns or self._missing_columns(table, columns, "unique", out):
                continue
            indexes = [table.header.index(c) for c in columns]
            seen: dict[tuple[str, ...], int] = {}
            for r, row in enumerate(table.rows):
                if any(i >= len(row) for i in indexes):
                    continue
                key = tuple(row[i] for i in indexes)
                if not any(key):
                    continue
                if key in seen:
                    first = seen[key]
                    out.issues.append(Issue(
                        self.name,
                        "unique_key_violation",
                        f"Rows {first + 2} and {r + 2} share the same declared unique key {columns}.",
                        "error",
                        row=r,
                        repairable=False,
                        metadata={"columns": columns, "key": list(key), "first_row": first},
                    ))
                else:
                    seen[key] = r

    def _functional_dependencies(self, table: Table, out: AnalysisResult) -> None:
        for idx, rule in enumerate(self.rules.get("functional_dependencies", [])):
            determinants = rule.get("determinant", [])
            if isinstance(determinants, str):
                determinants = [determinants]
            dependent = rule.get("dependent")
            if not determinants or not dependent:
                continue
            names = list(determinants) + [dependent]
            if self._missing_columns(table, names, "functional_dependency", out):
                continue
            det_idx = [table.header.index(c) for c in determinants]
            dep_idx = table.header.index(dependent)
            min_support = max(2, int(rule.get("min_support", 3)))
            repair = bool(rule.get("repair", False))
            groups: dict[tuple[str, ...], list[tuple[int, str]]] = defaultdict(list)
            for r, row in enumerate(table.rows):
                if any(i >= len(row) for i in det_idx) or dep_idx >= len(row):
                    continue
                key = tuple(row[i] for i in det_idx)
                dep = row[dep_idx]
                if not any(key) or dep == "":
                    continue
                groups[key].append((r, dep))
            for key, items in groups.items():
                if len(items) < min_support:
                    continue
                counts = Counter(v for _, v in items)
                if len(counts) <= 1:
                    continue
                winner, winner_count = counts.most_common(1)[0]
                runner_up = counts.most_common(2)[1][1]
                unambiguous = winner_count > runner_up and winner_count / len(items) >= float(rule.get("min_confidence", 0.75))
                for r, value in items:
                    if value == winner:
                        continue
                    can_repair = repair and unambiguous
                    out.issues.append(Issue(
                        self.name,
                        "functional_dependency_violation",
                        f"{determinants}={list(key)!r} usually maps to {dependent}={winner!r}, not {value!r}.",
                        "error",
                        row=r,
                        column=dep_idx,
                        value=value,
                        repairable=can_repair,
                        metadata={
                            "determinant": determinants,
                            "dependent": dependent,
                            "key": list(key),
                            "suggested": winner,
                            "support": len(items),
                            "winner_support": winner_count,
                            "confidence": winner_count / len(items),
                        },
                    ))
                    if can_repair:
                        out.candidates.append(_set_cell_candidate(
                            analyzer=self.name,
                            row=r,
                            column=dep_idx,
                            old=value,
                            new=winner,
                            reason="Repair a declared functional-dependency outlier using an unambiguous local majority.",
                            confidence=winner_count / len(items),
                            metadata={"rule_index": idx, "rule_type": "functional_dependency"},
                            tag=f"fd:{idx}",
                        ))

    def _eval_formula(self, row: list[str], table: Table, expression: dict[str, Any]) -> Decimal | None:
        op = expression.get("op", "sum")
        cols = expression.get("columns", [])
        if isinstance(cols, str):
            cols = [cols]
        if not cols:
            return None
        indexes = []
        for name in cols:
            ci = _column_index(table, name)
            if ci is None or ci >= len(row):
                return None
            indexes.append(ci)
        values = [_decimal(row[i]) for i in indexes]
        if any(v is None for v in values):
            return None
        vals = [v for v in values if v is not None]
        if op == "sum":
            return sum(vals, Decimal("0"))
        if op == "product":
            result = Decimal("1")
            for v in vals:
                result *= v
            return result
        if op == "difference" and len(vals) == 2:
            return vals[0] - vals[1]
        if op == "ratio" and len(vals) == 2 and vals[1] != 0:
            return vals[0] / vals[1]
        return None

    def _row_formulas(self, table: Table, out: AnalysisResult) -> None:
        for idx, rule in enumerate(self.rules.get("row_formulas", [])):
            target = rule.get("target")
            expression = rule.get("expression", {})
            cols = expression.get("columns", [])
            if isinstance(cols, str):
                cols = [cols]
            if not target or self._missing_columns(table, [target] + list(cols), "row_formula", out):
                continue
            ti = table.header.index(target)
            tolerance = Decimal(str(rule.get("tolerance", 0)))
            repair = bool(rule.get("repair", False))
            for r, row in enumerate(table.rows):
                if ti >= len(row):
                    continue
                expected = self._eval_formula(row, table, expression)
                actual = _decimal(row[ti])
                if expected is None or actual is None:
                    continue
                if abs(actual - expected) <= tolerance:
                    continue
                new = _format_decimal(expected)
                out.issues.append(Issue(
                    self.name,
                    "row_formula_violation",
                    f"Row {r + 2} has {target}={row[ti]!r}; declared formula gives {new!r}.",
                    "warning",
                    row=r,
                    column=ti,
                    value=row[ti],
                    repairable=repair,
                    metadata={"target": target, "expected": new, "tolerance": str(tolerance), "rule_index": idx},
                ))
                if repair:
                    out.candidates.append(_set_cell_candidate(
                        analyzer=self.name,
                        row=r,
                        column=ti,
                        old=row[ti],
                        new=new,
                        reason="Recompute a declared row formula.",
                        metadata={"rule_index": idx, "rule_type": "row_formula"},
                        tag=f"formula:{idx}",
                    ))

    def _units(self, table: Table, out: AnalysisResult) -> None:
        for idx, rule in enumerate(self.rules.get("units", [])):
            column = rule.get("column")
            canonical = rule.get("canonical")
            aliases = rule.get("aliases", {})
            if not column or not canonical or self._missing_columns(table, [column], "units", out):
                continue
            ci = table.header.index(column)
            factors: dict[str, Decimal] = {canonical.casefold(): Decimal("1")}
            for unit, factor in aliases.items():
                try:
                    factors[str(unit).casefold()] = Decimal(str(factor))
                except InvalidOperation:
                    continue
            repair = bool(rule.get("repair", False))
            separator = str(rule.get("separator", " "))
            for r, row in enumerate(table.rows):
                if ci >= len(row):
                    continue
                raw = row[ci]
                if not raw.strip():
                    continue
                match = _NUMBER_UNIT_RE.match(raw)
                if not match:
                    continue
                number_text, unit_text = match.group(1), match.group(2).strip()
                unit_key = unit_text.casefold()
                if unit_key not in factors:
                    out.issues.append(Issue(
                        self.name,
                        "unknown_unit",
                        f"Cell R{r + 2}C{ci + 1} uses unit {unit_text!r}, which is not declared for {column}.",
                        "warning",
                        row=r,
                        column=ci,
                        value=raw,
                        repairable=False,
                        metadata={"column": column, "unit": unit_text, "canonical": canonical},
                    ))
                    continue
                value = _decimal(number_text)
                if value is None:
                    continue
                canonical_value = value * factors[unit_key]
                new = f"{_format_decimal(canonical_value)}{separator}{canonical}"
                if raw == new:
                    continue
                out.issues.append(Issue(
                    self.name,
                    "noncanonical_unit",
                    f"Cell R{r + 2}C{ci + 1} can be normalized from {raw!r} to {new!r}.",
                    "info",
                    row=r,
                    column=ci,
                    value=raw,
                    repairable=repair,
                    metadata={"column": column, "canonical": canonical, "suggested": new},
                ))
                if repair:
                    out.candidates.append(_set_cell_candidate(
                        analyzer=self.name,
                        row=r,
                        column=ci,
                        old=raw,
                        new=new,
                        reason="Convert a declared unit alias to the declared canonical unit.",
                        metadata={"rule_index": idx, "rule_type": "units"},
                        tag=f"unit:{idx}",
                    ))

    def _foreign_keys(self, table: Table, out: AnalysisResult) -> None:
        for idx, rule in enumerate(self.rules.get("foreign_keys", [])):
            column = rule.get("column")
            reference_file = rule.get("reference_file")
            reference_column = rule.get("reference_column")
            if not column or not reference_file or not reference_column:
                continue
            if self._missing_columns(table, [column], "foreign_key", out):
                continue
            ref_path = (self.base_dir / str(reference_file)).resolve()
            if not ref_path.exists():
                out.issues.append(Issue(
                    self.name,
                    "reference_file_missing",
                    f"Foreign-key reference file does not exist: {reference_file}.",
                    "error",
                    repairable=False,
                    metadata={"reference_file": str(reference_file), "rule_index": idx},
                ))
                continue
            ref = read_table(ref_path)
            if reference_column not in ref.header:
                out.issues.append(Issue(
                    self.name,
                    "reference_column_missing",
                    f"Reference column {reference_column!r} is missing from {reference_file}.",
                    "error",
                    repairable=False,
                    metadata={"reference_file": str(reference_file), "reference_column": reference_column},
                ))
                continue
            ci = table.header.index(column)
            ri = ref.header.index(reference_column)
            allowed = {row[ri] for row in ref.rows if ri < len(row) and row[ri] != ""}
            for r, row in enumerate(table.rows):
                if ci >= len(row) or row[ci] == "":
                    continue
                value = row[ci]
                if value not in allowed:
                    out.issues.append(Issue(
                        self.name,
                        "foreign_key_violation",
                        f"{column}={value!r} at row {r + 2} is absent from {reference_file}:{reference_column}.",
                        "error",
                        row=r,
                        column=ci,
                        value=value,
                        repairable=False,
                        metadata={
                            "column": column,
                            "reference_file": str(reference_file),
                            "reference_column": reference_column,
                            "rule_index": idx,
                        },
                    ))

    def _allowed_values(self, table: Table, out: AnalysisResult) -> None:
        for rule in self.rules.get("allowed_values", []):
            column = rule.get("column")
            values = rule.get("values", [])
            if not column or self._missing_columns(table, [column], "allowed_values", out):
                continue
            ci = table.header.index(column)
            allowed = {str(v) for v in values}
            for r, row in enumerate(table.rows):
                if ci >= len(row) or row[ci] == "":
                    continue
                if row[ci] not in allowed:
                    out.issues.append(Issue(
                        self.name,
                        "allowed_value_violation",
                        f"{column}={row[ci]!r} is not in the declared allowed set.",
                        "warning",
                        row=r,
                        column=ci,
                        value=row[ci],
                        repairable=False,
                        metadata={"column": column, "allowed": sorted(allowed)},
                    ))

    def _ranges(self, table: Table, out: AnalysisResult) -> None:
        for rule in self.rules.get("ranges", []):
            column = rule.get("column")
            if not column or self._missing_columns(table, [column], "range", out):
                continue
            ci = table.header.index(column)
            minimum = Decimal(str(rule["min"])) if "min" in rule else None
            maximum = Decimal(str(rule["max"])) if "max" in rule else None
            for r, row in enumerate(table.rows):
                if ci >= len(row) or row[ci] == "":
                    continue
                value = _decimal(row[ci])
                if value is None:
                    continue
                bad = (minimum is not None and value < minimum) or (maximum is not None and value > maximum)
                if bad:
                    out.issues.append(Issue(
                        self.name,
                        "range_violation",
                        f"{column}={row[ci]!r} at row {r + 2} is outside the declared range.",
                        "warning",
                        row=r,
                        column=ci,
                        value=row[ci],
                        repairable=False,
                        metadata={"column": column, "min": str(minimum) if minimum is not None else None, "max": str(maximum) if maximum is not None else None},
                    ))
