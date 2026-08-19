from __future__ import annotations

import csv
import io
import json
import random
from pathlib import Path

import pytest

from csv_consistency_repair import RepairConfig, repair, undo
from csv_consistency_repair.io import read_table, write_table
from csv_consistency_repair.models import Table, TableDialect


def _write_csv(path: Path, header, rows, *, delimiter=",", lineterminator="\n", bom=False):
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=delimiter, quotechar='"', lineterminator=lineterminator)
    w.writerow(header)
    w.writerows(rows)
    raw = buf.getvalue().encode("utf-8")
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    path.write_bytes(raw)


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
@pytest.mark.parametrize("lineterminator", ["\n", "\r\n"])
def test_dialect_and_bom_are_preserved_through_repair_and_undo(tmp_path, delimiter, lineterminator):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    restored = tmp_path / "restored.csv"
    header = [" id ", "note", "unicode"]
    rows = [[" 1 ", f"hello{delimiter}world", "Québec"], ["2", "line1\nline2", "東京"]]
    _write_csv(src, header, rows, delimiter=delimiter, lineterminator=lineterminator, bom=True)

    original = read_table(src)
    result = repair(src, out, rep)
    repaired = read_table(out)
    assert repaired.dialect.delimiter == delimiter
    assert repaired.dialect.lineterminator == lineterminator
    assert repaired.utf8_bom is True
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert result.report["format_contract"]["preservation_pass"] is True
    assert result.report["closure"]["format_preservation_pass"] is True

    receipt = undo(out, rep, restored)
    assert receipt["logical_roundtrip_pass"] is True
    assert read_table(restored).logical_digest() == original.logical_digest()
    assert restored.read_bytes().startswith(b"\xef\xbb\xbf")


def test_embedded_quotes_newlines_and_formula_like_text_are_data_only(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    rows = [["1", 'say "hello"', "=2+2"], ["2", "a\nb", "@SUM(A1:A2)"], ["3", "+1", "-2"]]
    _write_csv(src, ["id", "note", "payload"], rows)
    result = repair(src, out, rep)
    table = read_table(out)
    assert table.rows == rows
    assert result.committed_edits == 0
    assert result.report["closure"]["forward_replay_pass"] is True
    assert result.report["closure"]["inverse_roundtrip_pass"] is True


def test_empty_file_roundtrips_without_inventing_a_header(tmp_path):
    src = tmp_path / "empty.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    src.write_bytes(b"")
    result = repair(src, out, rep)
    assert out.read_bytes() == b""
    assert read_table(out).header == []
    assert result.final_status in {"STABLE_WITH_REPORTED_ISSUES", "LIMIT_REACHED_WITH_REPORTED_ISSUES"}


def test_malformed_csv_is_rejected_instead_of_silently_reparsed(tmp_path):
    src = tmp_path / "bad.csv"
    src.write_text('a,b\n1,"unterminated\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed CSV"):
        repair(src)


def test_non_utf8_input_is_rejected_with_clear_contract(tmp_path):
    src = tmp_path / "latin1.csv"
    src.write_bytes(b"name\nAndr\xe9\n")
    with pytest.raises(ValueError, match="valid UTF-8"):
        repair(src)


def test_invalid_config_cannot_claim_two_cycle_closure_with_one_cycle(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stable_cycles_required cannot exceed max_cycles"):
        repair(src, config=RepairConfig(max_cycles=1, stable_cycles_required=2))


def test_rule_schema_rejects_unknown_fields_and_bad_ranges(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text("x\n1\n", encoding="utf-8")
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"mystery_rule": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown rules field"):
        repair(src, config=RepairConfig(rules_path=str(rules)))
    rules.write_text(json.dumps({"ranges": [{"column": "x", "min": 10, "max": 1}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="min greater than max"):
        repair(src, config=RepairConfig(rules_path=str(rules)))


def test_large_field_is_preserved(tmp_path):
    src = tmp_path / "large.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    payload = "x" * 200_000
    _write_csv(src, ["id", "payload"], [["1", payload], ["2", "small"]])
    result = repair(src, out, rep)
    assert read_table(out).rows[0][1] == payload
    assert result.report["closure"]["certified_for_configured_scope"] is True


def test_deterministic_randomized_roundtrip_fuzz(tmp_path):
    rng = random.Random(20260818)
    delimiters = [",", ";", "\t", "|"]
    tokens = ["alpha", " beta ", "Québec", "東京", 'say "hi"', "line1\nline2", "", "=1+1", "NA"]

    for case in range(64):
        delimiter = delimiters[case % len(delimiters)]
        line = "\r\n" if case % 2 else "\n"
        bom = case % 3 == 0
        src = tmp_path / f"fuzz_{case}.csv"
        out = tmp_path / f"fuzz_{case}.out.csv"
        rep = tmp_path / f"fuzz_{case}.json"
        restored = tmp_path / f"fuzz_{case}.restored.csv"
        header = ["id", "value", "note"]
        rows = []
        for r in range(5 + (case % 8)):
            value = rng.choice(tokens)
            note = rng.choice(tokens)
            if r == 0:
                value = f"embedded{delimiter}delimiter"
            rows.append([str(r), value, note])
        _write_csv(src, header, rows, delimiter=delimiter, lineterminator=line, bom=bom)
        original_digest = read_table(src).logical_digest()
        result = repair(src, out, rep, RepairConfig(normalize_null_markers=True))
        assert read_table(out).dialect.delimiter == delimiter
        assert result.report["format_contract"]["preservation_pass"] is True
        receipt = undo(out, rep, restored)
        assert receipt["logical_roundtrip_pass"] is True
        assert read_table(restored).logical_digest() == original_digest

@pytest.mark.parametrize("delimiter", [";", "\t", "|"])
def test_delimiter_detection_survives_embedded_quotes_multiline_and_spaced_fields(tmp_path, delimiter):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    rows = [
        [" 1 ", " Alice ", f"embedded{delimiter}delimiter"],
        [" 2 ", " Bob ", 'say "hi"'],
        [" 3 ", " Chloé ", "line1\nline2"],
    ]
    _write_csv(src, [" id ", " name ", "note"], rows, delimiter=delimiter, lineterminator="\r\n")
    result = repair(src, out, rep)
    table = read_table(out)
    assert table.dialect.delimiter == delimiter
    assert table.header == ["id", "name", "note"]
    assert [r[:2] for r in table.rows] == [["1", "Alice"], ["2", "Bob"], ["3", "Chloé"]]
    assert result.final_status == "PASS"
    assert result.report["format_contract"]["preservation_pass"] is True
