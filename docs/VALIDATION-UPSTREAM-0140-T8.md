# Strata-T8 0.1.40-t8.1 验收

本版同步上游 [v0.1.40.1](https://github.com/Niko1221/Strata/releases/tag/v0.1.40.1)，源码提交 `82f46a8c8f475f001ad76d92f58f4a4f8ffb0253`，引擎版本 `0.1.40`。ComfyUI 节点保持 `1.0.6`、Protocol 1；最低运行包版本仍为 `0.1.39-t8.14`。

## 测试范围

2026-10-07，在 Windows、嵌入式 Python 3.12.10 上验证；测试复用现有模型，不下载主模型、MTP 或视觉权重。

| 检查 | 已验证结果 |
| --- | --- |
| 合并版 serve 单独回归 | 491 项通过，无跳过 |
| 安装器回归 | 332 项通过，无跳过 |
| 独立节点回归 | 218 项通过，无跳过 |
| 实际 tokenizer | 8 项通过，无跳过；使用现有 IQ3_S pack |
| application 最终严格门槛 | 1,044 项通过，无跳过；429.203 秒，包含新增 14 项发行门槛回归 |
| 两版实际 ZIP 审计 | NoModels 8305、VisionReady 8306 文件逐 SHA/CRC 通过；版本和源码提交匹配 |
| 真实整包升级 | t8.8 两版升级、同版本 NoModels 切换 VisionReady，三条路径全部通过 |
| 网页与 API 生命周期 | Chat/Monitor/About 浏览器检查通过，控制台无错误；实际加载、文本、视觉、取消、卸载及重载通过 |
| 新版 GPU 工作流 | 文本、图片反推、两镜头结构化分镜与批量采样全部通过，共四张 384×384 PNG；双引擎退出已核验 |

完整 CPU 门槛由 `tools/run_release_checks.py` 自动发现 serve/tools 新旧测试，独立运行 application、setup、tokenizer 三组。tokenizer 组必须指定已有 pack 路径；CI 不下载模型，也不把缺少 GPU 的检查算作推理验收。

正式发行的 application、setup 和独立节点测试均使用 `--strict`，必须实际执行非零测试且无失败、错误或跳过，才进入打包与发布。测试数量由发现结果记录，避免固定数量阻断后续上游增加测试。新增 14 项真实 CLI 回归验证严格/轻量模式、跳过、失败、空目录、导入错误和节点源码环境；轻量 CI 的已说明跳过策略保持不变。

服务器增加工具示例引用、正常结束及 stop、重启等待队列、致命错误、图片处理和 T8 双引擎状态回归。节点新增真实回环 HTTP 的 stop、批量顺序、结构化 502 后有限修复、按需视觉和双资源释放检查。

同步/分发新增热修复标签、精确历史连接、版本下限、无变化摘要验证、本地规划排除、发行资产与实际提交一致性回归。Windows 路径测试使用真实文件身份比较，工具 schema 测试保留新版合法别名。

发布器另有 28 项真实 Git/ZIP 回归，已包含在 application 门槛中。实际只读 GitHub 查询也验证了 REST 404 → GraphQL 查草稿的流程，没有为此创建或发布 Release。

[main 运行包检查](https://github.com/T8mars/Strata-T8/actions/runs/37521632737)已通过。该次轻量 CI 的 application 1,030 项中有 5 个因缺少 Windows 内嵌运行环境而跳过的启动入口测试；本机最终严格复测的 1,044 项无跳过，包含随后新增的 14 项发行门槛测试。该工作流的安装器 332 项与节点 218 项均无跳过，节点固定到包含新版兼容测试的提交 `3dcf5fbd828437b0e11b5bab5ee7cd83dfbcf9d1`。[节点独立 CI](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37515854351)的 Windows 218 项全部通过；Linux 217 项通过，跳过 1 个 Windows 文件系统大小写别名测试。实际 [上游同步](https://github.com/T8mars/Strata-T8/actions/runs/37521943469)确认已是最新正式版本，返回无变化并跳过发行。

## 实际运行包与升级

完整功能源码冻结于 `be93eac4a1b3370a8e3eddf30645d80ed924322f` 后构建两版；正式发行的 PACKAGE-MANIFEST.json 另记录最终包含文档和工作流更新的提交。实际 ZIP 审计逐文件验证 SHA256、CRC、元数据、引擎来源、UTF-8 manifest 和权重分类；没有主模型、MTP、用户配置或 roadmap。

从本地历史 t8.8 NoModels 和 VisionReady ZIP 实际解压，在中文、空格路径分别运行旧安装自己的内嵌 Python、真实 updater prepare 和 PowerShell apply，再用升级后的 Python 核对全部文件。第三条路径从新 NoModels 实际切换到同版本 VisionReady。传输仅改为已验证的本地 ZIP，不重新下载历史包或模型。

三次升级逐 SHA 保留设置、端口/API key/采样/引擎参数、用户笔记、中文文本、日志及主模型/MTP 占位文件。历史包中的受管 ROADMAP 被删除，当前私人 roadmap 未被读取或复制。三次真实 API 在独立回环端口验证 401/200 认证、版本、Protocol 1、双后端和视觉路径，并正常退出。原生引擎启动次数均为零；这些升级检查不代替 GPU 推理。

## 来源与本机部署

官方引擎资产固定于该上游 Release：

| 资产 | SHA256 |
| --- | --- |
| strata-windows-x64.zip | `cd264b2125fdb85e84a8264da2ab2343463e6c7f9a12f317d4ce7192cd2c6b33` |
| strata-windows-x64-hip.zip | `b2fa4a660409fa0f842d442a6991f359a7f0aebefec6f854c852a8b639621d7a` |

校验后替换本机引擎，旧树保留备份。三份分发 EXE 已验证 UTF-8 manifest，BUILD.json 记录修改前后摘要及上游来源。副本打包时复核这些记录，并在 PACKAGE-MANIFEST.json 中保存实际 T8 源码提交。

本机服务在 `127.0.0.1:8082` 启动，状态报告 package `0.1.40-t8.1`、engine `0.1.40`、Protocol 1；语言和视觉保持按需加载。升级前后端口、监听地址、API key、采样和全部引擎参数逐项比较相同。浏览器实际检查 Chat、Monitor、About，显示新版引擎与本机 API 地址，控制台无错误；此只读检查未改变设置或触发推理。

另在该服务实际验证加载中断、取消后加载、文本、图片识别、预填充中断、生成中断、取消后恢复、卸载、新进程重载及重载后生成共十项。图片回答正确识别红圆、蓝方和测试文字；重载使用不同 PID/创建时间的两份原生进程。最终卸载后双进程退出，显存可用量从测试前 15,166MiB 回到 15,272MiB。预填充取消恢复等待 10.031 秒，生成取消恢复等待 0.235 秒；这是该次实际请求的墙钟测量，不代表所有请求时延。

## 边界

已有 [上一版验收](VALIDATION-COMFYUI-T8.md) 的文本、视觉和实际采样证据属于旧引擎。本版 GPU 验收使用独立 ComfyUI 端口和本轮托管实例，只停止本轮创建且身份核对一致的进程，不影响用户其他程序。

实机环境为 ComfyUI 0.38.0、前端 1.53.10、PyTorch 2.7.0+cu128、RTX 4060 Ti 16GB / 128GB RAM。前两轮在其他程序并发占用资源时各完成一张文本图，但视觉开始前可用 RAM 低于 60GiB，被正确拒绝；两次首图耗时 272.187 和 273.609 秒，不能作为速度基准，也未据此推定具体延迟原因。

资源释放后，第三轮在全新自有 ComfyUI 的第一条工作流执行纯 SD 对照，checkpoint/CLIP 节点无输出缓存，生成一张 384×384 PNG，墙钟 11.672 秒。随后同一实例执行真实 Strata 队列加载、文本提示词 → 绘图、图片反推 → 绘图、两镜头分镜 → 顺序批量 → 两张绘图，分别耗时 22.969、24.547 和 50.609 秒。三图复用该实例的 CUDA 上下文与部分 Comfy 缓存，不能称为三次冷启动基准。各 Strata 节点向下游返回前均验证 language/vision 的 running/loaded/starting 为 false，并按 PID/创建时间确认原生进程实际退出；WS 记录逐节点边界及原生退出时间。第三轮未复现此前四分钟延迟，仍不足以确定前两轮的具体原因。

三类流程全部成功后，自有托管服务和自有 ComfyUI 按身份核验退出；本机 8082 保持按需可用且双引擎未驻留。全过程复用现有模型，保持 60GiB RAM/12GiB VRAM 门槛。

节点 1.0.6 已提交 Registry，Publisher `t8star` 和节点本身为 Active，但[版本状态](https://api.comfy.org/nodes/strata-t8/versions?include_status_reason=true)仍为 Flagged，不能称已通过安全审核。自动扫描记录 4 处进程启动、配置目录环境读取及网络操作；人工复核申请已通过[官方支持入口](https://support.comfy.org/)提交，并收到提交成功回执，审核结果待官方确认。当前可使用 Git 或节点 Release ZIP 安装。

AMD 引擎随包分发，暂无 AMD 实机；Windows AMD 视觉暂不支持。上游新增实验开关维持 opt-in，没有扩大硬件性能声明。API 默认保持严格 JSON/工具检查，[兼容开关](../serve/API_COMPATIBILITY.md) 需要按请求显式启用。
