"""Feedback is interpreted in the report conversation; acknowledgement follows commit."""
import json
from .transport import controls
from . import workflow as w
from market_memory_lab import fixtures as f
from market_memory_lab.core import need, parse_proposal

FEEDBACK_FORMAT=(
    'Respond to the user comment in this same conversation. Return only the required JSON object in English, without tools. '
    'Convert the comment to a supported typed proposal; observations=[] and rule_references=[]. '
    'interpretation is a JSON-encoded object with exactly proposal and acknowledgement. '
    'proposal has exactly schema_version (integer 2), family (qualified_origins), scope (task or company), '
    'company (empty for task scope), min_origins (integer 1 through 3), separate_forecasts (boolean). '
    'Derive values only from the user comment, without defaults. acknowledgement is your brief natural reply accepting '
    'the requested review method for future company reviews. The application will display this reply only after it '
    'validates and commits the proposal. Do not claim a database write or verification you performed. '
    'Do not repeat the report or include new numerical facts.'
)


def followup_proof(session):
    effective=session.call('config/read',{'includeLayers':True,'cwd':str(session.cwd)})
    proof=controls.verify_config(effective['config'],effective.get('layers'),effective.get('origins'))
    account=session.call('account/read',{'refreshToken':False})
    need(account.get('account',{}).get('type')=='chatgpt','conversation_subscription_required')
    limits=session.call('account/rateLimits/read',{})['rateLimits']
    need(isinstance(limits,dict) and limits.get('spendControlReached') is False
         and 'rateLimitReachedType' in limits and limits['rateLimitReachedType'] is None,'conversation_quota')
    credits=limits.get('credits')
    need(isinstance(credits,dict) and credits.get('hasCredits') is False and credits.get('unlimited') is False,'conversation_no_paid_credits')
    windows=[limits[k] for k in ('primary','secondary') if limits.get(k) is not None]
    need(bool(windows),'conversation_known_quota_required')
    for window in windows:
        need(isinstance(window,dict) and type(window.get('usedPercent')) is int
             and 0<=window['usedPercent']<100,'conversation_quota_exhausted')
        for key in ('windowDurationMins','resetsAt'):
            value=window.get(key);need(value is None or type(value) is int and 0<=value<2**63,'conversation_quota_schema')
    return dict(proof,model=session.model,thread_id=session.thread,account='ChatGPT',paid_credits_available=False,
                generation_invocations=1,continued_existing_thread=True)


def feedback_answer(raw):
    from market_memory_lab.model import _unique_object
    answer=json.loads(raw,object_pairs_hook=_unique_object)
    need(isinstance(answer,dict) and set(answer)=={'title','interpretation','observations','rule_references'}
         and w.english_text(answer['title']) and 0<len(answer['title'])<=100
         and answer['observations']==answer['rule_references']==[],'conversation_feedback_schema')
    envelope=json.loads(answer['interpretation'],object_pairs_hook=_unique_object)
    need(isinstance(envelope,dict) and set(envelope)=={'proposal','acknowledgement'},'conversation_feedback_envelope')
    proposal=parse_proposal(json.dumps(envelope['proposal']))
    acknowledgement=envelope['acknowledgement']
    need(proposal==f.PROPOSAL and w.english_text(acknowledgement) and 0<len(acknowledgement)<=400,'conversation_exact_user_method')
    return answer,proposal,acknowledgement
