from __future__ import annotations

import csv
import json
from pathlib import Path

from csv_consistency_repair.engine import RepairConfig, repair
from csv_consistency_repair.knowledge import empty_knowledge_registry, update_knowledge_registry


def _write(path: Path, rows: list[list[str]]) -> None:
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['key', 'value', 'id'])
        w.writerows(rows)


def test_cumulative_knowledge_tracks_weak_to_certified_and_guides_next_cycle(tmp_path: Path):
    rows: list[list[str]] = []
    for key, value in [('A', 'red'), ('B', 'blue'), ('C', 'green'), ('D', 'gold')]:
        for _ in range(6):
            rows.append([key, value, str(len(rows))])
    # One formatting defect keeps key->value just below the strict stability probe.
    # A separate missing value becomes reconstructible only after that relation stabilizes.
    rows[1][1] = 'red '
    rows[7][1] = ''

    src = tmp_path / 'in.csv'
    out = tmp_path / 'out.csv'
    report = tmp_path / 'report.json'
    _write(src, rows)
    cfg = RepairConfig(
        auto_mode=False,
        discover_relationships=True,
        repair_discovered_relationships=True,
        repair_missing_values=True,
        discovery_min_rows=12,
        discovery_confidence=0.95,
        max_cycles=6,
        stable_cycles_required=2,
        cumulative_knowledge=True,
    )
    result = repair(src, out, report, cfg)
    data = json.loads(report.read_text(encoding='utf-8'))
    assert result.final_status == 'PASS'

    with out.open(newline='', encoding='utf-8') as f:
        repaired = list(csv.reader(f))
    assert repaired[8][1] == 'blue'

    registry = data['convergence_knowledge']
    matches = [
        e for e in registry['relations'].values()
        if e['definition'].get('determinant') == ['key'] and e['definition'].get('dependent') == 'value'
    ]
    assert len(matches) == 1
    relation = matches[0]
    observations = [o for o in relation['observations'] if o.get('observed')]
    assert observations[0]['stable'] is False
    assert any(o['provenance'] == 'certified_after_repair' for o in observations[1:])
    assert relation['ever_strengthened_after_repair'] is True
    assert relation['relation_id'] in registry['views']['newly_certified_relations']
    assert relation['relation_id'] in registry['views']['strengthened_by_repair_relations']

    related_edits = [e for e in data['committed_edits'] if e.get('metadata', {}).get('relation_id') == relation['relation_id']]
    assert related_edits
    assert related_edits[-1]['knowledge_support']['best_rank'] == 0
    assert related_edits[-1]['knowledge_support']['authority'] == 'ordering_and_provenance_only'


def test_knowledge_memory_preserves_historical_relation_without_promoting_it():
    state = empty_knowledge_registry()
    weak = {
        'relationship': {
            'relationships': [{
                'relation_id': 'r1', 'kind': 'functional_dependency',
                'determinant': ['a'], 'dependent': 'b',
                'stability': {'pass': False, 'raw': {'confidence': 0.91, 'coverage': 1.0, 'considered_rows': 20}},
            }]
        }
    }
    certified = {
        'relationship': {
            'relationships': [{
                'relation_id': 'r1', 'kind': 'functional_dependency',
                'determinant': ['a'], 'dependent': 'b',
                'stability': {'pass': True, 'raw': {'confidence': 1.0, 'coverage': 1.0, 'considered_rows': 20}},
            }]
        }
    }
    update_knowledge_registry(state, weak, cycle=0, state_digest='s0', edits_since_previous=[])
    update_knowledge_registry(state, certified, cycle=1, state_digest='s1', edits_since_previous=[{'candidate_id': 'e1'}])
    update_knowledge_registry(state, {}, cycle=2, state_digest='s2', edits_since_previous=[{'candidate_id': 'e2'}])
    entry = state['relations']['r1']
    assert entry['certified_cycle'] == 1
    assert entry['current_status'] == 'historical_certified'
    assert entry['observations'][-1]['observed'] is False
    assert entry['observations'][-1]['provenance'] == 'not_observed_after_repair'


def test_disabling_cumulative_knowledge_does_not_change_repair_authority(tmp_path: Path):
    rows = [['A', 'x', str(i)] for i in range(12)]
    rows[0][1] = ' x '
    a = tmp_path / 'a.csv'; b = tmp_path / 'b.csv'; c = tmp_path / 'c.csv'
    ra = tmp_path / 'ra.json'; rb = tmp_path / 'rb.json'
    _write(a, rows)
    base = dict(auto_mode=False, max_cycles=4, stable_cycles_required=2)
    r1 = repair(a, b, ra, RepairConfig(**base, cumulative_knowledge=True))
    r2 = repair(a, c, rb, RepairConfig(**base, cumulative_knowledge=False))
    assert b.read_bytes() == c.read_bytes()
    assert r1.committed_edits == r2.committed_edits
    d2 = json.loads(rb.read_text(encoding='utf-8'))
    assert d2['convergence_knowledge']['enabled'] is False
