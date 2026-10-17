"""Bounded fixed-category error classification; no diagnostic runner."""
import re

STDERR_LIMIT = 16384


CATEGORY_NAMES = {'argv_rejected','config_unknown_field','config_parse_or_type','config_load_failed',
                  'readonly_filesystem','permission_denied','sqlite_initialization','home_unavailable',
                  'missing_runtime_file','thread_or_process_limit','file_descriptor_limit',
                  'memory_allocation','rust_panic','proxy_or_network','unclassified_stderr_withheld',
                  'reserved_builtin_provider_override'}


RESERVED_OPENAI_MESSAGE = (
    'model_providers contains reserved built-in provider IDs: `openai`. '
    'Built-in providers cannot be overridden. Rename your custom provider '
    '(for example, `openai-custom`).')


CONFIG_ERROR_CODES = {'reserved_builtin_openai_provider','unknown_token_budget_configuration'}


def config_validation_codes(raw):
    """Match public pinned error templates; never emit raw messages or extracted values."""
    text = raw.decode('utf-8',errors='replace')
    codes = []
    if RESERVED_OPENAI_MESSAGE in text: codes.append('reserved_builtin_openai_provider')
    if re.search(r"unknown (?:configuration )?field [`'\"]token_budget(?:\.use_history_notes_extension)?[`'\"]",text):
        codes.append('unknown_token_budget_configuration')
    return codes


def categories(raw):
    text = raw.decode("utf-8", errors="replace").lower()
    patterns = {
        "argv_rejected": ("unexpected argument", "unrecognized subcommand", "unrecognized option"),
        "config_unknown_field": ("unknown field", "unknown configuration", "unknown feature"),
        "config_parse_or_type": ("error parsing -c", "toml parse", "invalid type:", "invalid value:"),
        "config_load_failed": ("failed to load config", "error loading config", "invalid configuration", "failed to read config file"),
        "readonly_filesystem": ("read-only file system",),
        "permission_denied": ("permission denied", "operation not permitted"),
        "sqlite_initialization": ("failed to initialize sqlite", "unable to open database", "database is locked"),
        "home_unavailable": ("error finding codex home",),
        "missing_runtime_file": ("no such file or directory",),
        "thread_or_process_limit": ("resource temporarily unavailable", "failed to spawn", "can't spawn worker thread"),
        "file_descriptor_limit": ("too many open files",),
        "memory_allocation": ("out of memory", "memory allocation"),
        "rust_panic": ("panicked at",),
        "proxy_or_network": ("proxy connection", "connect failed", "error sending request", "connection refused"),
    }
    found = sorted(key for key, words in patterns.items() if any(w in text for w in words))
    if 'reserved_builtin_openai_provider' in config_validation_codes(raw):
        found.append('reserved_builtin_provider_override')
    result = found or (["unclassified_stderr_withheld"] if raw else [])
    assert set(result) <= CATEGORY_NAMES
    return result
