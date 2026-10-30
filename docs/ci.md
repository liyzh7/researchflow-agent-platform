# 独立展示仓库的 CI 与验证边界

[源码仓库](https://github.com/liyzh7/researchflow-agent-platform)的 [main 工作流](https://github.com/liyzh7/researchflow-agent-platform/actions/workflows/ci.yml?query=branch%3Amain)提供分支运行记录；README 的动态 Badge 跟踪 `main`。每个 Run 的结果只属于其精确 `head_sha`，不能把历史通过记录归给其他提交。

已核实的历史运行：

- P6 根提交 `4a234f7a05603f642c7418f59142ecfdce636173` 的[首次 Run 40059814698](https://github.com/liyzh7/researchflow-agent-platform/actions/runs/40059814698)：main push、attempt=1、四个 Job 均 SUCCESS，Artifact=0。
- 文档提交 `132b0634f3113f6dcddaa280f690a547a0106ff2` 的[Run 40999503085](https://github.com/liyzh7/researchflow-agent-platform/actions/runs/40999503085)：四个 Job 均 SUCCESS。

原私库的运行、截图、评测输出和测试总数不作为此新仓验收证据；项目未部署生产环境。

## P6 实际云端分组

| Job | 实际范围及通过数 | failed / skipped / deselected |
| --- | --- | --- |
| [A - Python quality and units (no AI weights)](https://github.com/liyzh7/researchflow-agent-platform/actions/runs/40059814698/job/120770675107) | Python 单元 204 passed；锁文件、依赖、Ruff、compileall通过 | 0 / 0 / 103 |
| [B - Real PostgreSQL, pgvector and durable Agent (deterministic AI)](https://github.com/liyzh7/researchflow-agent-platform/actions/runs/40059814698/job/120770675370) | 95 passed；真实 PostgreSQL/pgvector、迁移、隔离、Checkpoint、审批事务与回滚 | 0 / 0 / 212 |
| [C - Frontend locked build, types, format and units](https://github.com/liyzh7/researchflow-agent-platform/actions/runs/40059814698/job/120770675619) | Vitest 4 passed；类型、格式、构建及通知生成通过 | 0 / 0 / 不适用 |
| [D - Chromium, real HTTP and PostgreSQL (MOCK AI, not Qwen/E5/BGE)](https://github.com/liyzh7/researchflow-agent-platform/actions/runs/40059814698/job/120770675744) | 真实 HTTP/PG + Mock AI 8 passed；另有全 HTTP Mock UI 3 passed | 两组均 0 / 0 / 不适用 |

A/B 互斥合计 299 个不同 Python 用例。Provider 的 61 项（9+52 参数化）已包含在 A 中，不另加到299，也没有单独的云端 Provider Job。A 的103 deselected包含95数据库+3本机Docker+5真实模型；B 的212 deselected包含204单元+3本机Docker+5真实模型。103与212不可相加为315个独立未验证用例，不计作通过或跳过。

两个Job合起来仍未执行的是 **5项真实模型+3项本机Docker专项**。真实模型为E5向量/Tokenizer、BGE、LLM及真实E5/BGE+PG组合；本机Docker为容器重启文档/索引持久化、备份恢复。没有删除测试或将其替换成Mock PASS。云端B/D确实使用数据库；推理是确定性Mock，不是Qwen/E5/BGE质量、阈值校准或真实TLS/生产安全验收。

D的8项检查登录撤销、合成上传/去重/索引/引用、审批重放单次写入、编辑重审与旧审批失效、拒绝、用户隔离、超时恢复、无效UTF-8及恢复。另3项ui-mock完全模拟HTTP，只证明UI合约；不能将8+3统称为全部真实HTTP/数据库测试。旧真实模型浏览器脚本、9张历史截图及所有HOLD语料/标签/模型输出继续排除。

## 本地 P4 记录与云端的区别

2026-10-23 最后一次候选本地 P4 复验使用全新55444隔离实例：Python 299 passed / 0 failed / 5 skipped / 3 deselected，其中95数据库通过，Provider61已包含；真实模型5项因未准备而跳过，前轮专用容器的3项Docker未执行而deselected。前端Vitest4、真实HTTP/数据库/MockAI浏览器8、全HTTP Mock UI3分别通过。

同日更早一轮P4的302 passed / 5 skipped / 0 deselected属于历史，包含当时运行的3项本机Docker测试；它不代替最后复验或P6中未运行的Docker专项。当前浏览器验收以已补编辑重审的8项为准，历史原始证据未改写。

本地使用Chrome155（Chromium内核）及Node24.22.1；P6标准GitHub-hosted ubuntu-24.04实际使用Python3.13.16、uv0.12.21、Node24.19.0、pnpm11.25.0、Playwright1.64.1和Chromium Headless Shell156.0.8078.4/v1248。PostgreSQL16.15、pgvector扩展0.8.7，不能与Python pgvector客户端0.5.0混称。详细复现入口见 [本地验收](local-validation.md)。这些版本与结果属于上述历史运行，不能代替其他环境或后续 SHA 的实测。

## 工作流、费用与材料边界

workflow仅普通push/pull_request/workflow_dispatch，contents:read；Action固定完整SHA，checkout不持久化凭据，无pull_request_target、高权限Token、部署、发布、镜像推送或Artifact/cache上传。四Job仅标准ubuntu-24.04，临时PG服务无本机卷。Artifact0、Cache0，浏览器截图/trace/录像/凭据状态不上传。

核心Python安装显式--no-default-groups --group dev，HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE启用；未安装AI/CUDA组、下载E5/BGE/Qwen或调用收费API。公开一次性测试口令不能复用于真实实例。运行不可信PR仍会执行其代码，Fork审批与main保护以仓库实际设置为准；固定Action SHA、只读Token和成功CI不能证明这些规则已启用。

只分发经过审查的源码、配置与必要文档，不分发模型权重、CUDA/NVIDIA包、Wheel、镜像或可执行程序。NVIDIA五项仍NEEDS REVIEW；完整模型许可附件和未来制品的再分发义务未清关，源码公开不意味着所有依赖制品已获再分发许可。见 [许可审查](license-review.md)与[材料来源](materials-provenance.md)。
