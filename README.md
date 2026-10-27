# Strata-T8

**By T8star**

[Strata](https://github.com/Niko1221/Strata) 的 Windows 整合发行版，提供网页聊天及 OpenAI / Anthropic API。[ComfyUI 节点](https://github.com/T8mars/Comfyui-Strata-T8) 在独立仓库维护和发布。

**内置 Python、锁定依赖、NVIDIA/AMD 引擎及运行库。VisionReady 另含视觉权重、配置模板和许可证；主模型与 MTP 独立分发。** 无需安装 Python、Git 或 CUDA Toolkit，仍需兼容显卡驱动。

## 下载与启动

从 [最新 Release](https://github.com/T8mars/Strata-T8/releases/latest) 下载发行资产：

| 资产 | 用途 |
| --- | --- |
| VisionReady-NoMainModel.zip | 推荐，包含视觉编码器 |
| Portable-NoModels.zip | 不含模型权重，需另行安装视觉权重才能分析图片和视频 |

1. 解压运行包，双击 **Strata-T8.exe**，直接打开本地模型设置页；没有主模型也可以进入。`START-HERE.bat` 保留兼容。
2. 选择收到的完整 **Strata-data** 模型目录，点击 **保存并进入**。模型可放在其他磁盘，页面会检查文件、保存配置并启动服务。
3. 后续启动自动进入聊天；在 **模型设置** 点击其他模型卡片即可自动停止当前服务并切换，正在生成的回答会中断。手动输入路径后点 **保存并切换模型**。SC117 变体显示为 **SC117 Uncensored**。关闭启动窗口或按 Ctrl+C 停止。

端口被占用时自动选择空闲端口，以页面显示的地址为准。尚无模型可在设置页主动下载并准备官方 IQ3_S；需要勾选约 89GB 的流量和磁盘说明，普通启动不会下载模型。[模型设置说明](docs/MODEL-SETUP.zh-CN.md)

大于单个附件限制的完整包会分为 `.zip.001` 等分卷。下载同版本同类型的全部分卷、`.parts.json`、`*-ASSEMBLE.ps1` 和 `*-ASSEMBLE.bat`，放在同一目录，双击 `*-ASSEMBLE.bat` 校验并合成 ZIP，再解压。已安装用户继续使用一键组件更新。

Release 附 SHA256，VERIFY-PACKAGE.bat 检查解压文件。请下载发行资产，Source code ZIP 不含运行环境。[整合包说明](README-PORTABLE.zh-CN.md)

## 可选视频对白分析

视频分析设置中可勾选“自动转录语音并分析对白”，默认关闭。使用外置 Confucius4-R2T2 2B Q4_K_M 主模型与配套音频 projector，约 1.46 GB；模型路径独立设置，默认 CPU 转录，按真实时间范围关联对白与画面。支持已有时标字幕、字幕校对和 SRT/JSON 导出。语音权重不进入运行包。[模型安装、路径和使用说明](docs/VIDEO-SPEECH.zh-CN.md)

## 视频进度与更新

可选原片复用、分段与汇总进度恢复、自适应采样和跨段参考，默认关闭，详见[视频进度说明](docs/VIDEO-PROGRESS.zh-CN.md)。完整离线安装入口为 `Strata-T8.exe`；分发时压缩整个文件夹，保留包内运行环境。一键更新可复用未变化的组件，并保留成功升级的离线回退备份。[组件更新与回退](docs/COMPONENT-UPDATES.zh-CN.md)

## ComfyUI

独立 [Strata-T8 节点](https://github.com/T8mars/Comfyui-Strata-T8) 提供 Git 和 Release ZIP 安装，Publisher `t8star`，节点 ID `strata-t8`。Registry 版本审核通过后可在 ComfyUI-Manager 搜索安装。节点仓库提供模型路径、连接配置与示例工作流，涵盖文本、图片、批量分镜和视频分析。

支持文本生成、扩写/翻译、正负提示词、结构化分镜、JSON 提取、图片描述/反推/OCR、批量处理和服务控制。同卡推理结束后确认语言与视觉引擎释放，再返回下游采样；API key 保存在本机，工作流只存档案名称。

[节点说明](https://github.com/T8mars/Comfyui-Strata-T8#readme) · [实机验收](docs/VALIDATION-COMFYUI-T8.md) · [第七组 20 轮检查](docs/AUDIT-20-ROUND7-T8.md)

## 模型下载

默认 GSQ-RCO IQ3_S：主模型两份 GGUF 合计约 **83.62GB**，另需约 **5.22GB** 的 MTP 辅助权重及预处理空间。

| 来源 | 地址 |
| --- | --- |
| ModelScope，优先 | [主模型与视觉权重](https://modelscope.cn/models/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF) · [MTP checkpoint](https://modelscope.cn/models/Qwen/Qwen3.8-Flash-Next) |
| 国内镜像 | [IQ3_S GGUF](https://hf-mirror.com/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S) |
| Hugging Face | [IQ3_S GGUF](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S) · [MTP checkpoint](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) · [固定视觉版本](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/ed59f92082b1e93c0e96d60a8b11aab089b52f09) |

收到完整 Strata-data 可直接导入。自行准备用 PREPARE-MODEL.bat，按 ModelScope → 国内镜像 → Hugging Face 下载缺失文件并核对 SHA256，只提取 MTP 所需张量。普通启动和更新不下载主模型或 MTP。VisionReady 内置 **907,543,008 字节**的 BF16 mmproj，首次看图无需补下载。

| 路径 | 内容 |
| --- | --- |
| Strata-T8/vision/weights/mmproj-Qwen3.8-Flash-Next-BF16.gguf | 整合包内置视觉编码器 |
| Strata-data/portable-model.json | 完整模型数据描述 |
| Strata-data/models/IQ3_S/ | 两个主模型 GGUF 分片 |
| Strata-data/packs/iq3_s/ | 预处理专家包与分词器 |
| Strata-data/mtp/rt/ | MTP experts.bin、dense.bin、dense.txt |

Strata-data 可位于运行包内、旁边或导入的其他路径；仅下载主模型 GGUF 后需运行 PREPARE-MODEL.bat 完成准备。

## DeepSeek 本地文本模型

支持独立的 DeepSeek-V4.1-Flash-1017 pack 目录，默认 Full 完整质量，可选择 Faster。上下文最高 8192；当前适配的 native 文件仅支持文本，视频和语音融合请使用 Qwen。[文件要求、模型来源与导入](docs/DEEPSEEK-T8.zh-CN.md)。

## 环境与更新

Windows 10 1903+/11 x64、AVX2 CPU、SSD。NVIDIA RTX 20/30/40/50 系列建议 12GB 以上显存、驱动 580+；IQ3_S 建议 96GB 以上 RAM。RTX 4060 Ti 16GB / 128GB RAM 的验证范围见[本版验收](docs/VALIDATION-UPSTREAM-026-T8.md)。AMD 文本兼容以[上游列表](docs/AMD_HIP.md)为准，尚无 AMD 实机验收，Windows AMD 视觉暂不支持。

启动时后台自动检查版本；模型设置页提供 **检查更新 / 一键更新**，聊天页 About 也可进入。点击更新后停止本启动器的服务，打开进度窗口，自动下载、校验及安装，完成后重新打开模型设置页；模型与配置保留，失败回滚。命令行仍可在退出服务后运行 **UPDATE-PORTABLE.bat**。旧 NoModels 用户先更新，再用 **INSTALL-VISION.bat** 切换；节点通过自己的 Manager/仓库更新后重启 ComfyUI。

仓库每日检查上游正式版本并生成升级候选；完成合并审查和回归验收后，再手动触发发布。用户整合包仍自动检查 T8 正式版，并支持一键更新。[更新机制](docs/UPDATING-T8.md)

当前候选源码基于上游 [v0.2.6](https://github.com/Niko1221/Strata/releases/tag/v0.2.6)，应用版本 **0.2.6-t8.1**，配套节点 **1.3.1** 和 ComfyUI Protocol 1。同步模型兼容、API、提示缓存、安装诊断及后端修复。T8 的 EXE 启动器、局域网 API、自动更新、视频进度恢复、自适应采样、语音转录与音画联合分析继续保留。现有主模型和视觉权重可沿用；已发布整合包的版本以 [Release](https://github.com/T8mars/Strata-T8/releases/latest) 为准。[同步策略](docs/UPDATING-T8.md)

上游在适用 NVIDIA 配置中默认启用专家尾部跳过和实测 PCIe 分配，回答可能略有变化。需要恢复此前行为，可在运行配置的 `env` 对象设置 `"STRATA_ROUTE_TAIL_SKIP":"0"` 和 `"STRATA_PCIE_FRAC_DEFAULT":"old"`，然后重启；已有显式 `--pcie-frac` 参数继续优先。上游性能数字取决于硬件与模型，不代表本机速度。

OpenAI Base URL：页面所示服务地址加 `/v1`（空闲时默认 `http://127.0.0.1:8080/v1`）；Anthropic：`/v1/messages`；Responses：`/v1/responses`。默认仅本机。在模型设置页展开 **API 访问设置**，选择 **开放局域网 API**、生成并复制至少 16 位 API Key，再保存；已运行的服务自动重启。就绪后复制 **局域网 Base URL** 和实际模型 ID 给同一局域网的客户端，填写相同密钥。设置页仍只在本机开放。[访问设置](docs/MODEL-SETUP.zh-CN.md#局域网-api)

源码 [MIT](LICENSE)，视觉权重 [Apache-2.0](vision/LICENSE.txt)，其他组件保留随附许可证。感谢 [Niko1221/Strata](https://github.com/Niko1221/Strata)、[Qwen](https://huggingface.co/Qwen)、[ISTA-DASLab](https://huggingface.co/ISTA-DASLab)、[ComfyUI](https://github.com/Comfy-Org/ComfyUI) 和社区贡献者。[上游 README](README-UPSTREAM.md)

## T8star 与资源

**By T8star** · [整合包仓库](https://github.com/T8mars/Strata-T8) · [ComfyUI 节点仓库](https://github.com/T8mars/Comfyui-Strata-T8)

| 链接 | 地址 |
| --- | --- |
| B站 | [B站](https://space.bilibili.com/385085361) |
| YouTube | [YouTube](https://www.youtube.com/@T8star-Aix/) |
| API | [API](https://api.seedance.nz/sign-up?aff=5f4w) |
| 免费画廊 | [免费画廊](https://www.openzhenzhen.com) |
| 在线 AI 应用 | [在线 AI 应用](https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121) |
| ComfyUI 整合包 | [ComfyUI 整合包](https://pan.quark.cn/s/264edb7e36bd) |
| Hugging Face | [Hugging Face](https://huggingface.co/t8star) |
