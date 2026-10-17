"""Offline packaging/fault tests. Doubles never prove real database execution."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from market_memory_lab.store import Store
from market_memory_lab.test_store import Wire
from market_memory_lab.model import ValidationError
from demo import database, prepare, replay

class PublicTests(unittest.TestCase):

    def test_saved_outputs_verified_without_network_process_or_model(self):
        with patch('subprocess.Popen', side_effect=AssertionError('no process')), patch('socket.socket', side_effect=AssertionError('no network')):
            result = replay.verify()
        self.assertEqual(result['status'], 'saved_two_conversation_evidence_verified')
        self.assertEqual(result['model_calls'], 0)

    def test_changed_raw_hash_refused(self):
        saved = replay.load()
        saved['responses']['review_acme']['raw_output'] += ' '
        with self.assertRaises(ValidationError):
            replay.verify(saved)

    def test_rehashed_invented_value_still_refused(self):
        saved = replay.load()
        response = saved['responses']['review_orbit']
        answer = json.loads(response['raw_output'])
        answer['observations'][0]['current'] = '999'
        response['raw_output'] = json.dumps(answer)
        response['raw_sha256'] = hashlib.sha256(response['raw_output'].encode()).hexdigest()
        with self.assertRaises(ValidationError):
            replay.verify(saved)

    def test_changed_model_prompt_refused(self):
        saved = replay.load()
        saved['requests']['review_acme']['prompt'] += ' Hidden method.'
        with self.assertRaises(ValidationError):
            replay.verify(saved)

    def test_changed_query_or_rows_refused(self):
        for field in ('query_sha256', 'rows'):
            saved = replay.load()
            query = next((q for q in saved['retrieval_queries']['review_orbit'] if q['query'] == 'paths'))
            query[field] = '0' * 64 if field == 'query_sha256' else []
            with self.assertRaises(ValidationError):
                replay.verify(saved)

    def test_extra_private_fields_refused(self):
        saved = replay.load()
        saved['transport_receipt'] = {'thread_id': 'synthetic-forbidden-extra'}
        with self.assertRaises(ValidationError):
            replay.verify(saved)

    def test_context_has_no_precomputed_answer(self):
        saved = replay.load()
        for phase in ('review_acme', 'review_orbit'):
            context = saved['requests'][phase]['context']
            self.assertEqual(set(context), {'request', 'records', 'policies', 'provenance_paths'})
            self.assertNotIn('selection', context)
            self.assertNotIn('observations', context)

    def test_tampered_source_refused_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'judge'
            with patch.object(prepare, 'sha', return_value='0' * 64), self.assertRaises(ValueError):
                prepare.prepare(target)
            self.assertFalse(target.exists())

    def test_no_confirmation_never_connects(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch.object(database.Store, 'connect', side_effect=AssertionError('no database')):
            with self.assertRaises(ValidationError):
                database.run(Path(directory))

    def test_container_flow_uses_saved_answers_and_does_not_invoke_model(self):
        wire = Wire()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def connect(graph, guards, charge):
                return Store(SimpleNamespace(graph=wire, client=wire, close=wire.close), guards, charge)
            mechanism = dict(status='real_FalkorDB_topology_proof_passed', model_calls=0, cases=[None] * 16, calls=0)
            with patch.dict(os.environ, {'MML_JUDGE_CONFIRM_FIXTURE_SHA256': prepare.sha(replay.SAVED)}), patch.object(database.proof, 'run', return_value=mechanism), patch.object(database.Store, 'connect', side_effect=connect), patch('subprocess.Popen', side_effect=AssertionError('no model process')):
                result = database.run(root)
            self.assertEqual(result['model_calls'], 0)
            self.assertEqual(result['origins'], ['filing_c', 'filing_w'])
            self.assertEqual(result['actual_revenue_current'], '240')
            self.assertTrue((root / 'results.json').is_file())
if __name__ == '__main__':
    unittest.main()
