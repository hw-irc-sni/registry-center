# Registry Center vs. Catalyst-Fabric — Comparative Analysis

**Date:** 2026-07-02
**Repos compared:** `registry-center` (this repo, aka "OpenAN" in catalyst-fabric's code) vs. `catalyst-fabric` (`backend/src/agent_fabric`, Huawei's "Agent Fabric" for the Catalyst 2026 telecom initiative)

## Scope and methodology

This document was produced by two independent code-research passes (one per repo) followed by a synthesis and direct verification step. A few scoping decisions were made:

- **Domain scope**: the comparison focuses on the overlapping concern — agent registration, identity, storage, discovery, trust/signatures, and approval lifecycle. catalyst-fabric is a much broader control-plane platform (PKI/CA, policy, a data-plane gateway, a Web UI, LLM/tool/resource catalogs, observability). Those areas are described for context but not compared feature-for-feature, since registry-center has no ambition to cover them (it is explicitly documented as "a functional module only, not a complete system").
- **Integration seam included**: catalyst-fabric's backend contains a client (`external_registry.py`, `upstream_cache.py`, `resync.py`) that treats "OpenAN" as an upstream registry. That relationship is covered in detail below, including a real contract mismatch this analysis surfaced.
- **Depth**: architecture-level and contract-level, with file/line citations for traceability, not a full line-by-line code diff.
- **Caveat on catalyst-fabric findings**: all citations for catalyst-fabric come from a single research pass over a large, actively-changing repo (100+ issue-scoped test folders). Line numbers are a snapshot as of this date and will drift. A few points are explicitly hedged below (marked ⚠) because they weren't fully confirmed.

---

## 1. What each project is

| | registry-center ("OpenAN") | catalyst-fabric ("Agent Fabric") |
|---|---|---|
| Positioning | A standalone, source-delivered **AgentCard registry module**: CRUD, semantic search, approval workflow. Explicitly "not a complete system" — auth, DB, encryption, key management are the host's responsibility. | A **control-plane platform**: agent registry + trust/PKI + policy + routing config generation for a separate data-plane component (`agentgateway`) + a Web UI + LLM/tool/resource catalogs + observability. |
| Deployment unit | Single Python process, single instance, internal-network only, not distributed. | Multi-container Docker Compose stack: FastAPI backend, Postgres, Traefik (mTLS reverse proxy), `agentgateway`, MCP tooling, an observability stack (Prometheus/Grafana/Jaeger/Loki/Phoenix). |
| Primary consumer | Any A2A-T-compliant client talking REST directly to the registry. | Downstream agents and operators, via Fabric's own API — which itself *may* proxy through an external registry like this one. |
| Relationship between the two | N/A | Fabric's backend can be configured with `external_registry_url` (env var `REGISTRY_BASE_URL`, unprefixed — see §7) pointing at an OpenAN-style registry, and layers caching, filtering, and Fabric-local enrichment on top of it. **As of this analysis, the wire contract it expects does not match what this repo serves — see §6.** |

---

## 2. Data model & identity

Both systems key an agent on **`(name, provider.organization)`** — this is the one invariant that is identical across both codebases, and it's the join key catalyst-fabric explicitly uses to reconcile local Fabric data with upstream OpenAN listings (`registry.py:549-563`, docstring: *"OpenAN keys agents by this pair, so we use it to merge upstream listings with locally-held Fabric data"*).

Beyond that, the two models diverge:

- **registry-center**: the `AgentCard` itself (imported from `a2a-sdk`, a protobuf-generated type — `agent_registry/server.py:33`) *is* the stored object. Status (`registered`/`published`) is not a card field — it's registry-center-only metadata (`AgentRecord.status`, `persistence/base.py:26-33`) that never appears in the wire response to a GET/list call. There is exactly one identifier: `(name, organization)`.
- **catalyst-fabric**: the local registry entry is an *aggregate root*, not a bare card. Its `agents` table (`orm/agent.py:17-71`) stores the raw `AgentCard` verbatim in a JSONB column, plus a parallel `fabric_profile` JSONB column (Fabric-only taxonomy: `agentKind`, `functionalScenario`, `deployment`) that has no A2A-spec equivalent, plus relations to `goals`, `objectives`, `agent_reputation`, `agent_certificates`, and LLM/resource/tool bindings. It also carries **three co-existing identifiers** per agent: the `(name, organization)` pair (matches OpenAN), a server-generated `agent_id` UUID (primary key), and a Fabric-minted `kid` (`_make_kid`, `registry.py:100-108`) used as the TLS cert CN/SAN and JWS `kid` — none of which registry-center has any concept of, and none of which an external OpenAN-only client could predict.
- For agents that exist upstream in OpenAN but were never locally registered in Fabric, Fabric **synthesizes** a stable `agent_id` via UUIDv5 over `name@organization` (`_synthesize_agent_id`, `api/agents.py:755-757`) — a direct compensation for the fact that OpenAN's API surface doesn't hand out an opaque agent identifier at all.

**Uniqueness enforcement differs in strictness.**
- registry-center rejects a duplicate `(name, organization)` registration outright with `409 CONFLICT` (`_check_duplicate_agent`, `server.py:436-444`).
- catalyst-fabric instead UPSERTs on the same key — re-registering an existing `(name, org)` updates the card in place and preserves `kid`/`agent_id`/`registered_at` (`registry.py:423-479`), falling back to the `409`-equivalent only on a genuine race via `IntegrityError`. This is a deliberate idempotency choice in Fabric, likely driven by its sync-from-upstream use case (§6), where re-processing the same upstream snapshot repeatedly must not fail.

---

## 3. API surface & lifecycle

**registry-center** exposes a small, fixed REST surface under `/rest/v1/registry-center/`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/agent-cards` | Register (batch body) |
| GET | `/agent-cards?name=&organization=` | List — exact-match filters only, **no pagination, no free-text/domain filter**; published agents only |
| GET | `/agent-cards/{organization}/{name}` | Get by key |
| PUT | `/agent-cards/{organization}/{name}` | Full replace |
| DELETE | `/agent-cards/{organization}/{name}` | Deregister |
| POST | `/agent-cards/semantic-query?top_n=` | LLM-judged fuzzy retrieve |
| GET | `/keys` | Registry's own JWKS (only if it self-signs, §4) |

Notably, `docs_url`/`redoc_url`/`openapi_url` are all disabled (`server.py:227-234`) — no live OpenAPI introspection.

**catalyst-fabric's** own external-facing surface (`/agents`, `/a2a/{agent_key}/*`) is considerably richer: server-side filtering by `domain`/`layer`/`agentKind`/`functionalScenario`/`capabilityTags`/free-text query, `limit`/`offset` pagination, an `AgentDiscoveryFilter` model (`models.py:255-262`), plus spec-compliant `.well-known/agent-card.json` / `extendedAgentCard` endpoints that fold Fabric enrichment into `capabilities.extensions[]` under versioned URIs rather than inventing new top-level fields (issue #92). This is a materially larger, filterable API than registry-center's exact-match-only list endpoint.

**Lifecycle/approval**:
- registry-center has a genuine two-state workflow — `registered → published`, gated by `agent_approval_enabled` (default off), driven exclusively through the internal UDS channel and CLI (`agent_registry.cli agent approval`), never through the public REST API (§5 of the research notes; `internal/handlers/approval_handler.py:29-145`).
- catalyst-fabric has **no equivalent**. Its only lifecycle-shaped field, `operational_state`, is a binary `ACTIVE`/`INACTIVE` reachability flag, not an approval gate — agents go straight to `ACTIVE` on registration, with no reviewer role, no pending state, and no human-in-the-loop step. (⚠ Fabric's sync code has a stale docstring reference to marking drifted agents `INACTIVE`, but the live `_apply_external_snapshot` implementation actually hard-deletes them — a docs/code drift note on Fabric's side, not a functional claim.)

---

## 4. Trust model — this is the largest structural divergence

- **registry-center** is fundamentally a *verifier and optional co-signer*. It verifies externally-supplied JWS signatures (RS256/ES256, via `a2a-sdk`) against either a static per-organization key file (`etc/sign_verify/jwks/{org}/{name}.json`) or a dynamic `jku` URL fetch (HTTPS-only, 1MB cap). If `registry.sign.enabled=true`, it *also* appends its own signature as a notary stamp on every register/update (`agent_card_signer.py`) and publishes its public key via `/keys`. It does **not** issue certificates or keys to third parties — no CA function.
- **catalyst-fabric operates a full two-tier CA** (`ca/` — Root + Intermediate, auto-bootstrapped, persisted in a Docker volume). It issues mTLS leaf certificates from agent-submitted CSRs (overriding the requested CN with a Fabric-assigned `kid`-derived identity), publishes a JWKS with RFC 7638 thumbprint `kid`s, appends its own JWS attestation to every card it stores (replacing prior Fabric signatures on update, retroactively backfilling any card synced in from OpenAN that lacks one), and serves a CRL. It can *also* verify provider-supplied signatures with a strict, gated (`AGENT_FABRIC_REQUIRE_PROVIDER_SIGNATURE`, default off) manual X.509 chain-walk.

Net:
- registry-center's trust surface is intentionally thin (verify + optionally notarize);
- catalyst-fabric's is a self-hosted CA with real key-custody, cert-issuance, and CRL-lifecycle responsibilities.

This is consistent with registry-center's documented positioning as a functional module that expects the host to supply its own PKI — it is not a gap in registry-center so much as a scope boundary.

---

## 5. Discovery / semantic search

This is one of the few areas where **registry-center is ahead**, not behind:

- **registry-center**: `/agent-cards/semantic-query` supports an optional Milvus vector-DB pre-filter (`use_vectordb`) followed by an **LLM-judged re-ranking** step (`RegistryCore._select_agents_by_llm`, `core.py:224-235`) that asks an LLM to pick the best-matching agents for a free-text task description and parses its JSON response. The LLM dependency (`get_llm_instance()`) is required unconditionally, vector DB is optional.
- **catalyst-fabric**: discovery is **plain in-process Python filtering** — exact-match on `fabric_profile` fields, set-intersection on skill tags, substring match on free text (`registry.py:1027-1063`). There is no vector index and no LLM-driven ranking anywhere in the discovery path; a code comment explicitly justifies this as a v1 scope decision ("no expression indexes in v1; data sets are small enough," `registry.py:589-591`). Note: catalyst-fabric does have an `LlmRegistry`/`issue_034_llm_registry_db`, but that is an **LLM provider/model catalog** an agent can be bound to (which backend it may call) — a different concept from semantic agent discovery, and easy to conflate by name alone.

Trade-off framing:
- catalyst-fabric's filtering is structured/deterministic and server-side-filterable across more dimensions (§3);
- registry-center's is fuzzier/semantic but only exposed through one endpoint with a single `task` string and no structured filter parameters.

---

## 6. The integration seam — verified contract mismatch

catalyst-fabric's `external_registry.py` client is written against a specific expected API shape. Reading its literal request paths against registry-center's literal route table:

| catalyst-fabric expects | registry-center actually serves |
|---|---|
| `POST /rest/a2a-t/v1/agents/register` | `POST /rest/v1/registry-center/agent-cards` |
| `GET /rest/a2a-t/v1/agents/query` | `GET /rest/v1/registry-center/agent-cards?name=&organization=` |
| `PUT /rest/a2a-t/v1/update_agent/{name}?organization=` | `PUT /rest/v1/registry-center/agent-cards/{organization}/{name}` |
| `DELETE /rest/a2a-t/v1/deregister_agent/{name}?organization=` | `DELETE /rest/v1/registry-center/agent-cards/{organization}/{name}` |
| `GET /rest/a2a-t/v1/events` (SSE, primary near-real-time sync path) | **No SSE/events endpoint exists anywhere in this repo** (confirmed by grep across `agent_registry/` — no `StreamingResponse`, no `text/event-stream`, no `/events` route) |

**These do not match**:
- different URL prefix (`/rest/a2a-t/v1/` vs `/rest/v1/registry-center/`);
- different resource naming (`agents/register` vs `agent-cards`);
- different parameter style (path segment vs query string for update/deregister); 
- a wholesale missing capability (SSE push).

As shipped, **catalyst-fabric's OpenAN client cannot talk to this repo's current API.**

**What *does* line up (real lineage, despite the broken wire contract):**
- Fabric's outbound payload shape deliberately mirrors this repo's persistence format: snake_case (not `by_alias`), and it explicitly strips a `"validated"` house-key it attributes to `postgresql_storage.py` (cross-referenced by the Fabric researcher against this repo's `postgresql_storage.py:144,167,190,210`).
- Fabric's `validators.py` docstring states it "mirrors OpenAN's `validated_agentcard.py` for max-length and format constraints... so Fabric rejects cards OpenAN would reject" (issue #29), and its length limits (`NAME_MAX_LENGTH=100`, `DESCRIPTION_MAX_LENGTH=1000`, etc.) and its `_DANGEROUS_CHARS` control-character regex are near-identical to this repo's `validated_agentcard.py` constants (§9 below) — credited by name in Fabric's own code comments as sourced from OpenAN.
- The behavioral contract Fabric's client assumes — "no filters ⇒ full listing, no server-side pagination" — matches registry-center's actual list endpoint behavior (it has no pagination params either), even though the *path* to reach that behavior differs.

So the two projects share real design lineage and a common data-shape ancestor, but the live wire integration, as currently coded on both sides, is broken. Fabric's resilience design (§ below) means this wouldn't necessarily be silent: `external_registry.py` maps non-2xx/malformed responses to `ExternalRegistryError` → HTTP 502 at Fabric's API layer, and registration forwarding failures cause Fabric to roll back the local write it just made — so a live deployment pointing Fabric at this repo's current API would fail loudly (502s) on every upstream call, not silently drift.

---

## 7. Persistence

- **registry-center**: pluggable — `file` (JSON files, in-process dict cache, coarse `threading.Lock` for concurrency) or `postgresql` (connection-pooled, auto-provisions its own DB/schema if missing). Config-selected via `persistence.mode`; `sqlite`/`gauss` are stubbed in config but **not implemented**.
- **catalyst-fabric**: Postgres-only, via SQLAlchemy + 18 Alembic migrations. Column types are Postgres-dialect-specific (`JSONB`, native `UUID`), so there's no portability path to another engine without a rewrite. ⚠ A module docstring in `db.py` indicates the project started with in-memory storage and was migrated to Postgres-only mid-project as a deliberate scope narrowing, not built for pluggability.

registry-center's dual-backend design gives it a lighter-weight deployment option (no DB dependency at all in `file` mode) that catalyst-fabric doesn't offer — consistent with registry-center's "single-instance, internal module" positioning vs. Fabric's containerized-platform positioning.

---

## 8. Extension points

registry-center ships two explicit, documented extension mechanisms with no catalyst-fabric equivalent:

1. **`HandlerRegistry`** (`common/custom/custom_handle.py`) — override any of `DECRYPT`/`AUDIT`/`AUTHENTICATE`/`INSERT`/`QUERY`/`UPDATE`/`GET`/`RETRIEVE`/`DEREGISTER` by registering a class before the first `get_handler()` call (cached singleton — a documented startup-ordering trap). This is the mechanism by which a host is expected to bolt on real authn/authz/audit/encryption, since registry-center ships only no-op/stub defaults for those.
2. **LLM provider config** (`common/llm/llm_config.json` + `AUTH_STRATEGIES` dict) — adding a model/vendor is data-only; adding a new auth scheme is one function.

catalyst-fabric has no comparable pluggable-handler pattern in the areas researched — its equivalents (auth, signing, storage) are hard-wired to specific implementations (Traefik mTLS + its own CA, Postgres-only).

⚠ registry-center also has an apparently-unused `common/plugin_framework/` (`base_plugin.py`, `registry.py`) not referenced by CLAUDE.md and not confirmed to be wired into `server.py`/`core.py` — flagged here as an open question rather than a claimed third extension mechanism.

---

## 9. Content safety

registry-center has a dedicated `agent_registry/model/blacklist_config.py`: CN/EN prompt-injection phrase lists and a dangerous-skill phrase list, checked (case-insensitive substring match) against agent name, description, and every skill's name/description/tags at register and update time, on top of a Unicode-control-character filter applied to `provider.organization`.

catalyst-fabric has **no content-safety/blacklist filtering** — searched for blacklist/prompt-injection/denylist patterns with zero hits. What it does have is purely *structural* validation (length limits, a name character-class regex, URL shape checks via Pydantic) plus that same `_DANGEROUS_CHARS` control-character regex on `organization` — explicitly credited in Fabric's code as sourced from this repo's regex, but Fabric never adopted the semantic (keyword) blacklist layer.

---

## 10. Deployment & operational model

| | registry-center | catalyst-fabric |
|---|---|---|
| Process topology | Single process | Multi-container (backend, Postgres, Traefik, agentgateway, MCP tooling, observability stack) |
| Messaging/queue infra | None | **None** (no Kafka, no Redis in the actual compose file — corrected assumption from initial framing) |
| Auth boundary | Host-supplied TLS termination populating `X-SSL-Client-DN`; registry itself does client-cert verification via uvicorn (`verify_client=true`) | Traefik-enforced mTLS at the edge for every service, plus an app-level `AuthMiddleware`; shipped compose sets `AGENT_FABRIC_AUTH_REQUIRED=false` at the app layer (relying on Traefik), explicitly documented as dev-only, "NEVER disable in production" |
| Scale ceilings | Explicit: 100 agents max, 1MB request cap, 50-100 concurrency per interface — by design, for a bounded internal deployment | Not similarly capped in the areas researched; positioned as a platform, not a bounded module |
| CLI/admin surface | UDS (Linux) / TCP 127.0.0.1:1108 (Windows dev) internal channel, filesystem-permission-gated, no additional auth layer on the socket itself | Web UI (Next.js/React) for both admin and end-user personas — no equivalent CLI-over-local-socket pattern found |

---

## 11. Config

Both use environment-variable-plus-file config, but with different philosophies:

- **registry-center**: bash-style `KEY=value` `.conf` files under `etc/conf/`, populated interactively via `python -m agent_registry.init`. Config *values* live in files; `agent_registry/config.py` holds only key-name constants.
- **catalyst-fabric**: `pydantic-settings`, with an explicit, documented precedence — **env vars win over the YAML bootstrap file** (`init kwargs > env > YAML > dotenv > secrets file > defaults`, `config.py:290-306`) — the inverse of what "YAML with env override" usually means. ⚠ Several integration-critical settings (`external_registry_url`, `AGENTGATEWAY_URL`, `DATABASE_URL`, etc.) use **bare, unprefixed** env var names instead of the otherwise-consistent `AGENT_FABRIC_*` prefix — a real, non-obvious inconsistency in Fabric's own config surface, unrelated to registry-center but worth flagging since it affects anyone wiring the two systems together (get the env var name wrong and the integration silently uses a default instead of erroring).

---

## 12. Gap analysis

### Gaps in registry-center relative to catalyst-fabric's needs (in-domain)

1. **API contract mismatch with Fabric's actual client (§6)** — the single most concrete, actionable finding. If Fabric is meant to integrate with this repo as shipped, the route prefix/paths need to be reconciled on one side or the other, and an SSE `/events` push endpoint would need to be added here (or Fabric's SSE-first sync path dropped in favor of its polling fallback only).
2. **No server-side pagination or structured discovery filters** — `GET /agent-cards` only supports exact `name`/`organization` match; no `limit`/`offset`, no domain/kind/tag filter. Fabric compensates by pulling the entire registry and filtering client-side, which only scales because Fabric also layers a TTL cache in front of it.
3. **Single opaque identifier only** — no server-generated ID separate from `(name, organization)`, and no equivalent of a routing-oriented identifier like Fabric's `kid`. Any consumer needing a stable non-PII agent handle has to synthesize one itself (as Fabric does via UUIDv5).
4. **Strict-reject vs. idempotent-upsert on duplicate registration** — registry-center's `409` on re-registration means any client that re-syncs its own state periodically (like Fabric's resync loop) must special-case "already exists" rather than relying on upsert semantics.
5. **No OpenAPI/Swagger introspection** (`docs_url`/`openapi_url` disabled) — makes it harder for an external integrator to verify the contract without reading source, which is arguably how the §6 mismatch went unnoticed.

### Gaps in catalyst-fabric relative to registry-center (in-domain)

1. **No approval/review workflow** — agents are active immediately on registration; no human-in-the-loop gate exists at all in Fabric's own registry.
2. **No semantic/LLM-driven discovery** — filtering is exact-match/substring only; no vector search, no LLM ranking.
3. **No content-safety (prompt-injection / dangerous-skill) blacklist filtering** — only structural/format validation.
4. **No pluggable storage backend** — Postgres-only, no lightweight file-based mode for smaller/simpler deployments.
5. **No equivalent custom-handler extension registry** — auth/audit/decrypt hooks are hard-wired rather than host-overridable via a documented registration pattern.

### Not gaps — out-of-scope-by-design (breadth, not deficiency)

registry-center is explicitly documented as "a functional module only, not a complete system" with no login/authz/audit/encryption/key-management/DB responsibilities. Listing the following as registry-center "gaps" would be misleading — they're deliberate scope boundaries, and catalyst-fabric is simply a broader platform that happens to include an agent registry as one of many components:

- Self-hosted CA / mTLS certificate issuance and lifecycle (§4)
- Policy/routing configuration generation for a data-plane gateway
- A Web UI for admins and end users
- Reputation/trust scoring, goals/objectives modeling, LLM/tool/resource catalogs
- Multi-service orchestration, observability stack (tracing/metrics/logging)

---

## 13. Open questions worth resolving with the respective owners

1. **Is catalyst-fabric actually meant to integrate with *this* repo in its current form**, or was it built against an earlier/different route scheme? The §6 mismatch is unambiguous in the code as it stands today — worth a direct conversation rather than further code archaeology.
2. Is `common/plugin_framework/` in this repo live, planned, or dead code? It's unreferenced by CLAUDE.md and its wiring wasn't confirmed.
3. This repo's own Development Guide states "no Agent owner design" for registered agents, but the shipped config (`owner.isolation.enabled=true`) and code path say otherwise — likely a stale doc that should be corrected regardless of this comparison.
4. Fabric's `operational_state=INACTIVE` value is referenced in a sync-related docstring but never actually set by the live code path (which hard-deletes drifted agents instead) — worth confirming with the Fabric team whether that's an intentional simplification or an incomplete migration.

---

## Sources

- `registry-center`: `agent_registry/server.py`, `core.py`, `config.py`, `middleware.py`, `model/blacklist_config.py`, `model/validated_agentcard.py`, `persistence/base.py`, `persistence/file_storage.py`, `persistence/postgresql_storage.py`, `signature/*`, `internal/*`, `cli/*`, `common/custom/custom_handle.py`, `common/llm/*`, `etc/conf/*.conf`, `docs/en/Registry Center Development Guide.md`.
- `catalyst-fabric`: `backend/src/agent_fabric/{registry,external_registry,upstream_cache,resync,validators,config,db,auth}.py`, `orm/agent.py`, `orm/certificate.py`, `models.py`, `api/{agents,a2a_cards,pki}.py`, `ca/{keystore,issuer,jwks,signing,verification,revocation}.py`, `specification/a2a.proto`, `docker-compose.yaml`, `config.example.yaml`, plus relevant `backend/tests/issue_*/features/*.feature` acceptance specs.
