# 源码候选许可证审查

审查日期：2026-10-23。范围为本候选实际源码、锁文件、声明和模型来源说明；
这是工程核对，不是完整 OS SBOM、漏洞审计或法律清关意见。
根 [LICENSE](../LICENSE) 原样保留，仅覆盖有权许可的 ResearchFlow 自有部分。

## 本次实际核对

[license-inventory.json](license-inventory.json) 保留 119 个 Python、94 个 npm 锁定包的名称、
版本、许可元数据和来源。当前 `uv.lock` 与 `frontend/pnpm-lock.yaml` 的包集合逐项相同，无缺项或多项。
这包括条件平台依赖，并不表示所有包都已安装。
本次对已有依赖环境的 227 个实际 wheel LICENSE/NOTICE 文件计算 SHA-256，与清单全部一致；
没有把 wheel 或该环境复制入候选，也没有下载依赖包重新审查。

[frontend-license-inventory.json](frontend-license-inventory.json) 是保留的 50 个已安装平台包元数据，
全部属于前端锁集合；它不是跨平台完整清单。完整锁集合使用上面的 94 项。
清单是许可来源记录，不能代替每个实际再分发软件包的完整许可证和版权通知。

## 主要依赖边界

- FastAPI、Pydantic、SQLAlchemy、Alembic、LangGraph 等按清单保留各自许可，OTel 有 Apache-2.0 条款。
- Psycopg / psycopg-binary **3.3.6**、psycopg-pool **3.3.3** 均记录为 **LGPL-3.0-only**。
  [固定版本许可](https://raw.githubusercontent.com/psycopg/psycopg/3.3.6/LICENSE.txt)
  与 [对应源码](https://github.com/psycopg/psycopg/tree/3.3.6) 保留其来源。
  当前仅引用未修改依赖。未来分发包含这些包的镜像或程序前，需按实际组合处理适用的许可文本、
  对应源码、替换/重新链接及捆绑 libpq/OpenSSL 的义务；不能只凭一个源码链接判定履约完成。
- certifi、orjson、tqdm、Lightning CSS 等涉及 MPL-2.0 或复合表达式；以各锁版本和完整声明为准。
- NumPy、SciPy、Torch wheel 含捆绑组件，顶层 BSD 简称不能代替包内完整条款。
- React、React DOM 和 scheduler 的实际 LICENSE 由前端构建脚本复制至 `dist/third-party-licenses.txt`，
  不替换其版权。Playwright、Chromium 及构建工具有各自许可，浏览器二进制不在候选中。
- `uv_build 0.12.21` 是 pyproject 的构建后端，单独依据
  [uv 许可](https://github.com/astral-sh/uv/tree/0.12.21) 核对，不混入 119 个运行/开发锁包的数量。

## NVIDIA 五项：全部维持 NEEDS REVIEW

| 锁定包 | 当前候选包含 | 状态与后续边界 |
| --- | --- | --- |
| cuda-toolkit 13.0.3.0 | 名称、版本、下载元数据及哈希 | **NEEDS REVIEW**；需确认实际 Toolkit 组成和对应 EULA |
| nvidia-cuda-runtime 13.0.96 | 同上 | **NEEDS REVIEW**；需确认实际可分发文件与条件 |
| nvidia-cudnn-cu13 9.24.0.43 | 同上 | **NEEDS REVIEW**；需取得对应版本完整条款及通知 |
| nvidia-nccl-cu13 2.30.7 | 同上 | **NEEDS REVIEW**；上游源码许可不等于整个 wheel 已核验 |
| nvidia-nvshmem-cu13 3.4.5 | 同上 | **NEEDS REVIEW**；实际包内许可与捆绑物尚未逐项核验 |

这些包没有作为 wheel、源码副本或二进制进入候选。公开锁定引用与分发实际软件是不同范围；
源码准备不能据此写成所有 GPU 软件均允许再分发，也不能因元数据缺失推断一律禁止使用。
[CUDA 13.0.3 EULA](https://docs.nvidia.com/cuda/archive/13.0.3/eula/index.html)
对可分发内容和条件作单独规定，需结合实际文件逐项核对。
[NCCL v2.30.7-1 上游声明](https://raw.githubusercontent.com/NVIDIA/nccl/v2.30.7-1/LICENSE.txt)
包含 Apache-2.0 主体及保留 BSD 部分，也不能自动覆盖 PyPI wheel 的全部捆绑物。

Linux AI 条件依赖还包括其他具有 NVIDIA 条款的 CUDA 组件。
核心 Docker 和 CI 的 `--no-default-groups` 路径排除 AI 组；裸 `uv sync` 的默认组仍含 AI。
本次不修改锁版本来隐藏许可未决项，不下载 CUDA/NVIDIA 软件包。

## 模型与运行时

固定来源和准备方法见 [models.md](models.md)。本次官方固定模型卡复核到的标记为：
[E5 MIT](https://huggingface.co/intfloat/multilingual-e5-small/blob/614241f622f53c4eeff9890bdc4f31cfecc418b3/README.md)、
[BGE reranker-base MIT](https://huggingface.co/BAAI/bge-reranker-base/blob/2cfc18c9415c912f9d8155881c133215df768a70/README.md)、
[Qwen Apache-2.0](https://huggingface.co/Qwen/Qwen3-4B-GGUF/blob/a9a60d009fa7ff9606305047c2bf77ac25dbec49/README.md)。
未取得并逐项核验所有模型的完整独立 LICENSE/NOTICE 附件，继续 **NEEDS REVIEW**。
模型卡许可标记不能保证训练数据、任意模型输出或科研用途不涉及其他权利。

llama.cpp 固定参考版本为 build 11680 / `b797b9ca816bbced1e71c29c8b28cb35b103b624`，
来源为 [官方源码](https://github.com/ggml-org/llama.cpp/tree/b797b9ca816bbced1e71c29c8b28cb35b103b624)。
其上游 MIT 来源记录不等于 portable、DLL 和捆绑库已清关；本次没有分发运行时。

## 当前范围与未决事项

候选不包含模型权重、CUDA/NVIDIA 包、Docker 镜像、可执行程序或 Python Wheel。
Dockerfile 和 Compose 是构建配置；未来分发镜像前仍需对实际 OS 包、Python 包、Node 构建产物、
运行时及捆绑物制作 SBOM，核对 GPL/LGPL/MPL/Apache 等适用义务。
Python/Node/Nginx/uv 与 Debian/Alpine 的许可不能统称项目 MIT。

未经确认的旧截图、语料、评测标签、模型输出和历史报告均未作为公开素材纳入。
新测试使用单独记录来源的合成输入；MIT 文件与“合成”字样均不能代替第三方权利证明。
独立源码候选仍需保留来源审查和实际发布前的协作策略决定，不宣称全部许可证已经合规。
