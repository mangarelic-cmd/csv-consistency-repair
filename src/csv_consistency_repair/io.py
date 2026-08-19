from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from pathlib import Path
from .models import Table, TableDialect

_UTF8_BOM = b"\xef\xbb\xbf"
_DELIMITERS = (",", ";", "\t", "|")


def _detect_lineterminator(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text and "\n" not in text:
        return "\r"
    return "\n"


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_candidate(text: str, delimiter: str, quotechar: str = '"', doublequote: bool = True,
                     escapechar: str | None = None) -> list[list[str]] | None:
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=delimiter,
        quotechar=quotechar,
        doublequote=doublequote,
        escapechar=escapechar,
        strict=True,
    )
    try:
        return [list(r) for r in reader]
    except csv.Error:
        return None


def _delimiter_score(records: list[list[str]] | None) -> tuple[float, ...]:
    """Score a delimiter by structural consistency, not token frequency.

    A wrong delimiter often parses every physical record as one giant field and can look
    superficially "consistent".  Therefore multi-column structure is ranked before the
    consistency fraction.  For genuine single-column files the tie is resolved later by
    Sniffer/default order.
    """
    if records is None or not records:
        return (-1.0,)
    widths = [len(r) for r in records]
    modal_width, modal_count = Counter(widths).most_common(1)[0]
    header_width = widths[0]
    same_header = sum(w == header_width for w in widths)
    consistency = same_header / len(widths)
    modal_consistency = modal_count / len(widths)
    multi = 1.0 if header_width > 1 else 0.0
    # Prefer coherent tables; then wider actual tables; then more parsed records.
    return (multi, consistency, modal_consistency, float(min(header_width, 1024)), float(len(records)))


def _detect_delimiter(text: str) -> str:
    if not text:
        return ","
    scored: list[tuple[tuple[float, ...], int, str]] = []
    for idx, delimiter in enumerate(_DELIMITERS):
        records = _parse_candidate(text, delimiter)
        scored.append((_delimiter_score(records), -idx, delimiter))
    scored.sort(reverse=True)
    best_score, _, best = scored[0]
    if best_score and best_score[0] > 0:
        return best

    # Single-column or otherwise ambiguous file: Sniffer can still provide a useful hint,
    # but never let an ambiguity invent a multi-column structure.
    sample = text[:65536]
    try:
        detected = csv.Sniffer().sniff(sample, delimiters="".join(_DELIMITERS))
        if detected.delimiter in _DELIMITERS:
            return detected.delimiter
    except csv.Error:
        pass
    return ","


def read_table(path: str | Path, encoding: str = "utf-8") -> Table:
    path = Path(path)
    raw = path.read_bytes()
    utf8_bom = raw.startswith(_UTF8_BOM)
    payload = raw[len(_UTF8_BOM):] if utf8_bom else raw
    try:
        text = payload.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Input is not valid UTF-8. Convert the file to UTF-8 before repair."
        ) from exc

    delimiter = _detect_delimiter(text)
    # RFC-style CSV quoting is deliberately fixed to the standard double-quote
    # convention.  csv.Sniffer is not trusted for quote mechanics: on ordinary
    # data containing embedded quotes it can infer doublequote=False and produce
    # an output dialect that cannot safely re-emit the parsed cells.
    quotechar = '"'
    doublequote = True
    escapechar = None

    dialect = TableDialect(
        delimiter=delimiter,
        quotechar=quotechar,
        doublequote=doublequote,
        escapechar=escapechar,
        lineterminator=_detect_lineterminator(text),
    )
    previous_field_limit = csv.field_size_limit()
    required_field_limit = max(previous_field_limit, len(payload) + 1)
    csv.field_size_limit(required_field_limit)
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=dialect.delimiter,
        quotechar=dialect.quotechar,
        doublequote=dialect.doublequote,
        escapechar=dialect.escapechar,
        strict=True,
    )
    try:
        records = [list(r) for r in reader]
    except csv.Error as exc:
        raise ValueError(f"Malformed CSV near parser line {reader.line_num}: {exc}") from exc
    finally:
        csv.field_size_limit(previous_field_limit)

    if not records:
        return Table(
            header=[], rows=[], dialect=dialect, encoding=encoding, utf8_bom=utf8_bom
        )
    return Table(
        header=records[0],
        rows=records[1:],
        dialect=dialect,
        encoding=encoding,
        utf8_bom=utf8_bom,
    )


def write_table(table: Table, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    writer = csv.writer(
        buffer,
        delimiter=table.dialect.delimiter,
        quotechar=table.dialect.quotechar,
        doublequote=table.dialect.doublequote,
        escapechar=table.dialect.escapechar,
        lineterminator=table.dialect.lineterminator,
    )
    if table.header or table.rows:
        writer.writerow(table.header)
        writer.writerows(table.rows)
    encoded = buffer.getvalue().encode(table.encoding)
    if table.utf8_bom:
        encoded = _UTF8_BOM + encoded
    path.write_bytes(encoded)
