# ComfyUI 与 VisionReady 验收

日期：2026-10-06；版本：0.1.39-t8.5；上游源码和预编译引擎：0.1.39。

## 实际推理与绘图

NVIDIA RTX 4060 Ti 16GB、128GB RAM、Intel i7-14700KF、驱动 616.56。Qwen3.8-Flash-Next GSQ-RCO IQ3_S，32K 上下文，GPU BF16 视觉编码器。主模型和 MTP 复用已校验数据；视觉文件从 ModelScope 下载并与固定官方 SHA256 核对。

ComfyUI 0.38.0、前端 1.53.10、Python 3.11.6、Torch 2.7.0+cu128，复用本机 DreamShaper 8 SD1.5。额外依赖在独立验证目录，原环境未修改。采样为 384×384、Euler 8 步、CFG 7，使用通用算子；未启用要求 cu130 的优化算子。

| 工作流 | 实际结果 | 整图耗时 |
| --- | --- | --- |
| 提示词助手 → CLIP → KSampler | 1 张图，覆盖预加载 Strata 后交接 | 28.31 秒 |
| 图片反推 → CLIP → KSampler | 1 张图，卸载后重新载入视觉 | 32.28 秒 |
| 两镜头 JSON → Batch → STRING 列表 → KSampler | 2 张图，由真实执行器逐项传播 | 55.58 秒 |

各图完成后语言和视觉进程均不运行。相同文字图再次排队使用缓存，约 0.03 秒；Control 状态仍执行。单独图片问答识别红圆、蓝方块及 STRATA VISION TEST；文字返回 STRATA_OK，分镜 JSON 校验通过。

门槛本次设置 8GiB 空闲 VRAM、60GiB RAM，系统有其他 GPU 常驻程序；发行默认空闲 VRAM 门槛为 12GiB。时间仅用于所列环境验收，不代表其他硬件性能。

## 回归与边界

测试覆盖真实子进程加载/编码取消、超时退出与重载；语言死亡仍清理视觉；退出失败保留引用；四个 HTTP 协议断开取消载入；排队取消补偿计数。

节点测试覆盖批量复用载入、外部服务边界、私有凭据、无效配置保留、预加载交接、进程身份与退出竞态、配置互斥、JSON 类型和采样校验。临时路径包含中文/空格，原生协议使用已验证 ASCII 无空格路径。

发行只允许指定路径、大小、SHA256 的 mmproj；主模型、MTP、用户路径、key、聊天与日志排除。每个 ZIP 构建核对文件清单、CRC、SHA256，并强制小于 2GiB。

AMD 无实机验收，Windows AMD 视觉不支持。旧 ComfyUI 适配未做完整 GPU 工作流验收；第三方异步 GPU 分支不在串行示例保证范围内。

## 取消、崩溃与自动检查

本机真实 ComfyUI 队列取消：模型加载阶段 0.42 秒、生成阶段 2.11 秒、批量第二项 1.64 秒内完成清理，两类引擎均确认不运行。视觉编码取消另以真实子进程回归覆盖。

实际结束托管实例的语言引擎后，状态仍识别视觉驻留，unload 成功清理；结束自有 HTTP 服务后两类子进程退出，按记录启动新实例成功。没有结束其他用户进程。

本机应用/服务器/节点/更新回归 272 项通过，安装回归 258 项通过。GitHub [发行工作流](https://github.com/T8mars/Strata-T8/actions/runs/37363072650) 已通过相同测试并构建资产。

前端实际导入 API 示例为节点图；Strata 侧栏能按需启动懒加载服务，并隐藏 API key。

## 0.1.39-t8.6 中文路径修复

Windows 10 1903+ 的原生引擎采用应用 UTF-8 manifest，保留已有权限、依赖及资源语言；不修改系统 locale。构建拒绝修改有签名的二进制，记录修改前后 SHA256。[Microsoft 兼容说明](https://learn.microsoft.com/en-us/windows/apps/design/globalizing/use-utf8-code-page)

本机将 BF16 mmproj 放在含中文和空格的目录，真实 helper 启动及图片编码通过，返回 192 个视觉 token。前述三类 ComfyUI 工作流测量基于 t8.5 功能实现。

实际用旧版更新器完成 t8.4 NoModels → t8.5 NoModels → 同版本 VisionReady，再从 t8.5 VisionReady 更新到 t8.6；8015 个发行文件全部通过 SHA256。端口、API key、手动采样、GPU 视觉模式和 384 token 设置保留，用户自建文件、日志也保留。

升级后安装目录和独立模型路径均含中文与空格。在禁止外网访问的代理设置下完成启动；OpenAI Chat、Anthropic Messages、Responses 均返回算术结果 42。两次图片问答均识别红圆、蓝方块和 STRATA VISION TEST，中间卸载并重新载入，两类引擎退出已确认。

新增 7 项 manifest 回归及 1 项旧 Windows 拒绝测试通过，连同既有 530 项回归共 538 项；已覆盖签名程序禁止修改、已有 UTF-8 时保持字节不变、真实 Win32 资源写入、原权限/依赖/资源语言保留及重复构建。

## 正式发行

[t8.6 Release](https://github.com/T8mars/Strata-T8/releases/tag/v0.1.39-t8.6) 发布六个资产：两种 Windows 整合包、节点 ZIP 及各自 SHA256。源提交为 2b58a24e077a43aaa517020d28ef44b3b7c88ac4；VisionReady 为 1,922,392,794 字节，NoModels 为 1,208,706,263 字节。六个资产均核对 GitHub 官方 digest 与本地 SHA256，主模型和 MTP 未包含。

本机完整回归 280+258 项通过；[GitHub CI](https://github.com/T8mars/Strata-T8/actions/runs/37368043450) 通过，1 项真实解释器签名检查因 runner 解释器未签名而跳过，可控签名拒绝与实际资源更新测试运行。Windows Release 任务未取得 hosted runner，本版由同一源码提交在本机打包、校验和发布，后续自动发行工作流保留。
