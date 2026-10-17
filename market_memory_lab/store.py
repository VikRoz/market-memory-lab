"""Immutable manifests, pending barrier and fixed parameterized Cypher.

Single host/check-out protocol. A failed write leaves the journal and blocks reads;
there is no automatic recovery, transaction/durability claim or reference fallback.
"""
import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from market_memory_lab.graph import GraphStore
from market_memory_lab.model import canonical
from .core import MAX_NODES, MAX_EDGES, RELS, need, sha, validate, evaluate

QUERIES = {
 'head': 'MATCH (h:V2Barrier) RETURN properties(h)',
 'nodes': 'MATCH (n) WHERE NOT n:V2Barrier RETURN labels(n), properties(n) LIMIT 14',
 'edges': 'MATCH (a)-[e]->(b) RETURN a.id,type(e),b.id,properties(e) LIMIT 29',
 'allocate': 'CREATE (h:V2Barrier {status:"pending", token:$token, manifest:$manifest, digest:$digest}) RETURN properties(h)',
 'begin': 'MATCH (h:V2Barrier {status:"completed",digest:$old}) SET h.status="pending",h.token=$token,h.manifest=$manifest,h.digest=$digest RETURN properties(h)',
 'nodes_add': 'MATCH (h:V2Barrier {status:"pending",token:$token,digest:$digest}) UNWIND $rows AS row CREATE (n:V2Record) SET n=row RETURN count(n)',
 'complete': 'MATCH (h:V2Barrier {status:"pending",token:$token,digest:$digest}) SET h.status="completed" RETURN properties(h)',
 'paths': '''MATCH p=(o:V2Record {kind:"Observation",tenant:$tenant,task:$task,company:$company})-[:ORIGIN*1..4]->(r:V2Record {kind:"Primary"})
 WHERE all(n IN nodes(p) WHERE n.tenant=$tenant AND n.task=$task AND n.company=$company AND n.known_at<=$knowledge AND n.valid_at<=$market)
 AND all(e IN relationships(p) WHERE e.known_at<=$knowledge AND e.valid_at<=$market)
 AND all(n IN nodes(p) WHERE single(other IN nodes(p) WHERE other.id=n.id))
 RETURN [n IN nodes(p) | n.id] ORDER BY o.id,r.id LIMIT 65''',
 'policies': '''MATCH (p:V2Record {kind:"Policy",tenant:$tenant,task:$task})-[c:CONFIRMED_BY]->(u:V2Record {kind:"User",id:$tenant}),
 (p)-[f:FROM_EPISODE]->(e:V2Record {kind:"Episode",tenant:$tenant,task:$task})
 WHERE p.known_at<=$knowledge AND p.valid_at<=$market AND (p.scope="task" OR p.company=$company)
 AND c.known_at<=$knowledge AND c.valid_at<=$market AND f.known_at<=$knowledge AND f.valid_at<=$market
 AND u.known_at<=$knowledge AND u.valid_at<=$market AND e.known_at<=$knowledge AND e.valid_at<=$market
 RETURN p.payload ORDER BY p.id LIMIT 14''',
}
for rel in sorted(RELS):
    QUERIES['edge_' + rel] = ('MATCH (h:V2Barrier {status:"pending",token:$token,digest:$digest}) '
        'UNWIND $rows AS row MATCH (a:V2Record {id:row.source}),(b:V2Record {id:row.target}) '
        'CREATE (a)-[e:' + rel + ' {known_at:row.known_at,valid_at:row.valid_at}]->(b) RETURN count(e)')


def durable(path, value, exclusive=False):
    path = Path(path)
    need(not path.is_symlink(), 'v2_state_symlink')
    target = path if exclusive else path.with_suffix('.writing')
    with target.open('x') as stream:
        os.chmod(target, 0o600)
        stream.write(canonical(value) + '\n'); stream.flush(); os.fsync(stream.fileno())
    if not exclusive: target.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def properties(n):
    result = {k:n[k] for k in ('id','kind','tenant','task','company','known_at','valid_at')}
    if n['kind'] == 'Policy': result['scope'] = n['scope']
    result.update(payload=canonical(n), digest=sha(n))
    return result


class Store:
    def __init__(self, raw, directory, charge):
        self.raw, self.directory, self.charge = raw, Path(directory), charge
        self.directory.mkdir(parents=True, exist_ok=True)
        identity = sha(raw.graph.name)
        self.lock = self.directory / (identity + '.lock')
        self.journal = self.directory / (identity + '.pending.json')
        self.receipts = []

    @classmethod
    def connect(cls, graph, directory, charge):
        need(graph.startswith('mml_market_memory_lab_'), 'v2_namespace_required')
        return cls(GraphStore.from_operator_env(graph_name=graph), directory, charge)

    def close(self): self.raw.close()

    @contextmanager
    def guarded(self):
        fd = os.open(self.lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            need(not self.journal.exists(), 'v2_uncertain_write_blocks_read_and_write')
            yield
        finally: os.close(fd)

    def query(self, name, params=None, write=False):
        need(name in QUERIES, 'v2_fixed_query_required'); self.charge()
        method = self.raw.graph.query if write else self.raw.graph.ro_query
        rows = method(QUERIES[name], params=params or {}, timeout=2000).result_set
        self.receipts.append({'query':name,'query_sha256':sha(QUERIES[name]),'params':params or {},'rows':rows})
        return rows

    def exists(self):
        self.charge(); value = self.raw.client.execute_command('EXISTS', self.raw.graph.name)
        need(type(value) is int and value in (0,1), 'v2_exists_contract')
        return value

    def head(self):
        rows = self.query('head')
        need(len(rows) == 1 and len(rows[0]) == 1, 'v2_single_barrier_required')
        h = rows[0][0]
        need(set(h) == {'status','token','manifest','digest'} and h['status'] in ('pending','completed')
             and isinstance(h['token'], str) and len(h['token']) == 32, 'v2_head_schema')
        m = json.loads(h['manifest']); need(set(m) == {'schema_version','nodes','edges'} and m['schema_version'] == 2,
                                           'v2_manifest_schema')
        need(validate(m['nodes'],m['edges']) == m and h['manifest'] == canonical(m) and sha(m) == h['digest'], 'v2_manifest_digest')
        return h, m

    def integrity(self, manifest):
        rows = self.query('nodes'); need(len(rows) <= MAX_NODES, 'v2_node_overflow')
        need(all(row[0] == ['V2Record'] for row in rows), 'v2_unexpected_label')
        need(sorted((row[1] for row in rows),key=canonical) == sorted(map(properties,manifest['nodes']),key=canonical),
             'v2_node_integrity')
        edges = self.query('edges'); need(len(edges) <= MAX_EDGES, 'v2_edge_overflow')
        expected = [[e['source'],e['rel'],e['target'],{'valid_at':e['valid_at'],'known_at':e['known_at']}]
                    for e in manifest['edges']]
        need(sorted(edges,key=canonical) == sorted(expected,key=canonical), 'v2_edge_integrity')

    def ingest(self, nodes, edges):
        new = validate(nodes,edges)
        with self.guarded():
            exists = self.exists()
            if exists:
                head, old = self.head(); need(head['status'] == 'completed', 'v2_pending_blocks')
                self.integrity(old)
                need(set(map(canonical,old['nodes'])) <= set(map(canonical,new['nodes']))
                     and set(map(canonical,old['edges'])) <= set(map(canonical,new['edges'])), 'v2_immutable_conflict')
                if old == new: return {'status':'idempotent','manifest_sha256':sha(new)}
            else: old = {'nodes':[],'edges':[]}
            token = uuid.uuid4().hex
            params = dict(token=token,manifest=canonical(new),digest=sha(new))
            durable(self.journal, {'graph':self.raw.graph.name,'manifest':new,'token':token}, exclusive=True)
            # Intent fsynced before the first allocation or any pending/domain write.
            if exists: params['old'] = head['digest']
            rows = self.query('begin' if exists else 'allocate', params, True)
            expected_head = {k:params[k] for k in ('token','manifest','digest')} | {'status':'pending'}
            need(rows == [[expected_head]], 'v2_pending_ack')
            additions = [n for n in new['nodes'] if n not in old['nodes']]
            if additions:
                need(self.query('nodes_add', params | {'rows':list(map(properties,additions))}, True) == [[len(additions)]], 'v2_node_ack')
            for rel in sorted(RELS):
                additions = [e for e in new['edges'] if e['rel'] == rel and e not in old['edges']]
                if additions:
                    need(self.query('edge_' + rel, params | {'rows':additions}, True) == [[len(additions)]], 'v2_edge_ack')
            self.integrity(new)
            need(self.query('complete', params, True) == [[expected_head | {'status':'completed'}]], 'v2_completion_ack')
            observed, actual = self.head()
            need(actual == new and observed['status'] == 'completed', 'v2_completed_readback')
            self.journal.unlink()
            fd = os.open(self.directory, os.O_RDONLY)
            try: os.fsync(fd)
            except OSError:
                durable(self.journal, {'graph':self.raw.graph.name,'manifest':new,'token':token}, exclusive=True)
                raise
            finally:
                os.close(fd)
            return {'status':'completed','manifest_sha256':sha(new),'new_nodes':len(new['nodes'])-len(old['nodes'])}

    def retrieve(self, req):
        with self.guarded():
            head, manifest = self.head(); need(head['status'] == 'completed', 'v2_pending_blocks')
            self.integrity(manifest)
            path_rows = self.query('paths', req); need(len(path_rows) <= 64, 'v2_path_truncation')
            paths = [row[0] for row in path_rows]
            policy_rows = self.query('policies', req); need(len(policy_rows) <= MAX_NODES, 'v2_policy_truncation')
            policies = [json.loads(row[0]) for row in policy_rows]
            result = evaluate(manifest,req,paths,policies)
            need(self.head()[0] == head, 'v2_changed_during_read')
            return {'request':req,'selection':result,'manifest_sha256':sha(manifest),
                    'selected_policies':policies,'manifest':manifest}
