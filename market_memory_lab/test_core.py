import copy
import unittest
from . import fixtures as f
from .core import evaluate, oracle_paths, eligible, scope, validate, request, parse_proposal
from market_memory_lab.model import canonical, ValidationError


def selected(m,r):
    return [n for n in m['nodes'] if n['kind']=='Policy' and eligible(n,r) and scope(n,r)
            and (n['scope']=='task' or n['company']==r['company'])]


class CoreTests(unittest.TestCase):
    def test_controls(self):
        for c in f.cases():
            with self.subTest(c['name']):
                result=evaluate(c['manifest'],c['request'],oracle_paths(c['manifest'],c['request']),selected(c['manifest'],c['request']))
                self.assertEqual(result['status'],c['expected_status'])
                if c['expected_origins'] is not None:self.assertEqual(result['origins'],c['expected_origins'])

    def test_same_payload_only_edge(self):
        a,b=f.base(True),f.base(False)
        self.assertEqual(a['nodes'],b['nodes'])
        self.assertEqual(len(set(map(canonical,a['edges'])) ^ set(map(canonical,b['edges']))),2)

    def test_scope_and_cutoffs(self):
        m=f.base()
        for r in [request('B','Orbit',f.END,f.END),request('A','Orbit',f.END,f.END,'other_task'),
                  request('A','Orbit',f.T0,f.END),request('A','Orbit',f.END,f.T0)]:
            result=evaluate(m,r,oracle_paths(m,r),selected(m,r))
            self.assertEqual(result['rule_references'],[])

    def test_live_fixture(self):
        m=f.live();m['nodes'].append(f.node('Episode','acme_episode','Acme',summary='Review.',answer_sha256='0'*64))
        m['edges'].append(f.edge('acme_episode','REVIEWED','acme_report'));f.add_policy(m['nodes'],m['edges'],f.PROPOSAL,f.T1)
        r=request('A','Orbit',f.END,f.END)
        result=evaluate(m,r,oracle_paths(m,r),selected(m,r))
        self.assertEqual(result['status'],'evidence_policy_met')
        self.assertEqual(result['origins'],['filing_c','filing_w'])
        self.assertEqual(result['forecast_ids'],['forecast_article'])
        self.assertEqual(result['observations'][0]['current'],'240')
        self.assertNotIn('acme_report',result['observations'][0]['evidence_ids'])

    def test_strict_schema(self):
        m=f.base();m['nodes'][0]['unexpected']=True
        with self.assertRaises(ValidationError):validate(m['nodes'],m['edges'])

    def test_tenant_cross_edge(self):
        m=f.base();m['nodes'][0]['tenant']='B'
        with self.assertRaises(ValidationError):validate(m['nodes'],m['edges'])

    def test_supported_proposal(self):
        self.assertEqual(parse_proposal(canonical(f.PROPOSAL)),f.PROPOSAL)
        for patch in [dict(min_origins=0),dict(min_origins=True),dict(family='freeform'),dict(scope='all_users'),dict(separate_forecasts=False)]:
            with self.assertRaises(ValidationError):parse_proposal(canonical(f.PROPOSAL|patch))


if __name__=='__main__':unittest.main()
