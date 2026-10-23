# 系统架构

本说明依据当前候选源码整理，描述实现结构，不引用旧私有验收材料。

## 后端分层

FastAPI 的应用工厂与 lifespan 管理数据库连接、模型 Provider 和工作流生命周期。
`api/` 负责协议和 schema，`documents/`、`indexing/`、`retrieval/`、`rag/`、`agent/`
负责用例，`db/` 和各 repository 执行持久化。迁移由 Alembic 显式执行，应用启动不自动迁移。

| 模块 | 主要源码 |
| --- | --- |
| 应用生命周期 | `src/researchflow/main.py` |
| 配置与安全异常 | `src/researchflow/core/` |
| 登录、Session、审计 | `src/researchflow/auth/` |
| 文档解析与版本 | `src/researchflow/documents/` |
| 模型接口与 E5 | `src/researchflow/providers/` |
| Token 分段与索引任务 | `src/researchflow/indexing/` |
| 向量、关键词与 RRF | `src/researchflow/retrieval/` |
| CrossEncoder | `src/researchflow/reranking/` |
| EvidenceGate 与引用回查 | `src/researchflow/rag/` |
| 图、审批、回执 | `src/researchflow/agent/` |

## 文档与检索

TXT/Markdown 仅接受 UTF-8 文本，按实际读取字节限制上传大小。
CRLF/CR 统一为 LF，Markdown 保留源文本。默认字符窗口 800、重叠 100；
位置指向规范化文本的 Unicode code point，`char_end` 为开区间。
导入在同一用户与逻辑文档下比较当前版本的哈希及原字节；内容改变则新增版本，保留历史。

E5 索引使用固定 revision、384 维归一化向量，显式区分 `query: ` 和 `passage: ` 前缀。
长内容通过 tokenizer offset 分段，记录原文位置和模型标识；模型推理使用有界并发和超时。
pgvector 提供 cosine HNSW，关键词分支结合 PostgreSQL 全文、子串与 trigram。
中文没有专门分词器，不能把这种支持描述为完整中文语义检索优化。

Hybrid Search 使用排名融合 RRF，而不是把向量距离、关键词分数和 CrossEncoder logit 直接相加。
检索仅面向用户可见的当前文档版本；BGE 在候选集上打分排序。

## 证据问答

`RAGService` 是普通问答和 Agent 共用的链路：检索 → 重排序 → EvidenceGate →
上下文预算 → 结构化生成 → 引用回查。EvidenceGate 检查 raw-logit 阈值、词项覆盖和有限矛盾规则；
证据不足时提前拒答，尚未调用生成模型。

生成输出必须通过 Pydantic schema；CitationService 回查源记录、当前版本、位置和选定证据。
它可以阻止引用伪造或版本漂移，不能机械证明答案中的所有推理都被证据支持。
RAG 阈值必须在独立校准材料上选择，候选没有携带旧评测集或质量结果。

## Agent 与持久化

LangGraph StateGraph 复用 RAGService，使用官方 PostgreSQL Checkpointer。
图状态主要保存运行 ID、意图、工具计数和草案 ID；业务内容与执行回执保存在业务表。
任务草案经人工审批后才写入正式任务。详见 [Agent 与审批](agent-architecture.md)。

PostgreSQL 同时保存文档/版本/分块/索引、身份/Session、审计/模型调用、运行/草案/审批/任务，
以及单独的 Checkpoint schema。数据库约束、所有权谓词和事务承担持久化不变量，
图的 Checkpoint 不是业务审批或任务写入凭据。

## 前端与运行边界

React / TypeScript 提供登录、知识库、索引、问答、审批和只读任务页面。
Bearer 与 workflow capability 留在页面内存；刷新后重新登录，并按权限读取数据库中的运行状态。
文档和模型文本以普通文本渲染，网络访问拒绝重定向。

基础 Compose 为 Nginx / API / PostgreSQL，API 不直接发布宿主端口，RAG/Agent 关闭。
真实 AI 使用本地 E5/BGE 和回环 OpenAI-compatible 服务；模型资源不在镜像上下文内。
两种方式均是本地原型，尚未生产部署。
