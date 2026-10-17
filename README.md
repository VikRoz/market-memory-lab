# Market Memory Lab

An agent that remembers a user's review method and applies it to new evidence
in a fresh conversation. FalkorDB stores confirmed policies, review episodes,
company observations and the relationships back to primary evidence.

## One example, two conversations

1. Ask the agent to review **Acme**. It produces a report from the supplied evidence.
2. In the same conversation, tell it: “For all my company reviews, keep forecasts
   separate from actual results. Use the confirmed label only when at least two
   distinct qualified primary evidence origins support the same actual figures
   for the same metric, period, unit and coverage.” The agent interprets that
   message; the application validates and commits the confirmed method to FalkorDB,
   then displays the agent's acknowledgement.
3. Start a new conversation and ask about **Orbit**. The agent receives the method
   only through retrieved graph memory, not the previous conversation. It reports
   actual revenue of **240**, keeps the **300** forecast separate and cites two
   primary evidence origins. A copied report does not count as another origin.

This example is an ordinary user/LLM dialogue with real model-generated replies.
The application retrieves bounded context from FalkorDB and validates the model's
structured output. The LLM does not write arbitrary Cypher or directly administer
the database. Company names, figures and documents are synthetic fixtures, not
live market data or investment advice.

## Try it without our accounts

Requirements: Git, Python 3.10+ and a running local Docker engine. Python 3.12 is
used inside the demo container. No model account, API key or host Python package
installation is required for this verification path.

```sh
git clone https://github.com/VikRoz/market-memory-lab.git
cd market-memory-lab
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
PYTHONDONTWRITEBYTECODE=1 python3 -m demo replay
PYTHONDONTWRITEBYTECODE=1 python3 -m demo prepare --output demo-run
PYTHONDONTWRITEBYTECODE=1 python3 -m demo verify --output demo-run
sh demo-run/run.sh --confirm-synthetic-rules
```

The last command downloads `falkordb/falkordb-server:v4.20.6` and
`python:3.12.14-slim-bookworm`, builds a client using hash-pinned dependencies,
and creates a fresh database. It executes real FalkorDB queries using the
preserved actual model replies in `demo/saved_run.json`: **no new LLM generation**.
Replay alone is offline and does not prove database execution; the Docker command
does. It is deliberately labelled as saved-response verification, not a live chat.

Expected final status:
`real_FalkorDB_two_conversation_verification_passed`, `model_calls: 0`,
`mechanism_cases: 16`, `conversation_steps: 3`.

Results are in `demo-run/state/summary.json`, `results.json`,
`conversation-queries.json` and `proof/`. Runtime container identities, image
digests and exit state are in `demo-run/attempt/`.

Stop only the database created by this exact attempt, preserving its volume and
all results:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m demo stop --output demo-run --confirm-stop-own-db
```

The guarded stop checks the recorded container ID, image, mounts and network.
It does not stop unrelated containers or delete results. Do not use global Docker
prune or `down --volumes`. A failed attempt is retained and never silently retried;
diagnose it before explicitly preparing a different output directory.

The database has no external network or published port. The read-only client
shares only that new database's network namespace. No account files, Docker
socket or existing project directories are mounted. Both containers have CPU,
memory and process limits. Dependencies require internet during image preparation;
the database verification itself has no model/network backend access.

## Why a graph database?

FalkorDB runs fixed, parameterized Cypher traversals over `ORIGIN` relationships
and selects confirmed user policies through `CONFIRMED_BY` and `FROM_EPISODE`.
Two reports can point to one original filing: counting reports would incorrectly
double-count evidence. The integration check keeps node payloads identical and
changes one origin relationship; the result switches from insufficient origins
to the policy being met. It also checks copied sources, cycles, missing lineage,
profile/task boundaries, conflicting figures and both temporal cutoffs.

The Python core validates the graph and evaluates supported policies; the database
supplies persisted records and their actual relationships. Neither a model guess
nor an in-memory reference traversal substitutes for a failed database read.
Incomplete mandatory context blocks analysis. Identical ingest is idempotent;
changing content under an existing identity is rejected. Confirmed events are
immutable. Uncertain writes leave a durable pending barrier and block further use.
The write protocol is single-host/shared-journal, not a distributed transaction.

## Agent and model integration

This is a demo-version agent with a model-independent memory core, not a general
natural-language policy engine. The current supported policy family is qualified
evidence origins with actual/forecast separation and task/company scope. Company
names and numbers are data; policy extraction is schema-validated and only becomes
persistent after the explicit user comment is validated.

`agent.engine.Agent` implements the live flow: report and feedback on the same
native thread, then another company's report on a fresh thread. It accepts a
store, session factory and an operator-owned durable budget reservation callback.
`agent.transport.rpc.Session` is the verified official Codex app-server adapter
for an existing ChatGPT subscription session, pinned to Codex **0.153.0**.
The adapter never logs in, accepts paid API credentials, enables tools or falls
back to a different provider. A reservation must be persisted before each model
turn; unknown outcomes count against the budget. Native transport retries are not
additional application turns.

A live deployment additionally needs an already authenticated, isolated operator
container, its configured scoped egress proxy and a shared durable call ledger.
The default commands above never use those operator resources. Do not run the
model adapter with your personal filesystem exposed. The live agent code is
included; the account-free reproducibility path reuses the recorded actual replies.

Claude and local open-source models could be connected by implementing the same
session interface; those integrations have **not** been tested. No direct API
service, universal provider support or automatic policy learning is claimed.

## Potential uses

- Research assistants can remember an analyst's evidence standards and apply them
  when reviewing a different company or a new reporting period.
- Support agents can retain a customer's confirmed preferences across separate
  conversations while keeping customer profiles isolated.
- Internal knowledge assistants can connect decisions to their original documents,
  avoiding double-counted copies and preserving the reasoning's provenance.

## Code map and documentation

- `market_memory_lab/`: strict graph schema, immutable ingest, retrieval, policy
  evaluation and graph-mechanism integration checks.
- `agent/`: conversation orchestration, prompts, grounded-output validation and
  the existing-subscription transport.
- `demo/`: the current synthetic example, saved-response verification, source-only
  Docker preparation and exact-own-database stop.
- `tests/`: offline negative controls and conversation/packaging checks.

FalkorDB references: [MATCH](https://docs.falkordb.com/cypher/match),
[Cypher](https://docs.falkordb.com/cypher/),
[Python client](https://github.com/FalkorDB/falkordb-py).
Codex protocol reference: [official pinned structured-request example](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/tui/src/temporary_structured_request.rs).
