# Project boundaries

This repository contains one current Market Memory Lab agent and one synthetic
Acme/comment/Orbit example. Keep all public text in English.

Preserve the immutable graph ingest barrier, identity conflict rejection,
idempotency, profile/task isolation, bounded context and both temporal cutoffs.
Missing or uncertain mandatory context blocks an analytical answer.

Default tests and replay must never contact a database or model. The Docker demo
requires explicit confirmation and creates its own isolated database. Never add
automatic login, paid API fallback, model retries, publication or submission.
Never include account files, private operational records, addresses or past runs.
