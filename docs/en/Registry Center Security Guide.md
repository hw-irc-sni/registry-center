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

# Registry Center Security Guide

### Transport, Caller Identity and Permissions

HTTP/HTTPS, incoming client certificates, caller Token authentication, owner authorization and
AgentCard JWS signatures are independent. Disabling peer-certificate verification does not disable authentication.

| Listener | Caller identity | Operation boundary |
| --- | --- | --- |
| Main `/rest/v1/registry-center/*` | `owner.identity.mode=token`: Bearer validated by the configured `integration.auth.*` provider | Ordinary roles discover public cards; `vendor_agent` additionally writes/heartbeats its own cards; `nms_oss` has administrative access. Existing owner guards still apply. |
| Main, certificate/proxy identity | Existing certificate or trusted proxy identity policy | Certificate identity requires HTTPS with client verification; only trust explicitly configured proxies. |
| Integration `/integration/*` | Independently configured Token/mTLS provider | Role/operation-specific policy; this is not the main API port. Token acquisition is available only when the integration API/provider supports it. |

Example main-port discovery (`REGISTRY_API_TOKEN` comes from your configured identity provider):

```bash
curl --cacert /secure/trust.cer \
  -H "Authorization: Bearer ${REGISTRY_API_TOKEN}" \
  https://registry.example/rest/v1/registry-center/agent-cards
```

401 means missing/invalid/expired credentials (or an independently failed card-signature check);
403 means verified identity without role/owner permission; 503 means required authentication,
signing or storage capability is unavailable. Provider failure does not become anonymous access.
This service is not the orchestration center's web-account login server.

The JWKS `/rest/v1/registry-center/keys` route is exempt from application Token authentication.
That exemption cannot bypass TLS: when `verify_client=true`, every HTTPS connection, including JWKS,
must present a valid client certificate. See [current transport details](../transport-runtime.md).



The Registry Center security capabilities are as follows:

- **Secure Communication**: Inter-system interactions use the HTTPS protocol by default, with secure TLS protocol versions and cipher suites.
- **Access Control**: Provides an authentication callback function for custom implementation. AgentCard operation isolation is enforced based on the agent owner, allowing only the agent owner to modify or delete their own AgentCard.
- **Storage Security**: Provides encryption/decryption callback functions for custom implementation to protect sensitive data; sensitive parameters in backend CLI commands use interactive input to prevent sensitive parameters from being logged by the system.
- **Audit Logging**: Default logging to a dedicated log file, recording six key elements of critical operations; also provides an audit log callback function for custom implementation.
- **AgentCard Content Security**: Identifies and blocks AgentCard registrations with malicious intent during the registration phase; validates AgentCard integrity by default and provides registry center signing; provides manual AgentCard review capability.
- **Certificate Generation Tool**: Provides a standalone tool for generating self-signed certificates for debugging scenarios.
- **Signature Verification Public Key Download**: Provides an interface to download the registry center's signature verification public key.

## Secure Communication (Inter-System TLS Communication)

The Registry Center provides REST interfaces such as AgentCard registration, modification, and deletion (refer to the [Registry Center User Guide "API Capabilities" section](https://github.com/project-openan/registry-center/blob/main/docs/en/Registry%20Center%20User%20Guide.md#api-capabilities)). These interfaces use the HTTPS protocol by default to ensure communication channel security, with secure TLS protocol versions and cipher suites.<br>
- TLS protocol versions: TLSv1.3, TLSv1.2<br>
- Cipher suites: TLS_AES_256_GCM_SHA384,TLS_AES_128_GCM_SHA256,TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,TLS_DHE_RSA_WITH_AES_256_GCM_SHA384,TLS_DHE_DSS_WITH_AES_256_GCM_SHA384,TLS_DHE_RSA_WITH_AES_128_GCM_SHA256,TLS_DHE_DSS_WITH_AES_128_GCM_SHA256<br>
> **TLS configuration**: `tls.cipher` in `server.properties` configures pre-TLS-1.3 suites on both listeners through CipherConverter/OpenSSL. TLS 1.3 suites are not controlled by `set_ciphers`; listing them does not configure them. Available suites also depend on OpenSSL and the certificate key type. `tls.version` is advisory only: no runtime consumer changes protocol bounds from that key. Validate an actual TLS handshake after changing policy.

The Registry Center's certificates must be prepared by the user in advance. Certificate requirements:<br>
- Identity certificate server.cer:
Required, only PEM encoding format supported<br>
Certificate format: X.509v3<br>
Certificate key algorithm, key length: RSA (>= 3072 bits), ECDSA (>= 256 bits)<br>
Validity period: valid at the current time<br>
Certificate key usage: digital signature, key encipherment<br>
Extended key usage: server authentication<br>

- Private key file server_key.pem:
Required, only PEM encoding format supported<br>
Private key and public key matching: must match the public key in server.cer<br>
The private key file must be protected by a private key password. The private key password must meet complexity requirements: at least 8 characters, containing at least two character types (digits, uppercase letters, lowercase letters, special characters `` `~!@#$%^&*()-_=+ | [{}]);:'",<.>/? `` and spaces)<br>

For debugging scenarios, the [Self-Signed Certificate Generation Tool](#self-signed-certificate-generation-tool) can be used to generate the two certificate files that meet the above requirements. Note that such certificates must not be used in production environments.<br>

- Trust certificate trust.cer:
Required by default, only PEM encoding format supported, only .cer files supported. If multiple certificates are involved, they must be merged into one.<br>
In scenarios where client certificate verification is enabled, this file must exist.<br>
Certificate format verification: X.509v3<br>
Validity period verification: valid at the current time<br>
Key algorithm, length: RSA (>= 3072 bits), ECDSA (>= 256 bits)<br>

- Certificate revocation list revocationlist.crl:
Optional, only PEM encoding format supported, only .crl files supported. If multiple certificates are involved, they must be merged into one. May not exist.<br>
Certificate format verification: X.509v2<br>
Validity period verification: valid at the current time<br>
National cipher (Guomi) certificates are not supported<br>

> **Note**: Client-certificate revocation is enforced only while `ssl_crl_file` is an ACTIVE (uncommented) line in `etc/conf/server.conf` and the referenced file exists; the line ships commented out, so revocation checking is off by default. The init wizard asks for this path under the name `ssl_cert_certs`, which writes a key that no runtime code reads; set `ssl_crl_file` directly instead.<br>

Minimize permissions for certificate files and their directories (e.g., file permissions 400/600, directory permissions 700), and ensure that the project process has read permission for the files<br>

Configure certificates for the Registry Center using the init command. This command configures relevant certificate paths and interactively inputs the private key password
```bash
# init command client starts and enters the interface
[user@host registry-center-main]# python -m agent_registry.init
Enable HTTPS (y/n, default: true):  y

Configure server TLS certificate (RSA only):
# Supports specifying relative and absolute paths. Relative paths are based on the project root path
Enter server certificate path ssl_certfile: (current: etc/ssl/server.cer): /testdir/test_server.cer
Enter server private key path ssl_keyfile: (current: etc/ssl/server_key.pem): /testdir/test_key.pem
Enter server trust certificate path ssl_ca_certs: (current: etc/ssl/trust.cer): /testdir/trust.cer
# Certificate revocation list, can be left blank
Enter server CRL file path ssl_cert_certs:
# The password is only required when the ssl_keyfile location is changed from the default
Enter server private key password:
# A prompt appears if the password does not meet complexity requirements
Private key password complexity is low (Include at least two character types), continue using this password? (y/n):  y

# Enable client certificate verification
Enable client certificate verification verify_client (y/n, default: true): y
```

This project only reads and uses these certificates and does not provide certificate management capabilities such as certificate expiration alerts, backup and recovery, etc.<br>
Configuration takes effect after restart<br>

The Registry Center also provides HTTP communication capability. HTTPS can be disabled and HTTP enabled through the initialization configuration command line
```bash
# init command client starts and enters the interface
[user@host registry-center-main]# python -m agent_registry.init
Enable HTTPS (y/n, default: true):  n
```
Configuration takes effect after restart<br>

## Access Control

### Authentication

Provides an authentication callback function for on-demand custom implementation, with no default authentication mechanism. For custom implementation, refer to the [Registry Center Development Guide "Custom Handler Usage" section](./Registry%20Center%20Development%20Guide.md#custom-handler-usage).

### AgentCard Operation Isolation

Set `owner.isolation.enabled=true` to enforce ownership. In the default
`owner.identity.mode=certificate` mode, the owner is the CN obtained from the
TLS connection's verified peer certificate. A client-supplied
`X-SSL-Client-DN` header does not establish identity. An explicitly configured
`trusted_proxy` mode may use a proxy-written header only when the actual TCP
peer belongs to `owner.trusted.proxy.ips`; the proxy must strip external identity
headers, and the application port must only be reachable through that proxy.

Registration requires a verified identity in `owner.validation.mode=strict`
(the default); `relaxed` keeps the pre-existing behaviour of accepting an
anonymous registration, which produces a card with no owner. In other words,
`relaxed` does not require an identity, so keep `strict` (or accept ownerless
cards deliberately) when isolation is enabled. Updates/deletes require the same
identity as the stored owner; missing/invalid identity returns 401, ownership
mismatch returns 403, and legacy cards with no owner require administrative
assignment before isolated writes are permitted. Heartbeat reporting is covered by
the same rule: a heartbeat claims that *this* card is alive (it drives health
hiding, the offline TTL and public health events), so with isolation enabled only
the stored owner may report one — a monitor that heartbeats on behalf of Agents
needs its own credential per owner, or must run with isolation disabled. Turning
listener HTTPS off does not implicitly disable ownership or AgentCard signature
verification.

If `owner.isolation.enabled=false`, ownership checks are disabled. Use this
only in controlled development or with a separately enforced authorization
policy. See the [upgrade notes](./Registry%20Center%20Upgrade%20Notes.md).

An isolation configuration that cannot verify callers still lets the service start
(it logs a warning and rejects ownership writes with 401). Set
`startup.strict.identity=true` to fail closed instead: startup aborts before any
port is bound when `verify_client=false` in `certificate` mode,
`owner.trusted.proxy.ips` is empty in `trusted_proxy` mode, or the mode is `none`.
```properties
# server.conf configuration file

# Whether to enable owner validation logic
owner.isolation.enabled=true

# Owner validation mode
# strict: Validate CN format; verified identity is always required
# relaxed: Skip CN-format validation; anonymous registration can produce ownerless cards
owner.validation.mode=strict
owner.identity.mode=certificate
# Used only in trusted_proxy mode; never use a wildcard
owner.trusted.proxy.ips=
# true: refuse to start when the identity configuration above cannot verify callers
startup.strict.identity=false
```

## Storage Security

1. Provides encryption/decryption callback functions for custom implementation to protect sensitive data. For custom implementation, refer to the [Registry Center Development Guide "Custom Handler Usage" section](./Registry%20Center%20Development%20Guide.md#custom-handler-usage).

2. Sensitive parameters in backend CLI commands use interactive input to prevent sensitive parameters from being logged by the system. For example, the private key password parameter in the self-signed certificate generation tool, the private key password parameter and database password parameter in the init command.<br>

## Audit Logging

Audit logs are recorded for critical operations and system state changes. The auditable critical operations are:<br>
- Registry Center initialization configuration
- Registry Center service start
- Registry Center service stop
- AgentCard registration
- AgentCard modification
- AgentCard deletion
- AgentCard review
- AgentCard tag setting
- Tag creation
- Tag modification
- Tag deletion

READ operations on the main port (card list/search `query`, get one card and `retrieve` semantic search) are audited only when `audit.read_operations=true` is set in `etc/conf/server.properties`; the default is `false`, so main-port reads are not audited and high-frequency query traffic stays out of the audit log. The integration port always audits its reads, regardless of this key.<br>

Six key elements of critical operations are recorded: time, client IP, user name, operation name, operation target, and operation result.<br>

There are two recording methods:
1. Default logging to a dedicated log file<br>
File path: log/audit/audit.log<br>
Maximum file size: 5 MB. When the limit is reached, existing log files are renamed in descending order by incrementing their numbers by 1, e.g., audit.log.1 -> audit.log.2, audit.log -> audit.log.1<br>
`audit_log_backup_count` is the total file count, including the active file. The shipped value `4` keeps audit.log plus audit.log.1–audit.log.3. With the key absent, the code fallback is 5 total files. Lower suffixes are newer; the oldest backup is removed during rotation.<br>
The file size and maximum file count are configurable. The configuration file path is etc/conf/log_config.conf, with the following configuration items:
```properties
audit_log_max_file_size_mb=5
audit_log_backup_count=4
```

The recording format is a JSON string, with examples as follows:<br>
- Service start operation:
```json
{"time": "2026-05-11T13:03:54Z", "clientIP": "", "userName": "root", "level": "Critical", "operationName": "Start Service", "object": "Service", "result": "Success", "details": {"ip": "10.244.183.56", "port": "1108"}}
```
- AgentCard registration operation:<br>
Success scenario:
```json
{"time": "2026-05-06T06:39:05Z", "clientIP": "127.0.0.1", "userName": "", "level": "General", "operationName": "Register Agent", "object": "Agent", "result": "Success", "details": {"agentName": "RAN Energy Saving Agent", "organization": "Huawei", "url": "https://www.huawei.com"}}
```
Failure scenario:
```json
{"time": "2026-05-12T01:43:26Z", "clientIP": "10.25.131.91", "userName": "", "level": "General", "operationName": "Register Agent", "object": "Agent", "result": "Failure", "details": {"agentName": "RAN Energy Saving Agent", "organization": "Huawei", "url": "https://www.huawei.com", "message": "Registration skipped: duplicate agent."}}
```
- AgentCard update operation:<br>
Success scenario:
```json
{"time": "2026-05-08T01:14:44Z", "clientIP": "127.0.0.1", "userName": "", "level": "General", "operationName": "Update Agent", "object": "Agent", "result": "Success", "details": {"name": "RAN Energy Saving Agent", "provider": {"organization": "Huawei", "url": "https://www.huawei.com"}, "description": "Responsible for autonomous closed-loop operation of RAN energy efficiency optimization, including intent exploration, intent implementation, effect evaluation and reporting.", "capabilities": {"streaming": true, "pushNotifications": false}, "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"], "version": "1.0.0", "skills": [{"id": "skill-1", "name": "TestSkill", "description": "Test Skill Description", "tags": ["test", "skill"], "input_modes": ["text/plain"], "output_modes": ["text/plain"]}], "supportedInterfaces": [{"protocolBinding": "GRPC", "protocolVersion": "1.0.0", "url": "http://127.0.0.1:5000/"}]}}
```
Failure scenario:
```json
{"time": "2026-05-08T00:44:48Z", "clientIP": "127.0.0.1", "userName": "", "level": "General", "operationName": "Update Agent", "object": "Agent", "result": "Failure", "details": {"agentName": "RAN Energy Saving Agent", "organization": "Huawei", "url": "https://www.huawei.com", "message": "Invalid agent data: Protocol message AgentCapabilities has no \"pushNotifications\" field."}}
```

- AgentCard deletion operation:
```json
{"time": "2026-05-06T06:45:08Z", "clientIP": "127.0.0.1", "userName": "", "level": "General", "operationName": "Deregister Agent", "object": "Agent", "result": "Failure", "details": {"agentName": "RAN Energy Saving Agent", "organization": "Huawei"}}
```


2. An audit log callback function is also provided for custom implementation.<br>
For custom implementation, refer to the [Registry Center Development Guide "Custom Handler Usage" section](./Registry%20Center%20Development%20Guide.md#custom-handler-usage).

### Archiving Audit Records to Customer MySQL

In addition to the local audit file, `audit.mysql.*` in `etc/conf/persistence.conf` archives third-party audit records asynchronously into the operator's MySQL database (table `integration_audit_records`):<br>
- Disabled by default (`audit.mysql.enabled=false`).<br>
- The local audit file remains the authoritative record; a failed archive only logs a warning and degrades to local-only without blocking business requests.<br>
- `REGISTRY_AUDIT_MYSQL_*` overrides target keys declared in the shipped `persistence.conf.example`, even if omitted or commented out in the deployment file; template values are not imported as defaults.<br>
- Connection credentials use `etc/conf/db/audit_mysql.json` and its `password_env` reference; legacy encrypted values require explicit migration.<br>

## AgentCard Content Security

AgentCard only supports Chinese and English. AgentCard must not carry sensitive/confidential data or personal information; otherwise, there is a risk of information leakage.<br>

### Blocking AgentCard Registration with Malicious Intent

In the AgentCard registration REST interface, the following fields in the AgentCard are subject to prompt injection validation and high-risk skill checks:<br>
- name
- description
- skill.name
- skill.description
- skill.tags

Registrations containing description keywords from the following blacklist will be rejected. This mechanism is enabled by default and cannot be disabled.<br>

> For the complete blacklist of prompt injection keywords and high-risk skill description keywords, see: [Appendix: Complete Blacklist](#appendix-complete-blacklist)


### AgentCard Integrity Validation

Checks whether the AgentCard has been tampered with based on the signature field in the AgentCard.<br>
Example of an AgentCard signature, as shown in the signatures field below:<br>
```json
{
  "name": "Energy Saving Agent",
  "description": "Responsible for autonomous closed-loop operation of energy efficiency optimization, including intent exploration, intent implementation, effect evaluation and reporting.",
  ...
  "signatures": [
    {
      "protected": "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpPU0UiLCJraWQiOiJrZXktMSIsImprdSI6Imh0dHBzOi8vZXhhbXBsZS5jb20vYWdlbnQvandrcy5qc29uIn0",
      "signature": "QFdkNLNszlGj3z3u0YQGt_T9LixY3qtdQpZmsTdDHDe3fXV9y9-B3m2-XgCpzuhiLt8E0tV6HXoZKHv4GtHgKQ"
    }
  ]
}
```
The protected field can be decoded to<br>
```json
{"alg":"ES256","typ":"JOSE","kid":"key-1","jku":"https://example.com/agent/jwks.json"}
```
The Registry Center performs signature verification according to the mechanism in https://a2a-protocol.org/latest/specification/#84-agent-card-signing<br>
As long as one signature verification passes, the integrity validation is considered successful.<br>

Signature algorithms supported: RS256, ES256<br>
The signature verification public key can be configured in the backend file: etc/sign_verify/jwks/{organization}/{agentname}.json<br>
The file content is in standard JWKS format, with an example as follows:<br>
```json
{
  "keys": [
    {
      "kty": "RSA",
      "use": "sig",
      "kid": "rsa-key-01",
      "alg": "RS256",
      "n": "0vx7agoebGcQSuu********JzKnqDKgw",
      "e": "AQAB"
    },
    {
      "kty": "EC",
      "use": "sig",
      "kid": "ec-key-01",
      "alg": "ES256",
      "crv": "P-256",
      "x": "MKBCTNIcKUS******KPAqv7D4",
      "y": "4Etl6SRW2Y******tmWWlbbM4IFyM"
    }
  ]
}
```

Alternatively, it can be carried in the jku field of the signature's protected field. The Registry Center will automatically call the interface via the GET method to query the signature public key for verification. Public keys obtained in this way are not cached.<br>

This integrity validation feature is enabled by default and can be disabled.<br>
Disabling method:<br>
init command<br>
```bash
# Start the Registry Center init configuration
[user@host registry-center-main]# python -m agent_registry.init
# Disable AgentCard signature verification
Enable signature validation (y/n, default: true): n
Signature validation disabled
```


### Providing Registry Center Signature

When `registry.sign.enabled=true` (default), the registry adds a standard JWS signature alongside
the original card signatures. Required signing material is separate from TLS identity:

```ini
registry.sign.enabled=true
jwk_cert_path=etc/sign_cert/sign.cer
jwk_private_key_path=etc/sign_cert/sign_key.pem
jwk_private_key_password=etc/sign_cert/cert_pwd
```

`jwk_private_key_password` is a passphrase **file path**, not the passphrase.
For an unencrypted private key, an empty setting/file is normalized to no password.
Use a matching PEM certificate/private key with supported signing algorithms; do not require
TLS `serverAuth` or reuse the HTTPS server key. Protect signing material with service-account permissions.

Missing/invalid material rejects startup while signing is enabled. If signing becomes unavailable at
runtime, affected requests fail with 503; cards are not silently emitted unsigned.
Disabling `registry.sign.enabled` is an explicit operator choice, not automatic degradation.

For development only: `python -m generate_selfsign_cert etc/sign_cert dataSigning` generates
`sign.cer`, encrypted `sign_key.pem` and `cert_pwd`. Production material comes from managed PKI.
The init wizard's historical `sign_certfile/sign_keyfile/sign_keyfile_password` settings are inert;
configure the actual `jwk_*` paths above. JWKS publishes that certificate's public key at
`GET /rest/v1/registry-center/keys`; see the TLS limitation in the caller-identity section.

### AgentCard Manual Review

The Registry Center provides a manual review capability, allowing administrators to review registered Agents from dimensions such as legal and data compliance, business logic and quality, resources and cost.<br>
Disabled by default. Enabling method:<br>
init command line<br>
```bash
# Start the Registry Center init configuration
python -m agent_registry.init
# Configure whether to enable the review switch
Enable agent approval (y/n, default: false): y
Approval function enabled
```
The modification takes effect after restart. If there are agents pending review, the agents must be approved or deleted before the review feature can be disabled.<br>

Agent states include registered and published. In the AgentCard query and semantic search interfaces, only AgentCards in the published state can be queried.<br>
When this feature is not enabled, registered Agents are in the published state by default.<br>
When the review feature is enabled, registered Agents are in the registered state by default, and administrators can query all agent information (including agents whose state is not published) via the CLI command line.<br>
Administrators can further query AgentCards in the registered state via the CLI command line for manual review.<br>
After the administrator approves the agent, they can execute the CLI command to approve the agent, and the agent's state will change to published.<br>
For CLI command line usage, refer to the [Registry Center User Guide "Management Capabilities (CLI)" section](./Registry%20Center%20User%20Guide.md#management-capabilities-cli), which includes operations such as Agent management and tag management.

## Change Broadcast Security

When the change broadcast capability is enabled (`broadcast.enabled=true`), the Registry Center pushes registry change events to subscriber callback URLs. The following security mechanisms apply:

### Callback URL SSRF Protection

Subscription callback URLs are HTTPS-only by default; HTTP can be allowed only in development environments via `broadcast.allow.http.callbacks=true`. `broadcast.callback.allowlist` (comma-separated domain names) is **mandatory**: it must be non-empty before subscriptions can be created, and every delivery re-validates the destination against it, preventing the Registry Center from being tricked into sending requests to internal network addresses. Leaving it empty fails closed — creating a subscription returns 422 and no delivery is attempted.

### Webhook Message Signature Verification

It is recommended to provide a `secret` when creating a subscription. When pushing events, the Registry Center attaches the following headers:

| Header | Description |
|--------|------|
| X-Registry-Signature | Signature value in the format `sha256={hex}`, computed as `HMAC-SHA256(secret, "{X-Registry-Timestamp}.{request body}")` |
| X-Registry-Timestamp | Delivery initiation timestamp (seconds). Subscribers should reject requests whose timestamp differs by more than 5 minutes to prevent replay |
| X-Registry-Event-Id | ID of the first event in the batch, for troubleshooting |

Subscribers must verify both the signature and the timestamp window, and reject events that fail verification. Events of subscriptions without a `secret` are unsigned and should only be used in trusted internal networks.

### Subscription Credential Management

Treat the subscription `secret` as a credential: database access permissions should be governed the same way as the primary storage; subscription management APIs (create/query/delete) are for administrators only, and the query API response never echoes the secret.

## Self-Signed Certificate Generation Tool

Development/debugging only. Production certificates must come from a trusted CA/enterprise PKI;
this helper is not a production certificate manager. Run from the repository root; output paths
are relative to the current working directory. Enter the password interactively, never as a CLI argument.

```bash
# TLS: matches the four default server.conf paths
python -m generate_selfsign_cert etc/ssl serverAuth
# Explicit SANs replace the entire default set
python -m generate_selfsign_cert etc/ssl-new serverAuth --dns registry.example.test --ip 192.0.2.10
# mTLS: issue a separate client certificate from existing development server material
python -m generate_selfsign_cert etc/ssl serverAuth --issue-client demo
# Independent signing material: matches server.conf.example jwk_* settings
python -m generate_selfsign_cert etc/sign_cert dataSigning
```

TLS exports `server.cer`, `server_key.pem`, `cert_pwd` and `trust.cer`.
Default SANs are `localhost`, `127.0.0.1` and `::1`; the trust anchor is a copy of
the self-signed server certificate. This development-only server profile has local CA capability
(CA:TRUE, keyCertSign and serverAuth/clientAuth) to issue development client certificates.
Client issuance exports `demo-client.cer` and unencrypted `demo-client.key`.
Configure those in the calling client and trust `trust.cer`. The tool never changes `verify_client`.
For nginx's unencrypted server key, select `--plain-key` on initial generation into a new directory.

dataSigning exports `sign.cer`, `sign_key.pem` and `cert_pwd`, without SANs or CA capability.
Use separate directories and key pairs for signing and TLS. The runtime uses these actual keys:

```ini
jwk_cert_path=etc/sign_cert/sign.cer
jwk_private_key_path=etc/sign_cert/sign_key.pem
jwk_private_key_password=etc/sign_cert/cert_pwd
```

Both profiles retain `server_RSA.cer` and `server_key_RSA.pem` for compatibility.
Keys are RSA 3072; the development certificate lifetime is 99 years, not a production lifetime policy.
`cert_pwd` contains the plaintext password without a trailing newline, readable by both the TLS loader
and AgentCardSigner. Storing a password beside its encrypted key provides no additional at-rest
protection: protect the entire directory as a credential. New directories/files use POSIX 700/600;
on Windows restrict access using service-account ACLs. Never commit certificates, keys or passwords.

Any existing output file, directory or symlink prevents overwrite, including deployment copies,
passwords, optional plaintext keys and client material. For rotation, generate into a new directory,
reissue clients, redistribute trust and deploy matching certificate/key/password files.
For non-default directories explicitly configure every `ssl_*` or `jwk_*` path.
Existing `server.conf` is never rewritten; update old signing settings manually to actual file paths.

## Appendix: Complete Blacklist

**Prompt Injection Keyword Blacklist:**

| Category | English Keywords |
|------|-----------|
| Instruction Override (14 items) | ignore previous instructions, ignore previous commands<br>ignore all instructions, ignore all commands<br>ignore above instructions, disregard instructions<br>ignore limits, ignore restrictions<br>ignore rules, ignore constraints<br>ignore security<br>override instructions, overwrite instructions<br>override rules, override system<br>forget previous, forget all, forget instructions |
| System Attack (8 items) | jailbreak, crack, bypass<br>break limits, break restrictions<br>developer mode, admin mode, administrator mode<br>superuser, prompt injection |
| Forced Execution (9 items) | must execute, must output, must answer<br>no matter what, regardless<br>must, definitely<br>unconditional execution, force execute, forced execution<br>execute immediately |
| Special Markers (12 items) | encoding bypass<br>\</system\>, \</instruction\>, \</prompt\><br>[END], [DONE], [FINISHED]<br>assistant:, system:, user: |

**High-Risk Skill Description Keyword Blacklist:**

| Category | English Keywords |
|------|-----------|
| Privilege Attack (12 items) | privilege escalation, escalate privileges<br>elevate privileges, raise privileges<br>gain privileges, obtain privileges<br>bypass security, bypass protection<br>bypass authentication, bypass verification<br>break security, break protection<br>security bypass<br>illegal admin privileges, unauthorized admin privileges<br>illegal superuser privileges, illegal root privileges |
| Database Attack (2 items) | database injection, SQL injection |
| Data Theft (11 items) | steal keys, steal secret keys<br>steal passwords, steal credentials<br>illegally obtain keys, illegally obtain passwords<br>illegally obtain credentials<br>steal data, data exfiltration<br>data leak, steal privacy<br>steal private data, illegally obtain privacy |
| Network Attack (6 items) | network attack, network penetration<br>network intrusion, port scan<br>vulnerability scan, attack scan |
| Command Execution (7 items) | execute code, execute command<br>run command, execute shell<br>remote execution, code execution, command execution |


数据库连接规范已更新 / Connection configuration now uses [etc/conf/db profiles](../database-configuration.md). `persistence.conf` retains the selector and audit policies only; connection settings live only in the etc/conf/db profiles.
