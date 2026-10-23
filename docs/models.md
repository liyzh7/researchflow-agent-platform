# 模型来源与版本固定

候选仅保留模型调用与加载源码、来源和准备方法，不携带权重、缓存或运行时二进制。
本次未下载、加载或调用真实模型。以下下载命令仅供今后核对许可、资源和用途后自行执行。

| 用途 | 官方来源 | 固定 revision / 参考版本 |
| --- | --- | --- |
| Embedding | [intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small/tree/614241f622f53c4eeff9890bdc4f31cfecc418b3) | `614241f622f53c4eeff9890bdc4f31cfecc418b3` |
| 重排序 | [BAAI/bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base/tree/2cfc18c9415c912f9d8155881c133215df768a70) | `2cfc18c9415c912f9d8155881c133215df768a70` |
| 生成 | [Qwen/Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF/tree/a9a60d009fa7ff9606305047c2bf77ac25dbec49) | `a9a60d009fa7ff9606305047c2bf77ac25dbec49`，`Qwen3-4B-Q4_K_M.gguf` |
| 外部运行时 | [llama.cpp](https://github.com/ggml-org/llama.cpp/tree/b797b9ca816bbced1e71c29c8b28cb35b103b624) | build 11680，`b797b9ca816bbced1e71c29c8b28cb35b103b624` |

E5/BGE revision 是 `core/config.py` 的默认配置，并传入实际 SentenceTransformer / CrossEncoder 加载器。
加载器默认 `local_files_only=True`、`trust_remote_code=False`，找不到固定缓存时返回不可用。
Qwen 是 OpenAI-compatible 服务的参考模型配置：应用 HTTP 层不能远程证明服务实际加载的权重，
操作者必须核对服务启动参数和本地文件。llama.cpp 是外部服务，并非 Python 打包依赖。

## 可选下载方法

需要已安装 AI 依赖并可用的 Hugging Face CLI；本候选不会自动执行下载。

```powershell
hf download intfloat/multilingual-e5-small `
  --revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 `
  --cache-dir .cache/huggingface/hub `
  --include config.json model.safetensors modules.json README.md `
    sentence_bert_config.json sentencepiece.bpe.model special_tokens_map.json `
    tokenizer_config.json tokenizer.json 1_Pooling/config.json

hf download BAAI/bge-reranker-base `
  config.json model.safetensors sentencepiece.bpe.model `
  special_tokens_map.json tokenizer.json tokenizer_config.json README.md `
  --revision 2cfc18c9415c912f9d8155881c133215df768a70 `
  --cache-dir .cache/huggingface/hub

hf download Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf `
  --revision a9a60d009fa7ff9606305047c2bf77ac25dbec49 --local-dir .cache/models
Get-FileHash -Algorithm SHA256 .cache/models/Qwen3-4B-Q4_K_M.gguf
```

Qwen 文件参考大小为 2,497,280,256 bytes，参考 SHA-256 为
`7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`。
BGE `model.safetensors` 参考 SHA-256 为
`ced967c45fd1902eb92716c9ceeca7c95a936770ea9db611f5a841b926e33fbd`。
这些是保留的版本识别信息，本次没有下载文件重新计算，不能表述为候选的新推理或下载验收。

官方 llama.cpp 程序需自行按平台准备。参考 CPU 启动参数：

```powershell
llama-server --model .cache/models/Qwen3-4B-Q4_K_M.gguf `
  --host 127.0.0.1 --port 18080 --alias Qwen3-4B-Q4_K_M --ctx-size 8192 `
  --threads 8 --threads-batch 8 --device none --gpu-layers 0 --n-predict 1024 `
  --parallel 1 --no-ui --offline --reasoning off
```

运行时版本会影响 CLI 选项；应使用表中固定版本核对帮助文本。
API 配置使用最终 `http://127.0.0.1:18080/v1` 地址，所有 HTTP 3xx 都会被拒绝。
不要把模型服务暴露公网，也不要向未经批准的远程模型发送文档。

## 许可与质量边界

固定模型卡分别标记 E5 MIT、BGE MIT、Qwen Apache-2.0。
模型卡标记不等于完整 LICENSE/NOTICE 附件已核对，也不保证任意训练数据、输出或用途无第三方权利问题。
模型权重、llama.cpp portable 及捆绑 DLL 的再分发尚未清关；见 [许可证审查](license-review.md)。

旧评测语料、标签和输出已排除，候选不声称复现原模型质量。
EvidenceGate 必须在新授权材料上校准并独立验收；引用位置正确仍不保证答案全部正确。
