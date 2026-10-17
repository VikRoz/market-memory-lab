"""Two conversations using one memory core, with durable turn reservations.

The caller supplies an already isolated, authenticated transport and a durable
budget implementation. This module never logs in, provisions access or retries.
"""
import hashlib
import json
import time
from pathlib import Path
from . import conversation, workflow
from market_memory_lab import fixtures
from market_memory_lab.core import need, request, sha
from market_memory_lab.store import durable


class Agent:
    def __init__(self, store, session_factory, reserve, directory, model=None):
        self.store, self.session_factory, self.reserve = store, session_factory, reserve
        self.directory, self.model = Path(directory), model
        self.directory.mkdir(exist_ok=False)

    def _generate(self, session, phase, prompt, context=None, bundle=None):
        durable(self.directory / (phase + '-request.json'), dict(prompt=prompt, context=context), True)
        raw, receipt = session.generate(prompt, lambda thread: self.reserve(phase, thread))
        durable(self.directory / (phase + '-raw.json'), dict(raw_output=raw,
                raw_sha256=hashlib.sha256(raw.encode()).hexdigest(), transport_receipt=receipt), True)
        if phase == 'propose_method':
            answer, proposal, ack = conversation.feedback_answer(raw)
            return answer, proposal, ack
        return workflow.validate_answer(raw, context, bundle)

    def _review(self, session, company):
        bundle, context = workflow.context_for(self.store, company)
        return self._generate(session, 'review_' + company.lower(),
                              workflow.prompt_for('review', context), context, bundle)

    def _manifest(self):
        return self.store.retrieve(request('A', 'Acme', workflow.utc(), workflow.utc()))['manifest']

    def run(self, read_comment, display):
        need(self.store.exists() == 0, 'fresh_example_graph_required')
        manifest = fixtures.live()
        self.store.ingest(manifest['nodes'], manifest['edges'])
        with self.session_factory() as first:
            proof = first.preflight(self.model)
            need(proof['status'] == 'effective_controls_verified', 'effective_controls_required')
            self.model = first.model
            first_thread = first.thread
            answer, _ = self._review(first, 'Acme')
            display(answer)
            manifest = self._manifest()
            raw = json.loads((self.directory / 'review_acme-raw.json').read_text())
            manifest['nodes'].append(fixtures.node('Episode', 'acme_episode', 'Acme', at=workflow.utc(),
                summary=json.loads(answer['interpretation'])['summary'], answer_sha256=raw['raw_sha256']))
            manifest['edges'].append(fixtures.edge('acme_episode', 'REVIEWED', 'acme_report', workflow.utc()))
            self.store.ingest(manifest['nodes'], manifest['edges'])
            comment = read_comment()
            need(comment == fixtures.MESSAGE, 'supported_synthetic_comment_required')
            first.deadline = time.monotonic() + 30
            conversation.followup_proof(first)  # Refresh controls, NOT a new thread.
            _, proposal, acknowledgement = self._generate(first, 'propose_method',
                conversation.FEEDBACK_FORMAT + '\nUSER COMMENT: ' + comment)
            manifest = self._manifest()
            event = fixtures.add_policy(manifest['nodes'], manifest['edges'], proposal, workflow.utc(), message=comment)
            self.store.ingest(manifest['nodes'], manifest['edges'])
            durable(self.directory / 'confirmed-event.json', dict(proposal=proposal,
                    approved_digest=sha(proposal), event=event), True)
            display(acknowledgement)  # Only after completed barrier and readback.
        with self.session_factory() as second:
            proof = second.preflight(self.model)
            need(proof['status'] == 'effective_controls_verified'
                 and second.thread != first_thread, 'fresh_conversation_required')
            answer, envelope = self._review(second, 'Orbit')
            display(answer)
        return dict(status='two_conversation_agent_completed', answer=answer, assessment=envelope)
