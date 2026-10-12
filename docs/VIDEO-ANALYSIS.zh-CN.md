# 视频分析

视频使用已有 Qwen 主模型和视觉 mmproj；主模型、MTP 继续独立安装，VisionReady 包内置视觉权重、Python 与 CPU 解码依赖。模型路径和下载来源见 [整合包说明](../README-PORTABLE.zh-CN.md)。

整合包无需另装解码器。直接运行项目源码的环境可用 `python -m pip install av==15.0.0` 安装 CPU 视频依赖。

网页上传 MP4、MKV、WebM、MOV 等本地视频，默认按真实 PTS 采样，最多保留 8 帧。输入上限 128 MiB，单个采样窗口最长 3600 秒；达到帧数上限时在整个选定窗口分布采样。报告中的采样范围不代表逐帧连续观察，短暂动作可能遗漏。声音不自动转写；可附 SRT、VTT 或转写文本。

ComfyUI 节点 1.1.1 另提供官方 VIDEO 和 IMAGE 批次输入、分段与有界汇总，支持概述、时间轴、分镜观察、问答、风格及提示词反推，输出文字、JSON、证据帧和覆盖信息。每条分析流水线复用一次模型加载并在结束或取消后执行清理。节点安装与示例见 [独立仓库](https://github.com/T8mars/Comfyui-Strata-T8)。

视频透明通道按白色背景合成。明确标记为 PQ/HLG 的 HDR 文件需先转换为 SDR/sRGB；当前不做 HDR tone mapping。裁剪和拼接保留校正后的源时间戳；混合尺寸帧在节点端保持比例并留白，manifest 记录实际输出尺寸。提示词反推为空时明确失败，不把普通报告当作生成提示词。

## 两种编码方式

| 模式 | 输入与能力 | 启用方法 |
| --- | --- | --- |
| frames | 带时间戳的图片序列，复用图片编码 | 视觉配置开启即可 |
| native | 每两帧融合，保留 video_pad、官方时间前缀和位置布局 | NVIDIA T8 自编译引擎，主动开启 |

整合包运行 `START-HERE.bat --native-video` 启用原生模式，`--no-native-video` 关闭。自定义配置使用 `vision.native_video: true`；服务据此为 helper 和引擎添加 opt-in 参数。默认文本和图片协议继续使用原先的 ENC/GENI/SVE1。AMD 发行引擎不宣称原生视频支持。

原生模式使用固定视觉权重的双帧 patch 融合；奇数帧复制末帧。一个 clip 内统一尺寸，融合时间取两帧时间的均值，按官方格式保留一位小数；原始 PTS 单独保留。编码缓存区分有序帧对，主引擎收到真实视频 token 和有序媒体布局。加载前视觉 helper 执行双帧工作区暖机。

## API

`GET /v1/status` 的 `capabilities.video` 返回合同版本、模式和限制。`native_verified` 仅表示本次已加载进程成功协商了能力，不能替代模型效果验收。

`POST /v1/video/sample` 接收 JSON：`data` 为本地视频字节的 base64，另可指定 `mode`、`start_s`、`end_s`、`sampling_fps`、`max_frames`。返回 `part` 与 `manifest`；采样在 CPU 上执行，不加载模型。可把返回的 `part` 放进 `/v1/chat/completions` 用户消息的 content 列表。

显式视频 part 使用 `type: strata_video`、`contract_version: 1`、`mode: frames/native`、`frames` 和 `metadata`。每帧包含内联图片 `source`、clip 时间 `timestamp_s`、源时钟 `source_timestamp_s` 和 `index`；`metadata.time_origin_s + timestamp_s` 必须等于源时钟。每次请求最多 64 帧，内容长度仍须适合模型上下文，服务不会静默截断。

所有接口沿用现有认证和本机监听规则；服务读取本地视频上传，不访问视频 URL。CPU 解码限制输入字节、像素、帧数、时间和保留内存；取消在解码、编码、生成和节点归并步骤分别检查。

## 来源与验证

原生数学按 Qwen 官方固定修订 `de4b8e4d43b917e7706784d8bb445c9af86a3540` 及 Transformers `55c3cc9f03cd4fbf2d27d382dcc8fcc544631f8e` 核对，视觉编码复用项目固定 llama.cpp 源码。感谢 Strata、Qwen、llama.cpp、PyAV/FFmpeg 和 ComfyUI 项目。

具体测试范围见[本版验收](VALIDATION-VIDEO-T8.md)。

已完成真实 CFR/VFR、非零时间原点、旋转和裁剪 CPU 检查；完整官方处理器 6 组黄金样本及项目/现有模型包模板共 12 组 token 与媒体 span 对照严格相同。真实视觉 forward 与 CPU/CUDA helper 输出做了数值对照。RTX 4060 Ti 16GB、128GB RAM、官方 IQ3_S 与 SC117 IQ3_S 的 8 秒合成真值视频，两种模式均正确识别红→绿→蓝背景和白圆向右移动；一次加载连续完成后确认双 native 退出。此案例不能代表长视频、全部动作或 AMD 效果验收。

真实 ComfyUI 0.38 队列完成两个视频分段、一次汇总、报告保存和 8 张采样图片下游保存；同卡交接测试把官方 ModelPatcher 注册的 4 MiB 验收模型移回 CPU，分配显存归零，Strata 结束后双进程退出。本项验证队列与资源管理，不代表本次重新跑过完整扩散绘图。
