# Strata-T8

[Strata](https://github.com/Niko1221/Strata) 的 Windows 整合发行版，提供网页聊天及 OpenAI / Anthropic API。[ComfyUI 节点](https://github.com/T8mars/Comfyui-Strata-T8) 在独立仓库维护和发布。

**内置 Python、锁定依赖、NVIDIA/AMD 引擎及运行库。VisionReady 另含视觉权重、配置模板和许可证；主模型与 MTP 独立分发。** 无需安装 Python、Git 或 CUDA Toolkit，仍需兼容显卡驱动。

## 下载与启动

从 [最新 Release](https://github.com/T8mars/Strata-T8/releases/latest) 下载发行资产：

| 资产 | 用途 |
| --- | --- |
| VisionReady-NoMainModel.zip | 推荐，包含视觉编码器 |
| Portable-NoModels.zip | 不含任何权重，兼容旧版更新器 |

1. 解压运行包，将完整 Strata-data 放到程序目录内或旁边；其他位置用 IMPORT-MODEL.bat 导入。
2. 运行 CHECK-ENV.bat，再双击 START-HERE.bat。
3. 打开 <http://127.0.0.1:8080>，关闭启动窗口或按 Ctrl+C 停止。

Release 附 SHA256，VERIFY-PACKAGE.bat 检查解压文件。请下载发行资产，Source code ZIP 不含运行环境。[整合包说明](README-PORTABLE.zh-CN.md)

## ComfyUI

独立 [Strata-T8 节点](https://github.com/T8mars/Comfyui-Strata-T8) 通过 Comfy Registry 发布，Publisher `t8star`，节点 ID `strata-t8`。版本审核通过后可在 ComfyUI-Manager 搜索安装；尚未显示时使用节点仓库的 Git 或 Release ZIP 安装。节点仓库提供模型路径、连接配置与三个示例工作流。

支持文本生成、扩写/翻译、正负提示词、结构化分镜、JSON 提取、图片描述/反推/OCR、批量处理和服务控制。同卡推理结束后确认语言与视觉引擎释放，再返回下游采样；API key 保存在本机，工作流只存档案名称。

[节点说明](https://github.com/T8mars/Comfyui-Strata-T8#readme) · [实机验收](docs/VALIDATION-COMFYUI-T8.md)

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

## 环境与更新

Windows 10 1903+/11 x64、AVX2 CPU、SSD。NVIDIA RTX 20/30/40/50 系列建议 12GB 以上显存、驱动 580+；IQ3_S 建议 96GB 以上 RAM。已在 RTX 4060 Ti 16GB / 128GB RAM 验证文本、视觉和实际采样。AMD 文本兼容以[上游列表](docs/AMD_HIP.md)为准，尚无 AMD 实机验收，Windows AMD 视觉暂不支持。

启动时异步检查版本。退出服务后用 **UPDATE-PORTABLE.bat** 校验更新当前发行类型，保留模型和配置，失败回滚。旧 NoModels 用户先更新，再用 **INSTALL-VISION.bat** 切换；节点通过自己的 Manager/仓库更新后重启 ComfyUI。

仓库每日检查上游正式版本，合并及测试通过后自动打包发布；可在 [Actions](https://github.com/T8mars/Strata-T8/actions) 手动同步 main。冲突、测试失败或缺少匹配引擎时停止发布。[更新机制](docs/UPDATING-T8.md) · [路线图](ROADMAP.MD)

OpenAI Base URL：`http://127.0.0.1:8080/v1`；Anthropic：`/v1/messages`；Responses：`/v1/responses`。默认监听本机，对外监听必须设置 API key。

源码 [MIT](LICENSE)，视觉权重 [Apache-2.0](vision/LICENSE.txt)，其他组件保留随附许可证。感谢 [Niko1221/Strata](https://github.com/Niko1221/Strata)、[Qwen](https://huggingface.co/Qwen)、[ISTA-DASLab](https://huggingface.co/ISTA-DASLab)、[ComfyUI](https://github.com/Comfy-Org/ComfyUI) 和社区贡献者。[上游 README](README-UPSTREAM.md)
