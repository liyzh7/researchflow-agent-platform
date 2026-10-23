# 开发与本地运行

命令面向 PowerShell，在候选根目录执行。需要 Python 3.12–3.13、uv 0.12.21、
Node 24 / pnpm 11.25.0，以及 Docker / Compose 或自己准备的独立 PostgreSQL 16 / pgvector。
候选没有附带这些程序、依赖缓存或数据库。

## 无模型开发环境

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 手动替换 .env 中的公开占位密码；不要覆盖既有私密配置
uv sync --locked --no-default-groups --group dev
pnpm --dir frontend install --frozen-lockfile
```

`uv` 默认组含 `dev+ai`。轻量开发显式使用 `--no-default-groups`；裸 `uv sync`
可能安装 Torch 和条件 NVIDIA 依赖，不能描述为 CPU-only 环境。
所有应用配置使用 `RESEARCHFLOW_` 前缀。RAG/Agent 默认关闭，没有默认账号。

## 新的独立数据库

候选 Compose project 是 `researchflow-public-candidate`，容器、卷和网络使用独立名称，
默认仅发布 `127.0.0.1:55442`；新实例默认数据库为 `researchflow`。
先检查 `docker compose config --quiet` 和本地配置，确认没有覆盖为原开发端口、卷或外部数据库。
以下只针对新候选实例，不授权迁移已有开发库。

```powershell
docker compose config --quiet
docker compose up -d --wait postgres
uv run --no-sync python -m alembic upgrade head
uv run --no-sync python -m researchflow.cli create-user --username researcher
```

CLI 通过 getpass 输入并确认密码，最少 12 字符，不把密码放进命令行。
应用启动不会迁移 schema、创建账号或认领旧 owner=NULL 数据。
`.env.example` 的占位密码尚无专门的运行时拒绝规则，必须自行替换；默认数据库引导账号
也尚未拆分为最小权限应用角色，不能直接用于公网部署。

## API 与前端开发

```powershell
uv run --no-sync python -m researchflow.server
```

另一终端执行 `pnpm --dir frontend dev`。API 与 Vite 分别绑定回环 8000 / 5173。
打开本地 Web 登录并上传自己有权处理的 TXT/Markdown。
轻量模式可以验证登录和文档管理，缺少真实模型时索引/RAG/Agent 明确不可用。

## 基础 Web 容器

`compose.web.yaml` 的 API 使用单独 `researchflow_web_demo_test` 数据库。
只在新候选 PostgreSQL 中首次创建该库；若已经存在，跳过创建并先核对其用途。

```powershell
docker compose exec postgres psql -U researchflow -d postgres -c "CREATE DATABASE researchflow_web_demo_test;"
$env:RESEARCHFLOW_DATABASE_NAME = 'researchflow_web_demo_test'
uv run --no-sync python -m alembic upgrade head
uv run --no-sync python -m researchflow.cli create-user --username researcher
Remove-Item Env:RESEARCHFLOW_DATABASE_NAME
docker compose -f compose.yaml -f compose.web.yaml build api web
docker compose -f compose.yaml -f compose.web.yaml up -d api web
```

Web 默认在回环 8080，API 只在容器网络内监听。API 使用非 root、只读根、tmpfs 和 no-new-privileges。
Docker 构建配置使用允许列表，不纳入 `.env`、模型、数据库或浏览器状态。
该步骤只是供自行运行的说明，本次候选准备没有构建或分发镜像。
安全停止使用 `docker compose -f compose.yaml -f compose.web.yaml stop web api`；不要清卷或全局 prune。

## 真实本地 AI

固定模型与来源见 [模型说明](models.md)。必须先自行核对许可和资源，再在独立环境准备依赖及模型。
不需要在常规单元、前端或 Mock AI 测试中下载权重。
真实运行配置需显式启用 `RAG_ENABLED`、`RERANKER_ENABLED`、`AGENT_ENABLED`，
选择 `LLM_PROVIDER_MODE=openai_compatible` 并设置最终回环 URL / 模型名。
启用 RAG 要求提供自己校准的 `EVIDENCE_MIN_RERANK_SCORE`；raw logit 不是概率。
Agent 还要求至少 32 字符随机 `AGENT_ACCESS_SECRET`，跨重启稳定且保存在私密配置中。

完成业务迁移后显式执行 `uv run --no-sync python -m researchflow.cli agent-setup`。
模型服务、API、前端保持回环监听，不启用模型运行时自带 Web UI 或工具执行功能。
文档和测试中的配置说明不代表本次已完成真实模型验收。

## 测试与隔离

先运行 README 中静态检查、Provider 安全和不依赖模型的单元测试。
真实 PostgreSQL 测试需要新的专用实例及合成凭据；仅设置测试库名不能证明连接对象安全。
测试入口忽略 `.env`，要求显式 `RESEARCHFLOW_TEST_DATABASE_HOST/PORT/USER/PASSWORD`。
核对这四项只指向新候选实例后，再设置 `RESEARCHFLOW_ENVIRONMENT=test`，执行
`uv run --no-sync python -m scripts.prepare_test_databases`。该脚本创建缺失的专用库并迁移，
不删除数据库；测试本身可能重建专用 schema，因此不得指向开发或其他人的数据库。

```powershell
# 仅在已确认的隔离测试实例上执行
$env:RESEARCHFLOW_ENVIRONMENT = 'test'
$env:RESEARCHFLOW_TEST_DATABASE_HOST = '127.0.0.1'
$env:RESEARCHFLOW_TEST_DATABASE_PORT = '55442'
$env:RESEARCHFLOW_TEST_DATABASE_USER = 'researchflow'
# RESEARCHFLOW_TEST_DATABASE_PASSWORD 由本地私密环境提供，不复用开发凭据
$env:RESEARCHFLOW_RUN_INTEGRATION = '1'
$env:RESEARCHFLOW_RUN_AGENT_POSTGRES = '1'
$env:RESEARCHFLOW_RUN_SECURITY_POSTGRES = '1'
uv run --no-sync python -m pytest -q -m "(integration or agent_postgres or security_postgres) and not local_docker and not real_embedding and not real_reranker and not real_llm"
```

`uv run --no-sync python -m scripts.validate_web --mode mock` 是真实 HTTP / 数据库、确定性 AI 的浏览器入口，
同样要求上述四个测试数据库变量；候选脚本只支持 `--mode mock`。
前端 `ui-mock` 项目只验证完全模拟 HTTP 的页面交互，不替代该入口。
所有实际结果应逐次记录 passed / failed / skipped / deselected；没有执行的组应说明阻塞原因。
旧评测程序和原始结果未纳入本候选，旧冻结评测不能由这些命令复现。

P4专门的隔离服务、测试子进程、Docker持久化与浏览器验收步骤见 [本地验收](local-validation.md)。
