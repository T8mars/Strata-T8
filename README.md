# Strata-T8

[Strata](https://github.com/Niko1221/Strata) 的 Windows 整合发行版。在本机运行 Qwen3.8-Flash-Next，提供网页聊天和兼容 OpenAI / Anthropic 的 API。

**内置 Python、Python 依赖、NVIDIA/AMD 引擎及运行库；不包含任何模型权重。** 无需安装 Python、Git 或 CUDA Toolkit，仍需兼容的显卡及驱动。

## 下载与启动

1. 从 [最新 Release](https://github.com/T8mars/Strata-T8/releases/latest) 下载 `Portable-NoModels.zip` 并解压。
2. 将另行提供的完整 `Strata-data` 目录放到程序目录内或程序目录旁；其他位置用 `IMPORT-MODEL.bat` 导入。
3. 双击 `CHECK-ENV.bat` 检查电脑，再运行 `START-HERE.bat`。
4. 打开 <http://127.0.0.1:8080>。关闭启动窗口或按 Ctrl+C 停止服务。

Release 附有 SHA256；`VERIFY-PACKAGE.bat` 检查解压后的文件。GitHub 自动生成的 Source code 压缩包是源码，请下载独立的整合包资产。

## 模型下载

默认使用 **Qwen3.8-Flash-Next GSQ-RCO IQ3_S**。主模型两份 GGUF 合计约 **83.62GB**，另需约 **5.22GB** 的 MTP 辅助权重及预处理空间。

| 来源 | 地址 |
| --- | --- |
| ModelScope，优先 | [官方量化模型](https://modelscope.cn/models/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF) · [MTP 原始 checkpoint](https://modelscope.cn/models/Qwen/Qwen3.8-Flash-Next) |
| 国内镜像 | [IQ3_S GGUF](https://hf-mirror.com/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S) |
| Hugging Face | [IQ3_S GGUF](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S) · [原始 checkpoint](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) |

收到完整 `Strata-data` 可直接导入。自行下载时运行 `PREPARE-MODEL.bat`：按 ModelScope → 国内镜像 → Hugging Face 获取缺失文件、验证 SHA256，并准备模型目录。只提取 MTP 所需张量，无需下载整个原始 checkpoint；已有正确 GGUF 会复用。**普通启动和程序更新不下载模型。**

## 环境要求

- Windows 10/11 x64，支持 AVX2 的 CPU，SSD。
- NVIDIA RTX 20/30/40/50 系列，建议至少 12GB 显存，驱动至少 580。
- AMD 支持以 [上游兼容列表](docs/AMD_HIP.md) 为准，需要对应 Adrenalin 驱动。
- IQ3_S 建议 96GB 以上内存；其他配置参考 [模型说明](docs/MODELS.md)。默认文本模式，视觉权重需另行准备。

本机验收：RTX 4060 Ti 16GB、128GB 内存。AMD 引擎已包含，尚未做本机 AMD 硬件推理验收。

## 更新

启动时异步检查新版本，离线仍可启动。退出服务后运行 **`UPDATE-PORTABLE.bat`**，自动下载最新正式版、校验并更新；保留模型、配置和浏览器聊天记录，替换失败时回滚。

仓库每日检查上游正式版本，测试通过后自动合并、打包并发布；可在 [Actions](https://github.com/T8mars/Strata-T8/actions) 手动同步上游 `main`。冲突、测试失败或缺少匹配引擎时停止自动发布。[更新机制](docs/UPDATING-T8.md) · [路线图](ROADMAP.MD)

## API 与来源

OpenAI Base URL：`http://127.0.0.1:8080/v1`；Anthropic：`/v1/messages`；Responses：`/v1/responses`。默认仅监听本机；对外监听必须设置 API key。

上游：[Niko1221/Strata](https://github.com/Niko1221/Strata) · [上游 README](README-UPSTREAM.md) · [整合包使用说明](README-PORTABLE.zh-CN.md)。Strata 源码遵循 [MIT](LICENSE)；包内保留第三方许可证，模型遵循各自许可证。
