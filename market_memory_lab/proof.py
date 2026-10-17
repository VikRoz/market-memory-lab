"""Explicit live FalkorDB mechanism test. Never invoked by offline tests."""
import argparse
import copy
import json
import time
from pathlib import Path
from . import fixtures as f
from .core import need, sha, oracle_paths, request, validate
from .store import Store, durable
from market_memory_lab.model import ValidationError

def run(directory, prefix):
    directory = Path(directory)
    directory.mkdir()
    need(prefix.startswith('mml_market_memory_lab_') and len(prefix) < 55, 'v2_proof_namespace')
    state = {'status': 'active', 'calls': 0, 'prior_calls': 0, 'prior_state_sha256': None, 'model_calls': 0, 'created_graphs': [], 'cases': [], 'deadline': time.monotonic() + 180}
    durable(directory / 'state.json', state, True)

    def charge():
        need(time.monotonic() < state['deadline'] and state['calls'] < 600, 'v2_proof_call_or_time_cap')
        state['calls'] += 1
        durable(directory / 'state.json', state)
    outcomes = {}
    try:
        cases = f.cases()
        need(len(cases) <= 20, 'v2_graph_count_cap')
        for case in cases:
            name = case['name']
            graph = prefix + '_' + name
            store = Store.connect(graph, directory / 'guards', charge)
            try:
                exists = store.exists()
                m = case['manifest']
                need(exists == 0, 'v2_proof_new_graph_required')
                state['created_graphs'].append(graph)
                durable(directory / 'state.json', state)
                ingest = store.ingest(m['nodes'], m['edges'])
                got = store.retrieve(case['request'])
                selected = got['selection']
                need(selected['paths'] == oracle_paths(m, case['request']), 'v2_live_path_oracle_mismatch')
                need(selected['status'] == case['expected_status'], 'v2_live_status_mismatch')
                if case['expected_origins'] is not None:
                    need(selected['origins'] == case['expected_origins'], 'v2_live_origin_mismatch')
                extra = []
                if name == 'split':
                    for req in (request('B', 'Orbit', f.END, f.END), request('A', 'Orbit', f.END, f.END, 'other_task'), request('A', 'Orbit', f.T0, f.END), request('A', 'Orbit', f.END, f.T0)):
                        check = store.retrieve(req)
                        need(check['selection']['rule_references'] == [], 'v2_scope_or_time_leak')
                        need(check['selection']['paths'] == oracle_paths(m, req), 'v2_control_path_mismatch')
                        extra.append(check)
                    need(store.ingest(m['nodes'], m['edges'])['status'] == 'idempotent', 'v2_idempotency')
                    changed = copy.deepcopy(m)
                    next((n for n in changed['nodes'] if n['id'] == 'report_b'))['text'] = 'Conflicting immutable identity.'
                    try:
                        store.ingest(changed['nodes'], changed['edges'])
                    except ValidationError as error:
                        need(str(error) == 'v2_immutable_conflict', 'v2_unexpected_conflict_reason')
                    else:
                        raise ValidationError('v2_identity_conflict_accepted')
                    need(store.retrieve(case['request']) == got, 'v2_conflict_mutated_graph')
                result = {'case': case, 'ingest': ingest, 'retrieval': got, 'controls': extra, 'queries': store.receipts}
                durable(directory / (name + '.json'), result, True)
                state['cases'].append({'name': name, 'status': selected['status'], 'origins': selected['origins'], 'receipt_sha256': sha(result)})
                durable(directory / 'state.json', state)
                outcomes[name] = got
                print(json.dumps({'case': name, 'status': selected['status'], 'roots': selected['origins']}), flush=True)
            finally:
                store.close()
        (a, b) = (outcomes['shared'], outcomes['split'])
        need(a['manifest']['nodes'] == b['manifest']['nodes'], 'v2_counterfactual_node_payload_changed')
        need(a['selection']['status'] == 'insufficient_origins' and b['selection']['status'] == 'evidence_policy_met', 'v2_topology_not_discriminating')
        state.update(status='real_FalkorDB_topology_proof_passed', same_node_payloads=True, only_one_origin_edge_changed=True)
        durable(directory / 'state.json', state)
        return state
    except BaseException as error:
        state.update(status='stopped', failure_class=type(error).__name__, reason=str(error)[:200])
        durable(directory / 'state.json', state)
        raise
