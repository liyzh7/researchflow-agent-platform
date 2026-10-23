# 官方参考与实现取舍

以下保留项目使用的官方 API 与设计来源。链接作为参考，不复制完整上游项目、模板或文档。
实际 API 使用与固定版本以源码、`pyproject.toml`、`uv.lock` 和前端锁文件为准。
本说明不包含旧私有测试结果、截图或评测语料。

| 官方来源 | ResearchFlow 中的应用 |
| --- | --- |
| [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)、[文件上传](https://fastapi.tiangolo.com/tutorial/request-files/) | 应用工厂、资源生命周期、有界 multipart 上传和 schema 校验 |
| [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | `RESEARCHFLOW_` 前缀、SecretStr 和启动配置校验 |
| [SQLAlchemy async ORM](https://docs.sqlalchemy.org/en/21/orm/extensions/asyncio.html)、[Alembic async cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic) | 显式事务、会话边界、手动迁移；不在启动时自动升级数据库 |
| [PostgreSQL 16 约束](https://www.postgresql.org/docs/16/ddl-constraints.html)、[锁](https://www.postgresql.org/docs/16/explicit-locking.html) | 文档版本、审批与幂等回执的不变量；advisory lock 序列化工作流 |
| [PostgreSQL 全文检索](https://www.postgresql.org/docs/16/textsearch.html)、[pg_trgm](https://www.postgresql.org/docs/16/pgtrgm.html) | 关键词、子串、trigram 分支；不声明完整中文分词支持 |
| [pgvector](https://github.com/pgvector/pgvector)、[pgvector-python](https://github.com/pgvector/pgvector-python) | 384 维 cosine HNSW、SQLAlchemy 向量列与 Hybrid RRF |
| [SentenceTransformer](https://www.sbert.net/docs/package_reference/sentence_transformer/SentenceTransformer.html)、[CrossEncoder](https://www.sbert.net/docs/package_reference/cross_encoder/cross_encoder.html) | 固定本地快照、CPU 推理、有界线程、offset 分段和 raw-logit 重排序 |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | 官方 Checkpointer、interrupt / resume；业务回执与 Checkpoint 分开提交 |
| [Psycopg async](https://www.psycopg.org/psycopg3/docs/advanced/async.html) | Windows Selector event loop、独立连接池与 PostgreSQL Checkpoint |
| [pwdlib](https://frankie567.github.io/pwdlib/)、[Python secrets](https://docs.python.org/3/library/secrets.html) | Argon2id、随机会话凭据；数据库仅保存 Session 摘要 |
| [Python urllib](https://docs.python.org/3.13/library/urllib.request.html) | 私有 opener 拒绝所有重定向；安全异常映射，不依赖默认自动跳转 |
| [React](https://react.dev/learn)、[TypeScript](https://www.typescriptlang.org/docs/)、[Vite](https://vite.dev/guide/) | 状态明确的本地审批工作台，同源 API 和普通文本渲染 |
| [Playwright](https://playwright.dev/docs/intro)、[Vitest](https://vitest.dev/guide/) | 独立 Mock 标注、浏览器协议与组件行为测试 |
| [OpenTelemetry](https://opentelemetry.io/docs/languages/python/) | 本地有界 tracing 和业务关联；不自动启用云 tracing |
| [uv 依赖同步](https://docs.astral.sh/uv/concepts/projects/sync/)、[Compose](https://docs.docker.com/compose/) | 锁定依赖、显式 AI group、独立本地容器资源 |

模型固定来源与许可分别见 [models.md](models.md) 和 [license-review.md](license-review.md)。
EvidenceGate 的简单覆盖/矛盾规则、所有权检查、引用验证、事务审批和前端业务逻辑属于项目实现；
框架和模型能力保留上游归属。引用官方设计不能替代源码来源与素材权利的最终确认。
