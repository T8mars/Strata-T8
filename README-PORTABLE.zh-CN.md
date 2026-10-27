# Strata-T8 Windows 整合包

**By T8star**

内置独立 Python、锁定 Python 依赖、NVIDIA CUDA / AMD HIP 引擎及运行库。无需安装 Python、Git、编译器或 CUDA Toolkit；显卡驱动由电脑提供。VisionReady 包含视觉权重与配置模板，主模型和 MTP 独立分发。

完整运行包较大时，Release 提供分卷。将同版本的全部 `.zip.001` 等分卷、`.parts.json` 和 `ASSEMBLE` 两个文件放在一起，双击 `*-ASSEMBLE.bat` 校验并合成 ZIP。自动更新使用组件，无需手动合并。

DeepSeek 使用独立引擎和模型目录，默认完整质量；当前本地 native 包仅支持文本。[DeepSeek 安装与路径](docs/DEEPSEEK-T8.zh-CN.md)。

## 开始使用

1. 解压到可写的 SSD 目录，不要在压缩包内运行。
2. 双击 **Strata-T8.exe**，浏览器直接打开模型设置页；尚未提供模型也能进入。`START-HERE.bat` 同样可用。分发时压缩整个文件夹，EXE 需要包内的 `runtime` 等目录。
3. 选择收到的完整 **Strata-data** 目录，点击 **保存并进入**。可选择页面发现的本机模型或其他磁盘目录。
4. 配置完成后进入聊天。后续启动自动使用已配置的服务；在 **模型设置** 点击其他模型卡片即可自动停止当前服务并切换，正在生成的回答会中断。手动填写路径后点击 **保存并切换模型**。SC117 显示为 **SC117 Uncensored**。按 Ctrl+C 或关闭启动窗口停止。

设置页使用本地空闲端口，默认从 17880 开始；聊天/API 端口默认 8080，忙碌时自动选择空闲端口，以页面显示的地址为准。模型按需加载，首次请求可能需要等待。没有模型时，可在网页确认约 89GB 的下载需求后准备官方 IQ3_S；普通启动、目录检查和导入仍不下载模型。详见[模型设置](docs/MODEL-SETUP.zh-CN.md)。

网页可以直接设置其他磁盘的模型目录。高级用户也可执行：

```bat
IMPORT-MODEL.bat --data-dir "D:\模型\Strata-data"
START-HERE.bat --port 8081
START-HERE.bat --context 32768
START-HERE.bat --backend hip
START-PORTABLE.bat settings
START-PORTABLE.bat start --no-browser
```

首次启动或换电脑时在网页保存配置；配置保存在 `strata-*.json`，模型位置在 `portable-settings.json`。同一电脑升级程序后保留 API key、上下文及手动引擎参数，刷新运行库路径和必要的上游兼容设置；端口忙碌时网页启动选择空闲端口。主动配置或更换硬件时重新生成引擎参数；配置失败恢复原文件，恢复被文件锁阻止时明确报告。

## 独立模型目录

```text
Strata-data/
  portable-model.json
  models/IQ3_S/       两份 GGUF，合计 83.62GB
  packs/iq3_s/        索引、dense.bin、tokenizer
  mtp/               MTP 辅助权重及运行数据
```

接收完整目录后导入即可。只有 GGUF 时需运行 `PREPARE-MODEL.bat`，按提示下载缺少的 MTP 张量并完成预处理；不下载整个原始 checkpoint。

```bat
PREPARE-MODEL.bat --data-dir "D:\模型\Strata-data"
```

下载顺序：ModelScope、hf-mirror、Hugging Face，固定版本与 SHA256 校验。下载入口：[ModelScope GGUF](https://modelscope.cn/models/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF)、[ModelScope MTP checkpoint](https://modelscope.cn/models/Qwen/Qwen3.8-Flash-Next)、[国内镜像](https://hf-mirror.com/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S)、[Hugging Face](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S)。普通启动、检查及导入不下载模型。

## 程序更新

设置页 **一键更新** 会确认更新程序就绪，再停止本启动器的服务，自动下载、校验、安装并重新打开设置页。也可退出 Strata 后运行 `UPDATE-PORTABLE.bat`（`UPDATE.bat` 同样可用）。更新从 T8mars/Strata-T8 最新正式 Release 获取，优先复用未变化的组件；保留模型、配置、日志及浏览器聊天记录。新文件与自建文件重名时拒绝覆盖；替换失败回滚。

结果保存在 `.portable-update/result.json`，更新进度写入 `logs/update-helper.log`；成功升级后可在页面查看并离线回退。旧版不支持组件时使用完整 ZIP。GitHub 不可达时检查失败不影响本地启动。NoModels 更新后运行 INSTALL-VISION.bat 切换至 VisionReady；启动器记录发行类型并刷新视觉配置。旧版未记录类型的配置可用 `START-HERE.bat --vision gpu` 启用视觉。

聊天记录由上游网页保存在浏览器；继续使用相同的浏览器、地址和端口即可。异步检查不会强制升级或打断推理。

## 兼容与完整性

Windows 10 1903+/11 x64、AVX2 CPU、兼容显卡与驱动。NVIDIA 建议 12GB 以上显存、驱动 580+；IQ3_S 建议 96GB 以上内存。AMD 兼容列表见 `docs/AMD_HIP.md`。VisionReady 在导入兼容 Qwen 主模型后自动配置视觉，NoModels 默认为文本模式；Windows AMD 视觉暂不支持。RTX 4060 Ti 16GB / 128GB 内存已验收；AMD 实机推理尚未验收。

OpenAI API：`http://127.0.0.1:8080/v1`；默认仅监听本机。

局域网：在模型设置页展开 **API 访问设置**，选择 **开放局域网 API**，生成并复制密钥后保存。服务自动重启，就绪后显示可复制的局域网地址。其他设备填写此地址、实际模型 ID 和相同 API Key；模型设置页始终只在本机开放。Windows 防火墙需允许包内 Python 在专用网络使用当前服务端口。[详细设置](docs/MODEL-SETUP.zh-CN.md#局域网-api)

`PACKAGE-MANIFEST.json` 记录每个程序文件的大小与 SHA256；运行 `VERIFY-PACKAGE.bat` 可检查。包内保留 Strata、Python、gguf-py、Python 依赖和 CUDA/ROCm 的随附许可证。模型另遵循自己的许可证。

启动报 `DLL load failed while importing _core` 时，运行 `CHECK-ENV.bat`；它会标明失败的 Python 组件，并将诊断写到 `logs/runtime-diagnostics.json`。再运行 `VERIFY-PACKAGE.bat` 区分解压文件损坏与系统 DLL 不可用。不要只凭 `_core` 判断是 CUDA 或模型问题。

IQ3_S 可在部分 64GB 内存电脑上运行，但必须留出系统及其他程序的空间。内存接近满载且磁盘持续繁忙时，先查看当前空闲内存、上下文长度和服务日志；可关闭其他大型程序、降低上下文，或另行准备 IQ2_XS。加载阶段 GPU 利用率低本身不能证明 CUDA 失效。文件总大小包含留在 SSD 的数据，不等于全部驻留内存。

使用 Release 中的 `Windows-x64-VisionReady-NoMainModel.zip` 或 `Portable-NoModels.zip` 完整解压后启动；GitHub 的 `Source code` 不是含 Python 的整合包。聊天地址以设置页的“进入聊天”为准。包内 `llama.cpp` 用于 GGUF/视觉依赖，整合包聊天引擎仍为 Strata；若看到其他服务页面，请附地址、包版本和诊断结果反馈。

[项目与最新 Release](https://github.com/T8mars/Strata-T8) · [上游 Strata](https://github.com/Niko1221/Strata)

## 视觉与 ComfyUI

VisionReady 内置固定 BF16 mmproj、模板、来源和许可证；主模型/MTP 不包含。可用 START-HERE.bat --vision gpu、--vision cpu、--vision no 切换，--vision-tokens 指定图片 token 预算。更新保留用户编码方式和 token 设置。

ComfyUI 节点在 [Comfyui-Strata-T8](https://github.com/T8mars/Comfyui-Strata-T8) 独立维护，可通过 Manager 安装与更新；[安装、模型路径与工作流](https://github.com/T8mars/Comfyui-Strata-T8#readme)。

## 视频分析

网页支持上传本地视频。“视频分析设置”默认折叠，填写秒数与每秒观察帧数后自动分段计算；10 秒、24 帧/秒规划约 240 帧，逐段观察后汇总。修改时长、FPS 或模式会在发送前自动重新采样；点“整个视频”可清除旧范围。原片时长、分析范围与实际采样时间分别显示。节点 1.2.0 的 Full Video Analyze 可直接接官方 VIDEO / IMAGE，空画面窗口明确记录，不阻止后续片段与对白。

勾选“自动转录语音并分析对白”可联合分析视频音轨与画面，默认关闭。独立 CPU Python 与语音推理依赖已内置；在模型设置页填写外置 Confucius4-R2T2 2B **Q4_K_M 主模型、Q8 音频 projector 和 FireRedVAD** 的目录。语音权重不包含，最低两份 GGUF 合计约 1.46 GB。也可直接提供时标字幕，无需开启 ASR；支持校对与 SRT/JSON 导出。[模型来源、下载和路径](docs/VIDEO-SPEECH.zh-CN.md)

NVIDIA 原生双帧编码需主动启用：`START-HERE.bat --native-video`；关闭用 `--no-native-video`。它复用现有 Qwen 主模型和 VisionReady 视觉权重，无需额外模型。网页的视频模式可切换抽帧或原生编码；AMD 目前只保留既有文本引擎能力。详细限制、API 与验收见 [视频分析说明](docs/VIDEO-ANALYSIS.zh-CN.md)。

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
