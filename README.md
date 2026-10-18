<!--
Copyright (c) 2026 Huawei Technologies Co., Ltd.
All Rights Reserved.

SPDX-License-Identifier: Apache-2.0

   Licensed under the Apache License, Version 2.0 (the "License"); you may
   not use this file except in compliance with the License. You may obtain
   a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
   License for the specific language governing permissions and limitations
   under the License.
-->

# A2A-T AgentCard Registry Center

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="License"></a>
</p>

<p align="center">
  <strong>A centralized registry for managing AgentCards across multi-vendor AI agents in the A2A-T ecosystem.</strong>
  <br>
  面向 A2A-T 生态的多厂商智能体 AgentCard 统一注册与管理服务。
</p>

<p align="center">
  <a href="./README_zh.md">中文</a>
</p>

---

## Overview

The Registry Center provides unified lifecycle management for **AgentCards** — structured descriptions of AI agents from different vendors. Built for the A2A-T protocol ecosystem, it enables controlled onboarding, discovery, and governance of multi-source agents.

**Use cases:** Telecom operators managing RAN optimization agents, enterprise platforms orchestrating vendor-provided AI services, internal systems with agent approval workflows.

<img src="docs/en/images/integrated_interactive_relationship.png" width="700" alt="Registry Center Integration Architecture">

## Features

| Category | Capability |
|----------|------------|
| **AgentCard CRUD** | Register, query (by name/organization), update, and deregister agent descriptions |
| **Semantic Search** | Natural-language task matching over published AgentCards via LLM; model failures return 503. Legacy Milvus mode remains experimental and does not provide SQL-backed approval/ownership parity: those endpoints answer 503 instead of empty results |
| **Agent Approval** | Optional manual review workflow — agents start as `registered`, admins promote to `published` |
| **Tag Management** | Independent tag entities with full CRUD, assignable to agents |
| **TLS Security** | TLS 1.2/1.3 with strong cipher suites, mutual TLS client certificate verification |
| **Signature Verification** | JWS-based AgentCard integrity checks (RS256, ES256), static JWK or dynamic `jku` lookup. `jku` lookup is gated by an operator-configured host allowlist (`jwk_allowlist` in `etc/conf/server.conf`, or `REGISTRY_JWK_ALLOWLIST`); with no allowlist configured the `jku` path is disabled (fail closed) and only backend keys are used |
| **Owner Isolation** | Ownership from verified TLS client certificates or an explicitly trusted proxy; ownerless legacy cards require administrative ownership assignment |
| **Content Safety** | Prompt injection and high-risk skill blacklist filtering on registration |
| **Rate Limiting** | Per-endpoint rate limits (configurable: 50–100 req/s, JWK endpoint: 10 req/s) with moving-window algorithm |
| **Heartbeat Detection** | Agents periodically report liveness; configurable failure threshold and grace period to identify offline agents promptly |
| **Change Broadcast** | Best-effort webhooks to operator-allowlisted destinations, with HMAC signing and version-based reconciliation; version allocation is commit-ordered in the SQL persistence modes (deployment stays single-instance) and delivery state is tracked per subscriber, while retry-after-failure and exactly-once semantics are not yet guaranteed |
| **Audit Logging** | Rotating JSON audit log (time, client IP, user, operation, object, result) |
| **CLI Administration** | Interactive CLI for agent approval, tag management, and full agent listing |
| **Custom Extensions** | Pluggable handlers (auth, audit, decrypt, storage) and LLM providers |

See [upgrade and deployment notes](docs/en/Registry%20Center%20Upgrade%20Notes.md)
([中文](docs/zh/注册中心升级与部署说明.md)) for identity configuration, approval
visibility, legacy-event synchronization, and container probe setup. The generic
knowledge-graph API is pre-embedded and disabled by default; enabling it requires
an authenticated operator and separate graph permissions. `docker-compose.yml`
is a loopback-only development example with client authentication disabled;
it is not a production access-policy template.

## Quick Start

### Prerequisites
- **Python** 3.12+
- **OS**: Linux for production; Windows supported for development/debugging

### Install & Run

```bash
# Clone the repository
git clone https://github.com/project-openan/registry-center.git
cd registry-center

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate      # Linux
# .venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt

# Development material only; production must provide managed certificates/keys.
# Existing outputs are not overwritten. Use separate directories for TLS and signing.
python -m generate_selfsign_cert etc/ssl serverAuth
python -m generate_selfsign_cert etc/ssl serverAuth --issue-client demo
python -m generate_selfsign_cert etc/sign_cert dataSigning
# Review public server.conf.example and persistence.conf.example; copy only if absent.
# Set actual jwk_* paths as documented in the Security Guide; init's old sign_* keys are inert.
# Configure models.yaml + named secrets and the selected DB profile before serving.
python -m agent_registry.init

# Start the service
python -m agent_registry.start
```

The public profile uses HTTPS on port 5000. Verify the generated CA/SAN and client identity before business requests. Merely setting `enable_https=false` is not a complete HTTP profile: certificate ownership requires mTLS. For HTTP with authenticated callers, configure `owner.identity.mode=token` and its authentication provider, independently of transport verification. See [transport policies](docs/transport-runtime.md) and the Security Guide; do not disable card integrity or caller authorization to get a quick-start example through.

For Docker/Podman packaging, runtime secret mounts, port precedence and
non-interactive initialization, see [Container deployment](docs/container-deployment.md).

### Register Your First Agent

Prepare `agent-card.signed.json` with the complete AgentCard contract, register its verification public key/JKU as described in the Security Guide, and configure the content-safety model. The registry's dataSigning key signs registry output; it does not replace the registering Agent's signature. With approval enabled, publish the card before expecting discovery to return it.

For the development mTLS profile above (same verified client identity must be used for later owner-scoped operations):

```python
import json
import requests

with open("agent-card.signed.json", encoding="utf-8") as source:
    card = json.load(source)
response = requests.post(
    "https://127.0.0.1:5000/rest/v1/registry-center/agent-cards",
    json={"agentCards": [card]},
    verify="etc/ssl/trust.cer",
    cert=("etc/ssl/demo-client.cer", "etc/ssl/demo-client.key"),
    timeout=30,
)
response.raise_for_status()
print(response.status_code)
```

Token mode additionally supplies an authorized vendor/admin Bearer; HTTPS with verify_client=false removes only the client-certificate requirement, not application identity or card signature checks. The example is conditional on the prepared signed card/provider, not an anonymous unsigned registration shortcut.

## Architecture

```mermaid
flowchart TB
    subgraph clients[" "]
        direction LR
        rest["REST Client<br/>(HTTPS/TLS)"]
        cli["CLI Admin<br/>(local)"]
    end

    subgraph server["Registry Center Server"]
        direction TB
        mw["Security Middleware<br/>Rate Limit · Signature Verify<br/>Owner Isolation · Content Safety"]
        core["RegistryCore<br/>CRUD · Semantic Search · Approval"]
    end

    subgraph storage["Storage Backends"]
        direction LR
        file[("File<br/>JSON")]
        pg[("SQL authority<br/>PostgreSQL / MySQL<br/>GaussDB / SQLite")]
        milvus[("Optional Milvus<br/>Vector projection")]
    end

    subgraph ext["External"]
        llm["LLM Provider"]
        auth["Auth System<br/>(Host-provided)"]
    end

    rest -->|"REST API"| mw
    cli -->|"UDS / TCP"| core
    mw --> core
    core --> file
    core --> pg
    core --> milvus
    core --> llm
    core --> auth

    style server fill:#e1f5fe,stroke:#0288d1
    style storage fill:#f3e5f5,stroke:#7b1fa2
    style clients fill:#e8f5e9,stroke:#388e3c
    style ext fill:#fff3e0,stroke:#f57c00
```

## API Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/rest/v1/registry-center/agent-cards` | Register an AgentCard |
| `GET` | `/rest/v1/registry-center/agent-cards` | Query agents (by name/organization) |
| `GET` | `/rest/v1/registry-center/agent-cards/{org}/{name}` | Get a specific agent |
| `PUT` | `/rest/v1/registry-center/agent-cards/{org}/{name}` | Update an agent |
| `DELETE` | `/rest/v1/registry-center/agent-cards/{org}/{name}` | Deregister an agent |
| `POST` | `/rest/v1/registry-center/agent-cards/semantic-query` | Semantic search by task description |
| `POST` | `/rest/v1/registry-center/agent-cards-with-layer` | Layer-aware AgentCard query |
| `GET` | `/rest/v1/registry-center/agent-cards-with-layer/{org}/{name}` | Get an AgentCard with its layer |
| `POST` | `/rest/v1/registry-center/agent-cards-with-layer/semantic-query` | Layer-aware semantic search |
| `GET` | `/rest/v1/registry-center/keys` | Retrieve registry signing public keys (JWK Set) |
| `POST` | `/rest/v1/registry-center/agent-cards/{org}/{name}/heartbeat` | Report agent heartbeat |
| `GET` | `/rest/v1/registry-center/agents/health` | Query agent health status list |
| `POST` | `/rest/v1/registry-center/subscriptions` | Create a change subscription |
| `GET` | `/rest/v1/registry-center/subscriptions` | List subscriptions |
| `DELETE` | `/rest/v1/registry-center/subscriptions/{id}` | Delete a subscription |
| `GET` | `/rest/v1/registry-center/changes` | Change reconciliation (incremental pull by version) |

See the [API Reference](docs/en/Registry%20Center%20API%20Reference.md) for full request/response schemas, error codes, and constraints.

## Configuration

| Config File | Purpose |
|-------------|---------|
| `etc/conf/server.conf` | Feature switches and deployment/access settings: IP, port, TLS/credential references, IAM endpoints and identity modes |
| `etc/conf/server.properties` | Operating parameters and business policies: TLS versions/ciphers, limits, heartbeat timing, notification retry and OAuth cache/scope policies |
| `etc/conf/persistence.conf` | Storage backend: `file` (default), `sqlite`, `postgresql`, `gauss`, `mysql` |
| `etc/conf/log_config.conf` | Audit log rotation (size, backup count) |
| `.env` | Local secrets (gitignored); model definitions live in `etc/config/models.yaml` |

Define each key in only one server file. Loading order remains `server.conf` →
`server.properties` → `REGISTRY_*` environment overrides. Duplicate definitions
produce a warning containing key names only; historical last-loaded precedence
is preserved for existing deployments. When migrating policies from `server.conf`,
move their **effective values**, not template defaults, into `server.properties`,
then remove the old definitions. The initialization wizard only edits deployment
and feature settings. The image initializes from `server.conf.example` plus the
public `server.properties`; local deployment files and secrets are not image inputs.

Model definitions live in the gitignored `etc/config/models.yaml` (copy
[`models.yaml.example`](etc/config/models.yaml.example)), while secrets come
from environment variables or a local gitignored `.env` (see
[`.env.example`](.env.example)). Built-in `openai_compatible` (`openai` alias) and `aoc_signed` profiles
provide request/response contracts, while each `models:` entry sets `model` and
`url` and names its secret through `api_key_env` or `auth.<field>_env`. The same
structure is used by Orchestration Center. Environment variables override
`.env`; restart the process after changing either source. A new model using an
existing protocol needs only configuration; a new protocol registers a profile
in `common/llm/config/model_sources.py` and adds tests.
Existing installations can run `python -m scripts.migrate_llm_config` once;
the application no longer reads or tracks the old JSON.

Configure interactively:

```bash
python -m agent_registry.init
```

## Documentation

| Document | Description |
|----------|-------------|
| [User Guide](docs/en/Registry%20Center%20User%20Guide.md) | Features, deployment, CLI quick reference, FAQ |
| [Development Guide](docs/en/Registry%20Center%20Development%20Guide.md) | Architecture, registration workflow, custom LLM/handler extensions |
| [API Reference](docs/en/Registry%20Center%20API%20Reference.md) | Full REST API specification with request/response examples |
| [Security Guide](docs/en/Registry%20Center%20Security%20Guide.md) | TLS, access control, audit logging, content safety, certificate tooling |
| [GCP Containerized Deployment Guide](docs/en/Registry%20Center%20GCP%20Containerized%20Deployment%20Guide.md) | Containerized deployment of Registry Center on Google Cloud Platform |
| [LLM Config](etc/config/README_en.md) | LLM configuration file reference |

> For Chinese documentation, see [中文 README](README_zh.md) or [docs/zh/](docs/zh/).

## Deployment

This project is delivered as **source code only**. Users are responsible for:

1. **Build & install** dependencies on a Linux server
2. **Provision TLS certificates** (development only: `python -m generate_selfsign_cert etc/ssl serverAuth`).
   This writes `server.cer`, `server_key.pem`, `cert_pwd` and `trust.cer` at the default TLS paths,
   with loopback SANs. Existing outputs are never overwritten; protect the plaintext password
   and keys (service-account ACLs on Windows). Keep signing material separate:
   `python -m generate_selfsign_cert etc/sign_cert dataSigning` matches the public `jwk_*` template.
3. **Configure** via `python -m agent_registry.init`
4. **Integrate** authentication, authorization, and database infrastructure from the host system
5. **Set minimal file permissions** (e.g., files `400`, directories `700`)

> **Warning:** This is an internal system module. Do not expose it to the public internet without additional firewall, WAF, and authentication layers.

### Docker Compose

Registry Center is one of three OpenAN components meant to run together
(`registry-center`, [`orchestration-center`](https://github.com/hw-irc-sni/orchestration-center),
[`prompt-registry`](https://github.com/hw-irc-sni/prompt-registry)), sharing
the `openan-net` Docker network so it's reachable from the other two by
container name (`openan-registry-center`).

**One-time setup** (once for all three components, not per-repo):
```bash
docker network create openan-net
```

**Prepare** (first run only, from the repository root):

1. Create the config files. `.env` and `models.yaml` are untracked; the
   container fails to start if `models.yaml` is missing:
   ```bash
   cp .env.example .env
   cp etc/config/models.yaml.example etc/config/models.yaml
   ```
2. Add two random secrets to `.env`. Without them the service exits at startup
   with `integration.auth.fingerprint_key is required` /
   `integration.auth.static.hmac_key is required`:
   ```bash
   echo "INTEGRATION_FINGERPRINT_KEY=$(openssl rand -hex 32)" >> .env
   echo "INTEGRATION_TOKEN_HMAC_KEY=$(openssl rand -hex 32)" >> .env
   ```
3. Add at least one caller credential to `etc/conf/integration_credentials.conf`
   (default `static_bearer` auth refuses to start with none). Store only the
   HMAC digest of a token, never the token itself. Generate the digest with
   `python -m agent_registry.integration.token_digest` (it prompts for the token
   and uses `INTEGRATION_TOKEN_HMAC_KEY`), then add:
   ```properties
   credential.dev.identity=dev-analytics
   credential.dev.token_hash=<64-hex-digest>
   credential.dev.role=analytics_tool
   ```
   Roles: `nms_oss`, `vendor_agent`, `partner_service`, `analytics_tool`. See the
   [Integration Guide](docs/en/Registry%20Center%20Integration%20Guide.md#static-bearer-token).
   Don't commit real digests.
4. Create the mounted directories owned by the container user (UID 10001).
   If Docker creates them first they are root-owned and the app crashes with
   `PermissionError` on `/opt/registry-center/log`
   (see `docs/container-deployment.md`):
   ```bash
   mkdir -p data log etc/ssl etc/sign_cert
   sudo chown -R 10001:10001 data log
   chmod 700 data log
   ```

**Run** — `docker-compose.yml` defaults to plain HTTP with token-based owner
identity (see `docs/transport-runtime.md`):
```bash
docker compose up -d --build
curl http://127.0.0.1:5000/health    # {"status":"ok"}
```

**HTTPS / mTLS** — opt in with `REGISTRY_ENABLE_HTTPS=true` (and
`REGISTRY_VERIFY_CLIENT=true` for client certificates) after provisioning
certs under `etc/ssl` (see the [Security Guide](docs/en/Registry%20Center%20Security%20Guide.md)).

Variables in `environment:` read from the host shell or `.env` first, falling
back to the default in the file — e.g. `REGISTRY_PORT=6000 docker compose up -d`.

> Keep dependent components on the same transport: if this service runs with
> HTTPS enabled, orchestration-center must be configured to call it over HTTPS.

## Constraints

- Single-instance deployment (not distributed)
- Linux production; Windows development/debugging only
- Max 100 registered agents (configurable via `agent.num.max`)
- 1 MB request body limit
- AgentCards must not contain personal data or sensitive credentials

Connections use `etc/conf/db/` profiles; passwords are named references to environment or `.env` secrets. See [Database configuration](docs/database-configuration.md). Legacy connection files are not runtime sources.

## License

This project is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE) for details.
