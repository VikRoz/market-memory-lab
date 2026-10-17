"""Synthetic inputs, kept separate from the generic v2 algorithm."""
import copy
from .core import sha, validate, request

T0, T1, T2 = ('2026-10-16T00:00:00Z','2026-10-16T01:00:00Z','2026-10-16T02:00:00Z')
END = '2026-10-17T00:00:00Z'
MESSAGE = ('For all my company reviews, keep forecasts separate from actual results. '
           'Use the confirmed label only when at least two distinct qualified primary evidence origins '
           'support the same actual figures for the same metric, period, unit and coverage.')
PROPOSAL = dict(schema_version=2,family='qualified_origins',scope='task',company='',min_origins=2,separate_forecasts=True)


def node(kind, id, company='', tenant='A', at=T0, **fields):
    return dict(kind=kind,id=id,company=company,tenant=tenant,task='company_reviews',valid_at=at,known_at=at,**fields)


def evidence(kind,id,company,previous='200',current='240',category='actual',**extra):
    values = dict(metric='revenue',period='FY2025',coverage='consolidated',unit='million_USD',category=category,
                  previous=previous,current=current,text=f'{id}: {category} consolidated annual revenue.',closed=True)
    if kind == 'Primary': values['qualified'] = True
    values.update(extra)
    return node(kind,id,company,**values)


def edge(source,rel,target,at=T0):
    return dict(source=source,rel=rel,target=target,known_at=at,valid_at=at)


def base(shared=False, policy=True):
    nodes = [node('User','A'), evidence('Observation','acme_report','Acme','100','110'),
             evidence('Primary','acme_filing','Acme','100','110'),
             evidence('Observation','report_a','Orbit'),evidence('Observation','report_b','Orbit'),
             evidence('Primary','filing_w','Orbit'),evidence('Primary','filing_c','Orbit')]
    edges = [edge('acme_report','ORIGIN','acme_filing'), edge('report_a','ORIGIN','filing_w'),
             edge('report_b','ORIGIN','filing_w' if shared else 'filing_c')]
    if policy:
        nodes.append(node('Episode','acme_episode','Acme',summary='Synthetic prior review.',answer_sha256='0'*64))
        edges.append(edge('acme_episode','REVIEWED','acme_report'))
        add_policy(nodes,edges,PROPOSAL,T1)
    return validate(nodes,edges)


def add_policy(nodes,edges,proposal,at,version=1,status='accepted',episode='acme_episode',message=MESSAGE):
    p = node('Policy',f'evidence_method_v{version}',proposal['company'],at=at,rule_id='evidence_method',version=version,
             status=status,scope=proposal['scope'],min_origins=proposal['min_origins'],separate_forecasts=True,
             confirmation='A',proposal_sha256=sha(proposal),message=message,
             supersedes='' if version == 1 else f'evidence_method_v{version-1}')
    nodes.append(p)
    edges.extend([edge(p['id'],'FROM_EPISODE',episode,at),edge(p['id'],'CONFIRMED_BY','A',at)])
    if version > 1: edges.append(edge(p['id'],'SUPERSEDES',p['supersedes'],at))
    return p


def live():
    m = base(policy=False)
    m['nodes'].extend([evidence('Observation','report_copy','Orbit'),
                       evidence('Observation','forecast_article','Orbit',current='300',category='forecast'),
                       evidence('Primary','forecast_origin','Orbit',current='300',category='forecast')])
    m['edges'].extend([edge('report_copy','ORIGIN','report_a'),edge('forecast_article','ORIGIN','forecast_origin')])
    return validate(m['nodes'],m['edges'])


def cases():
    values = []
    def add(name,m,status,origins=None,req=None):
        values.append(dict(name=name,manifest=validate(m['nodes'],m['edges']),
                           request=req or request('A','Orbit',END,END),expected_status=status,expected_origins=origins))
    add('shared',base(True),'insufficient_origins',['filing_w'])
    add('split',base(),'evidence_policy_met',['filing_c','filing_w'])
    m=base(True); m['nodes'].append(evidence('Observation','report_copy','Orbit'))
    m['edges'].append(edge('report_copy','ORIGIN','report_a')); add('copies',m,'insufficient_origins',['filing_w'])
    m=base(True); m['edges'].append(edge('report_b','ORIGIN','report_a')); add('shared_branch',m,'insufficient_origins',['filing_w'])
    for field,value,name,status in [('current','250','conflict','conflict'),('unit','million_EUR','unit','incompatible_evidence'),
                                    ('period','FY2024','period','incompatible_evidence'),('coverage','segment','coverage','incompatible_evidence')]:
        m=base(); next(n for n in m['nodes'] if n['id']=='filing_c')[field]=value; add(name,m,status)
    m=base(); m['edges']=[e for e in m['edges'] if e['source']!='report_b']; add('unknown',m,'incomplete_evidence')
    m=base(); m['edges'].extend([edge('report_a','ORIGIN','report_b'),edge('report_b','ORIGIN','report_a')]); add('cycle',m,'incomplete_evidence')
    m=base(); m['nodes'].extend([evidence('Observation',f'chain_{i}','Orbit') for i in range(4)])
    m['edges']=[e for e in m['edges'] if e['source']!='report_b']
    path=['report_b']+[f'chain_{i}' for i in range(4)]+['filing_c']
    m['edges'].extend(edge(a,'ORIGIN',b) for a,b in zip(path,path[1:])); add('truncated',m,'incomplete_evidence')
    m=base(); next(n for n in m['nodes'] if n['id']=='filing_c')['qualified']=False; add('unqualified',m,'incomplete_evidence')
    m=base(); next(e for e in m['edges'] if e['source']=='report_b')['known_at']=T2
    add('late_lineage',m,'incomplete_evidence',req=request('A','Orbit',END,T1))
    m=base(); add_policy(m['nodes'],m['edges'],PROPOSAL,T2,2,'revoked'); add('revoked',m,'policy_required')
    m=base(); p=next(n for n in m['nodes'] if n['kind']=='Policy'); p.update(scope='company',company='Acme')
    p['proposal_sha256']=sha(PROPOSAL|dict(scope='company',company='Acme')); add('company_scope',m,'policy_required')
    m=base(); renames={'Acme':'Nova','Orbit':'Quasar','report_a':'news_73','report_b':'wire_22',
                     'filing_w':'origin_17','filing_c':'origin_89'}
    for n in m['nodes']:
        n['id']=renames.get(n['id'],n['id']); n['company']=renames.get(n['company'],n['company'])
        if n['kind'] in ('Observation','Primary') and n['company']=='Quasar': n.update(previous='70',current='91')
    for e in m['edges']:
        for k in ('source','target'): e[k]=renames.get(e[k],e[k])
    add('held_out',m,'evidence_policy_met',['origin_17','origin_89'],request('A','Quasar',END,END))
    return values
