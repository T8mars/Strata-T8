# 视频对白与画面联合分析

可选语音转录使用 [Confucius4-R2T2](https://github.com/T8mars/comfyui-confucius-r2t2-t8) 的独立 GGUF 后端，默认关闭。勾选聊天页“视频分析设置 → 自动转录语音并分析对白”后，先转录选定的视频时间范围，再按同一时间轴分析画面与对白。每个视觉小段只使用实际交叠的对白，整片汇总按对白 ID 去重。

上传后修改分析时长、FPS、处理方式或视频模式，发送前会自动重新采样；失败或取消时不沿用旧范围发起分析。点击“整个视频”可清除开始时间和时长限制。页面分别显示原视频时长、选定范围和实际采样帧时间；最后一帧采样时间不代表视频结束时间。低帧率或可变帧率视频可能存在没有源画面的短窗口，这些窗口明确记录为缺少画面，相关对白仍保留，不补写未观察到的动作。

## 模型与设置

在“模型设置 → 可选语音转录 · 独立模型设置”填写自己的语音模型目录，保存设置。模型可位于任何本地磁盘，无需复制到主模型目录；保存路径不会启动语音推理。默认使用 CPU，不卸载已有 Strata 大模型。语言默认 Auto；中文夹英文可尝试 Chinese。轨道编号从 0 开始，可选择 mean、left、right 声道，以及人工毫秒偏移。

推荐最小的 2B Q4_K_M 配套文件：

| 相对语音模型目录的路径 | 大小 |
| --- | --- |
| `Confucius4-R2T2-Q4_K_M.gguf` | 1,107,404,736 bytes |
| `mmproj-Confucius4-R2T2-Q8_0.gguf` | 348,336,544 bytes |
| `FireRedVAD-ONNX/fireredvad_stream_vad_with_cache.onnx` | 2,306,042 bytes |
| `FireRedVAD-ONNX/cmvn.ark` | 1,311 bytes |
| `FireRedVAD-ONNX/LICENSE`、`MODEL_LICENSE`、`MODEL_LICENSE_zh` | 原始许可 |

主模型与音频 projector 合计约 **1.46 GB / 1.36 GiB**；不要使用视觉 projector 替代。Q8 主模型也受显式清单支持，但体积更大。VAD 目录可单独设置；留空时使用语音模型目录下的 `FireRedVAD-ONNX`。

下载优先 [官方 ModelScope](https://modelscope.cn/models/netease-youdao/Confucius4-R2T2-GGUF/files)，再使用国内镜像或 [官方 Hugging Face](https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF/tree/86ff0251cb9f456b63aeef5f80137f104e22869a)。辅助文件及许可见 [T8star 模型镜像](https://huggingface.co/t8star/Confucius-R2t2-Comfy/tree/2223a55593a85bbaf2b12d58c410da67a8822608)。需要自行准备时，在运行包目录显式执行：

```powershell
.\runtime\python\python.exe -X utf8 .\tools\prepare_asr_models.py --model-dir "D:\Models\Confucius4-R2T2-GGUF"
```

该命令会下载并核对固定版本、大小和 SHA256。普通启动、保存设置、关闭自动转录和软件更新均不下载语音权重。**Strata-T8 0.1.42-t8.8** 起默认提供独立 CPU Python/推理依赖；语音主模型、音频 projector 和 VAD 放在外置模型目录，不进入默认运行包。源码安装需要先准备 ASR 运行组件，详情见 `tools/build_asr_runtime.ps1`。旧版运行包请先更新；节点使用 **1.2.0** 或更新版本。

## 字幕、取消与错误

已有 SRT、VTT、Confucius result_json 或契约版本 1 的 timed transcript JSON，可直接粘贴到“已有时标字幕”；无需打开自动转录，不加载语音模型。SRT/VTT 使用输入视频时间轴；JSON 必须明确 `timeline=input` 或 `selected_window`。旧纯文本 transcript 仍作为无时标上下文，不生成虚构时间戳。

转录后可展开“对白预览与校对”，修改 SRT，点击“使用校对字幕”，下次分析复用修改后的对白；也可下载 SRT 和原始时标 JSON。字幕、缓存和模型路径保存在本机，不进入 Git 或发行包。升级保留 `asr-settings.json` 和 `.asr-cache/`。

Stop 取消当前请求并终止自有 ASR worker。无音轨、数字静音、未识别到语音、部分结果和失败分别报告；失败会停止本次音画分析，用户可关闭自动转录后明确选择仅分析画面。低音量非零音频仍送入识别，不按音量阈值直接跳过。

服务通过一次性 CPU 工作进程解码视频和提取音轨，最多同时运行一个媒体解码任务。取消或超时会结束自有工作进程；原生解码进程异常退出会返回明确错误，后续请求仍可继续。该隔离用于保护主服务，不表示已修复第三方原生库中的底层故障。

CPU 转录可与已驻留的主模型共存。CUDA 为可选运行组件；当前本机组件面向 RTX 4060 Ti（架构 89），须与自己的 Python 3.12、llama/ggml/CUDA DLL 匹配。CUDA 任务只在自有 Strata 服务空闲且串行服务时交接资源，不中断忙碌 DSH 请求，不查找或关闭其他应用；并行服务请使用 CPU。结束后 ASR 进程退出，再由下一视觉请求按需加载主模型。其他 GPU、AMD、Linux 的语音原生组件没有实机验收，不沿用文本引擎的支持声明。

## API 与 ComfyUI

API 新增 `GET /v1/asr/capabilities`、`GET /v1/asr/status`、`GET/POST /v1/asr/settings`、`POST /v1/audio/transcribe`、`POST /v1/video/transcribe`、`POST /v1/asr/transcript/parse`。沿用现有 API key、Host 与配置修改来源检查。视频上传上限 128 MiB，音频上传上限 256 MiB，选中范围最多 3600 秒。上传以 JSON base64 发送；ComfyUI 的客户端请求预算更小，长音频按节点提示缩短或分段。

独立 [Strata ComfyUI 节点](https://github.com/T8mars/Comfyui-Strata-T8) 使用同一 runtime 的模型设置和接口，新增语音转录节点及现有视频节点的可选音频/时标字幕输入。工作流保存 profile 名称；私有路径和 API key 不写进工作流。更新本地节点后重启 ComfyUI。

时标来自 VAD/识别分段，属于段级近似范围，不是逐词或口型强制对齐。对白是转录数据，画面与对白对应关系仍需校对；多人重叠、音乐人声、跨段长句可能产生识别或时间误差。功能不会自动识别说话人、环境音或音乐，也不能仅凭声音与人物同时出现就确定是谁在讲话。

代码来源、固定 llama.cpp 提交与许可记录在 `licenses/asr/`；模型适用网易有道模型许可，FireRedVAD 保留其许可。感谢网易有道、FireRedVAD、llama.cpp 及原项目贡献者。
