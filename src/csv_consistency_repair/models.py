from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional
import hashlib
import json


@dataclass(frozen=True)
class TableDialect:
    delimiter: str = ","
    quotechar: str = '"'
    doublequote: bool = True
    escapechar: Optional[str] = None
    lineterminator: str = "\n"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Table:
    header: list[str]
    rows: list[list[str]]
    dialect: TableDialect = field(default_factory=TableDialect)
    encoding: str = "utf-8"
    utf8_bom: bool = False

    def clone(self) -> "Table":
        return Table(
            header=list(self.header),
            rows=[list(r) for r in self.rows],
            dialect=self.dialect,
            encoding=self.encoding,
            utf8_bom=self.utf8_bom,
        )

    def logical_digest(self) -> str:
        payload = json.dumps(
            {"header": self.header, "rows": self.rows},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def format_contract(self) -> dict[str, Any]:
        return {
            "encoding": self.encoding,
            "utf8_bom": self.utf8_bom,
            "delimiter": self.dialect.delimiter,
            "quotechar": self.dialect.quotechar,
            "doublequote": self.dialect.doublequote,
            "escapechar": self.dialect.escapechar,
            "lineterminator": self.dialect.lineterminator,
        }


@dataclass(frozen=True)
class Issue:
    analyzer: str
    code: str
    message: str
    severity: str = "warning"
    row: Optional[int] = None
    column: Optional[int] = None
    value: Optional[str] = None
    repairable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def signature(self) -> tuple[Any, ...]:
        return (self.analyzer, self.code, self.row, self.column, self.value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    analyzer: str
    operation: str
    reason: str
    cost: int = 1
    confidence: float = 1.0
    row: Optional[int] = None
    column: Optional[int] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_row: Optional[list[str]] = None
    new_row: Optional[list[str]] = None
    reversible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def conflict_key(self) -> tuple[Any, ...]:
        if self.operation == "set_cell":
            return ("cell", self.row, self.column)
        if self.operation == "rename_column":
            return ("header", self.column)
        if self.operation in {"delete_row", "replace_row"}:
            return ("row", self.row)
        return (self.operation, self.row, self.column)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    issues: list[Issue] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class CycleRecord:
    cycle: int
    score_before: float
    score_after: float
    issue_count_before: int
    issue_count_after: int
    candidate_count: int
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    structural_signature: str = ""
    stable_streak: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
