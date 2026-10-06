# 20 轮联合检查（2026-10-06）

主审检查整合包、更新器与服务；独立子 Agent 检查 ComfyUI 节点。每轮使用不同检查目标，发现问题后增加有意义的回归。没有把同一套测试重复 20 次计为 20 轮。

| 轮次 | 整合包/服务检查与结果 | 证据 |
| --- | --- | --- |
| R01 | 修复五个 BAT 入口在 pause 后丢失 Python 错误码 | test_portable_entries，真实 CMD |
| R02 | 修复 INSTALL-VISION 的 edition 参数被拒绝及跳过 apply | test_portable_entries，真实 CMD/PowerShell |
| R03 | 修复 configure/import 忽略 --port，拒绝非法端口/上下文 | test_portable_launcher |
| R04 | 核对更新配置保留采样、端口、API key 与自定义参数 | test_portable_launcher |
| R05 | 核对视觉编码方式、token 和旧文字配置迁移 | test_portable_windows |
| R06 | 核对 Windows UTF-8 manifest、签名资产拒绝、原权限保留 | test_windows_utf8_manifest |
| R07 | 修复 NoModels 可包含矛盾权重角色声明；视觉仍限制固定 SHA | test_portable_weights、test_portable_audit |
| R08 | 修复 VERIFY-PACKAGE 未校验清单语义、重复路径；允许用户配置 | test_portable_audit |
| R09 | 核对 ZIP 越界、符号链接、保留路径与模型文件拒绝 | test_portable_update |
| R10 | 修复应用源码递归打包会收录本机未跟踪私密文件 | test_portable_audit，临时 Git 仓库 |
| R11 | 修复损坏下载 stamp/ranges JSON 导致全部镜像失败，重建非法状态 | test_portable_audit |
| R12 | 核对国内优先、固定版本、已下载模型复用与 SHA256 | test_model_sources |
| R13 | 修复暂存清单未在 apply 前再次核对哈希 | test_portable_update，真实 PowerShell |
| R14 | 修复 prepare 后用户新建同名文件可能被覆盖 | test_portable_update，真实 PowerShell |
| R15 | 核对中途失败及文件锁时逆序回滚、模型/配置保留 | test_portable_update，真实 PowerShell |
| R16 | 核对进程占用阻止更新；隔离无路径及消失的系统进程 | test_portable_update，真实自有子进程 |
| R17 | 核对本机监听、认证、跨站请求与控制端点 | serve.test_security |
| R18 | 核对引擎加载/编码取消、视觉临时路径与失败清理 | serve.test_lifecycle、serve.test_vision_lifecycle |
| R19 | 核对上游合并、版本更新、README 保留与冲突停止；拆开节点 CI | test_sync_upstream、两个仓库工作流 |
| R20 | 检查独立仓库及发行物，执行完整回归、文件 SHA/ZIP CRC 与实际工作流 | 最终验收记录见下文 |

节点每轮详细目标、修复及回归见独立节点仓库 docs/AUDIT-20-NODES.md。

## 实测边界

Windows、RTX 4060 Ti 16GB、128GB RAM；模型与驱动沿用之前实机验收。单元测试使用临时目录及模拟引擎，不下载模型。AMD 实机仍未验证。上述检查不能证明所有版本、硬件和第三方节点组合都无缺陷。

本次本机完整回归：整合包/服务 283 例、setup 258 例、独立节点 54 例，均通过（无跳过）。节点包含真实 Python HTTP Service 与模拟原生引擎的生命周期集成，实机绘图另行验收。

R20 实机追加发现：原生启动抛出 RuntimeError/OSError 时生成 API 直接断开。已在加载边界转换为 engine_load_failed，三种生成协议（流式与非流式）返回 JSON 503，保留诊断并清理资源。相关服务 49 例及节点 54 例复测通过。验收 helper 不再直接创建 CUDA context，改由 ComfyUI /prompt 队列执行 Control(load)。

本轮拆仓后实机复跑通过：text-to-image 37.33s，1张图；image-to-image 28.25s，1张图；storyboard-to-images 52.53s，2张图。共4张图；每次返回后语言/视觉进程均停止。Control(load)通过ComfyUI队列，缓存重复请求通过。
