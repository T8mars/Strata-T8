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
