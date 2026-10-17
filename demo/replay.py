"""Verify preserved model responses without contacting a model or a database."""
import copy
import hashlib
import json
from pathlib import Path

from agent import conversation as conversation
from agent import workflow as workflow
from market_memory_lab import fixtures
from market_memory_lab.core import evaluate, need, oracle_paths, sha, validate
from market_memory_lab.store import QUERIES
from market_memory_lab.model import _unique_object, canonical

SAVED = Path(__file__).with_name('saved_run.json')
PHASES = ('review_acme', 'propose_method', 'review_orbit')


def load(path=SAVED):
    path = Path(path)
    need(path.is_file() and not path.is_symlink() and path.stat().st_size < 128000,
         'evidence_saved_file_bound')
    return json.loads(path.read_text(), object_pairs_hook=_unique_object)


def context(bundle):
    req = bundle['request']
    return dict(request=req, policies=bundle['selected_policies'],
                records=[n for n in bundle['manifest']['nodes']
                         if n['company'] == req['company']
                         and n['kind'] in ('Observation', 'Primary')
                         and n['known_at'] <= req['knowledge'] and n['valid_at'] <= req['market']],
                provenance_paths=bundle['selection']['paths'])


def verify(saved=None):
    saved = load() if saved is None else copy.deepcopy(saved)
    need(set(saved) == {'schema_version', 'classification', 'execution', 'requests', 'responses',
                       'manifest_before', 'manifest_after', 'confirmation', 'retrieval_queries',
                       'recorded_scenario'} and saved['schema_version'] == 1
         and saved['classification'] == 'synthetic', 'evidence_saved_schema')
    before, after = saved['manifest_before'], saved['manifest_after']
    need(validate(before['nodes'], before['edges']) == before == fixtures.live(), 'evidence_original_inputs')
    need(validate(after['nodes'], after['edges']) == after, 'evidence_final_manifest')
    need(set(map(canonical, before['nodes'])) <= set(map(canonical, after['nodes']))
         and set(map(canonical, before['edges'])) <= set(map(canonical, after['edges'])),
         'evidence_immutable_history')
    confirmation = saved['confirmation']
    need(set(confirmation) == {'proposal', 'approved_digest', 'event'}
         and confirmation['proposal'] == fixtures.PROPOSAL
         and confirmation['approved_digest'] == sha(fixtures.PROPOSAL)
         and confirmation['event']['message'] == fixtures.MESSAGE
         and confirmation['event'] in after['nodes'], 'evidence_confirmation_binding')
    episode = next(n for n in after['nodes'] if n['id'] == 'acme_episode')
    need(episode['answer_sha256'] == saved['responses']['review_acme']['raw_sha256'],
         'evidence_review_provenance')
    need(set(saved['requests']) == set(saved['responses']) == set(PHASES), 'evidence_three_responses')
    need(set(saved['retrieval_queries']) == {'review_acme', 'review_orbit'}, 'evidence_query_phases')
    outputs = {}
    for phase in PHASES:
        response = saved['responses'][phase]
        need(set(response) == {'raw_output', 'raw_sha256'}
             and hashlib.sha256(response['raw_output'].encode()).hexdigest() == response['raw_sha256'],
             'evidence_raw_output_digest')
        request = saved['requests'][phase]
        need(set(request) == {'prompt', 'context'}, 'evidence_request_fields')
        if phase == 'propose_method':
            need(request['context'] is None
                 and request['prompt'] == conversation.FEEDBACK_FORMAT + '\nUSER COMMENT: ' + fixtures.MESSAGE,
                 'evidence_comment_is_method_input')
            answer, proposal, acknowledgement = conversation.feedback_answer(response['raw_output'])
            need(proposal == confirmation['proposal'], 'evidence_model_proposal')
        else:
            manifest = before if phase == 'review_acme' else after
            req = request['context']['request']
            queries = saved['retrieval_queries'][phase]
            need(len(queries) == 2 and {q['query'] for q in queries} == {'paths', 'policies'},
                 'evidence_selected_query_set')
            for query in queries:
                need(set(query) == {'query', 'query_sha256', 'params', 'rows'}
                     and query['query_sha256'] == sha(QUERIES[query['query']]) and query['params'] == req,
                     'evidence_fixed_query_binding')
            paths = [row[0] for q in queries if q['query'] == 'paths' for row in q['rows']]
            policies = [json.loads(row[0]) for q in queries if q['query'] == 'policies' for row in q['rows']]
            need(sorted(paths) == oracle_paths(manifest, req), 'evidence_saved_path_oracle')
            bundle = dict(request=req, manifest=manifest, selected_policies=policies,
                          selection=evaluate(manifest, req, paths, policies))
            need(context(bundle) == request['context']
                 and workflow.prompt_for(phase, request['context']) == request['prompt'],
                 'evidence_complete_prompt_binding')
            answer, envelope = workflow.validate_answer(response['raw_output'], request['context'], bundle)
            if phase == 'review_acme':
                need(request['context']['policies'] == [] and fixtures.MESSAGE not in request['prompt'],
                     'evidence_first_report_unprimed')
            else:
                need(request['context']['policies'] == [confirmation['event']]
                     and answer['rule_references'] == ['evidence_method_v1'], 'evidence_memory_from_graph')
        outputs[phase] = answer
    return dict(status='saved_two_conversation_evidence_verified', execution='OFFLINE SAVED OUTPUT VERIFICATION',
                model_calls=0, database_calls=0, live_run_performed=False,
                outputs=outputs, saved=saved)


def display(result):
    print('SAVED REAL MODEL RESPONSES | NO LIVE MODEL OR DATABASE CALLS\n')
    for phase in PHASES:
        if phase == 'propose_method':
            print('USER COMMENT: ' + fixtures.MESSAGE)
        print(phase.upper())
        print(json.dumps(result['outputs'][phase], ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ('outputs', 'saved')}, sort_keys=True))
