# DeepSeek 本地模型

T8 0.2.6 使用独立的 `engine-dsv41` 引擎运行 DeepSeek-V4.1-Flash-1017。模型仍需单独提供；导入只检查本地文件，不下载权重。来源：[DeepSeek 官方模型](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)、[上游格式与模型说明](https://github.com/Niko1221/Strata/blob/v0.2.6/docs/MODELS.md#deepseek-v41-flash)。

## 选择模型

在模型设置页选择已准备好的 DeepSeek pack 文件夹。该目录应包含 `ds4.txt`、`tensors.txt`、`engram.bin` 和 `tokenizer/`。索引引用的所有权重分片必须存在；可以保存在其他磁盘，不需要复制到运行包。

保存后会使用独立配置，API 模型 ID 为 `deepseek-v4.1-flash-1017`，可直接在设置页复制。点击 Qwen、SC117 或 DeepSeek 卡片会停止当前服务并切换。

- **完整质量 / Full**：默认使用 6 个专家。
- **更快 / Faster**：使用 5 个专家，可能改变回答质量；实际速度须在自己的硬件上比较。
- 本版本上下文最高 8192。需要兼容此 CUDA 13 引擎的 NVIDIA GPU、580 或更新驱动、至少 12GB 显存和 46GB 内存。权重体积、剩余内存和 SSD 速度会影响加载与推理。
- 当前适配的 native 包只有文本主干和 Engram，未包含视觉和 DSpark。图片、视频、音画对白分析请使用 Qwen；不能仅凭 DeepSeek 引擎支持就认定当前模型文件具备这些能力。

## 已有 native GGUF 的索引准备

仅适用于配方标记为 `native-mxfp4-q8-dense-raw-engram-v1` 的完整两片 native GGUF。`IQ3_XXS.gguf.partial` 等未完成量化文件不能代替完整模型。

在整合包目录运行以下命令，按实际位置修改路径：

```bat
runtime\python\python.exe -X utf8 tools\dsv41\pack_gguf.py --model "D:\ds1017\artifacts\native" --out "E:\Strata-data-deepseek41-native" --raw-engram --chat-template "serve\fixtures\deepseek41\chat_template.jinja"
```

这一步读取文件头并生成索引和分词器，不重新量化、不复制约 502GB 的原始权重，也不补下载视觉或 DSpark。之后选择输出目录。原 GGUF 需要留在索引引用的位置；移动它们后需重新准备索引。

普通的上游兼容 DeepSeek GGUF 不使用 `--raw-engram`。有模板时沿用其模板；缺失模板或 Engram 元数据时，工具会报出缺项，不以其他模型模板替代。转换工具的完整选项见 `tools/dsv41/pack_gguf.py --help`。

## 验证范围

本次接入复用了本机已完成的 native 产物；暂停的 IQ3 实验未恢复，模型权重未重复下载。短回答验证不等于长文、推理或量化质量评测，也不代表官方完整 checkpoint 的所有能力。正式验证结果与硬件限制记录在本版验收文档中。
