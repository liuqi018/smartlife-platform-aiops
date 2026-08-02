# SmartLife AIOps

基于 LLM Agent + RAG 的智能故障诊断平台。

SmartLife AIOps 面向企业应用的告警分析与诊断场景：接收 AlertManager 告警，通过 LangGraph 编排 Planner、Executor、Replanner，查询 Prometheus 指标与运维知识库，并输出带证据约束的 Markdown 诊断报告。

## 项目背景

企业系统运行过程中会持续出现 CPU 高负载、JVM Heap 异常、数据库慢查询、缓存或依赖服务不可用等故障。传统排障通常依赖工程师手工切换监控、日志和 Runbook，并依靠个人经验判断根因，定位效率和结果一致性都难以保证。

本项目把告警处理、诊断规划、工具调用、知识检索和报告生成组织为一条可执行工作流：Agent 根据告警上下文生成排查计划，采集真实监控证据，检索对应 Runbook，再基于已有证据生成诊断结论。无法取得的信息会标记为缺失证据，不作为已确认事实。

## 核心功能

### 1. 告警接入与故障分析

- 提供 `POST /api/alerts/webhook` 接收 AlertManager webhook，兼容入口为 `POST /api/alerts`。
- 解析 AlertManager 的 firing/resolved 状态、告警名称、级别、服务、实例、起止时间和指纹。
- 将标准化告警转换为 Agent 可执行的诊断上下文，并通过 SSE 返回计划、步骤和报告。
- 使用 Redis 保存当前告警及诊断状态，使用 MySQL 保存告警生命周期、诊断证据和报告。
- 定时与 AlertManager 活跃告警进行状态校准，用于补偿服务重启期间遗漏的恢复通知。
- 提供 Dashboard API 查询告警历史、诊断报告、执行轨迹、当前指标和服务健康状态。

### 2. Agent 自动诊断流程

```text
Alert
  │
  ▼
Planner
  │
  ▼
Executor
  │
  ▼
Replanner ── 证据不足或仍有计划 ──► Executor
  │
  ▼
Diagnosis Report
```

- **Planner**：根据告警名称、告警描述和已注册工具生成 3～5 个可执行排查步骤，并对 CPU、JVM Heap、MySQL 慢查询、服务不可用等场景应用对应诊断策略。
- **Executor**：执行当前计划步骤，调用 Prometheus、RAG、时间、JVM 和可用的 MCP 工具；独立工具任务可并行执行，单个工具失败不会直接中止整条诊断链。
- **Replanner**：检查已执行步骤和证据完整性，决定继续执行、补充 Runbook/监控证据，或生成最终报告。
- **Diagnosis Report**：输出告警摘要、关键证据、根因判断、缺失证据和处置建议，并保存诊断记录。

工作流由 `app/services/aiops_service.py` 中的 LangGraph `StateGraph` 实现，使用内存 Checkpointer 按 `thread_id` 保存运行状态。

### 3. 多工具协同

当前 Agent 本地工具包括：

- `query_prometheus_alerts`：查询 Prometheus 当前活动告警。
- `query_prometheus_metrics`：执行 PromQL 即时查询。
- `query_prometheus_range`：查询指定时间范围内的指标趋势。
- `retrieve_knowledge`：从 Milvus 检索相关 Runbook 和知识文档。
- `get_current_time`：获取指定时区的当前时间。
- `collect_jvm_thread_dump`：从目标 JVM 获取线程和调用栈信息；需要本机具备可用的 JDK/JVM 工具。

项目还通过 `langchain-mcp-adapters` 接入 MCP Server：

- `mcp_servers/cls_server.py`：日志检索类工具，默认监听 `8003`。
- `mcp_servers/monitor_server.py`：CPU、内存、进程和服务信息工具，默认监听 `8004`。

当前仓库内的两个 MCP Server 主要返回模拟数据，用于联调和演示；生产环境需要替换或接入真实日志、监控 API。MCP 启动探测失败时，Agent 会降级为仅使用本地工具。

### 4. RAG 运维知识库

- `data/knowledge/runbooks/` 保存 CPU、JVM、Docker、Linux、MySQL、Redis 等故障 Runbook。
- `data/knowledge/architecture/` 保存可用于检索的架构和基础知识资料。
- 支持递归索引 Markdown、TXT、PDF 和 DOCX 文档。
- 使用 DashScope 兼容 Embedding API 生成向量，并存入 Milvus。
- 检索结果保留来源信息，供 Executor 和最终报告引用。
- 可通过脚本或 API 建立索引：

```bash
python scripts/index_knowledge.py
```

也可以调用 `POST /api/index_directory` 指定需要索引的目录。

### 5. 故障自动化评测

`evaluation/runner.py` 提供完整的故障评测闭环，目前配置了六类场景：

| 场景 | 故障触发方式 | 期望告警 |
| --- | --- | --- |
| CPU 使用率过高 | SmartLife HTTP 故障接口 | `SmartLifeHighCPUUsage` |
| JVM Heap 内存异常/OOM | SmartLife HTTP 故障接口 | `SmartLifeJvmMemoryHighUsage` |
| MySQL 慢查询 | SmartLife HTTP 故障接口 | `SmartLifeMysqlSlowQueryHigh` |
| MySQL 服务不可用 | 停止/启动 `smartlife-mysql` 容器 | `MysqlUnavailable` |
| Redis 服务不可用 | 停止/启动 `smartlife-redis` 容器 | `RedisUnavailable` |
| 应用服务不可用 | 停止/启动 `smartlife-app` 容器 | `SmartLifeServiceDown` |

评测器会按配置执行以下流程：

1. 调用 HTTP 接口或 Docker 命令注入故障。
2. 从 MySQL `alert_event` 等待对应的新 firing 告警。
3. 等待该告警对应的 `diagnosis_report`。
4. 检查告警识别、报告生成、关键词根因匹配和证据内容。
5. 在 `finally` 中恢复故障，并等待同一告警记录变为 resolved。
6. 输出 `evaluation_result.json` 和 `evaluation_result.csv`。

默认配置每类故障运行 3 次，共 18 次。评测依赖另行运行的 SmartLife 测试服务及其故障注入接口，不由本仓库创建这些业务容器。

### 6. 大模型可靠性设计

- **Structured Output**：Planner 使用 `with_structured_output(Plan)`；Replanner 使用 `Act`/`Response` 模型约束继续执行或返回报告。
- **状态约束**：LangGraph 状态使用 `PlanExecuteState`，计划、已执行步骤和最终响应具有明确结构；`Plan`、`Act`、`Response` 使用 Pydantic 校验。
- **Planner 多层恢复**：结构化输出失败后，依次尝试从原始响应提取 JSON、无结构化参数重试，最后按告警类型生成确定性 fallback 计划。
- **工具异常隔离**：MCP 健康检查、工具加载和执行均有异常处理；MCP 不可用时保留本地工具路径，单个执行步骤失败会记录证据并继续诊断。
- **报告降级**：报告模型失败或超时时，可基于已执行工具结果生成降级报告。
- **证据约束**：Replanner 要求监控证据和 RAG 证据，区分已确认原因、候选原因、已排除项和缺失信息，并对特定告警执行额外的证据完整性检查。

## 系统架构

```text
用户 / 监控系统
       │
       ▼
 AlertManager
       │ Webhook
       ▼
  FastAPI 接入层
       │
       ▼
   AIOps Agent
       │
       ▼
LangGraph Workflow
Planner → Executor ⇄ Replanner
       │
       ▼
Prometheus Tools + MCP Tools + RAG/Milvus
       │
       ▼
Diagnosis Report（SSE + MySQL）
```

告警生命周期状态写入 Redis，历史告警、诊断证据和报告写入 MySQL。Milvus 使用 etcd 和 MinIO 保存向量索引元数据及对象数据。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端 | Python 3.11～3.13、FastAPI、Uvicorn、SSE |
| Agent | LangGraph、LangChain、Pydantic |
| 大模型 | OpenAI-compatible Chat API；当前工厂默认读取 AutoDL 配置 |
| Embedding / RAG | DashScope Embedding、LangChain Milvus、Milvus |
| 监控告警 | Prometheus、AlertManager、PromQL |
| 工具扩展 | MCP、FastMCP、langchain-mcp-adapters |
| 状态与持久化 | Redis、MySQL、SQLite（诊断历史服务） |
| 基础设施 | Docker Compose、Milvus、etcd、MinIO、Attu |
| 前端 | Vue 3、TypeScript、Vite、Pinia、Element Plus |
| 测试评测 | Pytest、pytest-asyncio、自定义 Evaluation Runner |

## 项目结构

```text
smartlife-aiops/
├── app/
│   ├── main.py                    # FastAPI 应用入口和生命周期
│   ├── config.py                  # 环境变量与运行配置
│   ├── agent/
│   │   ├── aiops/
│   │   │   ├── planner.py         # 诊断计划与 fallback
│   │   │   ├── executor.py        # 工具执行节点
│   │   │   ├── replanner.py       # 重规划与报告生成
│   │   │   └── state.py           # LangGraph 状态模型
│   │   └── mcp_client.py          # MCP 客户端、健康检查和重试
│   ├── api/                       # 告警、AIOps、聊天、文件和 Dashboard API
│   ├── core/                      # LLM 工厂、Milvus 和故障映射加载
│   ├── models/                    # API 与领域数据模型
│   ├── services/                  # 工作流、RAG、向量索引和状态持久化
│   ├── tools/                     # Prometheus、知识、时间和 JVM 工具
│   └── utils/                     # 日志和业务时区工具
├── data/
│   ├── config/                    # 告警到故障策略的映射
│   └── knowledge/                 # Runbook 与架构知识文档
├── evaluation/                    # 自动故障注入、诊断和恢复评测
├── frontend/                      # Vue 3 运维控制台
├── mcp_servers/                   # CLS 与监控 MCP Server
├── monitoring/
│   ├── prometheus.yml             # Prometheus 抓取配置
│   ├── alertmanager.yml           # AlertManager webhook 配置
│   └── rules/aiops_rules.yml      # SmartLife 告警规则
├── scripts/                       # 知识索引和数据库迁移脚本
├── tests/                         # 单元与场景测试
├── aiops.sql                      # MySQL 表结构
├── vector-database.yml            # Redis/MySQL/Milvus 基础设施
├── pyproject.toml                 # Python 依赖和工具配置
└── uv.lock                        # 锁定依赖版本
```

## 快速开始

### 1. 环境准备

需要安装：

- Python `>=3.11,<3.14`
- Docker 与 Docker Compose
- Node.js 与 npm（运行 Vue 前端时需要）
- 可用的 OpenAI-compatible Chat API
- DashScope-compatible Embedding API

使用 uv 安装依赖：

```bash
uv sync --extra dev
```

也可以使用标准虚拟环境：

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
```

在项目根目录创建 `.env`。下面只列出启动所需的主要配置；请替换示例值：

```dotenv
AUTODL_API_KEY=your-chat-api-key
AUTODL_BASE_URL=https://your-openai-compatible-endpoint/v1
AUTODL_MODEL=your-chat-model

DASHSCOPE_API_KEY=your-dashscope-api-key
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v4

MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
PROMETHEUS_BASE_URL=http://127.0.0.1:9090
ALERTMANAGER_BASE_URL=http://127.0.0.1:9093

AIOPS_REDIS_URL=redis://:1234@127.0.0.1:6380/1
AIOPS_MYSQL_HOST=127.0.0.1
AIOPS_MYSQL_PORT=3308
AIOPS_MYSQL_USER=root
AIOPS_MYSQL_PASSWORD=1234
AIOPS_MYSQL_DATABASE=aiops
```

> `vector-database.yml` 将 MySQL 暴露到主机 `3308`，而代码默认值目前是 `3307`，因此使用该 Compose 文件时必须设置 `AIOPS_MYSQL_PORT=3308`。

### 2. 启动基础设施

```bash
docker compose -f vector-database.yml up -d
```

该命令启动 AIOps Redis、MySQL、Milvus、etcd、MinIO 和 Attu。`vector-database.yml` **不包含** Prometheus 与 AlertManager；需要在现有监控环境中部署 `monitoring/` 下的配置，并保证：

- Prometheus 可访问 SmartLife `/actuator/prometheus`。
- Prometheus 加载 `monitoring/rules/aiops_rules.yml`。
- AlertManager 将 webhook 发送至 `http://host.docker.internal:9900/api/alerts/webhook`，或按实际网络调整地址。

首次使用可导入数据库结构；应用启动也会尝试创建所需表，但保留 SQL 文件便于部署审计：

```bash
docker exec -i aiops-mysql mysql -uroot -p1234 aiops < aiops.sql
```

建立知识库索引：

```bash
python scripts/index_knowledge.py
```

### 3. 启动 MCP 与后端

MCP Server 是可选扩展；不启动时 Agent 会使用本地 Prometheus/RAG 工具：

```bash
python mcp_servers/cls_server.py
python mcp_servers/monitor_server.py
```

启动 FastAPI：

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900
```

常用地址：

- API 文档：`http://127.0.0.1:9900/docs`
- 健康检查：`http://127.0.0.1:9900/health`
- AIOps SSE：`POST http://127.0.0.1:9900/api/aiops`
- AlertManager webhook：`POST http://127.0.0.1:9900/api/alerts/webhook`

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认使用相对路径 `/api`。分离部署时可通过 `VITE_API_BASE` 指向后端 API 地址。

### 5. 运行 evaluation

先确认 SmartLife 测试服务、Prometheus、AlertManager、AIOps 后端以及 MySQL 均已运行，再执行：

```bash
python -m evaluation.runner
```

只校验配置、依赖和输出结构，不注入故障：

```bash
python -m evaluation.runner --dry-run --repetitions 1
```

可以在 `evaluation/evaluation_config.yaml` 中调整故障接口、容器名称、超时时间和重复次数。

## 故障评测示例

以 CPU 高负载为例，默认评测配置调用 SmartLife 测试接口：

```bash
curl http://127.0.0.1:8081/test/fault/cpu
```

完整链路如下：

```text
调用 CPU 故障注入接口
          │
          ▼
SmartLife 暴露异常指标
          │
          ▼
Prometheus 检测并触发 SmartLifeHighCPUUsage
          │
          ▼
AlertManager 推送 AIOps Webhook
          │
          ▼
Agent 查询 process_cpu_usage、趋势和相关 Runbook
          │
          ▼
生成并保存诊断报告
          │
          ▼
调用 /test/fault/cpu/stop，等待告警恢复
```

直接运行 Evaluation Runner 时，上述注入、等待、诊断检查和恢复步骤由评测器自动完成。

## 注意事项

- 必须配置可用的模型 API Key；当前 LLM 工厂要求 `AUTODL_API_KEY`，Embedding 服务要求 `DASHSCOPE_API_KEY`。
- Docker Compose 中包含用于本地开发的默认密码，生产环境必须替换，并通过安全的配置管理系统注入。
- `monitoring/` 提供配置文件，但本仓库的 Compose 不负责启动 Prometheus 和 AlertManager。
- MCP Server 当前主要提供模拟数据；不要将模拟结果当作生产监控证据。
- 自动评测会调用故障注入接口并停止/启动指定 Docker 容器，只应在授权的测试环境运行。
- JVM Thread Dump 工具依赖目标 Java 进程和本机 JDK 工具，不保证在所有部署环境可用。
- `.env`、API Key、日志、本地数据库、Milvus 数据、Docker volume 和评测结果不应提交到 Git；项目根目录 `.gitignore` 已包含相应规则。
- 不要在诊断报告中填充未由工具取得的数据；当前工作流会尽量标注证据不足，但生产使用仍应由值班工程师复核高风险操作。

