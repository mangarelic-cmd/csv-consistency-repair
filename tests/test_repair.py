import csv
import json
from pathlib import Path

from csv_consistency_repair import RepairConfig, repair, undo
from csv_consistency_repair.io import read_table


def write_text(path: Path, text: str):
    path.write_text(text, encoding="utf-8")


def test_whitespace_repair_and_stability(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    write_text(src, " name ,age\n Alice ,30\nBob, 40 \n")
    result = repair(src, out, rep)
    table = read_table(out)
    assert table.header == ["name", "age"]
    assert table.rows == [["Alice", "30"], ["Bob", "40"]]
    assert result.final_score == 0
    assert result.strong_stable
    assert result.final_status == "PASS"
    assert result.committed_edits == 3


def test_duplicate_created_after_trim_is_removed_on_later_cycle(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    write_text(src, "name,age\nAlice,30\n Alice ,30\n")
    result = repair(src, out, rep, RepairConfig(remove_exact_duplicates=True))
    table = read_table(out)
    assert table.rows == [["Alice", "30"]]
    assert result.final_score == 0
    assert result.strong_stable
    assert any(e["operation"] == "delete_row" for e in result.report["committed_edits"])


def test_header_trim_collision_is_not_applied(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    write_text(src, "a, a \n1,2\n")
    result = repair(src, out, rep)
    table = read_table(out)
    assert table.header == ["a", " a "]
    assert any(i["code"] == "header_outer_whitespace" for i in result.report["remaining_issues"])


def test_optional_null_and_boolean_normalization(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    write_text(src, "id,active,note\n1,YES,NA\n2,false,ok\n3,TRUE,null\n")
    cfg = RepairConfig(normalize_null_markers=True, normalize_booleans=True)
    result = repair(src, out, rep, cfg)
    table = read_table(out)
    assert table.rows[0] == ["1", "true", ""]
    assert table.rows[1] == ["2", "false", "ok"]
    assert table.rows[2] == ["3", "true", ""]
    assert result.strong_stable


def test_undo_roundtrip_logical_table(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    restored = tmp_path / "restored.csv"
    rep = tmp_path / "report.json"
    write_text(src, " name ,age\n Alice ,30\nAlice,30\n")
    original_digest = read_table(src).logical_digest()
    repair(src, out, rep, RepairConfig(remove_exact_duplicates=True))
    receipt = undo(out, rep, restored)
    assert receipt["logical_roundtrip_pass"]
    assert read_table(restored).logical_digest() == original_digest


def test_semicolon_dialect_is_preserved(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    write_text(src, "name;age\n Alice ;30\nBob;40\n")
    repair(src, out, rep)
    assert read_table(out).dialect.delimiter == ";"
    assert ";" in out.read_text(encoding="utf-8")


def test_dry_run_does_not_write_repaired_output(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    write_text(src, "name\n Alice \n")
    result = repair(src, out, rep, RepairConfig(dry_run=True))
    assert not out.exists()
    assert result.committed_edits == 0
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert any(x["decision"] == "dry_run_would_commit" for x in data["rejected_candidates"])


def test_type_violation_reported_not_rewritten(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    write_text(src, "value\n1\n2\noops\n4\n5\n")
    result = repair(src, out, rep)
    assert any(i["code"] == "dominant_type_violation" for i in result.report["remaining_issues"])
    assert read_table(out).rows[2][0] == "oops"


def test_two_step_composition_can_cross_temporary_score_increase(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    # Converting NA to empty temporarily creates an exact duplicate row.
    # With deduplication explicitly enabled, the pair is safe only as an atomic composition.
    write_text(src, "active,note\ntrue,NA\nfalse,ok\ntrue,\n")
    cfg = RepairConfig(normalize_null_markers=True, remove_exact_duplicates=True)
    result = repair(src, out, rep, cfg)
    table = read_table(out)
    assert result.final_score == 0
    assert result.final_status == "PASS"
    assert len(table.rows) == 2
    assert any(e["decision"] == "committed_composed_step_1" for e in result.report["committed_edits"])
    assert any(e["decision"] == "committed_composed_step_2" for e in result.report["committed_edits"])


def test_overlapping_analyzers_do_not_suppress_a_safe_repair(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    write_text(src, "id,note\n1, NA \n2,ok\n")
    result = repair(src, out, rep, RepairConfig(normalize_null_markers=True))
    table = read_table(out)
    assert table.rows[0][1] == ""
    assert not any(i["repairable"] for i in result.report["remaining_issues"])


def test_declared_row_formula_is_repaired_and_reversible(tmp_path):
    src = tmp_path / "orders.csv"
    out = tmp_path / "orders.fixed.csv"
    restored = tmp_path / "orders.restored.csv"
    rep = tmp_path / "orders.report.json"
    rules = tmp_path / "orders.rules.json"
    write_text(src, "item,subtotal,tax,total\na,10,2,99\nb,5,1,6\n")
    rules.write_text(json.dumps({
        "row_formulas": [{
            "target": "total",
            "expression": {"op": "sum", "columns": ["subtotal", "tax"]},
            "tolerance": 0,
            "repair": True
        }]
    }), encoding="utf-8")
    original = read_table(src).logical_digest()
    result = repair(src, out, rep, RepairConfig(rules_path=str(rules)))
    assert read_table(out).rows[0][3] == "12"
    assert result.final_score == 0
    assert result.final_status == "PASS"
    receipt = undo(out, rep, restored)
    assert receipt["logical_roundtrip_pass"]
    assert read_table(restored).logical_digest() == original


def test_declared_functional_dependency_repairs_unambiguous_outlier(tmp_path):
    src = tmp_path / "people.csv"
    out = tmp_path / "people.fixed.csv"
    rep = tmp_path / "people.report.json"
    rules = tmp_path / "people.rules.json"
    write_text(src, "row_id,customer_id,country\n1,A,US\n2,A,US\n3,A,US\n4,A,CA\n5,B,FR\n6,B,FR\n7,B,FR\n")
    rules.write_text(json.dumps({
        "functional_dependencies": [{
            "determinant": ["customer_id"],
            "dependent": "country",
            "min_support": 3,
            "min_confidence": 0.75,
            "repair": True
        }]
    }), encoding="utf-8")
    result = repair(src, out, rep, RepairConfig(rules_path=str(rules)))
    assert read_table(out).rows[3][2] == "US"
    assert result.final_score == 0
    assert any(e["metadata"].get("rule_type") == "functional_dependency" for e in result.report["committed_edits"])


def test_declared_unit_aliases_are_normalized(tmp_path):
    src = tmp_path / "measurements.csv"
    out = tmp_path / "measurements.fixed.csv"
    rep = tmp_path / "measurements.report.json"
    rules = tmp_path / "measurements.rules.json"
    write_text(src, "id,length\n1,100 cm\n2,1 m\n3,250 cm\n")
    rules.write_text(json.dumps({
        "units": [{
            "column": "length",
            "canonical": "m",
            "aliases": {"cm": 0.01, "mm": 0.001},
            "repair": True
        }]
    }), encoding="utf-8")
    result = repair(src, out, rep, RepairConfig(rules_path=str(rules)))
    table = read_table(out)
    assert table.rows[0][1] == "1 m"
    assert table.rows[2][1] == "2.5 m"
    assert result.final_score == 0


def test_foreign_key_violation_is_reported_across_files(tmp_path):
    customers = tmp_path / "customers.csv"
    orders = tmp_path / "orders.csv"
    out = tmp_path / "orders.fixed.csv"
    rep = tmp_path / "orders.report.json"
    rules = tmp_path / "orders.rules.json"
    write_text(customers, "customer_id,name\nC1,Alice\nC2,Bob\n")
    write_text(orders, "order_id,customer_id\nO1,C1\nO2,C9\n")
    rules.write_text(json.dumps({
        "foreign_keys": [{
            "column": "customer_id",
            "reference_file": "customers.csv",
            "reference_column": "customer_id"
        }]
    }), encoding="utf-8")
    result = repair(orders, out, rep, RepairConfig(rules_path=str(rules)))
    assert result.final_status == "STABLE_WITH_REPORTED_ISSUES"
    assert any(i["code"] == "foreign_key_violation" and i["value"] == "C9" for i in result.report["remaining_issues"])


def test_declared_unique_key_violation_is_reported(tmp_path):
    src = tmp_path / "items.csv"
    out = tmp_path / "items.fixed.csv"
    rep = tmp_path / "items.report.json"
    rules = tmp_path / "items.rules.json"
    write_text(src, "id,name\n1,A\n1,B\n2,C\n")
    rules.write_text(json.dumps({"unique": ["id"]}), encoding="utf-8")
    result = repair(src, out, rep, RepairConfig(rules_path=str(rules)))
    assert any(i["code"] == "unique_key_violation" for i in result.report["remaining_issues"])


def test_declared_allowed_values_and_ranges_are_reported(tmp_path):
    src = tmp_path / "sensor.csv"
    out = tmp_path / "sensor.fixed.csv"
    rep = tmp_path / "sensor.report.json"
    rules = tmp_path / "sensor.rules.json"
    write_text(src, "state,temp\nOK,20\nBAD,999\n")
    rules.write_text(json.dumps({
        "allowed_values": [{"column": "state", "values": ["OK", "WARN"]}],
        "ranges": [{"column": "temp", "min": -50, "max": 150}]
    }), encoding="utf-8")
    result = repair(src, out, rep, RepairConfig(rules_path=str(rules)))
    codes = {i["code"] for i in result.report["remaining_issues"]}
    assert "allowed_value_violation" in codes
    assert "range_violation" in codes


def test_bundle_repairs_multiple_csv_files(tmp_path):
    from csv_consistency_repair import repair_bundle

    write_text(tmp_path / "a.csv", " name \n Alice \n")
    write_text(tmp_path / "b.csv", "value\n yes \nno\n")
    manifest = tmp_path / "bundle.json"
    manifest.write_text(json.dumps({
        "datasets": [
            {"name": "a", "input": "a.csv"},
            {"name": "b", "input": "b.csv", "config": {"normalize_booleans": True}}
        ]
    }), encoding="utf-8")
    output_dir = tmp_path / "out"
    summary = repair_bundle(manifest, output_dir, tmp_path / "bundle.report.json")
    assert summary["dataset_count"] == 2
    assert summary["committed_edits"] >= 2
    assert read_table(output_dir / "a.repaired.csv").header == ["name"]
    assert read_table(output_dir / "b.repaired.csv").rows[0][0] == "true"


def test_discovered_stable_mapping_can_repair_outlier(tmp_path):
    src = tmp_path / "people.csv"
    out = tmp_path / "people.fixed.csv"
    rep = tmp_path / "people.report.json"
    rows = ["row_id,customer_id,country"]
    row_id = 1
    for customer, country in [("A", "US"), ("B", "FR"), ("C", "CA"), ("D", "JP")]:
        for j in range(20):
            value = country
            if customer == "A" and j == 7:
                value = "GB"
            rows.append(f"{row_id},{customer},{value}")
            row_id += 1
    write_text(src, "\n".join(rows) + "\n")
    cfg = RepairConfig(
        discover_relationships=True,
        repair_discovered_relationships=True,
        discovery_confidence=0.95,
        discovery_min_rows=12,
        discovery_min_group_support=10,
        discovery_stress_tolerance=0.05,
    )
    result = repair(src, out, rep, cfg)
    table = read_table(out)
    assert table.rows[7][2] == "US"
    assert result.final_status == "PASS"
    assert result.report["closure"]["forward_replay_pass"] is True
    assert result.report["closure"]["inverse_roundtrip_pass"] is True
    relations = result.report["relationship_discovery"]["relationships"]
    relation = next(r for r in relations if r["determinant"] == ["customer_id"] and r["dependent"] == "country")
    assert relation["stability"]["pass"] is True
    assert any(e["metadata"].get("rule_type") == "discovered_functional_dependency" for e in result.report["committed_edits"])


def test_scope_sensitive_discovered_mapping_is_not_auto_repaired(tmp_path):
    src = tmp_path / "scope.csv"
    out = tmp_path / "scope.fixed.csv"
    rep = tmp_path / "scope.report.json"
    rows = ["id,key,value"]
    for i in range(50):
        rows.append(f"{i},X,A")
    for i in range(50, 100):
        value = "B" if i < 90 else "C"
        rows.append(f"{i},Y,{value}")
    write_text(src, "\n".join(rows) + "\n")
    cfg = RepairConfig(
        discover_relationships=True,
        repair_discovered_relationships=True,
        discovery_confidence=0.85,
        discovery_min_rows=12,
        discovery_min_group_support=8,
        discovery_stress_tolerance=0.02,
    )
    result = repair(src, out, rep, cfg)
    table = read_table(out)
    assert table.rows[90][2] == "C"
    relation = next(
        r for r in result.report["relationship_discovery"]["relationships"]
        if r["determinant"] == ["key"] and r["dependent"] == "value"
    )
    assert relation["stability"]["base_pass"] is True
    assert relation["stability"]["pass"] is False
    assert "row_scope_sensitive" in relation["stability"]["reasons"]
    matching = [i for i in result.report["remaining_issues"] if i["code"] == "discovered_functional_dependency_violation"]
    assert matching
    assert all(i["repairable"] is False for i in matching)


def test_relationship_discovery_surfaces_unique_key_candidates(tmp_path):
    src = tmp_path / "ids.csv"
    out = tmp_path / "ids.fixed.csv"
    rep = tmp_path / "ids.report.json"
    rows = ["id,group"] + [f"{i},{i % 3}" for i in range(20)]
    write_text(src, "\n".join(rows) + "\n")
    result = repair(src, out, rep, RepairConfig(discover_relationships=True))
    candidates = result.report["relationship_discovery"]["unique_key_candidates"]
    assert any(c["columns"] == ["id"] and c["uniqueness_ratio"] == 1.0 for c in candidates)


def test_closure_receipt_is_present_on_normal_repair(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    rep = tmp_path / "report.json"
    write_text(src, " name \n Alice \n")
    result = repair(src, out, rep)
    closure = result.report["closure"]
    assert closure["configured_scope_clean"] is True
    assert closure["two_cycle_stability_pass"] is True
    assert closure["forward_replay_pass"] is True
    assert closure["inverse_roundtrip_pass"] is True
    assert closure["certified_for_configured_scope"] is True


def test_redundant_numeric_constraints_repair_without_declared_formula(tmp_path):
    src=tmp_path/'ledger.csv'; out=tmp_path/'fixed.csv'; rep=tmp_path/'report.json'
    rows=['qty,price,subtotal,tax,total']
    for i in range(1,121):
        qty=(i%7)+1; price=(i%11)+2; subtotal=qty*price; tax=(i%5)+1; total=subtotal+tax
        if i==77: subtotal += 13
        rows.append(f'{qty},{price},{subtotal},{tax},{total}')
    write_text(src,'\n'.join(rows)+'\n')
    cfg=RepairConfig(discover_numeric_constraints=True,repair_numeric_constraints=True,discovery_confidence=0.98,discovery_min_rows=30)
    result=repair(src,out,rep,cfg)
    table=read_table(out)
    i=76
    assert table.rows[i][2] == str(int(table.rows[i][0])*int(table.rows[i][1]))
    edits=[e for e in result.report['committed_edits'] if e['metadata'].get('rule_type')=='redundant_numeric_constraint_consensus']
    assert edits and edits[0]['metadata']['independent_constraints'] >= 2
    assert result.report['closure']['forward_replay_pass'] is True
    assert result.report['closure']['inverse_roundtrip_pass'] is True


def test_single_numeric_relation_is_diagnosed_but_not_repaired(tmp_path):
    src=tmp_path/'single.csv'; out=tmp_path/'fixed.csv'; rep=tmp_path/'report.json'
    rows=['a,b,c']
    for i in range(1,101):
        a=i; b=(i%13)+1; c=a+b
        if i==50: c += 9
        rows.append(f'{a},{b},{c}')
    write_text(src,'\n'.join(rows)+'\n')
    cfg=RepairConfig(discover_numeric_constraints=True,repair_numeric_constraints=True,discovery_confidence=0.98,discovery_min_rows=30)
    result=repair(src,out,rep,cfg)
    assert read_table(out).rows[49][2] != str(50 + (50%13)+1)
    assert not any(e['metadata'].get('rule_type')=='redundant_numeric_constraint_consensus' for e in result.report['committed_edits'])
    assert any(i['code']=='numeric_constraint_violation' for i in result.report['remaining_issues'])


def test_missing_numeric_cell_is_projected_from_exact_discovered_formula(tmp_path):
    src = tmp_path / "ledger.csv"
    out = tmp_path / "fixed.csv"
    rep = tmp_path / "report.json"
    rows = ["qty,price,subtotal"]
    for i in range(1, 81):
        qty = (i % 7) + 2
        price = (i % 11) + 3
        subtotal = qty * price
        value = "" if i == 41 else str(subtotal)
        rows.append(f"{qty},{price},{value}")
    write_text(src, "\n".join(rows) + "\n")
    cfg = RepairConfig(
        discover_numeric_constraints=True,
        repair_missing_values=True,
        discovery_confidence=0.99,
        discovery_min_rows=24,
    )
    result = repair(src, out, rep, cfg)
    table = read_table(out)
    assert table.rows[40][2] == str(int(table.rows[40][0]) * int(table.rows[40][1]))
    assert any(e["metadata"].get("rule_type") == "missing_numeric_projection" for e in result.report["committed_edits"])
    assert result.report["closure"]["forward_replay_pass"] is True
    assert result.report["closure"]["inverse_roundtrip_pass"] is True


def test_three_term_formula_projects_missing_cell(tmp_path):
    src = tmp_path / "invoice.csv"
    out = tmp_path / "fixed.csv"
    rep = tmp_path / "report.json"
    rows = ["qty,price,shipping,total"]
    for i in range(1, 101):
        qty = (i % 9) + 1
        price = (i % 13) + 2
        shipping = (i % 5) + 1
        total = qty * price + shipping
        value = "" if i == 63 else str(total)
        rows.append(f"{qty},{price},{shipping},{value}")
    write_text(src, "\n".join(rows) + "\n")
    cfg = RepairConfig(
        discover_numeric_constraints=True,
        repair_missing_values=True,
        numeric_max_formula_terms=3,
        discovery_confidence=0.99,
        discovery_min_rows=30,
    )
    result = repair(src, out, rep, cfg)
    table = read_table(out)
    r = table.rows[62]
    assert r[3] == str(int(r[0]) * int(r[1]) + int(r[2]))
    rels = result.report["numeric_constraint_discovery"]["relations"]
    assert any(x["stable"] and x["operation"] == "product_plus" and x["target"] == "total" for x in rels)


def test_composite_mapping_projects_missing_value_only_when_pair_is_stable(tmp_path):
    src = tmp_path / "catalog.csv"
    out = tmp_path / "fixed.csv"
    rep = tmp_path / "report.json"
    rows = ["region,code,label"]
    mapping = {("A", "1"): "red", ("A", "2"): "blue", ("B", "1"): "green", ("B", "2"): "yellow"}
    n = 0
    target_index = None
    for _ in range(12):
        for (region, code), label in mapping.items():
            n += 1
            if n == 27:
                target_index = len(rows) - 1
                label = ""
            rows.append(f"{region},{code},{label}")
    write_text(src, "\n".join(rows) + "\n")
    cfg = RepairConfig(
        discover_relationships=True,
        repair_missing_values=True,
        discovery_max_determinant_columns=2,
        discovery_confidence=0.98,
        discovery_min_rows=16,
        discovery_min_group_support=6,
        discovery_min_coverage=0.5,
    )
    result = repair(src, out, rep, cfg)
    table = read_table(out)
    # Find the single originally missing row and verify the pair-derived label.
    missing_edits = [e for e in result.report["committed_edits"] if e["metadata"].get("rule_type") == "discovered_mapping_missing_projection"]
    assert missing_edits
    edit = missing_edits[0]
    row = table.rows[edit["row"]]
    assert row[2] == mapping[(row[0], row[1])]
    rels = result.report["relationship_discovery"]["relationships"]
    assert any(r["determinant"] == ["region", "code"] and r["dependent"] == "label" and r["stability"]["pass"] for r in rels)


def test_exact_temporal_relation_projects_missing_end_time(tmp_path):
    src = tmp_path / "events.csv"
    out = tmp_path / "fixed.csv"
    rep = tmp_path / "report.json"
    rows = ["start,end,duration"]
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 1, 8, 0, 0)
    for i in range(40):
        start = base + timedelta(days=i)
        duration = (i % 4) + 1
        end = start + timedelta(hours=duration)
        end_text = "" if i == 17 else end.isoformat(timespec="seconds")
        rows.append(f"{start.isoformat(timespec='seconds')},{end_text},{duration}")
    write_text(src, "\n".join(rows) + "\n")
    cfg = RepairConfig(
        discover_temporal_constraints=True,
        repair_temporal_missing=True,
        repair_missing_values=True,
        discovery_confidence=0.99,
        discovery_min_rows=16,
    )
    result = repair(src, out, rep, cfg)
    table = read_table(out)
    assert table.rows[17][1] != ""
    assert any(e["metadata"].get("rule_type") == "temporal_missing_projection" for e in result.report["committed_edits"])
    assert result.report["temporal_constraint_discovery"]["stable_relations"] >= 1


def test_two_missing_members_of_one_formula_are_not_guessed(tmp_path):
    src = tmp_path / "ambiguous.csv"
    out = tmp_path / "fixed.csv"
    rep = tmp_path / "report.json"
    rows = ["a,b,total"]
    for i in range(1, 60):
        a, b = i + 2, i * 3 + 1
        total = a + b
        if i == 33:
            rows.append(f",,{total}")
        else:
            rows.append(f"{a},{b},{total}")
    write_text(src, "\n".join(rows) + "\n")
    cfg = RepairConfig(discover_numeric_constraints=True, repair_missing_values=True, discovery_confidence=0.99, discovery_min_rows=20)
    repair(src, out, rep, cfg)
    table = read_table(out)
    assert table.rows[32][0] == ""
    assert table.rows[32][1] == ""


def test_constraint_graph_reports_multi_relation_support(tmp_path):
    src = tmp_path / "ledger.csv"
    out = tmp_path / "fixed.csv"
    rep = tmp_path / "report.json"
    rows = ["qty,price,subtotal,tax,total"]
    for i in range(1, 90):
        qty = (i % 5) + 1
        price = (i % 7) + 2
        subtotal = qty * price
        tax = (i % 3) + 1
        total = subtotal + tax
        if i == 44:
            subtotal += 9
        rows.append(f"{qty},{price},{subtotal},{tax},{total}")
    write_text(src, "\n".join(rows) + "\n")
    cfg = RepairConfig(discover_numeric_constraints=True, repair_numeric_constraints=True, discovery_confidence=0.98, discovery_min_rows=24)
    result = repair(src, out, rep, cfg)
    graph = result.report["constraint_graph"]
    assert graph["relation_count"] >= 2
    assert any(n["column"] == "subtotal" and n["constraint_degree"] >= 2 for n in graph["nodes"])
