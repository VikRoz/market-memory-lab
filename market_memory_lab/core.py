"""Strict v2 manifests and generic evidence evaluation. No fixture/name branches."""
import copy
import hashlib
import json
from decimal import Decimal

from market_memory_lab.model import canonical, decimal_value, identifier, timestamp, ValidationError

MAX_NODES, MAX_EDGES, MAX_HOPS = 13, 28, 4
BASE = {'id', 'kind', 'tenant', 'task', 'company', 'valid_at', 'known_at'}
FIELDS = {
    'User': set(),
    'Observation': {'metric', 'period', 'coverage', 'unit', 'category', 'previous', 'current', 'text', 'closed'},
    'Primary': {'metric', 'period', 'coverage', 'unit', 'category', 'previous', 'current', 'text', 'closed', 'qualified'},
    'Episode': {'summary', 'answer_sha256'},
    'Policy': {'rule_id', 'version', 'status', 'scope', 'min_origins', 'separate_forecasts',
               'confirmation', 'proposal_sha256', 'message', 'supersedes'},
}
RELS = {'ORIGIN', 'FROM_EPISODE', 'CONFIRMED_BY', 'REVIEWED', 'SUPERSEDES'}


def need(ok, reason):
    if not ok: raise ValidationError(reason)


def sha(value): return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate(nodes, edges):
    need(isinstance(nodes, list) and isinstance(edges, list)
         and 0 < len(nodes) <= MAX_NODES and len(edges) <= MAX_EDGES, 'v2_graph_bounds')
    index = {}
    versions = set()
    for n in nodes:
        need(isinstance(n, dict) and n.get('kind') in FIELDS
             and set(n) == BASE | FIELDS[n['kind']], 'v2_exact_node_schema')
        for k in ('id', 'tenant', 'task'): identifier(n[k])
        need(n['id'] not in index, 'v2_duplicate_id')
        if n['company']: identifier(n['company'])
        need(timestamp(n['valid_at']) <= timestamp(n['known_at']), 'v2_node_time_order')
        if n['kind'] in ('Observation', 'Primary'):
            need(n['company'] and n['category'] in ('actual', 'forecast') and type(n['closed']) is bool,
                 'v2_evidence_type')
            for k in ('metric', 'period', 'coverage', 'unit'): identifier(n[k])
            decimal_value(n['previous']); decimal_value(n['current'])
            need(decimal_value(n['previous']) != 0, 'v2_zero_denominator')
            need(isinstance(n['text'], str) and 0 < len(n['text'].encode()) <= 1000, 'v2_evidence_text')
            if n['kind'] == 'Primary': need(type(n['qualified']) is bool, 'v2_qualification')
        elif n['kind'] == 'Episode':
            need(n['company'] and isinstance(n['summary'], str) and 0 < len(n['summary'].encode()) <= 1000
                 and isinstance(n['answer_sha256'], str) and len(n['answer_sha256']) == 64, 'v2_episode')
        elif n['kind'] == 'Policy':
            identifier(n['rule_id'])
            version_key = (n['tenant'],n['task'],n['rule_id'],n['version'])
            need(version_key not in versions, 'v2_duplicate_rule_version'); versions.add(version_key)
            need(type(n['version']) is int and 1 <= n['version'] <= 16
                 and n['status'] in ('accepted', 'revoked') and n['scope'] in ('task', 'company')
                 and (n['scope'] == 'company') == bool(n['company'])
                 and type(n['min_origins']) is int and 1 <= n['min_origins'] <= 3
                 and n['separate_forecasts'] is True and n['confirmation'] == n['tenant']
                 and isinstance(n['message'], str) and 0 < len(n['message'].encode()) <= 2000,
                 'v2_supported_confirmed_policy')
            expected = {'schema_version': 2, 'family': 'qualified_origins', 'scope': n['scope'],
                        'company': n['company'], 'min_origins': n['min_origins'], 'separate_forecasts': True}
            need(n['proposal_sha256'] == sha(expected), 'v2_proposal_binding')
        index[n['id']] = n
    seen = set()
    for e in edges:
        need(isinstance(e, dict) and set(e) == {'source', 'rel', 'target', 'valid_at', 'known_at'}
             and e['source'] in index and e['target'] in index and e['rel'] in RELS, 'v2_edge_schema')
        need(timestamp(e['valid_at']) <= timestamp(e['known_at']), 'v2_edge_time_order')
        key = (e['source'], e['rel'], e['target'])
        need(key not in seen, 'v2_duplicate_edge'); seen.add(key)
        a, b = index[e['source']], index[e['target']]
        need(a['tenant'] == b['tenant'] and a['task'] == b['task'], 'v2_edge_scope')
        need(e['known_at'] >= max(a['known_at'], b['known_at'])
             and e['valid_at'] >= max(a['valid_at'], b['valid_at']), 'v2_edge_before_nodes')
        valid = ((e['rel'] == 'ORIGIN' and a['kind'] == 'Observation' and b['kind'] in ('Observation', 'Primary')
                  and a['company'] == b['company'])
                 or (e['rel'] == 'FROM_EPISODE' and a['kind'] == 'Policy' and b['kind'] == 'Episode')
                 or (e['rel'] == 'CONFIRMED_BY' and a['kind'] == 'Policy' and b['kind'] == 'User')
                 or (e['rel'] == 'REVIEWED' and a['kind'] == 'Episode' and b['kind'] == 'Observation'
                     and a['company'] == b['company'])
                 or (e['rel'] == 'SUPERSEDES' and a['kind'] == b['kind'] == 'Policy'
                     and a['rule_id'] == b['rule_id'] and a['version'] == b['version'] + 1))
        need(valid, 'v2_relation_contract')
    for n in nodes:
        outgoing = [e for e in edges if e['source'] == n['id']]
        if n['kind'] == 'Policy':
            need(len([e for e in outgoing if e['rel'] == 'FROM_EPISODE']) == 1
                 and len([e for e in outgoing if e['rel'] == 'CONFIRMED_BY']) == 1,
                 'v2_policy_confirmation_provenance')
            user = index[next(e['target'] for e in outgoing if e['rel'] == 'CONFIRMED_BY')]
            episode = index[next(e['target'] for e in outgoing if e['rel'] == 'FROM_EPISODE')]
            need(user['id'] == n['tenant'] and user['known_at'] <= n['known_at']
                 and episode['known_at'] <= n['known_at']
                 and (n['scope'] == 'task' or n['company'] == episode['company']), 'v2_confirmation_owner')
            prior = [p for p in nodes if p['kind'] == 'Policy' and p['tenant'] == n['tenant']
                     and p['task'] == n['task'] and p['rule_id'] == n['rule_id'] and p['version'] < n['version']]
            need(len(prior) == n['version'] - 1, 'v2_contiguous_versions')
            if n['version'] == 1:
                need(n['supersedes'] == '' and not any(e['rel'] == 'SUPERSEDES' for e in outgoing), 'v2_first_version')
            else:
                previous = next(p for p in prior if p['version'] == n['version'] - 1)
                need(n['supersedes'] == previous['id'] and previous['known_at'] <= n['known_at']
                     and (n['id'], 'SUPERSEDES', previous['id']) in seen, 'v2_version_chain')
    manifest = {'schema_version': 2, 'nodes': sorted(nodes, key=lambda n: n['id']),
                'edges': sorted(edges, key=canonical)}
    need(len(canonical(manifest).encode()) * 2 <= 65536, 'v2_payload_budget')
    return copy.deepcopy(manifest)


def request(tenant, company, market, knowledge, task='company_reviews'):
    for v in (tenant, company, task): identifier(v)
    timestamp(market); timestamp(knowledge)
    return dict(tenant=tenant, company=company, task=task, market=market, knowledge=knowledge)


def eligible(value, req):
    return value['known_at'] <= req['knowledge'] and value['valid_at'] <= req['market']


def scope(value, req):
    return value['tenant'] == req['tenant'] and value['task'] == req['task']


def oracle_paths(manifest, req):
    """Independent finite DFS oracle for tests; never a runtime graph fallback."""
    index = {n['id']: n for n in manifest['nodes']}
    result = []
    def visit(path):
        n = index[path[-1]]
        if n['kind'] == 'Primary':
            result.append(path); return
        if len(path) > MAX_HOPS: return
        for e in manifest['edges']:
            if e['source'] == n['id'] and e['rel'] == 'ORIGIN' and eligible(e, req):
                dest = index[e['target']]
                if dest['id'] not in path and eligible(dest, req): visit(path + [dest['id']])
    for n in manifest['nodes']:
        if n['kind'] == 'Observation' and scope(n, req) and n['company'] == req['company'] and eligible(n, req):
            visit([n['id']])
    return sorted(result)


def evaluate(manifest, req, paths, policies):
    """Validate graph-selected context, then fold policy and exact compatible evidence."""
    manifest = validate(manifest['nodes'], manifest['edges'])
    index = {n['id']: n for n in manifest['nodes']}
    observations = [n for n in manifest['nodes'] if n['kind'] == 'Observation' and scope(n, req)
                    and n['company'] == req['company'] and eligible(n, req)]
    def policy_provenance_visible(n):
        links = [e for e in manifest['edges'] if e['source'] == n['id'] and e['rel'] in ('FROM_EPISODE','CONFIRMED_BY')]
        return all(eligible(e,req) and eligible(index[e['target']],req) for e in links)
    expected_policies = [n for n in manifest['nodes'] if n['kind'] == 'Policy' and scope(n, req) and eligible(n, req)
                         and (n['scope'] == 'task' or n['company'] == req['company']) and policy_provenance_visible(n)]
    need(sorted(policies, key=lambda n:n['id']) == sorted(expected_policies, key=lambda n:n['id']),
         'v2_graph_policy_selection_mismatch')
    latest = {}
    for p in policies:
        if p['rule_id'] not in latest or p['version'] > latest[p['rule_id']]['version']: latest[p['rule_id']] = p
    active = [p for p in latest.values() if p['status'] == 'accepted']
    need(len(active) <= 1, 'v2_ambiguous_policy')
    refs = [p['id'] for p in active]
    # All outgoing lineage must be eligible, closed and finite. An omitted/future
    # branch cannot make the visible remaining roots look sufficient.
    unknown = False
    def closure(node_id, trail):
        nonlocal unknown
        n = index[node_id]
        if node_id in trail or len(trail) > MAX_HOPS or not eligible(n, req) or not n['closed']:
            unknown = True; return
        if n['kind'] == 'Primary':
            if not n['qualified']: unknown = True
            return
        outgoing = [e for e in manifest['edges'] if e['rel'] == 'ORIGIN' and e['source'] == node_id]
        if not outgoing: unknown = True
        for e in outgoing:
            if not eligible(e, req): unknown = True
            else: closure(e['target'], trail + [node_id])
    for n in observations: closure(n['id'], [])
    # Paths must genuinely exist, contain only eligible same-scope nodes and all
    # eligible ORIGIN edges; absence is not filled in by the Python DFS oracle.
    for path in paths:
        need(isinstance(path, list) and 2 <= len(path) <= MAX_HOPS + 1 and len(set(path)) == len(path), 'v2_invalid_path')
        need(path[0] in [n['id'] for n in observations] and index[path[-1]]['kind'] == 'Primary', 'v2_path_scope')
        for a, b in zip(path, path[1:]):
            need(any(e['source'] == a and e['target'] == b and e['rel'] == 'ORIGIN' and eligible(e, req)
                     for e in manifest['edges']) and eligible(index[b], req), 'v2_path_edge')
    roots = sorted({p[-1] for p in paths if index[p[0]]['category'] == 'actual'})
    actual = [n for n in observations if n['category'] == 'actual']
    comparable = actual + [index[r] for r in roots]
    signatures = {(n['metric'], n['period'], n['coverage'], n['unit'], n['category']) for n in comparable}
    numbers = {(n['previous'], n['current']) for n in comparable}
    if not observations or unknown: status = 'incomplete_evidence'
    elif not active: status = 'policy_required'
    elif not actual: status = 'actual_evidence_missing'
    elif len(signatures) != 1: status = 'incompatible_evidence'
    elif len(numbers) != 1: status = 'conflict'
    elif len(roots) < active[0]['min_origins']: status = 'insufficient_origins'
    else: status = 'evidence_policy_met'
    rows = []
    if actual and len(signatures) == len(numbers) == 1:
        n = actual[0]
        rows = [dict(metric=n['metric'], previous=n['previous'], current=n['current'],
                     change=format(Decimal(n['current']) - Decimal(n['previous']), 'f'), unit=n['unit'],
                     evidence_ids=sorted({n['id'] for n in actual} | set(roots)))]
    return {'status': status, 'rule_references': refs, 'origins': roots, 'paths': sorted(paths),
            'observations': rows, 'forecast_ids': sorted(n['id'] for n in observations if n['category'] == 'forecast'),
            'answer_allowed': status not in ('incomplete_evidence', 'incompatible_evidence', 'conflict', 'actual_evidence_missing')}


def parse_proposal(raw):
    from market_memory_lab.model import _unique_object
    value = json.loads(raw, object_pairs_hook=_unique_object)
    need(isinstance(value, dict) and set(value) == {'schema_version','family','scope','company','min_origins','separate_forecasts'}
         and value['schema_version'] == 2 and value['family'] == 'qualified_origins'
         and value['scope'] in ('task', 'company') and (value['scope'] == 'company') == bool(value['company'])
         and type(value['min_origins']) is int and 1 <= value['min_origins'] <= 3
         and value['separate_forecasts'] is True, 'v2_unsupported_policy_proposal')
    if value['company']: identifier(value['company'])
    return value
