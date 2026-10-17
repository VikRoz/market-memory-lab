"""Current example, negative controls and agent orchestration; never a real model."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from agent import conversation, workflow
from agent.engine import Agent
from demo import replay
from market_memory_lab import fixtures as f
from market_memory_lab.core import evaluate, oracle_paths, request
from market_memory_lab.model import canonical, ValidationError
from market_memory_lab.store import Store
from market_memory_lab.test_core import selected
from market_memory_lab.test_store import Wire


class ExampleTests(unittest.TestCase):
    def test_saved_actual_outputs(self):
        checked = replay.verify()
        self.assertEqual(checked['model_calls'], 0)
        self.assertEqual(checked['database_calls'], 0)

    def test_instruction_does_not_teach_user_policy(self):
        for word in ('forecast', 'qualified', 'at least', 'minimum', 'two', 'min_origins', 'separate_forecasts'):
            self.assertNotIn(word, workflow.COMMON.lower())

    def test_memory_only_in_new_company_context(self):
        saved = replay.load()
        first = saved['requests']['review_acme']
        last = saved['requests']['review_orbit']
        self.assertEqual(first['context']['policies'], [])
        self.assertNotIn(f.MESSAGE, first['prompt'])
        prefix, body = last['prompt'].split('FALKORDB MEMORY AND EVIDENCE: ', 1)
        self.assertNotIn(f.MESSAGE, prefix)
        self.assertEqual(json.loads(body), last['context'])
        self.assertNotIn('selection', last['context'])
        self.assertNotIn('observations', last['context'])
        self.assertTrue(all(n['company'] == 'Orbit' for n in last['context']['records']))
        altered = copy.deepcopy(last['context']); altered['policies'] = []
        self.assertNotIn(f.MESSAGE, workflow.prompt_for('review_orbit', altered))

    def test_numeric_invention_rejected(self):
        saved = replay.load(); c = saved['requests']['review_orbit']['context']; m = saved['manifest_after']
        b = dict(selection=evaluate(m, c['request'], c['provenance_paths'], c['policies']))
        a = json.loads(saved['responses']['review_orbit']['raw_output'])
        a['observations'][0]['current'] = '999'
        with self.assertRaises(ValidationError): workflow.validate_answer(canonical(a), c, b)

    def test_missing_mandatory_context_blocks_model(self):
        with self.assertRaises(ValidationError):
            workflow.context_for(Mock(retrieve=Mock(return_value={'selection': {'answer_allowed': False}})), 'Acme')

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValidationError):
            conversation.feedback_answer('{"title":"A","title":"B"}')

    def test_changed_feedback_proposal_rejected(self):
        saved = replay.load(); a = json.loads(saved['responses']['propose_method']['raw_output'])
        inner = json.loads(a['interpretation']); inner['proposal']['min_origins'] = 3
        a['interpretation'] = canonical(inner)
        with self.assertRaises(ValidationError): conversation.feedback_answer(canonical(a))

    def test_agent_uses_same_then_fresh_thread_and_ack_after_commit(self):
        saved = replay.load(); reservations = []; sessions = []; displayed = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); wire = Wire()
            store = Store(type('Raw', (), {'graph':wire, 'client':wire, 'close':lambda s:None})(), root/'guards', lambda:None)

            class FakeSession:
                def __init__(self):
                    self.thread = 'chat-' + str(len(sessions)); self.model = 'offline-test'; self.cwd = root
                    self.phases = iter(('review_acme','propose_method') if not sessions else ('review_orbit',))
                    sessions.append(self)
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def preflight(self, model): return dict(status='effective_controls_verified')
                def call(self, method, params):
                    if method == 'config/read':
                        from agent.transport.controls import CONFIG
                        return {'config': CONFIG, 'layers': [], 'origins': {}}
                    if method == 'account/read': return {'account': {'type':'chatgpt'}}
                    return {'rateLimits': {'spendControlReached':False, 'rateLimitReachedType':None,
                        'credits':{'hasCredits':False,'unlimited':False},
                        'primary':{'usedPercent':0,'windowDurationMins':300,'resetsAt':1}}}
                def generate(self, prompt, reserve):
                    reserve(self.thread); phase = next(self.phases)
                    if phase == 'propose_method':
                        self.assert_prompt = prompt == saved['requests'][phase]['prompt']
                    return saved['responses'][phase]['raw_output'], {'thread_id':self.thread}

            def display(value):
                if isinstance(value,str):
                    manifest = store.retrieve(request('A','Orbit',f.END,f.END))['manifest']
                    self.assertTrue(any(n['kind']=='Policy' for n in manifest['nodes']))
                displayed.append(value)

            # A test double of the effective-control response, not a model or SDK call.
            from unittest.mock import patch
            with patch('agent.conversation.controls.verify_config', return_value={'status':'effective_controls_verified'}):
                agent = Agent(store, FakeSession, lambda phase,thread: reservations.append((phase,thread)), root/'run')
                result = agent.run(lambda:f.MESSAGE, display)
            self.assertEqual(result['status'], 'two_conversation_agent_completed')
            self.assertEqual(len(sessions),2)
            self.assertTrue(sessions[0].assert_prompt)
            self.assertEqual(reservations, [('review_acme','chat-0'),('propose_method','chat-0'),('review_orbit','chat-1')])
            self.assertEqual(len(displayed),3)
            for phase in replay.PHASES:
                actual = json.loads((root/'run'/(phase+'-raw.json')).read_text())
                self.assertEqual(actual['raw_sha256'], saved['responses'][phase]['raw_sha256'])
