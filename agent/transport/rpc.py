"""Bounded official Codex app-server adapter with tool rejection and safe errors."""
import json
import os
import re
import selectors
import subprocess
import tempfile
import time
from pathlib import Path

from . import _protocol as frozen
from .environment import environment
from .schema import require, OUTPUT_SCHEMA
from market_memory_lab.model import ValidationError
from . import controls, probe

TURN_LIMIT = 100  # Cumulative durable application reservations; not a target or native HTTP count.
ERRORS = {
    'failure_details_withheld':'unknown', 'child_failure_unclassified':'child',
    'child_deadline_exceeded':'child', 'child_output_limit':'child',
    'Codex_timeout':'protocol', 'Codex_closed_stdio':'protocol', 'Codex_exited_before_response':'protocol',
    'Codex_RPC_rejected':'rpc', 'Codex_RPC_failed':'rpc', 'invalid_Codex_RPC':'protocol',
    'unexpected_Codex_RPC_response':'protocol', 'Codex_error_notification':'protocol',
    'Codex_bounded_output_limit':'protocol', 'Codex_stderr_limit':'protocol',
    'Codex_turn_failed_no_retry':'turn', 'single_bounded_real_answer_required':'answer',
    'unexpected_Codex_interaction_request':'tool_policy', 'unexpected_Codex_tool_notification':'tool_policy',
    'unexpected_Codex_tool_event':'tool_policy', 'Codex_model_reroute_no_fallback':'tool_policy',
    'model_answer_budget':'answer', 'model_answer_not_json':'answer', 'model_answer_schema':'answer',
    'model_text_limits':'answer', 'model_narrative_numbers_links_or_controls':'answer',
    'model_lexical_check_failed':'answer', 'model_numeric_or_provenance_mismatch':'answer',
    'model_rule_reference_mismatch':'answer', 'generation_budget_or_freshness':'budget',
    'workflow_deadline':'budget', 'C1_DB_or_time_cap':'budget', 'complete_model_answer_required':'context',
    'ordinary_ChatGPT_login_required':'auth', 'account_usage_limit_reached':'quota',
    'paid_or_unknown_credits_stop':'quota', 'quota_window_exhausted':'quota',
    'quota_window_malformed':'quota', 'quota_no_known_window':'quota',
    'quota_unavailable_or_malformed':'quota', 'quota_flags_unknown_or_malformed':'quota'}
PHASES = {'unknown','startup','initialize','config/read','account/read','account/rateLimits/read',
          'model/list','thread/start','turn/start','turn/completed','answer_validation',
          'bootstrap','review_acme','propose_method','review_orbit','incomplete'}
CODEX_UNIT_ERRORS = {'contextWindowExceeded','sessionBudgetExceeded','usageLimitExceeded','rateLimitExceeded',
    'serverOverloaded','cyberPolicy','misalignmentPolicyViolation','internalServerError','unauthorized',
    'badRequest','threadRollbackFailed','sandboxError','other'}
CODEX_HTTP_ERRORS = {'httpConnectionFailed','responseStreamConnectionFailed','responseStreamDisconnected',
                     'responseTooManyFailedAttempts'}
ITEM_TYPES = {'userMessage','agentMessage','reasoning','hookPrompt','functionCallOutput','plan',
              'commandExecution','fileChange','mcpToolCall','dynamicToolCall','collabToolCall','collabAgentToolCall','subAgentActivity','webSearch',
              'imageView','enteredReviewMode','exitedReviewMode','contextCompaction','unknown'}
EVENT_METHODS = {'item/started','item/completed','error','model/rerouted',
    'item/commandExecution/requestApproval','item/commandExecution/outputDelta','item/fileChange/requestApproval',
    'item/fileChange/outputDelta','item/mcpToolCall/progress','item/tool/call','item/tool/requestUserInput',
    'mcpServer/elicitation/request','item/webSearch/progress','item/collabToolCall/progress',
    'item/collabAgentToolCall/progress','item/subAgentActivity/progress','unknown'}


def error_fields(error, phase='unknown'):
    if isinstance(error, SafeFailure): return dict(error.safe)
    code = str(error) if isinstance(error, ValidationError) else ''
    if code.startswith('Codex_RPC_rejected:') and code.split(':',1)[1] in PHASES:
        phase = code.split(':',1)[1]; code = 'Codex_RPC_rejected'
    if isinstance(error, subprocess.TimeoutExpired): code = 'child_deadline_exceeded'
    if code not in ERRORS: code = 'failure_details_withheld'
    return {'code':code,'category':ERRORS[code],'phase':phase if phase in PHASES else 'unknown'}


def checked_error(value):
    if not isinstance(value,dict) or not {'code','category','phase'} <= set(value): return False
    if set(value)-{'code','category','phase','codex_error_code','http_status','will_retry','item_type','event_method'}: return False
    code=value.get('code')
    if not isinstance(code,str) or code not in ERRORS or value.get('category') != ERRORS[code]: return False
    if not isinstance(value.get('phase'),str) or value['phase'] not in PHASES: return False
    variant=value.get('codex_error_code')
    if variant is not None and (not isinstance(variant,str) or variant not in CODEX_UNIT_ERRORS|CODEX_HTTP_ERRORS|{'activeTurnNotSteerable','unknown'}): return False
    status=value.get('http_status')
    for key,allowed in (('item_type',ITEM_TYPES),('event_method',EVENT_METHODS)):
        if key in value and (not isinstance(value[key],str) or value[key] not in allowed):return False
    return ('will_retry' not in value or type(value['will_retry']) is bool) and (
        status is None or (variant in CODEX_HTTP_ERRORS and type(status) is int and 0 <= status <= 65535))


class SafeFailure(ValidationError):
    def __init__(self, fields, legacy_reason=None):
        require(checked_error(fields), 'unsafe_error_fields_rejected')
        if legacy_reason is not None:
            base,sep,codes=legacy_reason.partition(';config_code=')
            parts=base.split(':',2)
            require(len(parts)==3 and parts[0]=='Codex_RPC_failed' and parts[1] in PHASES
                    and set(filter(None,parts[2].split(',')))<=probe.CATEGORY_NAMES
                    and (not sep or bool(codes) and set(codes.split(','))<=probe.CONFIG_ERROR_CODES),
                    'unsafe_error_fields_rejected')
        self.safe = dict(fields)
        self.legacy_reason = legacy_reason  # Only constructed below from fixed phase/category/code names.
        super().__init__(legacy_reason or fields['code'])


def turn_error(error):
    value=error.get('codexErrorInfo') if isinstance(error,dict) else None
    result={'codex_error_code':'unknown'}
    if isinstance(value,str) and value in CODEX_UNIT_ERRORS: result['codex_error_code']=value
    elif isinstance(value,dict) and len(value)==1:
        key=next(iter(value)); fields=value[key]
        if key in CODEX_HTTP_ERRORS and isinstance(fields,dict) and set(fields)<= {'httpStatusCode'}:
            status=fields.get('httpStatusCode')
            if status is None or type(status) is int and 0 <= status <= 65535:
                result={'codex_error_code':key}
                if status is not None: result['http_status']=status
        elif key=='activeTurnNotSteerable' and isinstance(fields,dict) and set(fields)=={'turnKind'} and fields['turnKind'] in ('review','compact'):
            result={'codex_error_code':key}
    return result  # Never inspect/forward message, additionalDetails, URL, headers or identifiers.


class Session(frozen.Session):
    def __init__(self):
        env = environment(os.environ)
        self.temp = tempfile.TemporaryDirectory(prefix="mml-c1-writer-")
        self.cwd = Path(self.temp.name)/"empty"; self.cwd.mkdir(mode=0o700)
        try:
            self.process = subprocess.Popen(controls.command(),cwd=self.cwd,env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        except BaseException:
            self.temp.cleanup(); raise
        self.selector = selectors.DefaultSelector()
        for stream in (self.process.stderr,self.process.stdout):
            os.set_blocking(stream.fileno(),False); self.selector.register(stream,selectors.EVENT_READ)
        self.buffer,self.total,self.sequence,self.events = b"",0,0,[]
        self.stderr = bytearray(); self.stderr_bytes = 0; self.phase = "startup"
        self.deadline = time.monotonic()+30

    def receive(self):
        while b"\n" not in self.buffer:
            require(time.monotonic()<self.deadline,"Codex_timeout")
            events = self.selector.select(.1)
            if not events:
                require(self.process.poll() is None,"Codex_exited_before_response")
            for key,_ in events:
                chunk = os.read(key.fd,4096)
                if key.fileobj is self.process.stderr:
                    if not chunk: self.selector.unregister(key.fileobj); continue
                    self.stderr_bytes += len(chunk)
                    require(self.stderr_bytes<=probe.STDERR_LIMIT,"Codex_stderr_limit")
                    self.stderr.extend(chunk)
                else:
                    require(bool(chunk),"Codex_closed_stdio")
                    self.total += len(chunk)
                    require(self.total<=262144,"Codex_bounded_output_limit")
                    self.buffer += chunk
        # Preserve typed metadata BEFORE frozen receive rejects every error notification.
        # willRetry is observational only: retain the existing first-failure behavior.
        value=json.loads(self.buffer.split(b'\n',1)[0])
        if isinstance(value,dict) and value.get('method')=='error' and 'id' not in value:
            params=value.get('params'); params=params if isinstance(params,dict) else {}
            fields={'code':'Codex_error_notification','category':'protocol',
                    'phase':getattr(self,'phase','unknown') if getattr(self,'phase','unknown') in PHASES else 'unknown',
                    'event_method':'error', **turn_error(params.get('error'))}
            if type(params.get('willRetry')) is bool: fields['will_retry']=params['willRetry']
            raise SafeFailure(fields)
        self.buffer=self.buffer.partition(b'\n')[2]
        require(isinstance(value,dict),'invalid_Codex_RPC')
        method=value.get('method')
        require(method is None or isinstance(method,str),'invalid_Codex_RPC')
        def reject(code, item=None):
            fields=error_fields(ValidationError(code),getattr(self,'phase','unknown'))
            fields['event_method']=method if method in EVENT_METHODS else 'unknown'
            fields['item_type']='unknown'
            if item is not None:
                kind=item.get('type') if isinstance(item,dict) else None
                fields['item_type']=kind if isinstance(kind,str) and kind in ITEM_TYPES else 'unknown'
            raise SafeFailure(fields)
        if 'method' in value and 'id' in value:reject('unexpected_Codex_interaction_request')
        if method=='model/rerouted':reject('Codex_model_reroute_no_fallback')
        if method in ('item/tool/call','item/tool/requestUserInput') or any(part in (method or '').lower() for part in
               ('commandexecution','filechange','mcp','dynamictool','websearch','collab','subagent','requestapproval')):
            reject('unexpected_Codex_tool_notification')
        if method in ('item/started','item/completed'):
            params=value.get('params');item=params.get('item') if isinstance(params,dict) else None
            if not isinstance(item,dict) or item.get('type') not in ('userMessage','agentMessage','reasoning'):
                reject('unexpected_Codex_tool_event',item if item is not None else {})
        return value  # Only echoed userMessage is newly allowed; no content is executed or collected as an answer.

    def call(self,method,params):
        self.phase = method
        try: return super().call(method,params)
        except Exception as error:
            # Drain only immediately available stderr; no retry or additional RPC.
            try:
                while self.stderr_bytes < probe.STDERR_LIMIT:
                    chunk=os.read(self.process.stderr.fileno(),min(4096,probe.STDERR_LIMIT-self.stderr_bytes))
                    if not chunk: break
                    self.stderr.extend(chunk); self.stderr_bytes += len(chunk)
            except (BlockingIOError,OSError): pass
            # Only fixed method names from our own caller and fixed category names survive.
            allowed={"initialize","config/read","account/read","account/rateLimits/read","model/list","thread/start","turn/start"}
            phase=method if method in allowed else "unknown"
            reason="Codex_RPC_failed:"+phase+":"+','.join(probe.categories(self.stderr))
            codes=probe.config_validation_codes(self.stderr)
            if codes: reason += ';config_code='+','.join(codes)
            fields=error_fields(error,phase)
            if fields['code']=='failure_details_withheld': fields={'code':'Codex_RPC_failed','category':'rpc','phase':phase}
            raise SafeFailure(fields,reason) from None

    def generate(self, prompt, reserve):
        require(len(prompt.encode()) <= 24000 and hasattr(self, "thread"), "bounded_prompt_and_preflight_required")
        reserve(self.thread)  # Durable UNKNOWN reservation BEFORE the only turn/start dispatch.
        self.phase = "turn/start"
        result = self.call("turn/start", {"threadId": self.thread, "environments": [], "serviceTierForTurn": "default",
            "input": [{"type": "text", "text": prompt, "text_elements": []}], "outputSchema": OUTPUT_SCHEMA})
        turn = result["turn"]["id"]
        self.phase = "turn/completed"
        messages, finished = [], False
        while not finished:
            event = self.events.pop(0) if self.events else self.receive()
            method, params = event.get("method"), event.get("params", {})
            if method == "item/completed" and params.get("turnId") == turn:
                item = params["item"]
                if item["type"] == "agentMessage":
                    messages.append(item["text"])
            if method == "turn/completed" and params.get("turn", {}).get("id") == turn:
                if params["turn"]["status"] != "completed" or params["turn"].get("error"):
                    raise SafeFailure({'code':'Codex_turn_failed_no_retry','category':'turn',
                        'phase':'turn/completed', **turn_error(params['turn'].get('error'))})
                finished = True
        require(len(messages) == 1 and isinstance(messages[0], str)
                and len(messages[0].encode()) <= 8192, "single_bounded_real_answer_required")
        return messages[0], {"transport": "official_Codex_app_server_stdio", "thread_id": self.thread,
                             "turn_id": turn, "model": self.model, "effective_controls": "verified_before_turn"}

    def close(self):
        try: super().close()
        finally:
            self.process.stderr.close(); self.stderr.clear()

    def preflight(self, model=None):
        effective = self.call("config/read", {"includeLayers": True, "cwd": str(self.cwd)})
        proof = controls.verify_config(effective["config"], effective.get("layers"), effective.get("origins"))
        account = self.call("account/read", {"refreshToken": False})
        require(account.get("account", {}).get("type") == "chatgpt", "ordinary_ChatGPT_login_required")
        limits = self.call("account/rateLimits/read", {})["rateLimits"]
        require(isinstance(limits, dict), "quota_unavailable_or_malformed")
        spend, reached = limits.get("spendControlReached"), limits.get("rateLimitReachedType")
        known_reached = {"rate_limit_reached", "workspace_owner_credits_depleted", "workspace_member_credits_depleted",
                         "workspace_owner_usage_limit_reached", "workspace_member_usage_limit_reached"}
        require(type(spend) is bool and "rateLimitReachedType" in limits
                and (reached is None or type(reached) is str and reached in known_reached),
                "quota_flags_unknown_or_malformed")
        require(spend is False and reached is None, "account_usage_limit_reached")
        credits = limits.get("credits")
        require(isinstance(credits, dict) and credits.get("hasCredits") is False
                and credits.get("unlimited") is False, "paid_or_unknown_credits_stop")
        windows = [limits[key] for key in ("primary", "secondary") if limits.get(key) is not None]
        require(bool(windows), "quota_no_known_window")
        for limit in windows:
            require(isinstance(limit, dict) and type(limit.get("usedPercent")) is int
                    and 0 <= limit["usedPercent"] < 2**31, "quota_window_malformed")
            for key in ("windowDurationMins", "resetsAt"):
                value = limit.get(key)
                require(value is None or type(value) is int and 0 <= value < 2**63, "quota_window_malformed")
            require(limit["usedPercent"] < 100, "quota_window_exhausted")
        catalog = self.call("model/list", {"limit": 100, "includeHidden": False})
        require(not catalog.get("nextCursor"), "model_catalog_exceeds_bounded_page")
        models = catalog["data"]
        if model is None:
            defaults = [m for m in models if m.get("isDefault") is True]
            require(len(defaults) == 1, "unique_subscription_default_model_required")
            model = defaults[0]["model"]
        require(isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", model)
                and any(m.get("model") == model for m in models), "selected_model_unavailable_no_fallback")
        self.model = model
        result = self.call("thread/start", controls.thread_params(model, self.cwd))
        self.thread = controls.verify_thread(result, model, self.cwd)
        self.events.clear()  # No turn has been dispatched; ignore only preflight lifecycle notifications.
        return {**proof, "model": model, "thread_id": self.thread, "account": "ChatGPT",
                "paid_credits_available": False, "generation_invocations": 0}
