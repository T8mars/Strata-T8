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
