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

# 注册中心API参考

## 使用前必读

### 简介

  注册中心是一个专注于Agent统一管理的服务，主要开放提供以下接口:
  - **注册AgentCard**：支持将不同厂商的Agent注册到中心，统一纳管。
  - **查询AgentCard列表**：根据指定条件查询符合条件的AgentCard列表。
  - **查询指定AgentCard**：按AgentCard名称和组织精确查找唯一的AgentCard实例。
  - **更新指定AgentCard**：更新指定AgentCard的信息。
  - **删除指定AgentCard**：删除不再使用的AgentCard。
  - **按语义检索AgentCard**：根据自然语言语义检索相匹配的AgentCard。
  - **公钥管理**：提供注册中心签名公钥的获取接口。
  - **Agent心跳上报**：Agent周期性上报存活状态，注册中心据此维护健康状态。
  - **Agent健康状态查询**：查询心跳监控的Agent健康状态列表。
  - **变更订阅管理**：创建、查询、删除注册中心变更广播订阅。
  - **变更对账查询**：按版本号拉取注册中心变更事件，用于订阅方补齐数据。

### 约束与限制

  具体内容请参见各接口的接口约束。

  AgentCard 登记/更新的 `agentCards` 必须为非空对象列表，每项符合 AgentCard
  schema。PUT 中每张 Card 的 `name`、`provider.organization` 必须与 URL 一致，
  不承担身份重命名，也不会改写已签名正文。不合法条目返回 422；此前条目已成功
  时，`registeredAgents` / `updatedAgents` 保留部分成功记录，整批并非全有或全无事务。

### 传输、调用者身份与权限

HTTP/HTTPS、入站客户端证书、调用者 Token、所有者授权与 AgentCard JWS 签名是独立职责。
关闭对端证书校验不等于关闭认证。

| 监听器 | 调用者身份 | 操作边界 |
| --- | --- | --- |
| 主端口 `/rest/v1/registry-center/*` | `owner.identity.mode=token`：由 `integration.auth.*` provider 校验 Bearer | 普通角色发现公开卡片；`vendor_agent` 还可写入/心跳自己的卡片；`nms_oss` 可管理。原有所有者守卫仍执行。 |
| 主端口证书/代理模式 | 现有证书或受信代理身份策略 | 证书身份要求 HTTPS 与客户端校验；代理必须显式受信。 |
| 集成端口 `/integration/*` | 独立配置的 Token/mTLS provider | 按角色和操作授权，不是主端口；Token 获取仅在集成 API/provider 支持时提供。 |

主端口发现示例（`REGISTRY_API_TOKEN` 由已配置的身份系统提供）：

```bash
curl --cacert /secure/trust.cer \
  -H "Authorization: Bearer ${REGISTRY_API_TOKEN}" \
  https://registry.example/rest/v1/registry-center/agent-cards
```

401 表示缺失/无效/过期凭据，也可能是独立的卡片验签失败；403 表示已认证但角色/所有权不允许；
503 表示认证、签名或存储等必要能力不可用。provider 故障不会放行匿名请求。
注册中心不是编排中心的网页登录账号服务。

JWKS `/rest/v1/registry-center/keys` 免应用 Token 认证，但不绕过 TLS：
`verify_client=true` 时所有 HTTPS 连接（含 JWKS）仍须客户端证书。
详见[当前传输契约](../transport-runtime.md)。

## 注册AgentCard

- 典型场景

    运营商或设备厂商需要注册新AgentCard时，可通过调用该接口在注册中心组件中创建新的AgentCard信息。

- 功能描述

    注册指定AgentCard信息到注册中心。

- 接口约束

  - 单次注册AgentCard信息报文大小不得超过1024K。
  - 单实例上该接口最大并发数为50。

- 调用方法

    POST

- URI

    */rest/v1/registry-center/agent-cards*

- 请求参数

  <a id="表1-body参数列表"></a>**表1** body参数列表

    | 参数名称       | 是否必选 | 类型              | 值域                                            | 默认值 | 描述 |
    |------------|------|-----------------|-----------------------------------------------|-----|------|
    | agentCards | 是    | array_reference | 支持单卡或批量注册，必须为非空列表。批量注册按顺序逐卡处理，任一卡失败即中止（fail-fast），已成功注册的卡片通过错误响应的registeredAgents字段返回。详细请参见[表2](#表2-agentcard对象的参数列表)。 | -   | -    |

    `agentCards`中的每个元素支持两种格式。原有格式直接填写AgentCard对象；需要声明层级时，使用下面的包装格式：

    ```json
    {
      "agentCard": { "name": "...", "provider": { "organization": "..." } },
      "layer": "vendor-defined-layer"
    }
    ```

    `layer`为可选字符串，最长64个字符，服务端会去除首尾空白后保存。未提供时按`unknown`保存。原有直接填写AgentCard的格式继续有效，注册响应也保持原有结构。

  <a id="表2-agentcard对象的参数列表"></a>**表2** AgentCard对象的参数列表
    
    | 参数名称                | 是否必选 | 类型              | 值域                                                                 | 默认值 | 描述         |
    |---------------------|------|-----------------|--------------------------------------------------------------------|-----|--------------|
    | name                | 是    | string          | 1~100个字符。满足正则表达式`^[a-zA-Z0-9_-]+(?:\s+[a-zA-Z0-9_-]+)*$`。            | -   | AgentCard名称。  |
    | description         | 是    | string          | 1~1000个字符。                                                         | -   | AgentCard描述。  |
    | version             | 是    | string          | 1~50个字符。                                                           | -   | AgentCard版本。  |
    | provider            | 是    | reference       | 详细请参见[表4](#表4-agentprovider对象的参数列表)。                                       | -   | 提供商信息。        |
    | skills              | 是    | array_reference | 最大数量：100 个技能；每个技能的 JSON 序列化后最大长度：4096 字符；详细请参见[表7](#表7-agentskill对象的参数列表)。 | -   | 技能列表。         |
    | capabilities        | 是    | reference       | 详细请参见[表5](#表5-agentcapabilities对象的参数列表)。                                   | -   | AgentCard能力项。 |
    | supportedInterfaces | 是    | array_reference | 1~3个列表，详细请参见[表3](#表3-agentinterface对象的参数列表)。                               | -   | 支持的协议。        |
    | signatures          | 否    | array            | 签名列表，用于AgentCard完整性校验。                                             | -   | 数字签名。          |
    | defaultInputModes   | 否    | array of string  | 支持的默认输入媒体类型列表（MIME 类型）。                                           | -   | 默认输入模式。         |
    | defaultOutputModes  | 否    | array of string  | 支持的默认输出媒体类型列表（MIME 类型）。                                           | -   | 默认输出模式。         |

  <a id="表3-agentinterface对象的参数列表"></a>**表3** AgentInterface对象的参数列表

    | 参数名称            | 是否必选 | 类型     | 值域                        | 默认值 | 描述                        |
    |:----------------|:-----|:-------|:----------------------------|:----|:----------------------------|
    | url             | 是    | string | 1~1024个字符；必须为有效的 Web URL 格式。 | -   | 接口的基础 URL 地址，用于发送 A2A 请求。    |
    | protocolBinding | 是    | string | -                           | -   | 协议绑定标识，表示该接口使用的传输协议。         |
    | protocolVersion | 是    | string | -                           | -   | A2A 协议版本号，表示该接口支持的 A2A 协议版本。 |

  <a id="表4-agentprovider对象的参数列表"></a>**表4** AgentProvider对象的参数列表

    | 参数名称         | 是否必选 | 类型     | 值域                     | 默认值 | 描述                                 |
    |:-------------|:-----|:-------|:-------------------------|:----|:-------------------------------------|
    | organization | 是    | string | 1~100个字符；不能为空。           | -   | Agent 提供商的组织名称，用于标识 Agent 的来源组织或开发团队。 |
    | url          | 否    | string | 1~1024个字符；必须为有效的 Web URL。 | -   | Agent 提供商的官方网站链接，便于用户了解组织背景或获取技术支持。   |

  <a id="表5-agentcapabilities对象的参数列表"></a>**表5** AgentCapabilities对象的参数列表

    | 参数名称              | 是否必选 | 类型              | 值域                                                                | 默认值   | 描述                                                                                 |
    |:------------------|:-----|:----------------|:--------------------------------------------------------------------|:------|:-------------------------------------------------------------------------------------|
    | streaming         | 否    | boolean         | true 或 false。                                                        | false | 是否支持流式传输。如果为 true，Agent 能够通过 Server-Sent Events (SSE) 实时返回响应内容；如果为 false，则仅支持一次性同步响应。 |
    | pushNotifications | 否    | boolean         | true 或 false。                                                        | false | 是否支持推送通知。如果为 true，Agent 能够主动向客户端发送任务状态更新和产物通知；需要配合 PushNotificationConfig 进行配置。       |
    | extendedAgentCard | 否    | boolean         | true 或 false。                                                        | false | 声明 Agent 是否支持经过认证后提供扩展版的 Agent Card。                                                  |
    | extensions        | 否    | array_reference | 最大数量：10 个扩展；每个扩展的 JSON 序列化后最大长度：512 字符；详细请参见[表6](#表6-agentextension对象的参数列表)。 | -     | 支持的扩展能力列表，用于声明 Agent 实现的 A2A 协议扩展特性。                                                  |

  <a id="表6-agentextension对象的参数列表"></a>**表6** AgentExtension对象的参数列表

    | 参数名称        | 是否必选 | 类型      | 值域         | 默认值   | 描述                                                   |
    |:------------|:-----|:--------|:-------------|:------|:-------------------------------------------------------|
    | uri         | 是    | string  | -            | -     | 扩展的唯一标识符，必须是版本化的 URI 格式，用于全局唯一标识该扩展。                    |
    | description | 否    | string  | -            | -     | 扩展功能的人类可读说明，描述 Agent 如何使用此扩展。                           |
    | required    | 否    | boolean | true 或 false。 | false | 表示客户端是否必须支持此扩展。如果为 true，客户端必须理解并激活该扩展，否则 Agent 将拒绝处理请求。 |
    | params      | 否    | object  | -            | -     | 扩展的特定配置参数或发现参数，用于传递扩展初始化所需的额外信息。                        |

  <a id="表7-agentskill对象的参数列表"></a>**表7** AgentSkill对象的参数列表

    | 参数名称        | 是否必选 | 类型              | 值域 | 默认值 | 描述                           |
    |:------------|:-----|:----------------|:-----|:----|:-------------------------------|
    | id          | 是    | string          | -    | -   | 技能的唯一标识符，用于在 AgentCard 中区分不同技能。 |
    | name        | 是    | string          | -    | -   | 技能的人类可读名称，展示给最终用户的技能标题。         |
    | description | 是    | string          | -    | -   | 技能的详细功能说明，帮助客户理解该技能的具体作用和适用场景。  |
    | tags        | 否    | array of string | -    | -   | 用于分类和发现的关键词标签，便于客户端按类别检索和匹配技能。  |
    | inputModes | 否    | array of string | -    | -   | 支持的输入媒体类型列表（MIME 类型）。           |
    | outputModes | 否    | array of string | -    | -   | 支持的输出媒体类型列表（MIME 类型）。           |

- 请求示例

    ```http
    POST /rest/v1/registry-center/agent-cards HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json
    {
      "agentCards": [
        {
          "name": "RAN Energy Saving Agent",
          "description": "负责RAN能效优化的自主闭环运行，包括意图探索、意图实现、效果评估与报告。",
          "version": "1.0.0",
          "provider": {
            "organization": "Org",
            "url": "https://example.com"
          },
          "skills": [
            {
              "id": "ran-es-intent-exploration",
              "name": "RAN ES Intent Exploration",
              "description": "评估并确定指定RAN ES意图目标的最佳可能性，考虑当前资源状况和系统能力。",
              "tags": [
                "wireless",
                "energy-saving",
                "intent"
              ]
            },
            {
              "id": "ran-es-intent-lifecycle-management",
              "name": "RAN ES Intent Lifecycle Management",
              "description": "管理RAN节能意图的生命周期，包括创建、修改、删除、激活、去激活意图，并执行数据采集、分析、解决方案制定与配置。",
              "tags": [
                "wireless",
                "energy-saving",
                "intent"
              ]
            },
            {
              "id": "ran-es-intent-reporting",
              "name": "RAN ES Intent Reporting",
              "description": "提供意图报告查询、订阅、通知功能，报告意图实现状态、达成值、推荐值及配置修改信息。",
              "tags": [
                "wireless",
                "energy-saving",
                "reporting"
              ]
            }
          ],
          "capabilities": {
            "streaming": true,
            "pushNotifications": false,
            "extensions": []
          },
          "defaultInputModes": [
            "text",
            "json"
          ],
          "defaultOutputModes": [
            "text",
            "json"
          ],
          "supportedInterfaces": [
            {
              "protocolBinding": "GRPC",
              "protocolVersion": "1.0.0",
              "url": "http://127.0.0.1:5000/"
            },
            {
              "protocolBinding": "HTTP+JSON",
              "protocolVersion": "1.0.0",
              "url": "http://127.0.0.1:5000/"
            }
          ]
        }
      ]
    }
    ```

- 响应参数

    注册成功返回results数组，每张卡一个条目，顺序与请求agentCards一致。

    | 参数名称 | 类型 | 描述 |
    |------|----|------|
    | results[].name           | string  | AgentCard名称。 |
    | results[].organization   | string  | 所属组织机构。 |
    | results[].status         | string  | 注册后的状态：published（可被检索）或registered（待审核，审核开关agent_approval_enabled=true时）。 |
    | results[].registrySigned | boolean | 注册中心是否已对该卡片会签。 |

- 响应样例

    注册成功：

    ```json
    HTTP/1.1 201 Created
    {
      "results": [
        {
          "name": "RAN Energy Saving Agent",
          "organization": "Org",
          "status": "published",
          "registrySigned": false
        }
      ]
    }
    ```

    批量注册任一卡失败时按fail-fast中止并返回错误，错误响应额外携带registeredAgents字段，列出本请求中已成功注册的卡片，便于客户端确定重试范围：

    ```json
    {
      "errors": {"error": [{"errorMessage": "Card 2/2 (Bad Agent!): ..."}]},
      "registeredAgents": [
        {"name": "RAN Energy Saving Agent", "organization": "Org", "status": "published", "registrySigned": false}
      ]
    }
    ```

- 状态码

| 状态码 | 说明                    |
|--------|-----------------------|
| 201 | 注册成功。                 |
| 401 | 调用者认证或卡片签名验证失败。               |
| 413 | 请求体过大。                |
| 422 | 注册失败，AgentCard参数校验失败。 |
| 409 | 注册失败，注册数量超出上限或重复注册。   |
| 503 | 服务繁忙。                 |

## 查询AgentCard列表

- 典型场景

    当用户需要查询Agent信息时，可以通过该接口来返回Agent列表。

- 功能描述

  - 根据Agent名称进行模糊匹配（子串）查询，根据组织机构进行精确匹配查询。
  - 支持多条件组合查询（AND逻辑）。
  - 所有查询参数均为可选，不提供任何参数时返回所有已注册的Agent列表。

- 接口约束

  - 当不提供任何参数时，返回系统中所有已注册的Agent列表（默认注册上限为100个）。
  - 提供参数时，按实际情况返回，最少返回0个。
  - 单实例上该接口最大并发数为100。

- 调用方法

    GET

- URI

    */rest/v1/registry-center/agent-cards*

- 请求参数

  <a id="表8-query参数列表"></a>**表8** query参数列表

    | 参数名          | 类型     | 必填 | 默认值 | 描述                           |
    |--------------|--------|----|-----|------------------------------|
    | name         | string | 否  | -   | Agent 名称，进行模糊匹配（子串）查询。不区分大小写。 |
    | organization | string | 否  | -   | 组织机构名称，进行精确匹配查询。支持大小写敏感匹配。   |
    | layer        | string | 否  | -   | 按注册层级精确匹配。层级为非空字符串，最长64个字符，大小写按原值匹配。 |

- 请求示例

  - 查询所有Agent

    ```http
    GET /rest/v1/registry-center/agent-cards HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json
    ```

  - 按名称查询

    ```http
    GET /rest/v1/registry-center/agent-cards?name=RAN%20Energy%20Saving%20Agent HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json
    ```

  - 按组织机构精确查询

    ```http
    GET /rest/v1/registry-center/agent-cards?organization=Org HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json
    ```

  - 组合条件查询（AND）

    ```http
    GET /rest/v1/registry-center/agent-cards?name=RAN%20Energy%20Saving%20Agent&organization=Org HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json
    ```

  - 按层级查询

    ```http
    GET /rest/v1/registry-center/agent-cards?layer=OMC HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json
    ```

- 响应参数

    <a id="表9-响应参数列表"></a>**表9** 响应参数列表
    
    | 参数名称 | 类型              | 值域 | 默认值 | 描述         |
    |------|-----------------|----|-----|--------------|
    | agentCards | array_reference | -  | -   | 符合要求的Agent列表，详细请参见[表2](#表2-agentcard对象的参数列表)。 |

- 响应样例

    ```json
    {
      "agentCards": [
        {
          "name": "RAN Energy Saving Agent",
          "description": "负责RAN能效优化的自主闭环运行，包括意图探索、意图实现、效果评估与报告。",
          "version": "1.0.0",
          "provider": {
            "organization": "Org",
            "url": ""
          },
          "skills": [
            {
              "id": "ran-es-intent-exploration",
              "name": "RAN ES Intent Exploration",
              "description": "评估并确定指定RAN ES意图目标的最佳可能性，考虑当前资源状况和系统能力。",
              "tags": [
                "wireless",
                "energy-saving",
                "intent"
              ]
            },
            {
              "id": "ran-es-intent-lifecycle-management",
              "name": "RAN ES Intent Lifecycle Management",
              "description": "管理RAN节能意图的生命周期，包括创建、修改、删除、激活、去激活意图，并执行数据采集、分析、解决方案制定与配置。",
              "tags": [
                "wireless",
                "energy-saving",
                "intent"
              ]
            },
            {
              "id": "ran-es-intent-reporting",
              "name": "RAN ES Intent Reporting",
              "description": "提供意图报告查询、订阅、通知功能，报告意图实现状态、达成值、推荐值及配置修改信息。",
              "tags": [
                "wireless",
                "energy-saving",
                "reporting"
              ]
            }
          ],
          "capabilities": {
            "streaming": true,
            "pushNotifications": false,
            "extensions": []
          },
          "defaultInputModes": [
            "text",
            "json"
          ],
          "defaultOutputModes": [
            "text",
            "json"
          ],
          "supportedInterfaces": [
            {
              "protocolBinding": "GRPC",
              "protocolVersion": "1.0.0",
              "url": "http://127.0.0.1:5000/"
            },
            {
              "protocolBinding": "HTTP+JSON",
              "protocolVersion": "1.0.0",
              "url": "http://127.0.0.1:5000/"
            }
          ]
        }
      ]
    }
    ```

- 状态码

  | 状态码 | 说明    |
  |--------|-------|
  | 200 | 查询成功（无匹配Agent时返回空agentCards列表）。 |
  | 500 | 查询失败，服务内部错误。 |
  | 503 | 服务繁忙。 |

## 查询指定AgentCard

- 典型场景

    根据用户输入的name和organization参数，精准查询name和organization对应的Agent。

- 功能描述

    根据Agent的name和organization的唯一组合，精确查询并返回单个Agent的完整详细信息。仅返回已发布（published）状态的Agent；Agent不存在或存在但未发布时均返回200和空agentCards列表，两者不做区分。

- 接口约束

    单实例上该接口最大并发数为100。

- 调用方法

    GET

- URI

    */rest/v1/registry-center/agent-cards/{organization}/{name}*

- 请求参数

  <a id="表10-path参数列表"></a>**表10** path参数列表

    | 参数名          | 类型     | 必填 | 描述                                          |
    |--------------|--------|----|---------------------------------------------|
    | organization | string | 是  | Agent所属的组织机构名称，作为路径参数传递。与name共同唯一标识一个Agent。 |
    | name         | string | 是  | Agent名称，作为路径参数传递。用于指定要查询的Agent。        |

- 请求示例

  ```json
  GET /rest/v1/registry-center/agent-cards/Org/RAN%20Energy%20Saving%20Agent HTTP/1.1
  Host: your-domain.com
  Content-Type: application/json
  ```

- 响应参数

    <a id="表11-响应参数列表"></a>**表11** 响应参数列表
    
    | 参数名称 | 类型    | 值域 | 默认值 | 描述           |
    |------|-------|----|-----|--------------|
    | agentCards    | array_reference | -  | -   | 符合要求的Agent列表，详细请参见[表2](#表2-agentcard对象的参数列表)。 |

- 响应样例

   ```json
   {
     "agentCards": [
       {
         "name": "RAN Energy Saving Agent",
        "description": "负责RAN能效优化的自主闭环运行，包括意图探索、意图实现、效果评估与报告。",
        "version": "1.0.0",
        "provider": {
          "organization": "Org",
          "url": ""
        },
        "skills": [
          {
            "id": "ran-es-intent-exploration",
            "name": "RAN ES Intent Exploration",
            "description": "评估并确定指定RAN ES意图目标的最佳可能性，考虑当前资源状况和系统能力。",
            "tags": [
              "wireless",
              "energy-saving",
              "intent"
            ]
          },
          {
            "id": "ran-es-intent-lifecycle-management",
            "name": "RAN ES Intent Lifecycle Management",
            "description": "管理RAN节能意图的生命周期，包括创建、修改、删除、激活、去激活意图，并执行数据采集、分析、解决方案制定与配置。",
            "tags": [
              "wireless",
              "energy-saving",
              "intent"
            ]
          },
          {
            "id": "ran-es-intent-reporting",
            "name": "RAN ES Intent Reporting",
            "description": "提供意图报告查询、订阅、通知功能，报告意图实现状态、达成值、推荐值及配置修改信息。",
            "tags": [
              "wireless",
              "energy-saving",
              "reporting"
            ]
          }
        ],
        "capabilities": {
          "streaming": true,
          "pushNotifications": false,
          "extensions": []
        },
        "defaultInputModes": [
          "text",
          "json"
        ],
        "defaultOutputModes": [
          "text",
          "json"
        ],
        "supportedInterfaces": [
          {
            "protocolBinding": "GRPC",
            "protocolVersion": "1.0.0",
            "url": "http://127.0.0.1:5000/"
          },
          {
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0.0",
            "url": "http://127.0.0.1:5000/"
          }
        ]
      }
    ]
  }
  ```

- 状态码

  | 状态码 | 说明             |
  |--------|----------------|
  | 200 | 查询成功（Agent不存在或未发布时返回空agentCards列表）。 |
  | 500 | 查询失败，服务内部错误。        |
  | 503 | 服务繁忙。 |

## 更新指定AgentCard

- 典型场景

    已注册的Agent的信息如果有变更，则需要用户调用该接口来进行Agent信息的更新。

- 功能描述

    完全替换一个已存在的Agent。该接口使用请求体中的完整AgentCard数据替换现有Agent的全部信息。请求体中的名称和组织机构必须与路径参数和查询参数匹配。请求体agentCards必须为非空列表，为空时返回422。

- 接口约束

    单实例上该接口最大并发数为100。

- 调用方法

    PUT

- URI

    */rest/v1/registry-center/agent-cards/{organization}/{name}*

- 请求参数

  <a id="表12-path参数列表"></a>**表12** path参数列表

    | 参数名          | 类型     | 必填 | 描述                                                  |
    |--------------|--------|----|-----------------------------------------------------|
    | name         | string | 是  | 待更新的Agent名称，作为路径参数传递。该值必须与请求体中的name字段匹配。            |
    | organization | string | 是  | 待更新Agent的组织机构名称。该值必须与请求体中provider.organization字段匹配。 |

- 请求示例

  ```json
  PUT /rest/v1/registry-center/agent-cards/Org/RAN%20Energy%20Saving%20Agent HTTP/1.1
  Host: your-domain.com
  Content-Type: application/json
  {
    "agentCards": [
      {
        "name": "RAN Energy Saving Agent",
        "description": "负责RAN能效优化的自主闭环运行，包括意图探索、意图实现、效果评估与报告。",
        "version": "1.0.0",
        "provider": {
          "organization": "Org",
          "url": ""
        },
        "skills": [
          {
            "id": "ran-es-intent-exploration",
            "name": "RAN ES Intent Exploration",
            "description": "评估并确定指定RAN ES意图目标的最佳可能性，考虑当前资源状况和系统能力。",
            "tags": [
              "wireless",
              "energy-saving",
              "intent"
            ]
          },
          {
            "id": "ran-es-intent-lifecycle-management",
            "name": "RAN ES Intent Lifecycle Management",
            "description": "管理RAN节能意图的生命周期，包括创建、修改、删除、激活、去激活意图，并执行数据采集、分析、解决方案制定与配置。",
            "tags": [
              "wireless",
              "energy-saving",
              "intent"
            ]
          },
          {
            "id": "ran-es-intent-reporting",
            "name": "RAN ES Intent Reporting",
            "description": "提供意图报告查询、订阅、通知功能，报告意图实现状态、达成值、推荐值及配置修改信息。",
            "tags": [
              "wireless",
              "energy-saving",
              "reporting"
            ]
          }
        ],
        "capabilities": {
          "streaming": true,
          "pushNotifications": false,
          "extensions": []
        },
        "defaultInputModes": [
          "text",
          "json"
        ],
        "defaultOutputModes": [
          "text",
          "json"
        ],
        "supportedInterfaces": [
          {
            "protocolBinding": "GRPC",
            "protocolVersion": "1.0.0",
            "url": "http://127.0.0.1:5000/"
          },
          {
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0.0",
            "url": "http://127.0.0.1:5000/"
          }
        ]
     }
    ]
  }
  ```

- 响应参数

    修改成功返回results数组，每张卡一个条目，顺序与请求agentCards一致。

    | 参数名称 | 类型 | 描述 |
    |------|----|------|
    | results[].name           | string  | AgentCard名称。 |
    | results[].organization   | string  | 所属组织机构。 |
    | results[].registrySigned | boolean | 注册中心是否已对该卡片会签。 |

- 响应样例

    修改成功：

    ```json
    HTTP/1.1 200 OK
    {
      "results": [
        {
          "name": "RAN Energy Saving Agent",
          "organization": "Org",
          "registrySigned": false
        }
      ]
    }
    ```

    批量更新任一卡失败时按fail-fast中止并返回错误，错误响应额外携带updatedAgents字段，列出本请求中已成功更新的卡片。

- 状态码

    | 状态码 | 说明                    |
    |--------|-----------------------|
    | 200 | 修改成功。                 |
    | 400 | 参数校验失败。               |
    | 401 | 调用者认证或卡片签名验证失败。               |
    | 403 | 权限拒绝。                 |
    | 404 | 修改失败，Agent未找到。        |
    | 422 | 修改失败，AgentCard参数校验失败。 |
    | 503 | 服务繁忙。                 |

## 删除指定AgentCard

- 典型场景

    已注册的Agent的如果想要注销，需要用户调用该接口来进行Agent信息的注销。

- 功能描述

    从Agent注册中心中移除指定的Agent。该操作会彻底删除Agent的注册信息，删除后该Agent将无法被工作流调度使用。

- 接口约束

    单实例上该接口最大并发数为50。

- 调用方法

    DELETE

- URI

    */rest/v1/registry-center/agent-cards/{organization}/{name}*

- 请求参数

  <a id="表13-path参数列表"></a>**表13** path参数列表

    | 参数名          | 类型     | 必填 | 描述                                           |
    |--------------|--------|----|----------------------------------------------|
    | name         | string | 是  | 待注销的Agent名称，作为路径参数传递。用于唯一标识要删除的Agent。        |
    | organization | string | 是  | 待注销Agent的组织机构名称，作为路径参数传递。与name共同唯一标识一个Agent。 |

- 请求示例

  ```json
  DELETE /rest/v1/registry-center/agent-cards/Org/RAN%20Energy%20Saving%20Agent HTTP/1.1
  Host: your-domain.com
  Content-Type: application/json
  ```

- 响应参数

    | 参数名称 | 类型 | 描述 |
    |------|----|------|
    | name         | string  | 已删除的Agent名称。 |
    | organization | string  | 所属组织机构。 |
    | deleted      | boolean | 固定为true，表示删除成功。 |

- 响应样例

    删除成功：

    ```json
    HTTP/1.1 200 OK
    {
      "name": "RAN Energy Saving Agent",
      "organization": "Org",
      "deleted": true
    }
    ```

- 状态码

  | 状态码 | 说明             |
  |--------|----------------|
  | 200 | 删除成功。          |
  | 403 | 权限拒绝。          |
  | 404 | 删除失败，Agent未找到。 |
  | 503 | 服务繁忙。          |

## 按语义检索AgentCard

- 典型场景

    用户输入一段自然语言描述的任务需求，系统可识别任务语义意图，并返回与任务最匹配的Agent列表，供用户选择调用。

- 功能描述

    该接口接收自然语言任务描述并选择匹配的已发布 Agent；待审批 Card 在构造模型提示词前即被过滤。需在 `etc/config/models.yaml` 中配置可用的 `chat` 能力（通过 `api_key_env` 指明密钥所在的环境变量，值放在进程环境变量或 `.env`）。模型未配置、调用失败或返回无法解析的内容时返回 503；正常检索无匹配时才返回 200 和空 agentCards 列表。

- 接口约束

    单实例上该接口最大并发数为100。

- 调用方法

    POST

- URI

    */rest/v1/registry-center/agent-cards/semantic-query*

- 请求参数

  <a id="表14-body参数列表"></a>**表14** body参数列表

    | 参数名  | 类型     | 必填 | 默认值 | 描述                                     |
    |------|--------|----|-----|----------------------------------------|
    | task | string | 是  | -   | 自然语言任务描述，用于语义检索相关Agent。例如："需要查询意图报告"等。 |
    | layer | string | 否  | -   | 按注册层级过滤候选Agent。层级为非空字符串，最长64个字符，大小写按原值匹配。 |
    | topN | integer | 否 | 10 | 集成端口使用的返回数量上限，取值范围 1–50；主端口使用查询参数 `top_n`。 |

  `task` 必须为非空白字符串，最多 10,000 个字符。集成端口不接受布尔值、
  小数或数字字符串作为 `topN`，不再强制转换或截断。不合法的任务描述或数量
  参数在模型调用前返回 422。

- 请求示例

  - 基本检索

    ```http
    POST /rest/v1/registry-center/agent-cards/semantic-query HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json
    {
      "task": "需要查询意图报告"
    }
    ```

- 响应参数

    <a id="表15-响应参数列表"></a>**表15** 响应参数列表
    
    | 参数名称 | 类型    | 值域 | 默认值 | 描述         |
    |------|-------|---|-----|--------------|
    | agentCards    | array_reference | - | -   | 符合要求的Agent列表，详细请参见[表2](#表2-agentcard对象的参数列表)。 |

- 响应样例

   - 查询成功
      ```json
      {
        "agentCards": [
          {
            "name": "RAN Energy Saving Agent",
            "description": "负责RAN能效优化的自主闭环运行，包括意图探索、意图实现、效果评估与报告。",
            "version": "1.0.0",
            "provider": {
              "organization": "Org",
              "url": ""
            },
            "skills": [
              {
                "id": "ran-es-intent-exploration",
                "name": "RAN ES Intent Exploration",
                "description": "评估并确定指定RAN ES意图目标的最佳可能性，考虑当前资源状况和系统能力。",
                "tags": [
                  "wireless",
                  "energy-saving",
                  "intent"
                ]
              },
              {
                "id": "ran-es-intent-lifecycle-management",
                "name": "RAN ES Intent Lifecycle Management",
                "description": "管理RAN节能意图的生命周期，包括创建、修改、删除、激活、去激活意图，并执行数据采集、分析、解决方案制定与配置。",
                "tags": [
                  "wireless",
                  "energy-saving",
                  "intent"
                ]
              },
              {
                "id": "ran-es-intent-reporting",
                "name": "RAN ES Intent Reporting",
                "description": "提供意图报告查询、订阅、通知功能，报告意图实现状态、达成值、推荐值及配置修改信息。",
                "tags": [
                  "wireless",
                  "energy-saving",
                  "reporting"
                ]
              }
            ],
            "capabilities": {
              "streaming": true,
              "pushNotifications": false,
              "extensions": []
            },
            "defaultInputModes": [
              "text",
              "json"
            ],
            "defaultOutputModes": [
              "text",
              "json"
            ],
            "supportedInterfaces": [
              {
                "protocolBinding": "GRPC",
                "protocolVersion": "1.0.0",
                "url": "http://127.0.0.1:5000/"
              },
              {
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0.0",
                "url": "http://127.0.0.1:5000/"
              }
            ]
          }
        ]
      }
      ```

- 状态码

  | 状态码 | 说明             |
  |--------|----------------|
  | 200 | 查询成功（无匹配Agent时返回空agentCards列表）。 |
  | 500 | 查询失败，服务内部错误。   |
  | 503 | 服务繁忙。          |

## 查询带层级的AgentCard

主端口 Token 模式下，三个带层级元数据接口均要求 `nms_oss` 管理员凭据，普通发现/vendor 角色会返回 403；身份认证与 mTLS 分开执行。只返回 published 卡片，是否隐藏不健康卡片取决于健康隐藏开关。下面响应示例中的简化 AgentCard 仅展示返回形态，不可直接用于注册。

- 典型场景

    调用方需要按注册层级筛选Agent，并同时获取AgentCard及其注册元数据时，使用该接口。

- 功能描述

    按层级、分页参数查询已发布且符合健康可见性策略的 Agent。`layer`缺省时不限制层级；提供`layer`时按字符串精确匹配。响应中的`agentCard`内容与注册时保存的AgentCard一致，层级作为同级字段返回。

- 调用方法

    POST

- URI

    */rest/v1/registry-center/agent-cards-with-layer*

- 请求参数

  <a id="带层级普通查询body参数"></a>**带层级普通查询** body参数列表

    | 参数名 | 类型 | 必填 | 默认值 | 描述 |
    |------|------|------|------|------|
    | layer | string | 否 | - | 注册层级。非空字符串，最长64个字符，大小写按原值匹配。 |
    | limit | integer | 否 | 100 | 返回数量，取值范围1~1000。 |
    | offset | integer | 否 | 0 | 起始偏移量，不能小于0。 |

- 请求示例

    ```http
    POST /rest/v1/registry-center/agent-cards-with-layer HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json

    {
      "layer": "OMC",
      "limit": 20,
      "offset": 0
    }
    ```

- 响应参数

    <a id="带层级普通查询响应参数"></a>**带层级普通查询** 响应参数列表

    | 参数名 | 类型 | 描述 |
    |------|------|------|
    | agents | array | Agent条目列表，条目结构见下表。 |
    | agents[].agentCard | object | AgentCard对象，结构参见[表2](#表2-agentcard对象的参数列表)。 |
    | agents[].layer | string | 注册时保存的层级；旧数据未声明层级时为`unknown`。 |
    | count | integer | 本次实际返回的条目数。 |
    | hasMore | boolean | 是否还有下一页。 |

- 响应样例

    ```json
    {
      "agents": [
        {
          "agentCard": {
            "name": "RAN Energy Saving Agent",
            "provider": { "organization": "Org", "url": "" }
          },
          "layer": "OMC"
        }
      ],
      "count": 1,
      "hasMore": false
    }
    ```

- 状态码

  | 状态码 | 说明 |
  |--------|------|
  | 200 | 查询成功。无匹配Agent时返回空`agents`列表。 |
  | 422 | 请求参数不合法。 |
  | 500 | 查询失败，服务内部错误。 |
  | 503 | 数据存储尚未完成层级迁移或服务繁忙。 |

## 查询带层级的AgentCard详情

- 典型场景

    调用方已经知道Agent的组织和名称，需要同时获取AgentCard及其注册层级。

- 功能描述

    按`organization`和`name`精确查询一个已发布且符合健康可见性策略的 Agent。查询不到时返回404。

- 调用方法

    GET

- URI

    */rest/v1/registry-center/agent-cards-with-layer/{organization}/{name}*

- 请求参数

    路径参数与[查询指定AgentCard](#查询指定agentcard)相同。

- 请求示例

    ```http
    GET /rest/v1/registry-center/agent-cards-with-layer/Org/RAN%20Energy%20Saving%20Agent HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json
    ```

- 响应参数

    | 参数名 | 类型 | 描述 |
    |------|------|------|
    | agentCard | object | AgentCard对象，结构参见[表2](#表2-agentcard对象的参数列表)。 |
    | layer | string | 注册时保存的层级；旧数据未声明层级时为`unknown`。 |

- 响应样例

    ```json
    {
      "agentCard": {
        "name": "RAN Energy Saving Agent",
        "provider": { "organization": "Org", "url": "" }
      },
      "layer": "OMC"
    }
    ```

- 状态码

  | 状态码 | 说明 |
  |--------|------|
  | 200 | 查询成功。 |
  | 404 | Agent 不存在、未发布或被健康策略隐藏。 |
  | 503 | 服务繁忙。 |

## 按层级语义检索AgentCard

- 典型场景

    调用方需要根据自然语言任务描述检索Agent，并将候选范围限制在指定注册层级内。

- 功能描述

    接口先按`layer`限定候选范围，再执行原有语义检索流程。未提供`layer`时检索所有层级。语义检索依赖LLM服务，配置及异常处理与[按语义检索AgentCard](#按语义检索agentcard)相同。

- 调用方法

    POST

- URI

    */rest/v1/registry-center/agent-cards-with-layer/semantic-query*

- 请求参数

  <a id="带层级语义查询body参数"></a>**带层级语义查询** body参数列表

    | 参数名 | 类型 | 必填 | 默认值 | 描述 |
    |------|------|------|------|------|
    | task | string | 是 | - | 自然语言任务描述。 |
    | layer | string | 否 | - | 注册层级。非空字符串，最长64个字符，大小写按原值匹配。 |
    | topN | integer | 否 | 10 | 返回数量，取值范围1~50。 |

- 请求示例

    ```http
    POST /rest/v1/registry-center/agent-cards-with-layer/semantic-query HTTP/1.1
    Host: your-domain.com
    Content-Type: application/json

    {
      "task": "需要查询意图报告",
      "layer": "domain-workbench",
      "topN": 10
    }
    ```

- 响应参数

    | 参数名 | 类型 | 描述 |
    |------|------|------|
    | agents | array | 匹配结果列表，条目结构为`agentCard`和`layer`。 |
    | agents[].agentCard | object | AgentCard对象，结构参见[表2](#表2-agentcard对象的参数列表)。 |
    | agents[].layer | string | 注册时保存的层级。 |
    | count | integer | 本次实际返回的条目数。 |

- 响应样例

    ```json
    {
      "agents": [
        {
          "agentCard": {
            "name": "RAN Energy Saving Agent",
            "provider": { "organization": "Org", "url": "" }
          },
          "layer": "domain-workbench"
        }
      ],
      "count": 1
    }
    ```

- 状态码

  | 状态码 | 说明 |
  |--------|------|
  | 200 | 查询成功。无匹配Agent时返回空`agents`列表。 |
  | 422 | 请求参数不合法。 |
  | 500 | 查询失败，服务内部错误。 |
  | 503 | 数据存储尚未完成层级迁移或服务繁忙。 |

## 获取公钥信息

- 典型场景

    运营商或设备厂商需要获取公钥信息时，可通过该接口获取。

- 功能描述

    提供注册中心签名证书的公钥JWK Set格式，用于验证AgentCard的注册中心签名。

- 接口约束

  - 接口流控：10次/秒。
  - 接口认证：免应用 Token 认证；HTTPS 配置 `verify_client=true` 时，TLS 握手仍须有效客户端证书。

- 调用方法

    GET

- URI

    */rest/v1/registry-center/keys*

- 请求参数

    无。

- 请求示例

  ```json
  GET /rest/v1/registry-center/keys HTTP/1.1
  Host: your-domain.com
  Content-Type: application/json
  ```

- 响应参数

  <a id="表16-jwk对象数组"></a>**表16** JWK对象数组

    | 参数名称 | 类型     | 值域 | 默认值 | 描述         |
    |------|--------|------|-----|--------------|
    | keys | array_reference  | -    | -   | JWK Set格式的公钥列表。 |

  <a id="表17-jwk对象"></a>**表17** JWK对象

    | 参数名称   | 是否必选 | 类型              | 值域             | 默认值 | 描述                        |
    |:---------|:-----|:----------------|:------------------|:----|:----------------------------|
    | kty      | 是    | string          | RSA               | -   | 密钥类型。                        |
    | n        | 是    | string          | base64url编码      | -   | RSA模数。                        |
    | e        | 是    | string          | base64url编码      | -   | RSA公钥指数。                     |
    | alg      | 是    | string          | RS256             | -   | 签名算法。                        |
    | use      | 是    | string          | sig               | -   | 密钥用途。                        |
    | kid      | 是    | string          | -                 | -   | 密钥标识符。                       |
    | key_ops  | 否    | array of string | ["verify"]        | -   | 密钥操作用途。                     |

- 响应样例

    ```json
    {
      "keys": [
        {
          "kty": "RSA",
          "n": "base64url-encoded-modules",
          "e": "AQAB",
          "alg": "RS256",
          "use": "sig",
          "kid": "test-key-1",
          "key_ops": ["verify"]
        }
      ]
    }
    ```

- 状态码

  | 状态码 | 说明           |
  |--------|--------------|
  | 200 | 获取成功。        |
  | 429 | 获取失败，超过流控限制。 |
  | 500 | 获取失败，服务内部错误。 |
  | 503 | 签名未启用、签名器不可用或证书加载失败。 |

## Agent心跳上报

- 典型场景

    Agent部署后周期性调用该接口上报存活状态；注册中心开启心跳检测后，依据心跳时间与失败阈值维护Agent健康状态。

- 功能描述

    记录Agent最近一次心跳的服务端接收时间。若Agent此前处于可疑（suspect）或离线（offline）状态，收到心跳后立即恢复为健康（healthy）并发布健康变更事件。

- 接口约束

  - 仅当`heartbeat.enabled=true`时进行健康跟踪；开关关闭时接口返回200但`heartbeat_enabled`为false，不做任何记录。
  - Agent必须已注册，未注册的Agent返回404。
  - 开启`owner.isolation.enabled=true`时，只有该卡片的owner能上报心跳：身份无法验证返回401，
    非owner返回403（心跳决定健康隐藏、离线TTL自动注销与公开健康事件，属所有权声明）。
  - 接口流控：默认100次/秒/IP，可通过`flowcontrol.ratelimit.heartbeat`配置。
  - 心跳时间以服务端接收时间为准，不信任请求携带的时间戳。

- 调用方法

    POST

- URI

    */rest/v1/registry-center/agent-cards/{organization}/{name}/heartbeat*

- 请求参数

  <a id="表18-心跳路径参数"></a>**表18** 路径参数

    | 参数名称       | 是否必选 | 类型     | 值域 | 默认值 | 描述            |
    |------------|------|--------|-----|-----|---------------|
    | organization | 是   | string | 1~100个字符 | -   | Agent所属组织，与注册时一致。 |
    | name         | 是   | string | 1~100个字符 | -   | Agent名称，与注册时一致。   |

    请求体可为空。

- 请求示例

    ```http
    POST /rest/v1/registry-center/agent-cards/TestOrg/DemoAgent/heartbeat HTTP/1.1
    Host: your-domain.com
    ```

- 响应参数

  <a id="表19-心跳响应参数"></a>**表19** 响应参数

    | 参数名称              | 类型     | 描述                                       |
    |-------------------|--------|------------------------------------------|
    | heartbeat_enabled | boolean | 心跳检测是否开启。                                 |
    | interval          | int    | 期望心跳周期（秒），Agent应以此周期附加±10%随机抖动上报。          |
    | failure_threshold | int    | 连续错过的周期数量达到该值后判定为离线。                       |
    | grace_period      | int    | 可疑状态缓冲时长（秒）。                               |
    | server_time       | string | 服务端当前时间（ISO 8601），供Agent校准时钟。               |
    | health_status     | string | 当前健康状态：healthy/suspect/offline/unknown。    |

- 响应样例

    ```json
    {
      "heartbeat_enabled": true,
      "interval": 30,
      "failure_threshold": 3,
      "grace_period": 10,
      "server_time": "2026-09-17T12:00:00.123456+00:00",
      "health_status": "healthy"
    }
    ```

- 状态码

  | 状态码 | 说明                     |
  |--------|------------------------|
  | 200 | 上报成功。                  |
  | 401 | 认证失败。                  |
  | 404 | Agent未注册。              |
  | 429 | 上报失败，超过流控限制。           |
  | 500 | 上报失败，服务内部错误。           |
  | 503 | 服务繁忙。                  |

## 查询Agent健康状态列表

- 典型场景

    系统管理员通过监控大盘查询所有心跳监控Agent的健康状态，及时发现离线Agent。

- 功能描述

    返回所有已纳入心跳监控的Agent健康状态列表，支持按健康状态过滤。

- 接口约束

  - 仅当`heartbeat.enabled=true`时返回数据，否则返回空列表。
  - status过滤值必须是healthy/suspect/offline/unknown之一。

- 调用方法

    GET

- URI

    */rest/v1/registry-center/agents/health*

- 请求参数

  <a id="表20-健康查询参数"></a>**表20** Query参数

    | 参数名称  | 是否必选 | 类型     | 值域                                      | 默认值 | 描述        |
    |-------|------|--------|-------------------------------------------|-----|-----------|
    | status | 否   | string | healthy/suspect/offline/unknown | -   | 按健康状态过滤。  |

- 请求示例

    ```http
    GET /rest/v1/registry-center/agents/health?status=offline HTTP/1.1
    Host: your-domain.com
    ```

- 响应参数

  <a id="表21-健康状态对象"></a>**表21** 健康状态对象

    | 参数名称               | 类型     | 描述                    |
    |--------------------|--------|-----------------------|
    | name               | string | Agent名称。              |
    | organization       | string | Agent所属组织。            |
    | health_status      | string | 健康状态。                 |
    | last_heartbeat_at  | string | 最近一次心跳的服务端接收时间。        |
    | status_changed_at  | string | 最近一次健康状态迁移时间。          |

- 响应样例

    ```json
    {
      "agents": [
        {
          "name": "DemoAgent",
          "organization": "TestOrg",
          "health_status": "offline",
          "last_heartbeat_at": "2026-09-17T11:07:00.056845+00:00",
          "status_changed_at": "2026-09-17T11:07:07.595837+00:00"
        }
      ]
    }
    ```

- 状态码

  | 状态码 | 说明           |
  |--------|--------------|
  | 200 | 查询成功。        |
  | 400 | status过滤值非法。 |
  | 500 | 查询失败，服务内部错误。 |

## 创建变更订阅

- 典型场景

    服务运营方需要在注册中心数据变化（注册、更新、注销、健康状态变化）时收到实时通知，先通过该接口登记回调地址。

- 功能描述

    创建一个变更广播订阅。注册中心在产生匹配的变更事件后，通过HTTPS回调将事件批量推送给订阅方，事件携带HMAC-SHA256签名供订阅方验签。

- 接口约束

  - 仅当`broadcast.enabled=true`时可用，否则返回503。
  - callback_url必须为HTTPS地址；开发环境可通过`broadcast.allow.http.callbacks=true`放开HTTP。
  - `broadcast.callback.allowlist`为必填：白名单为空时请求被拒绝（422），且不会发起投递。
  - 回调TLS证书按默认CA加上可选的`broadcast.callback.ca.file`证书包校验（用于使用私有证书的监听方）。
  - 接口流控：默认50次/秒/IP。

- 调用方法

    POST

- URI

    */rest/v1/registry-center/subscriptions*

- 请求参数

  <a id="表22-订阅body参数"></a>**表22** body参数列表

    | 参数名称        | 是否必选 | 类型              | 值域 | 默认值 | 描述                                        |
    |-------------|------|-----------------|-----|-----|-------------------------------------------|
    | callback_url | 是   | string          | 1~2048个字符，HTTPS地址 | -   | 事件回调地址。                                    |
    | event_types | 否    | array of string | AGENT_REGISTERED/AGENT_UPDATED/AGENT_DEREGISTERED/AGENT_HEALTH_CHANGED | 全部类型 | 订阅的事件类型白名单。                                |
    | filters     | 否    | reference       | -   | -   | 过滤条件，详细请参见[表23](#表23-订阅过滤条件)。              |
    | secret      | 否    | string          | 1~256个字符 | -   | HMAC-SHA256验签密钥，建议提供；不提供则事件不签名。            |

  <a id="表23-订阅过滤条件"></a>**表23** 订阅过滤条件

    | 参数名称          | 是否必选 | 类型              | 描述           |
    |---------------|------|-----------------|--------------|
    | organizations | 否    | array of string | 按Agent组织过滤。  |
    | tags          | 否    | array of string | 按Agent标签过滤。  |

- 请求示例

    ```json
    {
      "callback_url": "https://operator.example.com/registry-events",
      "event_types": ["AGENT_REGISTERED", "AGENT_UPDATED", "AGENT_DEREGISTERED", "AGENT_HEALTH_CHANGED"],
      "filters": {"organizations": ["acme"]},
      "secret": "whsec-demo"
    }
    ```

- 响应参数

  <a id="表24-订阅对象"></a>**表24** 订阅对象

    | 参数名称           | 类型     | 描述                                        |
    |----------------|--------|---------------------------------------------|
    | subscription_id | string | 订阅唯一标识，用于查询与删除。                             |
    | callback_url    | string | 回调地址。                                       |
    | event_types     | array  | 订阅的事件类型。                                    |
    | filters         | object | 过滤条件。                                       |
    | created_at      | string | 创建时间。                                       |

- 响应样例

    ```json
    {
      "subscription_id": "sub_6133a2d93b32459990bf",
      "callback_url": "https://operator.example.com/registry-events",
      "event_types": ["AGENT_REGISTERED", "AGENT_UPDATED"],
      "filters": {"organizations": ["acme"], "tags": null},
      "created_at": "2026-09-17T11:07:14.009321+00:00"
    }
    ```

- 状态码

  | 状态码 | 说明                     |
  |--------|------------------------|
  | 201 | 创建成功。                  |
  | 422 | 请求参数非法（回调地址、事件类型等）。    |
  | 429 | 创建失败，超过流控限制。           |
  | 500 | 创建失败，服务内部错误。           |
  | 503 | 变更广播功能未开启。             |

## 查询订阅列表

- 典型场景

    管理员查看当前有效的变更订阅及其回调配置。

- 功能描述

    返回所有变更订阅列表。响应不包含secret。

- 接口约束

  - 仅当`broadcast.enabled=true`时可用，否则返回503。

- 调用方法

    GET

- URI

    */rest/v1/registry-center/subscriptions*

- 请求参数

    无。

- 响应样例

    ```json
    {
      "subscriptions": [
        {
          "subscription_id": "sub_6133a2d93b32459990bf",
          "callback_url": "https://operator.example.com/registry-events",
          "event_types": null,
          "filters": {"organizations": null, "tags": null},
          "created_at": "2026-09-17T11:07:14.009321+00:00"
        }
      ]
    }
    ```

- 状态码

  | 状态码 | 说明           |
  |--------|--------------|
  | 200 | 查询成功。        |
  | 500 | 查询失败，服务内部错误。 |
  | 503 | 变更广播功能未开启。   |

## 删除订阅

- 典型场景

    订阅方不再需要接收变更通知时，通过该接口取消订阅。

- 功能描述

    按订阅ID删除变更订阅，删除后注册中心不再向该回调地址推送事件。

- 接口约束

  - 仅当`broadcast.enabled=true`时可用，否则返回503。

- 调用方法

    DELETE

- URI

    */rest/v1/registry-center/subscriptions/{subscription_id}*

- 请求参数

  <a id="表25-删除订阅路径参数"></a>**表25** 路径参数

    | 参数名称            | 是否必选 | 类型     | 描述      |
    |-----------------|------|--------|---------|
    | subscription_id | 是   | string | 订阅唯一标识。 |

- 响应样例

    ```json
    {
      "subscription_id": "sub_6133a2d93b32459990bf",
      "deleted": true
    }
    ```

- 状态码

  | 状态码 | 说明           |
  |--------|--------------|
  | 200 | 删除成功。        |
  | 404 | 订阅不存在。       |
  | 500 | 删除失败，服务内部错误。 |
  | 503 | 变更广播功能未开启。   |

## 变更对账查询

- 典型场景

    订阅方收到`SYNC_REQUIRED`摘要事件、怀疑漏事件或定期校验时，通过该接口按版本号增量拉取变更事件。

- 功能描述

    返回`registry_version`大于since的事件列表（按版本号升序），配合全量AgentCard查询可实现初始同步与漏事件补齐。

- 接口约束

  - 事件保留期由`broadcast.outbox.retention.days`配置（默认7天），超出保留期的事件无法拉取。
  - 单页最大1000条。

- 调用方法

    GET

- URI

    */rest/v1/registry-center/changes*

- 请求参数

  <a id="表26-对账Query参数"></a>**表26** Query参数

    | 参数名称  | 是否必选 | 类型 | 值域       | 默认值 | 描述                     |
    |-------|------|----|------------|-----|------------------------|
    | since | 否   | int | >=0        | 0   | 上次已知的registry_version。 |
    | limit | 否   | int | 1~1000     | 100 | 单页最大事件数。               |

- 响应参数

  <a id="表27-变更事件对象"></a>**表27** 变更事件对象

    | 参数名称             | 类型     | 描述                                        |
    |------------------|--------|---------------------------------------------|
    | event_id         | string | 事件唯一标识（UUID），订阅方用于幂等去重。                     |
    | event_type       | string | 事件类型，枚举见[表22](#表22-订阅body参数)event_types说明。  |
    | timestamp        | string | 事件产生时间（服务端时钟）。                              |
    | registry_version | int    | 注册中心全局单调递增版本号。                              |
    | data             | object | 事件负载：卡片事件含name/organization/agent_card/tags；健康事件含health_status/previous_health_status。 |
    | has_more         | boolean | 是否还有下一页。                                    |
    | next_since       | int     | 下一页拉取起点（当前页最后一条的registry_version）。           |

- 响应样例

    ```json
    {
      "changes": [
        {
          "event_id": "0198f6a2-7c1d-7cc2-9a3b-4f2e1d0c9b8a",
          "event_type": "AGENT_REGISTERED",
          "timestamp": "2026-09-17T12:00:00.123456+00:00",
          "registry_version": 1,
          "data": {"name": "DemoAgent", "organization": "TestOrg"}
        }
      ],
      "has_more": false,
      "next_since": 1
    }
    ```

- 状态码

  | 状态码 | 说明           |
  |--------|--------------|
  | 200 | 查询成功。        |
  | 429 | 查询失败，超过流控限制。 |
  | 500 | 查询失败，服务内部错误。 |

## 管理列表的登记层级

`GET /rest/v1/registry-center/agent-cards/manage` 返回各审核状态的卡片（也可用 `status` 筛选），每项附带 `publishStatus` 和 `registration.layer`。`statusCounts` 仍是未按审核状态筛选的全量审核计数。`registration.layer` 返回实际保存的厂商自定义层级字符串，旧登记未声明时为 `unknown`。管理响应用于 UI 展示，不是可直接作为签名 AgentCard 提交的登记载荷；公共 AgentCard 查询响应保持不变。

原生前端仅把显式 service/service-layer、network/network-layer（兼容大小写及分隔符）归入服务层、网络层；其他自定义值保留原文，归入“其他/未指定”，缺失元数据显示“未指定”。不再根据所属厂商推断层级。应配套更新前后端；新前端连接旧管理 API 时，未返回的元数据按“未指定”展示。

## TMF639资源清单接口

可选的 TM Forum TMF639 Resource Inventory Management **v5** 视图，供在 ODA Canvas 中把注册中心对齐 TMFC012 Resource Inventory 的部署使用。默认关闭：设置 `tmf639.enabled=true`（或 `REGISTRY_TMF639_ENABLED=true`）开启。关闭时下列接口一律返回404。

基础路径：`/tmf-api/resourceInventoryManagement/v5`

| 方法 | 路径 | 对应功能 |
|------|------|----------|
| `GET` | `/resource` | 查询AgentCard |
| `GET` | `/resource/{id}` | 获取单个AgentCard |
| `POST` | `/resource` | 注册一个已签名的AgentCard |
| `PATCH` | `/resource/{id}` | 替换已签名的AgentCard（全量更新） |
| `DELETE` | `/resource/{id}` | 注销（204） |
| `POST` | `/hub` | 创建变更订阅（格式 `tmf639`） |
| `GET` | `/hub/{id}` | 查询hub订阅 |
| `DELETE` | `/hub/{id}` | 删除hub订阅（204） |

与实现完全一致的OpenAPI描述见 [`docs/openapi/tmf639-a2a-agent-v5.yaml`](../openapi/tmf639-a2a-agent-v5.yaml)。

该接口不引入新的业务规则。注册和更新沿用原生接口的内容安全检查、签名校验、注册中心签名和审批流程，PATCH和DELETE沿用相同的属主校验，读取沿用发现接口的可见性规则。

**资源映射。** 每个AgentCard对应一个 `category: A2AAgent` 的 `LogicalResource`：

- `id`：`base64url(organization) + "." + base64url(name)`（UTF-8，无填充），为不透明标识，可放入单个路径段。
- `name`、`description`、`resourceVersion`：取自卡片的 `name`、`description`、`version`；`creationDate` 为注册时间。
- `resourceSpecification`：`{id: A2AAgentCard, name: "A2A AgentCard", version: <protocolVersion>}`。
- `relatedParty`：提供方组织（角色 `provider`）；管理员还能看到属主身份（角色 `owner`）。
- `resourceCharacteristic`：`agentCard`（保存的已签名卡片，驼峰命名，含注册中心签名）、`layer`、`reviewStatus`、`skills`、`tags`、`url` 和 `protocolVersion`（取第一个 `supportedInterfaces` 条目）；有心跳数据时还包含 `healthStatus` 和 `lastHeartbeat`。
- 设置了 `registry.public.base_url` 时，`href` 使用该地址；应将其设为对外可见的基础地址（例如包含组件前缀的 ODA Canvas 网关地址）。

| 审核状态 | `administrativeState` | `lifecycleState` |
|----------|-----------------------|------------------|
| `published` | `unlocked` | `installed` |
| `registered`（待审核） | `locked` | `planned` |
| `rejected` | `locked` | `planned` |
| `deprecated` | `shuttingDown` | `pendingRemoval` |

| 健康状态 | `operationalState` | `availabilityStatus` |
|----------|--------------------|----------------------|
| `healthy` | `enabled` | `online` |
| `suspect` | `enabled` | `degraded` |
| `offline` | `disabled` | `offline` |
| 未知 / 未启用心跳 | 不返回 | 不返回；`unknownStatus: true` |

**可见性。** 调用方可看到可发现的卡片（已发布且未被心跳过滤隐藏，与原生发现接口一致），以及自己拥有的全部卡片（已验证的属主身份，需启用属主隔离）。管理员（`owner.identity.mode=token` 且角色为 `nms_oss`）可看到所有审核状态，与管理列表一致。其他未发布的卡片对调用方在所有方法上都视为不存在：GET、PATCH、DELETE均返回404，也不计入 `X-Total-Count`。对他人拥有的可见卡片执行PATCH或DELETE返回403，且不透露属主。

**`GET /resource` 查询参数。** 支持 `fields`、`offset`、`limit`，以及对 `name`、`category`、`administrativeState`、`operationalState`、`lifecycleState`、`availabilityStatus` 的精确匹配过滤（逗号表示“或”），另有 `relatedParty.name`（提供方组织）。响应带 `X-Total-Count` 和 `X-Result-Count` 头。`filter`（JSONPath）、`sort`、`before`、`after` 返回501。

**POST 请求体。** 一个 Resource，其 `resourceCharacteristic` 包含 `agentCard`（完整、未经修改的已签名AgentCard对象），可选包含 `layer`。每次请求一张卡片。从GET响应复制来的只读属性会被忽略，但 `name`、`category`、`@type` 不得与卡片矛盾。成功返回201、资源内容和 `Location` 头；启用审批时返回的资源为 `locked` / `planned`。

```json
{
  "@type": "LogicalResource",
  "resourceCharacteristic": [
    {"name": "agentCard", "value": {"name": "RanOptimizer", "provider": {"organization": "acme", "url": "https://acme.example"}, "...": "...", "signatures": ["..."]}},
    {"name": "layer", "value": "network"}
  ]
}
```

**PATCH 请求体。** `Content-Type: application/merge-patch+json`（或 `application/json`），`resourceCharacteristic` 中携带新的已签名 `agentCard`，可选 `layer`。卡片的名称和组织必须与资源id一致。除 `@type` 和 `resourceCharacteristic` 外的属性返回400；替换已驳回的卡片会像原生更新一样重新提交审核；审核状态变更（发布、驳回、废弃）仍使用原生审核接口。JSON Patch（`application/json-patch+json`）返回415。AgentCard带签名，逐字段修改会破坏签名。

**认证。** 主端口Token中间件只覆盖 `/rest/v1/registry-center`，因此该接口自带校验。在 `owner.identity.mode=token` 下使用同一认证提供方：

- 所有已认证角色都可读取，也都可创建 `/hub` 订阅。
- `/resource` 上的POST、PATCH、DELETE需要 `vendor_agent` 或 `nms_oss` 角色，其他角色返回403。

其他身份模式沿用主端口行为（证书或可信代理的属主身份，以及AUTHENTICATE处理器）。

**错误。** 采用 TMF630 错误体：`{"@type": "Error", "code": "404", "reason": "Not Found", "status": "404", "message": "..."}`。状态码与原生接口一致（401签名或身份无效、403非属主或角色不允许、409重复、422卡片校验失败、429限流）。仅Milvus部署（无权威存储）时所有操作返回503。超过1 MB的请求体（413）和超过1024字符的URL（414）在路由前被拒绝，响应为纯文本。

**变更通知（`/hub`）。** hub就是以TMF639格式投递的普通变更广播订阅，因此需要 `broadcast.enabled=true`（否则返回503），且回调主机须在 `broadcast.callback.allowlist` 中（否则返回400）。hub与原生订阅共用订阅存储，因此也会出现在原生管理订阅列表中（`filters.format: tmf639`），并可在那里删除。`/hub/{id}` 对原生订阅返回404。hub归属于创建它的已验证身份（token主体，或其他模式下已验证的属主身份）：除 `nms_oss` 管理员外，其他调用方对 `GET` 和 `DELETE /hub/{id}` 均得到404。未经验证身份创建的hub与原生订阅一样，任何已认证调用方都可管理。

- `POST /hub` 请求体：`{"callback": "https://listener.example/tmf639", "query": "eventType=ResourceCreateEvent,ResourceDeleteEvent"}`。`query` 可选，仅支持 `eventType`（逗号分隔或重复出现），其他查询返回400。可选的 `secret`（注册中心扩展字段，不会回显）与原生webhook一样对每个请求体签名：`X-Registry-Signature: sha256=HMAC(secret, "<X-Registry-Timestamp>.<body>")`。
- 每个事件单独POST到 `callback`，地址原样使用（不追加 `/listener/...`）。请求体为TMF639 v5事件：`@type`/`eventType`、`eventId`、`eventTime`、`event.resource`。
- 事件描述的是**已发布的资源清单**，因为它们来自原生的可发现性事件：

| 原生事件 | TMF639事件 | `event.resource` |
|----------|------------|------------------|
| `AGENT_REGISTERED`（卡片变为已发布） | `ResourceCreateEvent` | 由事件中的卡片生成的完整资源 |
| `AGENT_UPDATED`（已发布卡片变更） | `ResourceAttributeValueChangeEvent` | 由事件中的卡片生成的完整资源 |
| `AGENT_DEREGISTERED`（卡片退出发现） | `ResourceDeleteEvent` | `id`、`href`、`name` |
| 已发布卡片的 `AGENT_REVIEW_DEPRECATED` | `ResourceStateChangeEvent` | `id`、`href`、`name`、`administrativeState: shuttingDown`、`lifecycleState: pendingRemoval` |
| `AGENT_HEALTH_CHANGED` | `ResourceStateChangeEvent` | `id`、`href`、`name`、`operationalState`、`availabilityStatus` |
| `AGENT_REVIEW_REJECTED`、`SYNC_REQUIRED`、未发布卡片的废弃 | 不发送 | |

由此可知：

- `POST /resource` 若进入待审核（`registered`）状态，不发送任何事件；卡片审批通过后才发送 `ResourceCreateEvent`。
- 已发布卡片退回审核时发送 `ResourceDeleteEvent`。
- 卡片废弃后最终被删除时不再发送 `ResourceDeleteEvent`，因为它已退出发现。
- 事件从不包含属主、卡片变更时的健康状态或废弃原因。
- 仅在设置了 `registry.public.base_url` 时 `href` 才是绝对地址，否则为服务端相对路径。

投递沿用变更广播的保证：至少一次，先进程内重试，再由持久化重试扫描重试，直到 `broadcast.delivery.max.attempts`。同一hub内按顺序投递，但重试时不保证顺序。某次POST失败会中止当前批次，其余事件在下一轮轮询时发送。hub超过 `broadcast.max.events.per.second` 时，超出的事件会等待，而不会降级为原生的 `SYNC_REQUIRED` 摘要。TMF639没有变更游标：中断恢复后请用 `GET /resource` 重新列出。
