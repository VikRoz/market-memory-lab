"""Bounded model input and grounded answer validation."""
import json
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from market_memory_lab import fixtures as f
from market_memory_lab.core import need, request, eligible, parse_proposal
from market_memory_lab.model import canonical, _unique_object, decimal_value

def utc():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def english_text(value):
    # English punctuation is Unicode too (e.g. a curly apostrophe). This is a
    # bounded Latin-script gate, not a claim to automatically prove English prose.
    return isinstance(value,str) and all(not c.isalpha() or unicodedata.name(c,'').startswith('LATIN ') for c in value)
COMMON = (
    'Return only the required JSON object. Do not use tools. All human-readable text must be English. '
    'Use only the provided records and provenance paths for numerical claims. Do not invent facts or user preferences. '
    'Apply any applicable confirmed user policy retrieved in memory. Make your assessment from the records and that memory; '
    'no assessment has been supplied. title is a concise heading. '
    'interpretation is a JSON-encoded string object with exactly summary, status, origin_ids. '
    'summary is your concise review. status is policy_required if no applicable user policy was retrieved, '
    'otherwise evidence_policy_met or policy_not_met according to that policy. '
    'origin_ids lists the primary-source record IDs supporting the main numerical result you report. '
    'observations lists one or more grounded numeric rows with metric, previous, current, change, unit and evidence_ids '
    'from the provided records. change is current minus previous in the same unit. '
    'rule_references lists the IDs of the retrieved user policies you applied, or an empty list.'
)


def context_for(store,company):
    bundle=store.retrieve(request('A',company,utc(),utc()))
    need(bundle['selection']['answer_allowed'],'unprimed_incomplete_context_blocks_model')
    req=bundle['request']
    context={'request':req,'policies':bundle['selected_policies'],
             'records':[n for n in bundle['manifest']['nodes'] if n['company']==company
                        and n['kind'] in ('Observation','Primary') and eligible(n,req)],
             'provenance_paths':bundle['selection']['paths']}
    need(len(canonical(context).encode())<=16000,'unprimed_context_bound')
    need(set(context)=={'request','policies','records','provenance_paths'},'unprimed_no_precomputed_selection')
    return bundle,context


def prompt_for(phase,context):
    return COMMON+'\nUSER REQUEST: Prepare a company review of '+context['request']['company']+\
        ' using its latest available evidence.\nFALKORDB MEMORY AND EVIDENCE: '+canonical(context)


def metric_matches(label,record):
    # The transport permits a metric string, not only an identifier. Accept the
    # identifier or an exact, fully grounded display label, never fuzzy aliases.
    return label in (record['metric'],f"{record['period']} {record['coverage']} {record['metric']} — {record['category']}")


def validate_answer(raw,context,bundle,proposal=False):
    answer=json.loads(raw,object_pairs_hook=_unique_object)
    need(isinstance(answer,dict) and set(answer)=={'title','interpretation','observations','rule_references'}
         and english_text(answer['title']) and 0<len(answer['title'])<=100
         and isinstance(answer['interpretation'],str) and len(answer['interpretation'].encode())<=4000,'unprimed_answer_schema')
    if proposal:
        parsed=parse_proposal(answer['interpretation'])
        need(parsed==f.PROPOSAL and answer['observations']==answer['rule_references']==[], 'unprimed_proposal_not_user_method')
        return answer,parsed
    parsed=json.loads(answer['interpretation'],object_pairs_hook=_unique_object)
    hidden=bundle['selection']
    expected_status='policy_required' if not context['policies'] else (
        'evidence_policy_met' if hidden['status']=='evidence_policy_met' else 'policy_not_met')
    need(isinstance(parsed,dict) and set(parsed)=={'summary','status','origin_ids'}
         and english_text(parsed['summary']) and 0<len(parsed['summary'])<=1000
         and parsed['status']==expected_status,'unprimed_policy_assessment_mismatch')
    for values in (parsed['origin_ids'],answer['rule_references']):
        need(isinstance(values,list) and all(isinstance(v,str) for v in values) and len(values)==len(set(values)),
             'unprimed_unique_references')
    need(sorted(answer['rule_references'])==sorted(hidden['rule_references'])
         and sorted(parsed['origin_ids'])==sorted(hidden['origins']),'unprimed_memory_or_origin_reference_mismatch')
    # Validate what the model actually chose to report, rather than supplying a
    # precomputed observations array in its prompt or forcing citation order.
    index={n['id']:n for n in context['records']}
    need(isinstance(answer['observations'],list) and 1<=len(answer['observations'])<=8,'unprimed_numeric_rows')
    actual_seen=False
    for row in answer['observations']:
        need(isinstance(row,dict) and set(row)=={'metric','previous','current','change','unit','evidence_ids'}
             and isinstance(row['evidence_ids'],list) and 1<=len(row['evidence_ids'])<=13
             and all(isinstance(v,str) and v in index for v in row['evidence_ids'])
             and len(set(row['evidence_ids']))==len(row['evidence_ids']),'unprimed_evidence_ids')
        previous,current,change=(decimal_value(row[k]) for k in ('previous','current','change'))
        need(current-previous==change,'unprimed_arithmetic')
        evidence=[index[v] for v in row['evidence_ids']]
        need(all(metric_matches(row['metric'],n) and n['unit']==row['unit']
                 and Decimal(n['previous'])==previous and Decimal(n['current'])==current for n in evidence),
             'unprimed_ungrounded_numeric_row')
        signatures={(n['category'],n['period'],n['coverage']) for n in evidence}
        need(len(signatures)==1,'unprimed_mixed_numeric_categories')
        actual_seen=actual_seen or evidence[0]['category']=='actual'
    need(actual_seen,'unprimed_actual_result_missing')
    return answer,parsed
