## Strata-T8 0.2.6-t8.2

集中修复发布后六批审查发现的启动、更新与音视频问题。配套独立 ComfyUI 节点 **1.3.3**，保留上游 v0.2.6、现有引擎和协议 1。[修复与验证范围](https://github.com/T8mars/Strata-T8/blob/v0.2.6-t8.2/docs/AUDIT-026-POSTRELEASE-T8.md)

- 托管配置正确选择 CUDA 12/13、AMD 分型及 DeepSeek 引擎；保留模型 API ID、纯文本限制和已有设置。
- 后台任务或可选更新线程启动失败时显示错误并恢复可操作状态，避免首次设置页被阻断。
- 完善更新与回退的文件校验、取消、重试及自有临时文件清理，保留用户数据和真实备份。
- 视频进度校验原片归属及记录结构；锁定文件清理失败可重试，未知文件保留。
- 补齐语音、视频和节点的取消检查；异常时标返回明确错误，损坏语音缓存可重算，可选语音配置损坏不阻断纯视频。

两版继续提供 **EXE、Python 3.12.10、完整依赖、CPU 语音环境及显卡引擎**；VisionReady 另含视觉权重与配置。主模型、MTP 和语音权重单独安装。完整 ZIP 使用校验分卷；现有用户可在设置页一键更新或运行 `UPDATE-PORTABLE.bat`。私有 roadmap 不发布。

本轮为 CPU/HTTP、网页、轻量进程和更新事务验证，未重新测量大模型 GPU 推理或语音识别准确率。发布前完整测试、环境升级回退及公开更新信息的核对结果另附本版 Release。

## Strata-T8 0.2.6-t8.1

同步上游 **v0.2.6**，拆分应用、Control 与引擎版本管理，保留 T8 的 EXE 和网页入口、SC117、API 模型名称、自动模型切换、局域网鉴权、视频完整时间范围、可选语音对白融合及组件更新。配套独立 ComfyUI 节点 **1.3.2 / 协议 1**。

新增已有 **DeepSeek-V4.1-Flash-1017 本地 pack** 接入，模型路径独立，默认完整 6 专家，Faster 5 专家需主动选择。当前接入为文本、思考与工具调用，最多 8K 上下文；图片和视频继续使用 Qwen/SC117。普通启动和导入不下载主模型。[DeepSeek 模型与限制](https://github.com/T8mars/Strata-T8/blob/v0.2.6-t8.1/docs/DEEPSEEK-T8.zh-CN.md)

完整环境包含 Python 3.12.10、锁定依赖、CUDA 12/13 和 AMD 分型引擎、DeepSeek 引擎及 CPU 语音环境；VisionReady 另含视觉权重。主模型、MTP 与语音权重独立安装。超过单资产限额的完整 ZIP 使用带校验的分卷，自动更新仍走原组件协议。上游定时同步只生成审查分支，验收后由维护者发布。[更新说明](https://github.com/T8mars/Strata-T8/blob/v0.2.6-t8.1/docs/UPDATING-T8.md)

修复工具调用结束时的参数保留、误把普通文本识别为调用、关闭服务时的子进程清理、Windows 配置替换、HEAD 重定向意外下载正文、短请求测速计时与超时分类，以及终端小窗口长路径遮挡保存选项。验证范围和硬件限制记录在 [本版验收](https://github.com/T8mars/Strata-T8/blob/v0.2.6-t8.1/docs/VALIDATION-UPSTREAM-026-T8.md)。

三轮联合检查进一步修复模型切换后的端口显示、DeepSeek 设置与文本模式参数、实际 API 鉴权提示、分卷合并失败重试、ASR 相对路径、视频进度存储与失败重试，以及节点排队时旧进度和连接配置被复用的问题。保留默认关闭的语音与进度功能。[修复与验证范围](https://github.com/T8mars/Strata-T8/blob/v0.2.6-t8.1/docs/AUDIT-026-T8.md)

发布复验补充修复 POSIX 备用进程守护：进程由运行转为休眠后仍能核对身份并清理子进程；僵尸状态不再被当成存活进程。Windows 继续使用已有 Job Object 机制。

## Strata-T8 0.1.45-t8.1

同步上游 **v0.1.45**，升级 CUDA/HIP 引擎，合入模型兼容、提示缓存、工具调用解析、思考设置与配置诊断等稳定修复。新安装在低于 24GB 的 NVIDIA 卡上自动推荐 CPU 视觉编码；保留已有 CPU/GPU 选择及显式参数，实验性功能仍按需开启。

修复上游提示缓存与 T8 视频预检、上下文超限拒绝之间的交互，保留临时视觉文件清理和视觉子进程恢复。模型自动切换、局域网 API、视频完整时长分段、可选语音对白融合、自适应采样、进度恢复、自动更新与离线回退继续可用。配套 ComfyUI **1.3.1**，14 个节点 ID 与协议 1 不变。

3011 项严格回归与 Windows/Linux CI 通过。Windows / RTX 4060 Ti 16GB 与 SC117 IQ3_S 完成真实文本、图片恢复、12 秒原生视频 288 帧的时间覆盖、三段语音转录及真实 ComfyUI 工作流验证；ComfyUI 清空内存缓存后复用了 5 个磁盘阶段。AMD/HIP、Intel/SYCL 为编译验证，没有对应硬件推理结论；不据此宣称长视频理解准确率。

两版内置 **Python 3.12.10、完整依赖、显卡运行库、CPU 语音环境和 EXE**。推荐 VisionReady，另含视觉权重与配置；主模型、MTP 与语音模型独立安装。现有用户在设置页一键更新或运行 `UPDATE-PORTABLE.bat`，模型与设置保留。[模型与路径](https://github.com/T8mars/Strata-T8/blob/main/README.md#模型下载) · [本版验证范围](https://github.com/T8mars/Strata-T8/blob/main/docs/VALIDATION-UPSTREAM-0145-T8.md)

## Strata-T8 0.1.44-t8.1

同步上游 **v0.1.44.1**，更新 CUDA/HIP 引擎及固定依赖源码。包含上游的旧 CPU 启动、CUDA 12.0/12.1 编译、安装时保留既有模型规格、Responses API 思考设置等修复。既有模型可继续使用，普通启动和更新不下载主模型。

保留 T8 的 EXE 启动器、无模型设置页、自动模型切换、完整 API ID 复制、默认关闭的带密钥局域网 API、视频全程分段、可选语音对白融合、进度恢复、组件更新与离线回退。配套 ComfyUI **1.3.0** 和协议 1 继续兼容。

修复视觉子进程意外退出后的恢复：主模型仍驻留时仅重启视觉进程，避免把本程序占用的显存误判为其他程序占用而持续返回 503。RTX 4060 Ti 16GB 实测保留主模型进程并恢复图片问答及提示缓存。12 秒、24 FPS 测试视频在网页原生模式完成 288 帧、20 段，覆盖至 11.96 秒，并结合三段带时间戳的真实语音转录。

两版继续内置 Python **3.12.10**、CPU 语音运行环境、显卡运行库和 EXE；VisionReady 含视觉权重及配置。**主模型、MTP 和语音模型独立安装**。[模型与路径](https://github.com/T8mars/Strata-T8/blob/main/docs/VIDEO-SPEECH.zh-CN.md) · [同步与验证范围](https://github.com/T8mars/Strata-T8/blob/main/docs/VALIDATION-UPSTREAM-01441-T8.md)

## Strata-T8 0.1.42-t8.10

新增 **Strata-T8.exe** 启动器，使用包内 Python，无需管理员权限；分发时压缩完整环境文件夹，批处理入口继续可用。新增默认关闭的**局域网 API**：在模型设置页选择访问范围，生成并复制 API Key 后保存，自动重启模型服务并显示局域网 Base URL。设置页始终只在本机开放；访问设置跨模型及升级保留。

修复网页一键更新的启动移交：确认辅助程序就绪后才停止本启动器拥有的服务；更新程序提前退出、启动超时或停止服务异常时保留设置页并显示错误。进度同时记录到 `logs/update-helper.log`。服务重启后旧网页重新获取会话；连接超时可恢复轮询，长时间断开时显示手动恢复说明。

启动依赖失败时标明具体组件，`CHECK-ENV.bat` 自动生成 `logs/runtime-diagnostics.json`，区分 PyAV 文件缺失、系统 DLL 不可用及运行环境问题，并记录内存余量、NVIDIA 状态、包入口和本地服务标识。诊断不下载或加载模型，不记录 API key、完整引擎参数或聊天内容。完善 64GB 内存及 `llama.cpp` 依赖的说明；没有依据用户截图擅自修改模型、内存算法或显卡参数。

验证覆盖启动入口、真实 Windows 更新辅助程序、更新前后的网页会话、完整 ZIP 与组件更新、配置保留及离线回退。发行包继续包含 Python 3.12.10、CUDA/HIP 引擎和 CPU 语音运行环境；VisionReady 含视觉权重、配置与许可证，主模型、MTP 和语音模型独立安装。上游及原生引擎保持 0.1.42，已有视频、语音对白融合和 ComfyUI 1.3.0 / 协议 1 继续保留，本版不改变原生推理计算。

现有用户从设置页或 `UPDATE-PORTABLE.bat` 升级；旧版网页更新没有响应时，可先关闭本整合包的启动窗口，再运行 `UPDATE-PORTABLE.bat`，完成后双击 `Strata-T8.exe`。首次安装建议下载完整 VisionReady ZIP；GitHub 的 Source code 不包含运行环境。

## Strata-T8 0.1.42-t8.9

新增默认关闭的**原片复用与视频进度恢复**：原片上传一次供分段和语音转录复用，已完成的分析与汇总在本机保存 24 小时；刷新或重启相同服务后可恢复。模型、配置或代码变化时拒绝混用旧结果。修复恢复时 12 秒输入与容器小数时长不一致的问题，保留原选择范围和片尾覆盖。

新增可选**自适应采样与跨段衔接**：在全程均匀覆盖的基础上分配部分预算到像素变化附近，使用上一段末帧与有界历史辅助判断。两种视频模式仍使用采样帧；默认均匀采样和已有语音对白融合继续可用。

**一键更新支持组件复用和成功升级后的离线回退**：校验本地文件，只下载变化或损坏的组件；初次安装继续下载完整 ZIP。成功升级保留经校验的备份，可在模型设置页查看并回退。模型、配置、ASR 缓存和视频进度保留。

配套 ComfyUI 节点 **1.3.0**，保留 14 个节点 ID 与协议 1。上游与 CUDA/HIP 引擎仍为 **0.1.42**，Python **3.12.10** 与 CPU 语音组件随包提供；VisionReady 含视觉权重与配置，主模型、MTP、语音模型、音频 projector 和 VAD 独立安装。既有权重可沿用。

验证覆盖 CPU/HTTP、网页恢复、服务重启、官方 ComfyUI VIDEO/PyTorch、组件校验与更新回退。缓存恢复使用受控模型响应检查重复调用；未据此宣称真实长片理解准确率提升。[视频使用与验证范围](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.9/docs/VIDEO-PROGRESS.zh-CN.md) · [组件更新与回退](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.9/docs/COMPONENT-UPDATES.zh-CN.md)

## Strata-T8 0.1.42-t8.8

新增默认关闭的**语音转录与音画联合分析**：外置 Confucius4-R2T2 2B Q4_K_M + Q8 音频 projector，模型路径独立设置；对所选范围转录一次，按时间戳关联画面与完整对白，支持手工时标字幕、校对和 SRT/JSON 导出。默认 CPU 转录不卸载驻留主模型。

修复上传后修改时长、FPS 或模式仍沿用旧采样；发送前自动重采样，“整个视频”清除旧范围，原视频时长、分析窗口和实际采样时间分开显示。低帧率空窗口明确记录，后续片段与对白继续分析。媒体解码在一次性进程中执行，异常退出最多干净重试一次；取消、超时和损坏结果明确失败，主服务可恢复后续请求。

配套节点 **1.2.0**，新增 Speech Transcribe 并保留原 13 个节点 ID、协议 1。节点修复首尾空窗口对白、字幕偏移与裁切/拼接时间轴。上游及 CUDA/HIP 主引擎保持 **0.1.42**，既有主模型与视觉权重可沿用。模型卡片显示完整 API ID，支持复制，用于 DSH/OpenAI 客户端配置。

两版均内置 Python **3.12.10**、运行依赖及独立 CPU 语音组件。**主模型、MTP、语音模型、音频 projector 和 VAD 不包含**；VisionReady 含视觉权重、配置与许可，Portable-NoModels 不含权重。默认包不含实验性 CUDA 语音组件。更新保留模型、配置及 ASR 缓存，继续支持一键版本更新与上游自动同步。

12 秒合成视频的真实 HTTP/ASR/native 链路覆盖 0–12 秒并包含片尾证据；24 FPS 分段采样取得 288 个不同 PTS。网页 38 项、视频/语音专项 163 项、节点 419 项通过。对白时间为段级近似，不是逐词或口型对齐；原生库偶发故障底层原因尚未确认，已验证进程隔离和恢复。[模型安装与路径](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.8/docs/VIDEO-SPEECH.zh-CN.md) · [验收范围](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.8/docs/VALIDATION-VIDEO-SPEECH-T8.md)

## Strata-T8 0.1.42-t8.7

模型设置页和聊天页 About 增加 **By T8star**、整合包/节点仓库，以及可展开的社媒与资源链接。包含 B站、YouTube、API、免费画廊、在线 AI 应用、ComfyUI 整合包和 Hugging Face；外部链接只在用户点击时打开。

设置页启动时自动检查最新正式版本，提供 **检查更新 / 一键更新**。点击后停止本启动器的服务，打开进度窗口，沿用既有 SHA256、文件清单、来源校验和失败回滚流程，完成后重新打开设置页。模型及配置保留，离线检查不阻塞使用；源码 checkout 继续通过 Git 更新。

配套节点 **1.1.3** 更新署名、仓库与资源说明，保留 13 个节点、协议 1 和既有视频功能。两版均内置 Python 3.12.10 与依赖；VisionReady 含视觉权重和配置，主模型/MTP 仍独立分发。[安装与更新](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.7/README.md)

## Strata-T8 0.1.42-t8.6

双击 **START-HERE.bat** 直接进入模型设置，没有主模型也能打开。点击其他模型卡片自动校验、停止当前服务并切换，显示进度后进入新模型聊天；手动输入路径仍可保存。启动后台检查新版本，离线可用。

视频分析设置默认折叠，按秒数和每秒观察帧数自动规划；10 秒、24 FPS 规划约 240 帧，逐段观察后汇总。修复上传错误无反馈、浮点边界漏帧及汇总时间范围，保留取消时已完成的段数和观察记录；原始视频与图片 payload 不保存到聊天存储。均匀画面可提供 CPU 像素统计，辅助核对颜色与位移。

配套独立节点 **1.1.2** 新增 Full Video Analyze，直接接 VIDEO 或原始 IMAGE 批次，保留原有节点 ID 与协议 1。12 秒、24 FPS 的 CPU 解码取得 288 个不同 PTS；SC117 IQ3_S / RTX 4060 Ti 16GB / 128GB RAM / 32K 上下文完成 2 秒、48 帧合成片的两模式实际推理，正确汇总红→蓝与白圆向右。复杂动作、真实长片及高密度长窗的 GPU 内容质量尚未验收；两种模式都使用采样帧，声音需另附转写文字。[视频说明与验证范围](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.6/docs/VIDEO-ANALYSIS.zh-CN.md)

两版内置 Python **3.12.10**、运行依赖与 CUDA/HIP 引擎；推荐 VisionReady，另含视觉权重、配置和许可证。主模型与 MTP 独立安装，现有权重可沿用。上游与原生引擎仍为 **0.1.42**；退出服务后运行 **UPDATE-PORTABLE.bat** 更新，节点独立安装并重启 ComfyUI。

## Strata-T8 0.1.42-t8.5

恢复网页启动入口的后台版本检查，发现新版本时在启动窗口提示，离线启动继续可用。双击 START-HERE.bat 即可进入模型设置，没有主模型也能打开；保存完整模型目录后进入聊天，聊天页可返回设置并更换模型。已有配置在下次启动时自动使用。

运行中可直接选择其他模型，点击保存后自动停止当前服务并切换；目录或端口输入无效时保留原服务。SC117 Uncensored 显示清晰名称和选中状态，聊天页也显示当前模型。NVIDIA 原生视频继续主动开启，未启用时提供设置位置提示。

两版内置 Python 3.12.10 与依赖，VisionReady 另含视觉权重和配置；主模型/MTP 独立分发。官方 IQ3_S 可在网页确认下载需求后准备，优先 ModelScope、国内镜像，再尝试 Hugging Face。上游和 CUDA/HIP 引擎仍为 0.1.42，独立节点保持 1.1.1、协议 1。参见[模型设置说明](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.5/docs/MODEL-SETUP.zh-CN.md)。

## Strata-T8 0.1.42-t8.4

首次启动直接打开本地模型设置页，没有主模型也可进入。支持发现或选择完整模型目录、检查文件、保存配置并进入聊天；聊天页可返回模型设置，停止当前服务后切换模型。页面显示处理进度与日志，端口占用时选择空闲端口。

官方 IQ3_S 可在网页确认约 89GB 的下载需求后下载与准备，沿用 ModelScope → 国内镜像 → Hugging Face 和固定 SHA256。普通启动、目录检查及导入不下载；其他变体使用完整模型包，避免混用权重。模型按需加载，关闭启动窗口结束本启动器的子进程。

两版继续内置 Python 3.12.10 与运行依赖；VisionReady 含视觉权重和配置，主模型/MTP 独立分发。上游与 CUDA/HIP 引擎仍为 0.1.42，配套节点保持 1.1.1 和协议 1。无浏览器启动、导入、更新与既有视频功能继续兼容。安装流程见 [模型设置](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.4/docs/MODEL-SETUP.zh-CN.md)。

## Strata-T8 0.1.42-t8.3

修复视频采样与分析的时间、证据、透明度和取消边界：MKV 时间偏移、小数裁剪、拼接时钟、采样预算及元数据一致性；原生末帧配对保留真实证据。配套 ComfyUI 节点 **1.1.1** 修复分组汇总引用、空提示词、混合帧尺寸、独立 alpha 和服务重启后的清理。

显式 PQ/HLG HDR 输入需先转 SDR/sRGB，当前不提供自动 tone mapping。普通 SDR 路径及协议 1 保持兼容；已有主模型、MTP 和视觉权重无需重新下载。

基于上游 **v0.1.42**，复用来源已验证的相同 CUDA/HIP 引擎，未修改原生数学。应用 1543 项、节点 347 项 CPU/HTTP 回归通过；新增输入边界经真实 PyAV、官方 VIDEO 与 torch CPU 复验。本版没有重新验收大模型 GPU 或真实长片理解质量；历史实机范围见[视频验收](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.3/docs/VALIDATION-VIDEO-T8.md)。

两版都内置 Python **3.12.10** 与运行依赖；VisionReady 另含视觉权重及配置，主模型/MTP 继续独立分发。退出服务后运行 **UPDATE-PORTABLE.bat**，节点单独更新并重启 ComfyUI。

## Strata-T8 0.1.42-t8.2

新增本地视频上传、真实 PTS 采样及带时间戳的视觉分析，配套 ComfyUI 节点 **1.1.0** 的视频采样、分段分析、汇总和证据输出。NVIDIA 另提供主动开启的双帧原生编码，保留官方视频 token、时间前缀和位置布局；默认文字、图片和协议 1 继续兼容。声音可附字幕或转写文本。

整合包内置固定 PyAV/FFmpeg 依赖及许可记录，CUDA 引擎和视觉 helper 由 T8 固定源码构建并记录七架构、来源和 SHA256。HIP 仍使用上游发行引擎。两版都含 Python；VisionReady 含视觉权重和配置，主模型/MTP 独立安装。退出服务后运行 UPDATE-PORTABLE.bat 升级，原生模式使用 `START-HERE.bat --native-video` 开启。

官方完整处理器黄金对照、CPU/CUDA 视觉数值、官方 IQ3_S 与 SC117 短视频真值、真实 Comfy 队列和资源释放均已有验收；具体范围见[视频说明](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.2/docs/VIDEO-ANALYSIS.zh-CN.md)。抽帧覆盖不能代表连续观察，长视频质量与 AMD 原生视频不在本次实机结论内。

## Strata-T8 0.1.42-t8.1

同步 [上游 v0.1.42](https://github.com/Niko1221/Strata/releases/tag/v0.1.42)，含 0.1.41 至 0.1.42 的累计变化，CUDA/HIP 引擎升级到 **0.1.42**。包括请求排队保活、取消后缓存恢复、工具调用与思考处理、上下文边界、Windows AMD RAM/commit 保护，以及多卡、Intel 和旧卡修复。上游适用 NVIDIA 配置的两个新默认加速选项可能改变回答，README 提供恢复旧行为的设置；不把上游速度作为本机测量。

修复模型目录默认值导致的整包导入/启动报错、按需加载首个 logit_bias 请求，以及请求大小配置未接通的问题，保留 T8 的请求总时限、严格 JSON/工具校验、视觉私有缓存、取消释放和更新回滚。独立节点保持 **1.0.7** 和协议 1；两版内置 Python **3.12.10** 与运行依赖，VisionReady 含视觉权重、配置及许可证，主模型/MTP 单独分发。现有模型和配置可沿用，退出服务后运行 UPDATE-PORTABLE.bat 升级。

验证范围、实机记录与资源限制见[本版验收](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.1/docs/VALIDATION-UPSTREAM-0142-T8.md)。

## Strata-T8 0.1.40-t8.4

同步 [上游 v0.1.40.4](https://github.com/Niko1221/Strata/releases/tag/v0.1.40.4) 及 0.1.40.3 的累计更新，配套 CUDA/HIP 引擎升级到 **0.1.40.4**。包括 Windows AMD 缓存显存预留和 HIP 完整依赖、网页空回答的对话历史、Tokenizer、MTP、Docker 配置及 Intel Linux 源码修复。T8 的请求超时、进程清理、视觉并发、鉴权和更新回滚继续保留。

独立 ComfyUI 节点保持 **1.0.7** 和协议 1。两版均内置 Python **3.12.10** 与运行依赖；VisionReady 另含视觉权重、配置及许可证，主模型/MTP 独立分发。既有模型与用户配置可直接沿用，退出服务后运行 UPDATE-PORTABLE.bat 升级。验证范围见[本版记录](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.4/docs/VALIDATION-UPSTREAM-01404-T8.md)。

**1870 项严格回归**通过，零失败、错误或跳过。RTX 4060 Ti 16GB / 128GB RAM 完成三类 ComfyUI 实际绘图、API 十项生命周期、双引擎退出和显存恢复。Windows AMD、Intel、Pascal 未做本机硬件验收；本包使用 CUDA 13，Pascal 修复属于上游 CUDA 12 版。

## Strata-T8 0.1.40-t8.3

修复 HTTP 慢流可延长读取时限、原生启动返回畸形或零 context 的 READY 时留下子进程，以及连续视觉请求在复制图像嵌入前发生缓存驱逐的问题。非有限环境超时回退默认值，显式 0 仍关闭超时。

与独立节点 **1.0.7** 配套；节点修复 Release ZIP / Registry 安装后的托管启动元数据缺失，并新增打包版本和兼容信息门禁。完整逐轮检查和验证范围见 [第八组报告](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.3/docs/AUDIT-20-ROUND8-T8.md)。

沿用上游及 CUDA/HIP 引擎 **0.1.40.2**、Python **3.12.10** 和协议 1。两版均包含运行依赖；VisionReady 含视觉权重、配置及许可证，主模型/MTP 独立分发。既有模型无需重新下载；旧整包运行 UPDATE-PORTABLE.bat 升级，模型、配置和用户文件保留。节点 Registry 审核以官方状态为准。

## Strata-T8 0.1.40-t8.2

同步 [上游 v0.1.40.2](https://github.com/Niko1221/Strata/releases/tag/v0.1.40.2)，更新至 0.1.40.2 CUDA/HIP 引擎。主模型、MTP 和既有视觉权重可直接复用，ComfyUI 节点 1.0.6 与 Protocol 1 保持兼容。

同步引擎的 KV、resident experts、多 GPU 和文件缓存修复；服务新增 READY/ENC 超时、分块 HTTP 请求、EXIF 图片方向校正、多图缓存保护、Responses JSON/工具校验和 Prometheus 输出。保留 T8 的取消清理、双引擎释放、严格 JSON/工具参数检查及更新回滚。实验性参数继续按需显式开启。

自动同步、构建和发布支持四段源码/引擎版本，发行包保持 0.1.40-t8.2，以兼容旧更新器；元数据和 SHA256 记录准确来源。补齐新运行文档，安装器测试隔离于本机安装，Docker 的 POSIX 回归由 Linux CI 执行。

两版均内置 Python 3.12.10、锁定依赖及 NVIDIA/AMD 运行库，不含主模型、MTP 或用户配置。VisionReady 另含固定 BF16 视觉权重、配置和许可证；Portable-NoModels 不含任何权重。附 SHA256 和逐文件清单。[下载与模型路径](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.2/README.md)

严格回归通过 1190 项应用、397 项安装器、219 项节点和 8 项分词器测试，无失败或跳过。RTX 4060 Ti 16GB / 128GB RAM 完成文本、图片、结构化批量到实际绘图，以及 API 十项生命周期、双引擎退出和显存恢复复验。AMD、Intel 无本机硬件验收；Windows AMD 视觉暂不支持。节点 Registry 审批仍待确认。[本版验证范围](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.2/docs/VALIDATION-UPSTREAM-01402-T8.md)
