# 独立候选的 CI 与验证边界

**独立公开版 GitHub Actions 尚待验证。** 本地候选未创建 Git 仓库，没有新远端运行和绿色 Badge。
原私有仓库的历史运行、截图、评测输出和测试总数没有被纳入候选的通过证明。

`.github/workflows/ci.yml` 保留以下四组意图，实际成功与数量必须在新候选运行后记录：

| 组 | 设计范围 | 限制 |
| --- | --- | --- |
| A | Python 格式、静态检查、依赖锁、不依赖模型的单元测试 | 不运行真实模型和数据库组 |
| B | 独立 PostgreSQL/pgvector、身份隔离、Checkpoint、审批事务 | 使用确定性 AI；排除本机 Docker 重启/备份与真实模型 |
| C | 前端 TypeScript、格式、构建和 Vitest | 不证明后端行为 |
| D | Chromium、真实 HTTP / PostgreSQL、Playwright Mock AI | 不下载 E5/BGE/Qwen 或执行真实模型推理 |

新增 `ui-mock` Playwright 项目完全模拟 HTTP，用于页面行为验证，与真实 HTTP / 数据库 Mock AI 分开报告。
旧真实模型浏览器脚本、截图和历史证据不在候选中。

## 工作流限制

使用普通 `push` / `pull_request` / `workflow_dispatch` 和 `contents: read`。
Action 引用固定完整 SHA，checkout 不持久化认证；没有自动部署、新建远端仓库、发布、镜像推送或 Artifact/cache 上传步骤。
一次性测试凭据是公开合成值，不能复用于真实实例。禁止接入开发数据库、真实模型密钥或本地浏览器账户。

依赖按锁文件安装，核心 Python 路径显式使用 `--no-default-groups --group dev`。
CI 的 PostgreSQL 是临时 runner 内服务；不能把本机命名卷映射进 CI。
运行不可信 PR 仍会执行其代码，未来创建远端后需单独决定分支保护、required checks 和外部 PR 审批。
固定 SHA 与最小权限不等同于这些远端规则已经启用。

## 结果记录规则

本地和将来云端运行分别记录命令、环境、passed、failed、skipped、deselected 和错误。
未启动数据库、缺少浏览器或未准备模型的组必须明确写未运行或阻塞原因。
Mock AI 不能算真实模型验收；deselected 不能算 passed 或 skipped。
替换测试材料后应重新运行相关链路，不能用原测试总数维持展示。

旧语料/标签/模型输出权利未决，已从候选范围排除。
当前候选的合成输入和测试变动应保留来源记录，未来不能根据旧冻结评测宣称可复现同样质量。

## P4本地非真实模型基线

2026-10-23本地Python完整307项为302 passed / 5 skipped / 0 failed / 0 deselected。
302包含204项单元（其中61项Provider）、95项数据库和3项本地Docker测试，不重复计算子集。
前端Vitest4项；Playwright真实HTTP/数据库/MockAI7项、全HTTP Mock UI3项。
真实模型的5项跳过没有删除或换标记。静态、格式、构建及许可证通知生成均通过。

未来四个Job的可核对范围为A单元204、B数据库95、C前端4、D真实HTTP浏览器7加UI-only3；
这只是应在新CI实测的基线，不是该工作流已运行成功。3项本地Docker测试不进入云端B组。
本机使用已有Chrome155（Chromium内核）及Node24.22.1，CI仍固定Linux、Node24.19.0和
Playwright1.64.1的Chromium headless shell。没有为本轮下载浏览器或升级锁文件。
完整隔离/持久化测试入口见 [本地验收](local-validation.md)。

本轮全新55444实例复验为299 passed / 5 skipped / 3 deselected，数据库95项通过；未重跑前轮容器的3项Docker测试。浏览器真实HTTP组已补编辑重审场景为8项，UI-only3项，前端4项；新离线frozen安装后实际执行。前轮302/5仅为历史。
