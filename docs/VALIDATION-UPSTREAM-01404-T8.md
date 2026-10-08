# Strata-T8 0.1.40-t8.4 验证

同步上游 [v0.1.40.4](https://github.com/Niko1221/Strata/releases/tag/v0.1.40.4)，源码 `6674a0065fb96bacde33e3eb10f91a1df86f95f2`，CUDA/HIP 引擎均为 `0.1.40.4`。发行版本为 `0.1.40-t8.4`，独立节点保持 `1.0.7` 和 Protocol 1。

## 同步范围

完整合入 0.1.40.3 和 0.1.40.4 的累计变化：Windows AMD 自动专家缓存保留显存、HIP 完整 DLL 依赖与运行库诊断、网页空回答/思考历史、Tokenizer 并发、Docker CONFIG 与已有配置链接、MTP 路由 guard，以及 Linux Intel Arc 安装和 A 系列修复。

保留 T8 的请求绝对总时限、启动失败清理、视觉缓存私有快照、严格 JSON/工具校验、取消和双引擎释放、离线模型导入与更新回滚。将上游依赖作者本机旧 ZIP 的 HIP 集成测试改为读取当前匹配引擎，核对实际 PE 依赖及 EXE 旁 DLL 的完整摘要；正式发行不得跳过。

本整包提供 CUDA 13 和 Windows HIP 引擎。上游 .4 的 Pascal 修复位于其 CUDA 12 资产与 Linux 源码；本包显卡范围仍为 README 中的 RTX 20/30/40/50。AMD、Intel 和 Pascal 不作为本机硬件验收结果。

## CPU 与依赖

2026-10-08，严格回归通过应用 **1210 项**、安装器 **412 项**、独立节点 **239 项**、真实分词器 **9 项**，共 **1870 项**，零失败、错误或跳过。应用回归墙钟 476.407 秒。分词器检查包含并发与串行结果一致性，使用已有 IQ3_S tokenizer；不将本机 Python 3.12 的结果当作 Python 3.14 验收。内置 Python 3.12.10 的依赖检查通过。

网页两入口通过 JavaScript 语法检查，实际执行了空回答、思考内容、错误回合、历史顺序及未完成回答排除五个场景；不以此宣称浏览器视觉验收。Docker 的配置链接与 CONFIG 回归由 Linux CI 单独执行。

## 原生资产

| 官方资产 | SHA256 |
| --- | --- |
| strata-windows-x64.zip | `6844c477e74ba8e7305a4efd473ee3c9aaf15f9afc67e84db2db6076f9a3701a` |
| strata-windows-x64-hip.zip | `f1ce2ee1a88d16af79c09aef0cf9d25f658c3332b211c4cb90d64c1fb95f6b44` |

完整摘要通过后才解压，核对 BUILD.json、源码提交和发行 tag，给分发 EXE 加入 UTF-8 manifest，保留修改前后 SHA256。实际 HIP PE 依赖检查确认 amdhip64_7、amd_comgr、rocm_kpack 和三份 Microsoft C++ 运行库位于 EXE 旁，摘要与 rocm/bin 中各自原件一致。此项验证运行库打包，不代表 AMD 硬件推理。

## ComfyUI 实机

Windows、RTX 4060 Ti 16GB、128GB RAM；ComfyUI 0.38.0、PyTorch 2.7.0+cu128，节点 1.0.7，独立回环端口 18488、全新本机档案、32768 context。复用 IQ3_S、MTP、BF16 mmproj 和 DreamShaper 8，按 60GiB 可用 RAM / 12288MiB 可用 VRAM 门槛执行。

| 工作流 | 输出 | 本轮墙钟 |
| --- | --- | --- |
| 文本提示词 → SD 绘图 | 1 张 384×384 PNG | 36.781 秒 |
| 图片反推 → SD 绘图 | 1 张 384×384 PNG | 25.703 秒 |
| 两镜头分镜 → 顺序批量 → SD 绘图 | 2 张 384×384 PNG | 51.734 秒 |

先通过真实队列的服务加载节点，再依次执行上述工作流。继承同一 ComfyUI 进程和部分节点缓存；墙钟用于记录验收，不作为冷启动性能基准。每个 Strata 节点返回下游前复核语言/视觉 loaded、running、starting 均为 false；按 PID/创建时间确认原生进程退出，WebSocket 记录真实执行边界。完成后退出本轮自有托管服务与 ComfyUI。

## 本机 API

`127.0.0.1:8082` 部署新版源码与 0.1.40.4 引擎，按实际 PID、创建时间和命令核对所有权。完成加载中断、取消后加载、文本、看图、预填充中断、生成中断、取消后恢复、卸载、新进程重载、重载后生成十项验收。最终双引擎退出、进行中请求为零，空闲显存从 **15137MiB** 回到 **15137MiB**；服务保持按需可用。

功能源码和原生文件在严格回归、工作流与 API 验收期间逐项核对摘要。后续发行记录和节点 CI 固定引用的文档修改不改变上述功能文件。主模型、MTP、视觉和用户配置复用，没有重新下载这些权重。

## 分发范围

两版内置 Python、依赖、CUDA/HIP 引擎和逐文件清单。VisionReady 含固定视觉权重、配置及许可证；NoModels 不含权重；两版均排除主模型、MTP、本机档案、凭据和私人路线图。正式发布前再次核对包清单、摘要和来源，公开资产在发布后单独核验。节点保持 1.0.7，Registry 审核以官方状态为准。
