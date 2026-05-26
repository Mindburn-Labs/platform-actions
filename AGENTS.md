# Agent Operational Guidelines for platform-actions

Welcome to **platform-actions**. This is a first-class repository inside the Mindburn Labs 2026-2027 Polyrepo estate.

## Dev Commands
* Setup environment: `make setup`
* Run test suite: `make test`
* Run lint checks: `make lint`
* Build artifacts: `make build`

## Architectural Boundaries
* Maintain zero active static credentials inside the codebase.
* Rely on OIDC trust relationships for any third-party cloud brokers or identity enclaves.
* Keep definitions highly modular and strictly structured.
