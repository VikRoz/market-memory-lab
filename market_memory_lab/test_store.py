"""Fault-injection tests, NOT evidence of Cypher execution."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from .store import Store, QUERIES
from . import fixtures as f
from .core import oracle_paths, request
from .test_core import selected
from market_memory_lab.model import ValidationError, canonical


class Wire:
    name='mml_market_memory_lab_offline'
    def __init__(self):self.h=None;self.nodes=[];self.edges=[];self.calls=[];self.fail=None
    def execute_command(self,*args):return int(self.h is not None)
    def close(self):pass
    def ro_query(self,q,params,timeout):return self.query(q,params,timeout)
    def query(self,q,params,timeout):
        name=next(k for k,v in QUERIES.items() if q==v);self.calls.append(name)
        assert timeout==2000
        if name==self.fail:raise RuntimeError('Injected lost acknowledgement')
        p=params
        if name=='allocate':self.h={k:p[k] for k in ('token','manifest','digest')}|dict(status='pending');rows=[[copy.deepcopy(self.h)]]
        elif name=='head':rows=[] if self.h is None else [[copy.deepcopy(self.h)]]
        elif name=='begin':self.h={k:p[k] for k in ('token','manifest','digest')}|dict(status='pending');rows=[[copy.deepcopy(self.h)]]
        elif name=='nodes_add':self.nodes.extend(copy.deepcopy(p['rows']));rows=[[len(p['rows'])]]
        elif name.startswith('edge_'):
            self.edges.extend([[e['source'],name[5:],e['target'],{k:e[k] for k in ('valid_at','known_at')}] for e in p['rows']]);rows=[[len(p['rows'])]]
        elif name=='complete':self.h['status']='completed';rows=[[copy.deepcopy(self.h)]]
        elif name=='nodes':rows=[[['V2Record'],copy.deepcopy(n)] for n in self.nodes]
        elif name=='edges':rows=copy.deepcopy(self.edges)
        elif name=='paths':rows=[[path] for path in oracle_paths(json.loads(self.h['manifest']),p)]
        elif name=='policies':rows=[[canonical(n)] for n in selected(json.loads(self.h['manifest']),p)]
        else:raise AssertionError(name)
        return SimpleNamespace(result_set=rows)


class StoreTests(unittest.TestCase):
    def test_cyclic_walks_excluded_by_fixed_query(self):
        self.assertIn('single(other IN nodes(p) WHERE other.id=n.id)',QUERIES['paths'])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.w=Wire()
        self.s=Store(SimpleNamespace(graph=self.w,client=self.w,close=self.w.close),Path(self.temp.name),lambda:None)
        self.m=f.base()
    def tearDown(self):self.temp.cleanup()
    def ingest(self):return self.s.ingest(self.m['nodes'],self.m['edges'])
    def test_ingest_read_and_idempotency(self):
        self.assertEqual(self.ingest()['status'],'completed')
        self.assertFalse(self.s.journal.exists())
        self.assertEqual(self.ingest()['status'],'idempotent')
        self.assertEqual(self.s.retrieve(request('A','Orbit',f.END,f.END))['selection']['status'],'evidence_policy_met')
    def test_conflicting_record_no_write(self):
        self.ingest();self.m['nodes'][0]['task']='other';before=len(self.w.calls)
        with self.assertRaises(ValidationError):self.ingest()
        self.assertFalse(self.s.journal.exists())
        self.assertNotIn('begin',self.w.calls[before:])
    def test_removing_edge_rejected(self):
        self.ingest();self.m['edges'].pop();before=len(self.w.calls)
        with self.assertRaises(ValidationError):self.ingest()
        self.assertNotIn('begin',self.w.calls[before:])
    def test_lost_ack_journal_retained(self):
        self.w.fail='nodes_add'
        with self.assertRaises(RuntimeError):self.ingest()
        self.assertTrue(self.s.journal.exists())
        self.assertEqual(self.w.h['status'],'pending')
        before=len(self.w.calls)
        with self.assertRaises(ValidationError):self.s.retrieve(request('A','Orbit',f.END,f.END))
        self.assertEqual(len(self.w.calls),before)
    def test_foreign_mutation_blocks(self):
        self.ingest();self.w.nodes[0]['payload']='{}'
        with self.assertRaises(ValidationError):self.s.retrieve(request('A','Orbit',f.END,f.END))
    def test_pending_head_blocks(self):
        self.ingest();self.w.h['status']='pending'
        with self.assertRaises(ValidationError):self.s.retrieve(request('A','Orbit',f.END,f.END))
    def test_append_episode(self):
        self.ingest();self.m['nodes'].append(f.node('Episode','another','Orbit',summary='A review.',answer_sha256='1'*64))
        self.m['edges'].append(f.edge('another','REVIEWED','report_a'))
        self.assertEqual(self.ingest()['new_nodes'],1)
