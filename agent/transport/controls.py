"""Pinned official controls for the verified subscription adapter."""
import json
from pathlib import Path
from .schema import require

VERSION = "0.153.0"


SOURCE = "https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/tui/src/temporary_structured_request.rs"


FLAGS = ("apps", "code_mode", "code_mode_only", "context_management", "current_time_reminder",
         "deferred_executor", "enable_fanout", "goals", "hooks", "image_generation", "memories",
         "multi_agent", "multi_agent_v2", "plugins", "request_permissions_tool", "shell_snapshot",
         "shell_tool", "standalone_web_search", "token_budget", "tool_suggest", "unified_exec", "view_image")


def get(config, dotted):
    value = config
    for name in dotted.split("."):
        if not isinstance(value, dict) or name not in value:
            return None
        value = value[name]
    return value


def verify_thread(result, model, cwd):
    require(result.get("model") == model and result.get("modelProvider") == "openai"
            and result.get("approvalPolicy") == "never" and result.get("cwd") == str(cwd)
            and result.get("runtimeWorkspaceRoots") == []
            and result.get("sandbox", {}).get("type") == "readOnly", "effective_thread_policy_mismatch")
    thread = result.get("thread", {})
    require(isinstance(thread.get("id"), str) and thread["id"] and thread.get("ephemeral") is True,
            "ephemeral_thread_not_confirmed")
    require(thread.get("cliVersion") == VERSION, "thread_Codex_version_mismatch")
    return thread["id"]


def schema_contract(directory):
    root = Path(directory)
    for name, required in {
        "ThreadStartParams": {"config", "environments", "dynamicTools", "selectedCapabilityRoots",
                              "runtimeWorkspaceRoots", "ephemeral", "allowProviderModelFallback"},
        "ThreadStartResponse": {"thread", "model", "modelProvider", "sandbox", "approvalPolicy", "runtimeWorkspaceRoots"},
        "TurnStartParams": {"input", "threadId", "outputSchema", "environments"},
    }.items():
        value = json.loads((root / "v2" / (name + ".json")).read_text())
        require(required <= set(value["properties"]), "installed_Codex_contract_missing:" + name)
    return {"status": "pinned_Codex_schema_contract_passed", "model_turns": 0}


def caps(directory=Path("/sys/fs/cgroup")):
    root = Path(directory)
    memory = int((root / "memory.max").read_text())
    pids = int((root / "pids.max").read_text())
    quota, period = map(int, (root / "cpu.max").read_text().split())
    require(0 < memory <= 256 * 1024 * 1024 and 0 < pids <= 64 and 0 < quota / period <= .5,
            "container_hard_caps_not_enforced")
    return {"memory_bytes": memory, "pids": pids, "CPU_quota": quota, "CPU_period": period}

CONFIG = {'features.apps': False, 'features.code_mode': False, 'features.code_mode_only': False, 'features.context_management': False, 'features.current_time_reminder': False, 'features.deferred_executor': False, 'features.enable_fanout': False, 'features.goals': False, 'features.hooks': False, 'features.image_generation': False, 'features.memories': False, 'features.multi_agent': False, 'features.multi_agent_v2': False, 'features.plugins': False, 'features.request_permissions_tool': False, 'features.shell_snapshot': False, 'features.shell_tool': False, 'features.standalone_web_search': False, 'features.token_budget': False, 'features.tool_suggest': False, 'features.unified_exec': False, 'features.view_image': False, 'orchestrator.skills.enabled': False, 'skills.include_instructions': False, 'tools.experimental_request_user_input.enabled': False, 'tools.update_plan.enabled': False, 'web_search': 'disabled', 'project_doc_max_bytes': 0, 'history.persistence': 'none', 'model_provider': 'openai', 'approval_policy': 'never'}

def thread_params(model, cwd):
    return {"model": model, "modelProvider": "openai", "allowProviderModelFallback": False,
            "cwd": str(cwd), "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": True,
            "serviceTier": "default",
            "runtimeWorkspaceRoots": [], "environments": [], "dynamicTools": [],
            "selectedCapabilityRoots": [], "developerInstructions": "",
            "config": {**CONFIG, "mcp_servers": {}}}
BUILTIN_REQUEST_RETRIES = 4


BUILTIN_STREAM_RETRIES = 5


def command():
    args = ["codex", "app-server", "--strict-config", "--listen", "stdio://"]
    for key, value in CONFIG.items(): args.extend(["-c", key+"="+json.dumps(value,ensure_ascii=False)])
    return args


def verify_config(config, layers=None, origins=None):
    require(isinstance(config, dict), "effective_config_missing")
    # ConfigRead's typed ToolsV2 projection omits some TOML controls in0.153.0.
    # For omitted values require BOTH the winning origin and actual session layer;
    # a requested argv by itself is never accepted as effective configuration.
    layers, origins = layers or [], origins or {}
    sessions = [r["config"] for r in layers if not r.get("disabledReason")
                and r["name"]["type"] == "sessionFlags"]
    for row in layers:
        if not row.get("disabledReason"):
            require(row["name"]["type"] in ("packagedDefaults", "sessionFlags") or not row["config"],
                    "unexpected_user_project_or_managed_config")
    for key, expected in CONFIG.items():
        actual = get(config, key)
        if actual is None:
            require(origins.get(key, {}).get("name", {}).get("type") == "sessionFlags"
                    and len(sessions) == 1, "effective_control_origin_missing:" + key)
            actual = get(sessions[0], key)
        require(actual == expected and type(actual) is type(expected), "effective_control_missing_or_changed:" + key)
    # Fresh image + fresh Codex home must not acquire any third-party tools.
    servers = config.get("mcp_servers", {})
    require(isinstance(servers, dict) and not servers, "unexpected_MCP_configuration")
    require(not config.get("plugins") and not config.get("developer_instructions")
            and not config.get("instructions"), "unexpected_private_instructions_or_plugins")
    providers = config.get('model_providers', {})
    require(isinstance(providers, dict) and not providers and not config.get('openai_base_url'),
            'custom_provider_not_allowed')
    return {"status": "effective_controls_verified", "disabled_features": list(FLAGS),
            "MCP_servers": 0, "environment_access": False, "dynamic_tools": 0,
            "proof": "pinned_official_controls + effective_config + thread_response + isolated_container"}
