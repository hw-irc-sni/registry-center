# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A2A-T AgentCard Registry Center — a Python service providing centralized CRUD, semantic search, and approval
workflows for **AgentCards** (structured descriptions of AI agents) across multi-vendor deployments. Delivered as
source code only; hosts must supply their own auth/authz, DB, and encryption infrastructure. Single-instance,
internal-network deployment only (not distributed, not exposed to the public internet).

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Interactive config wizard (certs, storage, security toggles) — writes etc/conf/*.conf
python -m agent_registry.init

# Run the service (HTTPS on 127.0.0.1:5000 by default)
python -m agent_registry.start

# Run tests (CI's unit-test step: PYTHONPATH=. pytest tests/ -v --tb=short; CI additionally runs a container
# smoke test, an OAuth-over-HTTPS smoke test, and ESLint/tests/build for registry-center-web/)
PYTHONPATH=. pytest tests/ -v --tb=short

# Run a single test file / test
PYTHONPATH=. pytest tests/test_register.py -v
PYTHONPATH=. pytest tests/test_register.py::TestName::test_case -v

# CLI (Linux only — talks to the running server over a Unix Domain Socket)
python -m agent_registry.cli
```

There is no lint/format tooling configured in this repo (no pyproject.toml, flake8, or pylintrc) — don't invent one.

## Architecture

### Request flow

```
REST client --HTTPS/TLS--> Security Middleware --> RegistryCore --> Storage Backend (file / sqlite / PostgreSQL /
                                                                       MySQL / GaussDB)
                            (rate limit, sig                     --> Milvus (optional, semantic search)
                             verify, owner isolation,             --> LLM provider (semantic search)
                             content safety)
```

- `agent_registry/server.py` — FastAPI app, route handlers, request lifecycle glue.
- `agent_registry/middleware.py` — security middleware chain (rate limiting, signature verification, owner
  isolation, content safety) applied before requests reach core logic.
- `agent_registry/core.py` — `RegistryCore`: the CRUD/approval/semantic-search business logic, storage-agnostic.
- `agent_registry/registry_instance.py` — process-wide singleton accessor (`get_registry()`) that wires
  `RegistryCore` to the configured persistence backend. Read config here before assuming a storage mode.
- `agent_registry/config.py` — central place for config keys / constants (limits, flow-control keys, feature
  flags) sourced from `common/util/app_config.py` readers over `etc/conf/*`.
- `agent_registry/internal/` — the CLI-facing internal service: a separate transport (UDS on Linux at
  `run/registry-center/internal.sock`, TCP `127.0.0.1:1108` on Windows for dev) exposing agent/tag approval and
  admin operations distinct from the public REST API. `internal/handlers/` implement each internal action;
  `internal/protocols/` define the request/response wire format between CLI and server.
- `agent_registry/signature/` — JWS AgentCard signature verification (RS256/ES256): `jwk_fetcher.py` (static or
  dynamic `jku` key lookup), `public_key_manager.py`, `agent_card_signature_validator.py`.
- `agent_registry/persistence/` — `base.py` defines `StorageBackend` (ABC) and `AgentRecord`; `file_storage.py`
  plus the SQL backends (`sql_backend.py` shared logic; `sqlite`, `postgresql`, `mysql`, `gaussdb` variants whose
  dialect SQL lives in `sql_queries.py`) implement it. Persistence mode is selected via `persistence.mode` in
  `etc/conf/persistence.conf` (overridable with `REGISTRY_*` env vars), read once into `PERSISTENCE_MODE` in
  `config.py`. With `use_vectordb=true`, `RegistryCore` bypasses `StorageBackend` entirely and uses Milvus only
  (`core.py` branches on this in most methods).
- `agent_registry/integration/` — a *second* FastAPI app on its own port (`listener.py`) for third-party systems:
  pluggable authN in `authn.py` (static bearer / OAuth2 JWT / introspection / mTLS / custom), four caller roles,
  per-credential rate limits, IAM token-acquisition proxy, audit mirror (`audit_sink.py`). Secrets
  (`INTEGRATION_FINGERPRINT_KEY`, `INTEGRATION_TOKEN_HMAC_KEY`) come from env; callers are listed in
  `etc/conf/integration_credentials.conf`.
- `agent_registry/broadcast/` — transactional outbox (SQL/file/memory) + webhook dispatcher for subscriptions.
- `agent_registry/health/` — heartbeat store, sweeper and SSE stream.
- `agent_registry/knowledge_graph_api/` and `agent_to_graph/` — Neo4j CRUD API and the `/sync` registry→graph diff.
- `agent_registry/cli/` — the standalone CLI entry point (`__main__.py`) and command tree (`commands/agent.py`,
  `commands/tag.py`), communicating with the running server via `uds_client.py`.

### Extension points (registry pattern, not inheritance-per-feature)

Two independent config-driven extension mechanisms exist so users customize behavior **without editing core
code**:

1. **Custom handlers** (`common/custom/custom_handle.py` + `interface_type.py`): `HandlerRegistry.register(
   InterfaceType.X, MyHandlerClass)` overrides one of `DECRYPT`, `AUDIT`, `AUTHENTICATE`, `INSERT`, `QUERY`,
   `UPDATE`, `GET`, `RETRIEVE`, `DEREGISTER`. `HandlerRegistry.get_handler(type)` falls back to a built-in default
   handler if nothing is registered. Registration must happen at process startup (in `common/custom/__init__.py`
   or the app entry point) — anything registered after a handler has already been fetched won't take effect for
   in-flight code paths.
2. **LLM providers** (`common/llm/`): a single concrete class, `GenericLLM` (`provider/generic_llm.py`), backs
   every capability (`chat`, `embed`, `rerank`). Model definitions are loaded from `etc/config/models.yaml` (or the
   file named by `LLM_CONFIG_FILE`; untracked, copy `models.yaml.example`) by `config/model_sources.py`; secrets are
   referenced by environment-variable name (`*_env`, read from the process env then `.env`). Request/response
   shape is fixed per *profile* (`openai`, `openai_compatible`, `aoc_signed`, registered via `register_profile`);
   the YAML selects a profile and sets url/model/auth/timeout, and rejects free-form `body`/`response`/`headers`.
   A vendor with a different wire format therefore needs a new profile in Python. `get_llm_instance()/
   get_embed_instance()/get_rerank_instance()` are cached-singleton factories keyed by capability name.
   Non-standard auth schemes are functions in `common/llm/provider/auth_strategies.py` registered into the
   `AUTH_STRATEGIES` dict (keyed by the `auth.type` string).

### Key invariants

- An AgentCard's unique identity is `(name, provider.organization)` — duplicates are rejected.
- Agents start `registered` and move to `published` only through the (optional) approval workflow, driven via the
  CLI's `agent approval` command — approval is off by default (`agent_approval_enabled`).
- Owner isolation binds an agent to the registering TLS client certificate CN; `owner.validation.mode` can be
  `strict` or `relaxed`.
- Content safety (prompt-injection keyword filtering, high-risk skill blacklist) runs at registration time — see
  `agent_registry/model/blacklist_config.py` and `tests/test_blacklist_performance.py`.
- Windows is dev/debug only (uses TCP for the internal service instead of UDS); production must be Linux.

### Things that bite

- The default `AUTHENTICATE` handler allows every caller; authorization for approval/review, subscriptions and
  ops endpoints exists only in token mode or via a host-registered handler. See
  `research/Registry Center Code Analysis.md` for the full review and known defects.
- The unique-identity rule is enforced by an in-process lock plus a DB index that includes `owner` (none on SQLite),
  not by a `(name, organization)` unique constraint — single-instance only.
- Docker: the container runs as UID 10001, so mounted `data/` and `log/` must be owned by it; the service will not
  start without `INTEGRATION_*` secrets in `.env`, an enabled entry in `integration_credentials.conf`, and
  `etc/config/models.yaml`.

## Docs

Full guides live under `docs/en/` (Chinese under `docs/zh/`): User Guide, **Development Guide** (extension
scenarios, full config-item tables, error codes, FAQ — read this before implementing custom handlers/LLMs),
API Reference, Security Guide, GCP Deployment Guide. `etc/config/README_en.md` documents the LLM config file
schema in detail.
