# Registry Center vs CAT26 Registration Center — Comparative Analysis

**Repositories compared:**
- **OPENAN-VERSION** — `/home/lorenzo/git/openan/registry-center` (this repo, `hw-irc-sni/registry-center`, branch `lorenzo-analysis`)
- **CATALYST-VERSION** — `/home/lorenzo/git/openan-catalyst/cat26-registration-center` (`fcamacho-Huawei/cat26-registration-center`)

## Scope, methodology, and assumptions

Both repos are the same project — "A2A-T AgentCard Registry Center" — under different names and different git remotes: it is a **fork relationship**. \
**CATALYST-VERSION is an earlier snapshot** and **OPENAN-VERSION is the evolved codebase**.

1. **Framing: baseline-vs-current**, not neutral-parallel — the evidence (see below) supports treating CATALYST-VERSION as chronologically prior.
2. **Comparison depth: behavioral/functional**, not exhaustive line diffs — pure renames, refactors, and license-header additions are named once and then skipped.
3. **LLM provider subsystem gets a dedicated, detailed section** — it's the single largest architectural divergence between the two repos.

A companion document, [`Registry Center vs Catalyst-Fabric Comparative Analysis.md`](Registry%20Center%20vs%20Catalyst-Fabric%20Comparative%20Analysis.md), compared OPENAN-VERSION against a third, architecturally distinct repo (`catalyst-fabric`). §7 below connects a finding from that document to this one.

---

## 1. Direct feature comparison

### 1a. Implemented only on one side

| Feature | Only in CATALYST-VERSION | Only in OPENAN-VERSION |
|---|---|---|
| SSE registry-change event stream (`GET /rest/a2a-t/v1/events`, `RegistryEventBroadcaster`) | ✅ | — |
| Internal CLI + UDS/TCP admin service (`agent_registry/cli/`, `agent_registry/internal/`) | — | ✅ |
| Approval workflow (`registered` → `published`, `agent_approval_enabled`) | — | ✅ |
| Tag entity + agent↔tag association (`model/tag.py`, tag storage/tables, tag CLI/internal handlers) | — | ✅ |
| Content-safety blacklist (prompt-injection + dangerous-skill keyword filtering) | — | ✅ |
| Owner isolation (TLS client-CN binding, `strict`/`relaxed` modes) | — | ✅ |
| Registry co-signing of AgentCards + live JWKS endpoint (`GET /rest/v1/registry-center/keys`) | — | ✅ (present-but-dead in CATALYST-VERSION, see §6) |
| `common/plugin_framework` (generic pluggable-provider registry) | — | ✅ |
| Public-key **write** API (`add_public_keys`/`remove_public_key`, `AgentKeysStorage`) | ✅ (dead — see §6) | — |
| Secondary/duplicate embedding abstraction (`common/vector_db/embedding_model/`, `embedding_config.json`) | ✅ (dead — see §6) | — |
| Windows dev support (`IS_WINDOWS`, TCP internal-service fallback) | — | ✅ |
| Structured JSON error envelope (`{"errors": {...}}`) | — | ✅ |
| Docker multi-stage build, non-root user, `bin/entrypoint.sh` (Cloud Run-style env→conf bridging) | — | ✅ |
| Redis/Kafka-free deployment (file storage as default, Postgres optional) | — | ✅ (CATALYST-VERSION defaults to Postgres, no file-mode-by-default story) |

### 1b. Same function, different implementation

| Function | CATALYST-VERSION | OPENAN-VERSION |
|---|---|---|
| LLM provider access | Class-per-vendor + `LLMType` enum dispatch (`OpenAIStyleLLM`, `AOCChatLLM`/`AOCEmbeddingLLM`/`AOCRerankerLLM`) | Single config-driven `GenericLLM` + `auth_strategies.py`, keyed by capability string (see §5) |
| REST namespace | `/rest/a2a-t/v1/...` | `/rest/v1/registry-center/agent-cards...` (full rename, see §7) |
| Register/Update/Deregister/Query semantics | Single-agent body, unfiltered listing, no status concept | Batch body, signature verification + optional co-signing, status-filtered listing (`published` only) |
| Fuzzy/semantic retrieve | `GET /rest/a2a-t/v1/agents/retrieve?task=...` | `POST .../agent-cards/semantic-query` with JSON body |
| Signature `kid` derivation | Tail of `jku_url` string / absent from JWKs entirely | X.509 cert serial number, present on both signer and JWKS output (CATALYST-VERSION's mismatch meant kid lookups couldn't round-trip) |
| Signature validation input type | Custom Pydantic `ValidatedAgentCard.model_dump()` | Native `a2a.types.AgentCard` protobuf, `MessageToDict` |
| JWKS fetch transport | Sync `requests.Session`, no size cap | Async `httpx.AsyncClient`, 1&nbsp;MB response cap |
| Config module | `common/util/config_util.py` + `conf_util.py` (generic + SSL logic co-located) | `common/util/app_config.py` (generic + env-override layer) + `common/util/ssl_config.py` (SSL-specific, split out) |
| Vector-DB backend registry | Bespoke `VectorDBToolRegistry` class | Generic `common/plugin_framework.PluginRegistry`, reused registry pattern |
| File storage duplicate handling | Silent overwrite on key collision | Explicit rejection (`create()` returns `False` if key exists) |
| Postgres identity constraint | `UNIQUE(name, organization)` | `UNIQUE(name, organization, owner)` at the DB layer — **but see §7.1: app-layer enforcement is unchanged and still keys on `(name, organization)` alone**, so the stated CLAUDE.md invariant holds in practice despite the looser DB constraint |
| Middleware error handling | Blanket `try/except Exception → 500` in `ConnectionLimitMiddleware.dispatch()` | That catch-all removed; relies on the new global `HTTPException` handler + per-route guards instead |

---

## 2. Core service layer (`agent_registry/{core,server,middleware,config,registry_instance,start,init}.py`)

**New in OPENAN-VERSION:** 
- the tag subsystem (`core.py:407-462`);
- the approval workflow (`register_with_status`, status filtering on list/get endpoints, `server.py:551-552,591-597,749-752`);
- owner isolation (`server.py:341-406`, `_verify_owner_permission`);
- content-safety blacklist validation wired into card validation;
- live JWS signing/verification plus the JWKS endpoint (`server.py:768`);
- the internal UDS/TCP admin service spun up from `start.py:137-152`;
- Windows dev-mode branching;
- a thread-safe registry singleton (`registry_instance.py`, guarded by `threading.Lock`, fixing a check-then-set race in CATALYST-VERSION);
- a structured JSON error envelope (`server.py:265-282`) replacing FastAPI's default `{"detail": ...}` shape;
- audit logging on failure paths (`_audit_failure`/`_audit_result`);
- a hardened `init.py` wizard (persistence-mode allowlist with re-prompt, `getpass` for DB passwords, approval-toggle guarded against agents stuck in `registered`, in-place config-file rewriting that preserves comments).

**Removed in OPENAN-VERSION (present only in CATALYST-VERSION):**
- the SSE event stream (`RegistryEventBroadcaster`, `GET /rest/a2a-t/v1/events` — genuinely dropped, not renamed);
- the `from_ui`/`validated`-field query-flag handling in `normalize_agents()`/`extract_validated_from_body()` (superseded by the formal status model);
- the blanket `except Exception → 500` in `ConnectionLimitMiddleware`;
- unused `MAX_REGISTER_NUM`/`TLS_VERSION` config constants.

**Hardening:**
- OPENAN-VERSION disables Swagger/OpenAPI docs (`docs_url=None, redoc_url=None, openapi_url=None`) and drops CATALYST-VERSION's any-origin `CORSMiddleware(allow_origins=["*"], allow_credentials=True, ...)` entirely
- CATALYST-VERSION ships both public API docs and permissive CORS-with-credentials by default.

**Route rename:**
- every REST path moved from `/rest/a2a-t/v1/...` to `/rest/v1/registry-center/agent-cards...`. This is load-bearing for §7.

---

## 3. Persistence (`persistence/`, `sql_queries.py`, `persistence.conf`)

OPENAN-VERSION's `StorageBackend` ABC and `AgentRecord` dataclass add owner/status/tags/timestamps end-to-end: new abstract methods (`find_by_owner`, `find_by_status`, `find_by_tag`, `update_status`, full tag CRUD) with no removals from CATALYST-VERSION's interface. `file_storage.py` grows from 154 to 452 lines, adding parallel maps for status/owner/tags/timestamps and splitting storage across three files (agent cards, metadata, tags) instead of CATALYST-VERSION's single JSON blob; it also now rejects duplicate keys on `create()` instead of silently overwriting.

`postgresql_storage.py`/`sql_queries.py` add `owner`, `status`, and a `tags JSONB` column plus a dedicated `tag` table, and drop CATALYST-VERSION's `validated BOOLEAN` column entirely. The unique constraint widens from `(name, organization)` to `(name, organization, owner)` — flagged as a possible invariant relaxation in the original research pass, **resolved in §7.1: it is not**, because `server.py:436-444`'s `_check_duplicate_agent` still keys the app-layer rejection on `(name, organization)` alone via `registry.get_agents()`, independent of owner. The wider DB constraint is therefore inert in current call paths, not an active relaxation of the CLAUDE.md invariant.

`StorageRegistry` lost its generic `.register()` plugin mechanism in OPENAN-VERSION in favor of a hardcoded if/elif in `get_backend()` — a minor extensibility regression, though nothing in either repo exercises third-party backend registration today. `persistence.conf` flips the default mode from `postgresql` (CATALYST-VERSION) to `file` (OPENAN-VERSION), consistent with OPENAN-VERSION's broader "Postgres is optional" posture (see §8).

---

## 4. Signature / trust / certificates

OPENAN-VERSION activates a pipeline that exists only as inert scaffolding in CATALYST-VERSION (see §6 for the "dead code" framing) and fixes a `kid`-derivation mismatch that would have broken signature verification round-trips in CATALYST-VERSION even if it had been wired up: CATALYST-VERSION's `AgentCardSigner` derives `kid` from a URL tail while its `JWKProvider` emits JWKs with **no `kid` field at all**; OPENAN-VERSION derives `kid` from the X.509 cert serial number on both sides consistently.

Other hardening in OPENAN-VERSION: an algorithm allowlist (`ES256`/`RS256` only) enforced at the Pydantic model layer (`signature/models.py:34-39`); a 1 MB response cap on JWKS fetches plus a switch from sync `requests.Session` to async `httpx.AsyncClient`; path-traversal protection in key storage (`signature/storage.py:47-60`, `os.path.realpath()` + `BASE_DIR` containment check); and the new `common/cert/cert_cn_parser.py` (`extract_cn_from_subject`, `validate_cn`) backing owner isolation, entirely absent from CATALYST-VERSION.

CATALYST-VERSION's `PublicKeyManager` supports key **writes** (`add_public_keys`, capacity-limited to 5 keys/agent, `remove_public_key`) that OPENAN-VERSION's read-only equivalent drops — but a repo-wide grep shows those write methods were never called from outside their own file in CATALYST-VERSION either, so this is dead-capability removal, not a functioning-feature loss. The validator also shifted from validating a custom `ValidatedAgentCard` Pydantic model to validating the native `a2a.types.AgentCard` protobuf directly — a real architecture change, not cosmetic.

One fragility introduced in OPENAN-VERSION: `validate_agent_card` remains a sync method but wraps the now-async `fetch_jku_key` in `asyncio.run(...)` (`agent_card_signature_validator.py:125-127`), which will raise `RuntimeError` if ever called from a thread with an already-running event loop.

---

## 5. LLM provider architecture (the largest single divergence)

**CATALYST-VERSION** uses a class-per-vendor, registry+inheritance pattern: an abstract `BaseLLM`, a `LLMProviderRegistry` keyed by a `LLMType` enum, and concrete classes — `OpenAIStyleLLM` (wraps the `openai` SDK for OpenAI-compatible endpoints) and three siblings of `AOCBaseLLM` (`AOCChatLLM`/`AOCEmbeddingLLM`/`AOCRerankerLLM`) implementing a Huawei-internal gateway's custom `x-sg-*` header-signing scheme (app-key + MD5(message_id + app_secret + timestamp), base64-encoded). Adding a genuinely new vendor (new auth scheme or response envelope) requires writing a new Python class; a same-shape vendor can sometimes be added via a `request_template` string in JSON config — a half-step toward genericity.

**OPENAN-VERSION** collapses all of this into one config-driven `GenericLLM` (`common/llm/provider/generic_llm.py`): every vendor is a JSON entry (`url`, `body` template with `$MODEL`/`$PROMPT`/`$QUERY`/`$DOCUMENTS`/`$ENABLE_THINKING` placeholders, `response` dot-path extraction map, `auth` — a bare Bearer token, `null`, or a named strategy from `auth_strategies.py`). CATALYST-VERSION's AOC signing scheme survives as a faithful port, registered under the string key `"aoc_signed"` — the one component that couldn't be expressed purely in JSON. Per CLAUDE.md's claim, adding a new vendor now requires **zero Python changes** for the common case (Bearer/no-auth); only a genuinely new signing scheme needs one new function in `auth_strategies.py`.

The public API also improved:
- CATALYST-VERSION's `get_llm_instance(llm_type: LLMType)` has no embed/rerank convenience functions (callers reach embeddings via low-level enum plumbing);
- OPENAN-VERSION adds symmetric `get_llm_instance(capability="chat")`, `get_embed_instance()`, `get_rerank_instance()` factories, all cache-backed by capability string.

Two side-effects of the refactor:
1. OPENAN-VERSION deletes an entire dead, parallel embedding abstraction that CATALYST-VERSION carried (`common/vector_db/embedding_model/` — `BgeM3EmbeddingTool`, `EmbeddingConfig` — imported/re-exported but never instantiated by `core.py`, which actually used `AOCEmbeddingLLM` all along);
2. OPENAN-VERSION's `llm_config.json` replaces CATALYST-VERSION's checked-in, **live-looking credentials** (a DeepSeek `sk-...` API key and an AOC bearer token/app-secret) with placeholders — a real secrets-hygiene fix worth calling out explicitly, independent of the architectural merits.

One capability trade-off, not a regression:
- CATALYST-VERSION's AOC embed/rerank classes raised `NotImplementedError` with a clear message if misconfigured;
- `GenericLLM` has no such hardcoded fallback, so an incomplete `body` template in OPENAN-VERSION can silently produce a malformed request rather than fail fast.
- OPENAN-VERSION is also far better tested here — `tests/test_llm.py` (43 test functions) has no CATALYST-VERSION counterpart at all.

Vector-DB/Milvus integration itself (collection/insert/delete/upsert/query) is functionally the same in both; OPENAN-VERSION generalizes the registry pattern into the reusable `common/plugin_framework` (see §6) and fixes one real bug — `insert_entity` now checks `insert_count == 0` and reports failure, where CATALYST-VERSION never verified the insert succeeded.

---

## 6. Dead, vestigial, and newly-wired code — a precise accounting

Several CATALYST-VERSION→OPENAN-VERSION "capability" comparisons are misleading unless dead code is called out explicitly rather than folded into a tidy present/absent table:

- **CATALYST-VERSION's signing/JWKS pipeline is present but never wired.** `AgentCardSigner`, `JWKProvider`, and the `registry.sign.enabled` config prompt all exist in CATALYST-VERSION, but nothing constructs or calls them outside their own files/tests, and there is no `GET /rest/v1/registry-center/keys` route at all. OPENAN-VERSION is the first version where this pipeline is actually invoked from the request path (`server.py:75-133,550-552,652-654,768-806`). Treat this as "CATALYST-VERSION shipped the parts, OPENAN-VERSION assembled and activated them," not "both support signing."
- **CATALYST-VERSION's `agent_registry/persistence.py` is shadowed, unreachable code**, not a legacy code path in active use — Python resolves `import agent_registry.persistence` to the co-located `persistence/` package, never the sibling `.py` file, and `core.py` imports from the package. OPENAN-VERSION simply doesn't carry the orphaned file forward; nothing was "removed" at runtime.
- **CATALYST-VERSION's `signature/validator_instance.py`** (an `lru_cache`-wrapped singleton factory) is never imported anywhere in CATALYST-VERSION. OPENAN-VERSION doesn't lose this capability by deleting the file — it inlines the same pattern directly into `server.py:75-87` and actually wires it as a FastAPI dependency, exercised by `tests/test_server_endpoints.py`.
- **CATALYST-VERSION's `PublicKeyManager.add_public_keys`/`remove_public_key`** (§4) and **`common/vector_db/embedding_model/`** (§5) are both confirmed-dead by repo-wide grep — real code, zero call sites outside their own module/tests.
- **OPENAN-VERSION's `common/plugin_framework`**, by contrast, is genuinely wired, just narrowly: only `common/vector_db/vector_db_client/config/vector_db_client_registry.py` and `vector_db_client.py` import from it today. It is not dead, but it is not (yet) the general-purpose extension mechanism its generic design suggests it could become.

This distinction matters most for anyone deciding whether to "port a feature forward" from CATALYST-VERSION to OPENAN-VERSION (or vice versa) — porting `PublicKeyManager`'s write methods, for instance, would be reviving dead CATALYST-VERSION code with no established caller, not restoring a working feature.

---

## 7. Cross-reference: this explains the catalyst-fabric contract mismatch

The companion analysis (`Registry Center vs Catalyst-Fabric Comparative Analysis.md`, §6) flagged a **verified contract mismatch**:
- catalyst-fabric's `external_registry.py` calls `/rest/a2a-t/v1/agents/register`, `/agents/query`, `/update_agent/{name}`, `/deregister_agent/{name}`, and expects an SSE `/rest/a2a-t/v1/events` stream — none of which registry-center (OPENAN-VERSION) actually serves.

**CATALYST-VERSION serves exactly those routes**, including the SSE broadcaster (§2, §1a). So catalyst-fabric's client code matches cat26's API surface, not registry-center's current one. Two explanations are consistent with this and can't be distinguished from code alone:
1. Fabric was originally built/tested against cat26 (or a shared ancestor snapshot both forked from), and registry-center's later namespace rename (`/rest/a2a-t/v1/` → `/rest/v1/registry-center/agent-cards`) plus SSE removal broke that integration without Fabric being updated.
2. `/rest/a2a-t/v1/` was an early A2A-T protocol-naming convention that registry-center deliberately abandoned in favor of its current REST shape, and Fabric's client was never updated to track the change.

Either way, the actionable conclusion is the same as before: **cat26's contract is what Fabric's client currently expects; registry-center's is not.** This is worth resolving directly with whoever owns the Fabric↔registry integration, now with a concrete "which version's contract is the target" question to ask rather than an open-ended one.

### 7.1 The identity-invariant question from §3

Directly verified:
- OPENAN-VERSION's app-layer duplicate check (`server.py:436-444`, `_check_duplicate_agent`) computes `key = make_agent_key(agent.name, agent.provider.organization)` and checks membership in `registry.get_agents()` — owner is not part of the key. So even though the Postgres schema widened its unique constraint to `(name, organization, owner)`, the application still rejects same-`(name, organization)` registrations regardless of owner before a second Postgres row could ever be inserted. CLAUDE.md's stated invariant — "`(name, provider.organization)` — duplicates are rejected" — holds in the code path that's actually exercised. The wider DB constraint looks like unused headroom (or an oversight) rather than an intentional per-owner-namespacing feature; worth flagging to the owning team since a DB constraint that's looser than the enforced invariant is the kind of thing that quietly breaks if the app-layer check is ever refactored.

---

## 8. Infrastructure, config, and operations

`common/util/app_config.py` is a genuine consolidation of CATALYST-VERSION's `config_util.py` + `conf_util.py` (byte-identical core logic), plus a new `apply_env_overrides()` layer (`REGISTRY_*` env vars) that didn't exist in CATALYST-VERSION. `ssl_config.py` is new, splitting CATALYST-VERSION's inline SSL/cert config handling out of `conf_util.py` into its own module. This matches CLAUDE.md's description of `app_config.py` as the central config-reading module.

`common/log/audit_logger.py`'s `OperationName` grows from two constants (`START_SERVICE`, `REGISTER_AGENT`) to thirteen, covering the full CRUD/tag/approval/cert-generation surface OPENAN-VERSION added — a direct, expected consequence of the larger feature set, not an independent design choice.

Concrete `etc/conf/server.conf` key changes: OPENAN-VERSION adds `jwk_private_key_path`, `jwk_private_key_password`, `agent_approval_enabled`, `owner.isolation.enabled`, `owner.validation.mode`, `ssl_certfile`; drops `JWK_KID`; flips defaults `IP` 0.0.0.0→127.0.0.1, `PORT` 8080→5000, `enable_https` false→true (secure-by-default). `server.properties` adds `tag.max.count=10`/`tag.max.length=50` and raises `connection.timeout` 30→300s and `agent.num.max` 40→100.

Deployment posture changed materially:
- CATALYST-VERSION's `docker-compose.yaml` is a single-stage `python:3.10-alpine` image, hardcoded DB credentials, Postgres-mode-by-default.
- OPENAN-VERSION is a multi-stage `python:3.12-slim` build, non-root user, `bin/entrypoint.sh` bridging container env vars into the file-based config system (including Cloud Run's injected `PORT`), and Postgres commented out as optional — file storage is the default runtime mode.

`docs/internal_service_design.md` in CATALYST-VERSION is worth noting precisely: it's a **pre-implementation Chinese-language design doc** proposing an "Agent audit" (approval) feature with a raw-socket UDS service and a different naming/framing than what OPENAN-VERSION actually built (`agent_audit_enabled` vs `agent_approval_enabled`; bespoke raw-socket client vs the structured `internal/protocols/` + CLI). It documents an early proposal that diverged during implementation, not a description of code that was later removed — CATALYST-VERSION has no `internal/`/`cli/`/`audit/` directories at all.

---

## 9. Test coverage as a signal

| | CATALYST-VERSION | OPENAN-VERSION |
|---|---|---|
| Total `def test_` functions | 58 | 495 (incl. `tests/cli/`, 193 functions) |
| Shared-file coverage | `test_register.py`: 3 cases; `test_signature.py`: 1 case | `test_register.py`: 46 cases (~15x); `test_signature.py`: 14 cases (~14x) |
| Feature-unique suites | — | `test_blacklist_performance.py`, `test_llm.py` (43), `test_middleware.py`, `test_semantic_query.py`, `test_server_endpoints.py`, `test_storage_tags.py`, `test_tag_handler.py`, `test_tag_validator.py`, `test_validated_agentcard.py` (47), `test_validated_agentcard_fixes.py`, and the entire `tests/cli/` tree |

OPENAN-VERSION's ~8.5x test volume tracks directly with its larger feature surface (tags, blacklist, semantic search/LLM, owner isolation, CLI/internal service) rather than reflecting deeper scrutiny of unchanged code — six of eight shared-and-unmodified test files have identical case counts in both repos.

---

## 10. Open questions worth resolving with the repo owners

1. **Which contract is authoritative for the Fabric integration** — cat26's `/rest/a2a-t/v1/...` + SSE, or registry-center's current `/rest/v1/registry-center/agent-cards...`? (§7)
2. Should CATALYST-VERSION's SSE event stream be ported into OPENAN-VERSION?
3. Is the Postgres `UNIQUE(name, organization, owner)` constraint in OPENAN-VERSION (§7.1) intentional headroom for a future per-owner-namespacing feature, or an oversight that should be tightened back to `(name, organization)` to match the enforced app-layer invariant exactly?

---

## Sources

Findings above were gathered via direct `ls`/`diff -rq`/`diff -u`/`grep` comparison of both repository trees and by reading the cited files in full, dispatched across seven parallel research passes (core service layer; persistence; signature/trust/certificates; LLM & vector-DB; CLI/internal/tags/blacklist/extension-points; infrastructure/config/ops; test coverage) and cross-verified against the companion `Registry Center vs Catalyst-Fabric Comparative Analysis.md`. The identity-invariant question in §7.1 was independently re-verified by direct grep of `agent_registry/server.py` before inclusion.
