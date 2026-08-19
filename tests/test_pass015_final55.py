from __future__ import annotations

import json
from pathlib import Path

import csv_consistency_repair as ccr
from csv_consistency_repair import RepairConfig, repair
from csv_consistency_repair.io import read_table
from csv_consistency_repair.maxima55 import (
    FINAL55_IDS, feature_registry, semantic_diagnostics, selection_bias_ledger,
    stream_scan, sparse_constraint_graph, deterministic_parallel_map, shard_plan,
    sample_then_certify_plan, sequential_support_probe, canonical_form_cache,
    acceleration_status, vectorized_column_stats, build_mmap_line_index,
    file_snapshot, verify_file_snapshot, automatic_foreign_keys, source_lineage_graph,
    source_metadata, rule_precedence_graph, deduplicate_witnesses, cascade_impact,
    transaction_checkpoint, load_checkpoint, explain_edits, unrepaired_reason,
    next_evidence_from_issues, export_learned_rules, drift_report,
)
from csv_consistency_repair.models import Table
from csv_consistency_repair.benchmarking import lock_benchmark_protocol, run_benchmark


def test_final55_registry_exact():
    reg = feature_registry()
    assert len(FINAL55_IDS) == 55
    assert len(reg) == 55
    assert {x["id"] for x in reg} == set(FINAL55_IDS)
    assert {x["status"] for x in reg} <= {"IMPLEMENTED", "VERIFIED_BASELINE"}


def test_semantic_identity_layer():
    t = Table(
        header=["cust_id", "status", "other_status", "version"],
        rows=[
            ["A", "Active", "Active", "1"],
            ["A", "Active", "Inactive", "2"],
            ["B", "Active", "Active", "1"],
            ["C", "Active", "Inactive", "1"],
            ["D", "Active", "Active", "1"],
            ["E", "Actve", "Inactive", "1"],
        ],
    )
    d = semantic_diagnostics(t)
    assert d["header_ontology_alignment"][0]["canonical"] == "customer_id"
    assert any(x["token"] == "active" for x in d["polysemy_guards"])
    assert any(x["entity"] == "A" for x in d["entity_version_identity"])
    assert any(x["value"] == "Actve" and x["suggested"] == "Active" for x in d["typo_category_proposals"])


def test_selection_ledger_and_sparse_parallel():
    led = selection_bias_ledger({"a": [1,2]}, {"b": [3]})
    assert led["adaptive_candidate_count"] == 3
    g = sparse_constraint_graph({
        "nodes": [{"id":"a"},{"id":"b"},{"id":"c"}],
        "edges": [{"source":"a","target":"b"},{"source":"b","target":"c"}],
    })
    assert g["indptr"] == [0,1,3,4]
    out = deterministic_parallel_map({"z": lambda: 2, "a": lambda: 1}, max_workers=2)
    assert list(out["results"]) == ["a","z"]


def test_streaming_scan_and_state_handoff(tmp_path: Path):
    p = tmp_path / "x.csv"
    p.write_text("a;b\n1;2\n3;4\n5;6\n7;8\n", encoding="utf-8")
    d = stream_scan(p, chunk_rows=2)
    assert d["rows"] == 4 and d["columns"] == 2 and d["delimiter"] == ";"
    assert d["bounded_memory"] is True
    assert d["chunk_boundary_state_handoffs"] >= 1


def test_scale_plans_and_cache_acceleration(tmp_path: Path):
    s1 = sample_then_certify_plan(100, "abc", 10)
    s2 = sample_then_certify_plan(100, "abc", 10)
    assert s1 == s2 and len(s1["sample_indices"]) == 10
    sh = shard_plan(10, 3, "hash")
    assert sh["shards"][0]["start"] == 0 and sh["shards"][-1]["stop"] == 10
    probe = sequential_support_probe([False]*20)
    assert probe["decision"] == "EARLY_REJECT"
    t = Table(header=["a"], rows=[["1"],["1.0"],["2"]])
    cache = canonical_form_cache(t)
    assert cache["unique_canonical_rows"] <= 3
    acc = acceleration_status()
    assert 108 in acc["feature_ids"] and 109 in acc["feature_ids"]
    vs = vectorized_column_stats(["1","2","x","3"])
    assert vs["count"] == 3 and vs["mean"] == 2.0
    p = tmp_path / "m.csv"; p.write_text("a\n1\n2\n", encoding="utf-8")
    idx = build_mmap_line_index(p, tmp_path / "lines.sqlite3")
    assert idx["line_count"] == 3


def test_snapshot_toctou_checkpoint(tmp_path: Path):
    p = tmp_path / "a.csv"; p.write_text("id\n1\n", encoding="utf-8")
    snap = file_snapshot({"a": p})
    assert verify_file_snapshot(snap)["pass"] is True
    p.write_text("id\n2\n", encoding="utf-8")
    assert verify_file_snapshot(snap)["pass"] is False
    cp = transaction_checkpoint(tmp_path / "cp.json", {"done":["a"]})
    loaded = load_checkpoint(cp["path"])
    assert loaded["state"]["done"] == ["a"]


def test_multifile_identity_lineage_precedence():
    parent = Table(header=["customer_id","name"], rows=[["1","A"],["2","B"]])
    child = Table(header=["customer_id","amount"], rows=[["1","5"],["2","6"],["1","7"]])
    fks = automatic_foreign_keys({"parent":parent,"child":child})
    assert any(x["child_file"] == "child" and x["parent_file"] == "parent" for x in fks)
    line = source_lineage_graph({"parent":parent,"child":child}, fks)
    assert len(line["nodes"]) >= 6
    snap = {"files":{"parent":{"sha256":"x","mtime_ns":0}}}
    md = source_metadata(snap, {"source_metadata":{"parent":{"trust":"authoritative","epoch":"v1"}}})
    assert md["sources"]["parent"]["trust_label"] == "authoritative"
    pg = rule_precedence_graph([
        {"id":"low","target":"x","value":1,"priority":1},
        {"id":"high","target":"x","value":2,"priority":2},
    ])
    assert pg["precedence"][0]["id"] == "high" and pg["conflicts"]
    dd = deduplicate_witnesses([{"source_id":"a"},{"source_id":"a"},{"source_id":"b"}])
    assert dd["independent_count"] == 2
    impact = cascade_impact({"edges":[{"source":"column:0","target":"column:1"}]}, [0])
    assert impact["feature_id"] == 97


def test_safe_mode_maxima55_report_and_explanations(tmp_path: Path):
    p = tmp_path / "d.csv"; out = tmp_path / "o.csv"; rep = tmp_path / "r.json"
    p.write_text("cust_id,name\n1, Alice \n2,Bob\n", encoding="utf-8")
    r = repair(p, out, rep, RepairConfig(safe_mode=True))
    assert r.report["version"] == ccr.__version__
    assert r.report["maxima55"]["after"]["feature_count"] == 55
    assert r.report["edit_explanations"]
    assert read_table(out).rows[0][1] == "Alice"


def test_unrepaired_taxonomy_and_next_evidence():
    issue = {"code":"row_width_mismatch","message":"bad width","repairable":False,"row":1,"column":2}
    assert unrepaired_reason(issue) == "ambiguous_structure"
    e = next_evidence_from_issues([issue])
    assert e[0]["suggested_next_evidence"]
    x = explain_edits([{"candidate_id":"c","operation":"set_cell","row":0,"column":0,"old_value":"x","new_value":"y","reason":"r","decision":"committed","metadata":{"constraint_ids":["a","b"]}}])
    assert x[0]["independent_evidence_count"] == 2


def test_rule_export_and_drift(tmp_path: Path):
    report1 = {"input_logical_digest":"x","relationship_discovery":{"stable":[{"a":1}]}}
    report2 = {"input_logical_digest":"y","relationship_discovery":{"stable":[{"a":2}]}}
    x = export_learned_rules(report1, tmp_path / "rules.json")
    assert Path(x["path"]).exists()
    d = drift_report(report1, report2)
    assert d["drift_detected"] is True


def test_locked_benchmark_harness(tmp_path: Path):
    dirty = tmp_path / "dirty.csv"; clean = tmp_path / "clean.csv"
    dirty.write_text("id,name\n1, Alice \n2,Bob\n", encoding="utf-8")
    clean.write_text("id,name\n1,Alice\n2,Bob\n", encoding="utf-8")
    manifest = tmp_path / "bench.json"
    manifest.write_text(json.dumps({
        "corpus_origin":"test_fixture",
        "cases":[{"id":"ws","family":"whitespace","dirty":"dirty.csv","clean":"clean.csv"}],
    }), encoding="utf-8")
    lock = lock_benchmark_protocol(manifest, tmp_path / "lock.json")
    result = run_benchmark(manifest, lock)
    assert result["summary"]["cases"] == 1
    assert result["summary"]["zero_config"]["precision"] == 1.0
    assert result["cases"][0]["modes"]["zero_config"]["exact_dataset_recovery"] is True
    assert result["external_corpus_claim_allowed"] is False


def test_bounded_memory_stream_repair(tmp_path: Path):
    from csv_consistency_repair import stream_repair, StreamRepairConfig
    p=tmp_path/'s.csv'; out=tmp_path/'s.out.csv'; rep=tmp_path/'s.json'
    p.write_text('id,name,flag\n1, Alice ,YES\n2,Bob,no\n2,Bob,no\n',encoding='utf-8')
    r=stream_repair(p,out,rep,StreamRepairConfig(normalize_booleans=True,remove_exact_duplicates=True))
    assert r['bounded_memory'] is True and r['replay_pass'] is True
    assert r['rows_read']==3 and r['rows_written']==2 and r['duplicates_removed']==1
    assert out.read_text(encoding='utf-8') == 'id,name,flag\n1,Alice,true\n2,Bob,false\n'


def test_incremental_and_sharded_execution(tmp_path: Path):
    from csv_consistency_repair.maxima55 import incremental_recompute, sharded_stream_scan
    calls=[]
    out=incremental_recompute([(0,1)], {'1':['c1'],'2':['c2']}, {'c1':lambda: calls.append('c1') or 1,'c2':lambda: calls.append('c2') or 2})
    assert out['results']=={'c1':1} and calls==['c1'] and out['skipped']==1
    p1=tmp_path/'s1.csv';p2=tmp_path/'s2.csv'
    p1.write_text('id,x\n1,2\n',encoding='utf-8');p2.write_text('id,x\n2,4\n3,6\n',encoding='utf-8')
    sh=sharded_stream_scan({'a':p1,'b':p2},max_workers=2)
    assert sh['rows']==3 and sh['consistent_column_count'] and sh['precommit_snapshot_check']['pass']


def test_bounded_memory_stream_undo(tmp_path: Path):
    from csv_consistency_repair import stream_repair, stream_undo, StreamRepairConfig
    p=tmp_path/'s.csv'; out=tmp_path/'s.out.csv'; rep=tmp_path/'s.json'; restored=tmp_path/'restored.csv'
    p.write_text('id,name,flag\n1, Alice ,YES\n2,Bob,no\n2,Bob,no\n',encoding='utf-8')
    r=stream_repair(p,out,rep,StreamRepairConfig(normalize_booleans=True,remove_exact_duplicates=True,journal_edits=True))
    assert r['replay_pass'] is True and r['journal']
    u=stream_undo(out,rep,restored)
    assert u['logical_roundtrip_pass'] is True
    # Logical CSV equality is the stream contract; formatting itself is separately recorded.
    from csv_consistency_repair.io import read_table
    assert read_table(restored).header == read_table(p).header
    assert read_table(restored).rows == read_table(p).rows
