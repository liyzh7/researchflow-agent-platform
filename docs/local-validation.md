# 非真实模型验收入口

此流程验证 FastAPI、PostgreSQL/pgvector、LangGraph Checkpoint、人工审批和 React 交互。
Embedding、Reranker 和 LLM 使用明确注入的确定性实现，不验证真实 E5/BGE/Qwen 的效果。
素材均为新编的卡片/托盘/计数物文本，旧截图、冻结标签、语料和模型输出继续排除。

## 独立服务

`compose.validation.yaml` 使用项目 `researchflow-public-validation-p4`、回环端口55443、
专用容器和专用命名卷。它与普通候选演示实例也分开。以下 PowerShell 命令在候选根目录执行，
要求已准备项目锁定依赖；密码只在本地测试进程环境内传递。

```powershell
$env:RESEARCHFLOW_P4_DATABASE_PASSWORD = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
docker compose -f compose.validation.yaml config --quiet
docker compose -f compose.validation.yaml up -d --wait postgres
$env:RESEARCHFLOW_ENVIRONMENT = 'test'
$env:RESEARCHFLOW_DATABASE_HOST = '127.0.0.1'
$env:RESEARCHFLOW_DATABASE_PORT = '55443'
$env:RESEARCHFLOW_DATABASE_PASSWORD = $env:RESEARCHFLOW_P4_DATABASE_PASSWORD
$env:RESEARCHFLOW_LLM_PROVIDER_MODE = 'mock'
$env:RESEARCHFLOW_TEST_DATABASE_HOST = '127.0.0.1'
$env:RESEARCHFLOW_TEST_DATABASE_PORT = '55443'
$env:RESEARCHFLOW_TEST_DATABASE_USER = 'researchflow'
$env:RESEARCHFLOW_TEST_DATABASE_PASSWORD = $env:RESEARCHFLOW_P4_DATABASE_PASSWORD
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
uv run --no-sync python -m scripts.prepare_test_databases
```

只对首次创建的新测试服务生成密码。若专用卷已经存在，应使用其既有测试凭据并核实归属；
不要换密码后删除旧卷“重试”。没有任何命令需要读原项目配置或连接其他数据库。
这里使用管理员引导角色以便故障/迁移测试，不是生产应用角色设计。

## Python 与 Docker 持久化

```powershell
$env:RESEARCHFLOW_RUN_INTEGRATION = '1'
$env:RESEARCHFLOW_RUN_AGENT_POSTGRES = '1'
$env:RESEARCHFLOW_RUN_SECURITY_POSTGRES = '1'
uv run --no-sync python -m pytest -q -m "not local_docker"
```

保留5项真实模型测试的 opt-in 跳过，不设置对应模型开关。
本地 Docker 组会重启测试服务，必须在浏览器/其他数据库测试结束后单独执行：

```powershell
$env:RESEARCHFLOW_RUN_RESTART_TEST = '1'
$env:RESEARCHFLOW_TEST_DOCKER_CONTAINER = 'researchflow-public-validation-p4-postgres'
uv run --no-sync python -m pytest -q -m local_docker
```

测试先用只读 Docker 元数据核对项目标签、Volume、端口和容器身份，再用不可变容器ID重启或备份。
备份测试只恢复到随机新库，不覆盖原库。其数据文件放入 pytest 临时目录，源码中不附带备份。
不得把 `RUN_RESTART_TEST` 指向普通开发实例。

测试子进程通过 `tests/process_support.py` 的 `-I -S -B` 入口运行，显式选取候选源码并断言导入位置，
防止复用某个已安装环境时被 editable `.pth` 指向其他 checkout。解释器及依赖仍需预先可信。

## 浏览器

```powershell
uv run --no-sync python -m scripts.validate_web --mode mock
$env:RESEARCHFLOW_UI_MOCK_ONLY = '1'
pnpm --dir frontend exec playwright test --project=ui-mock
```

第一组使用真实 HTTP、单独 `researchflow_web_e2e_test` 数据库和 Mock AI，检查登录撤销、
上传/重复导入、RAG引用、审批幂等、拒绝、跨用户访问和失败工作流恢复。
受控的单次Provider超时仅存在测试服务中，不是产品API的故障开关。
第二组完全模拟HTTP响应，只验证UI边界；两组分别计数。

本地可通过 `RESEARCHFLOW_WEB_FRONTEND_DIR` 指向锁定依赖的外置前端验证副本，
通过 `RESEARCHFLOW_WEB_CACHE_DIR` 保存临时日志/报告。实际执行的副本应与候选前端逐文件核对哈希。
脚本最终移除本次临时登录凭据；截图、trace和视频均关闭，不保存浏览器状态。

普通本地配置使用已有Chrome（Chromium内核）；CI在临时Linux runner安装锁版本的Chromium headless shell。
两种环境应分别记录。本机运行不等于已验证未来的精确CI镜像、Node patch版本或浏览器二进制。

测试完仅停止本次专用服务即可保留重现材料：

```powershell
docker compose -f compose.validation.yaml stop postgres
```

不要清理原数据库、卷或缓存。实际本地结果见 [README](../README.md)，CI边界见 [ci.md](ci.md)。
