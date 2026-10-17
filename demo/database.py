"""Real isolated FalkorDB verification using preserved responses, never a model."""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from market_memory_lab import proof
from market_memory_lab.core import need, sha
from market_memory_lab.store import Store, durable
from agent import conversation
from agent import workflow
from .replay import SAVED, context, verify


def run(directory=Path('/state')):
    directory = Path(directory)
    need(os.environ.get('MML_JUDGE_CONFIRM_FIXTURE_SHA256') == hashlib.sha256(SAVED.read_bytes()).hexdigest(),
         'evidence_explicit_synthetic_confirmation_required')
    checked = verify()
    saved = checked['saved']
    durable(directory / 'confirmation.json', dict(fixture_sha256=hashlib.sha256(SAVED.read_bytes()).hexdigest(),
            scope='fixed synthetic Acme review and task-scoped evidence method for profile A',
            model_calls=0), True)
    deadline = time.monotonic() + 180
    prefix = 'mml_market_memory_lab_' + uuid.uuid4().hex[:12]
    mechanism = proof.run(directory / 'proof', prefix)
    need(mechanism['status'] == 'real_FalkorDB_topology_proof_passed'
         and mechanism['model_calls'] == 0 and len(mechanism['cases']) == 16,
         'evidence_real_graph_mechanism_required')
    state = dict(calls=mechanism['calls'], model_calls=0, phase='prepared')

    def charge():
        need(state['calls'] < 600 and time.monotonic() < deadline, 'evidence_DB_call_or_time_cap')
        state['calls'] += 1
        durable(directory / 'accounting.json', state)

    store = Store.connect(prefix + '_conversation', directory / 'guards', charge)
    results = {}
    try:
        need(store.exists() == 0, 'evidence_new_graph_required')
        before, after = saved['manifest_before'], saved['manifest_after']
        state['phase'] = 'review_acme'
        store.ingest(before['nodes'], before['edges'])

        def report(phase):
            source = saved['requests'][phase]['context']
            bundle = store.retrieve(source['request'])
            actual = context(bundle)
            need(actual == source, 'evidence_actual_DB_context_differs_from_recording')
            answer, envelope = workflow.validate_answer(saved['responses'][phase]['raw_output'], actual, bundle)
            results[phase] = dict(answer=answer, envelope=envelope,
                                  manifest_sha256=bundle['manifest_sha256'], context=actual)
            return bundle

        report('review_acme')
        state['phase'] = 'save_episode'
        intermediate = copy.deepcopy(before)
        episode = next(n for n in after['nodes'] if n['kind'] == 'Episode')
        intermediate['nodes'].append(episode)
        intermediate['edges'].extend(e for e in after['edges'] if e['rel'] == 'REVIEWED')
        store.ingest(intermediate['nodes'], intermediate['edges'])
        answer, proposal, acknowledgement = conversation.feedback_answer(saved['responses']['propose_method']['raw_output'])
        need(proposal == saved['confirmation']['proposal'], 'evidence_confirm_exact_saved_proposal')
        state['phase'] = 'confirm_method'
        store.ingest(after['nodes'], after['edges'])
        # Successful ingest includes completed barrier and exact readback. No canned
        # success is emitted before this point, and no new model is contacted.
        results['propose_method'] = dict(answer=answer, acknowledgement=acknowledgement,
                                         proposal_sha256=sha(proposal), committed_to_real_DB=True)
        state['phase'] = 'review_orbit'
        final = report('review_orbit')
        need(final['selection']['status'] == 'evidence_policy_met'
             and final['selection']['origins'] == ['filing_c', 'filing_w'], 'evidence_final_selection')
        need(store.ingest(after['nodes'], after['edges'])['status'] == 'idempotent', 'evidence_identical_retry')
        durable(directory / 'conversation-queries.json', store.receipts, True)
        durable(directory / 'results.json', results, True)
        state['phase'] = 'completed'
        durable(directory / 'accounting.json', state)
        summary = dict(status='real_FalkorDB_two_conversation_verification_passed',
                       execution='REAL FALKORDB + SAVED MODEL RESPONSES; NOT A NEW LIVE LLM RUN',
                       model_calls=0, application_calls=state['calls'], mechanism_cases=16, created_graphs=17,
                       conversation_steps=3, same_nodes_one_changed_origin_edge=True,
                       source_copy_not_extra_origin=True, actual_revenue_current='240',
                       separate_forecast_current='300', origins=final['selection']['origins'],
                       rule_references=final['selection']['rule_references'],
                       persistence_verified=False, source_responses_unchanged=True)
        durable(directory / 'summary.json', summary, True)
        return summary
    finally:
        store.close()


def main():
    try:
        print(json.dumps(run(), sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps(dict(status='evidence_database_check_stopped', model_calls=0,
                              automatic_retry=False, error_type=type(error).__name__), sort_keys=True))
        return 2
