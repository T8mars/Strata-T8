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
| application 完整门槛 | 1,030 项通过，无跳过；433.891 秒 |
| 真实整包升级、逐文件审计 | 构建完成后执行，尚未验收 |
| 新版 GPU 工作流与取消/重载 | 文本 → 384×384 实际绘图通过；视觉、批量、取消/重载等待空闲资源 |

完整 CPU 门槛由 `tools/run_release_checks.py` 自动发现 serve/tools 新旧测试，独立运行 application、setup、tokenizer 三组。tokenizer 组必须指定已有 pack 路径；CI 不下载模型，也不把缺少 GPU 的检查算作推理验收。

服务器增加工具示例引用、正常结束及 stop、重启等待队列、致命错误、图片处理和 T8 双引擎状态回归。节点新增真实回环 HTTP 的 stop、批量顺序、结构化 502 后有限修复、按需视觉和双资源释放检查。

同步/分发新增热修复标签、精确历史连接、版本下限、无变化摘要验证、本地规划排除、发行资产与实际提交一致性回归。Windows 路径测试使用真实文件身份比较，工具 schema 测试保留新版合法别名。

发布器另有 28 项真实 Git/ZIP 回归，已包含在 application 门槛中。实际只读 GitHub 查询也验证了 REST 404 → GraphQL 查草稿的流程，没有为此创建或发布 Release。

## 来源与本机部署

官方引擎资产固定于该上游 Release：

| 资产 | SHA256 |
| --- | --- |
| strata-windows-x64.zip | `cd264b2125fdb85e84a8264da2ab2343463e6c7f9a12f317d4ce7192cd2c6b33` |
| strata-windows-x64-hip.zip | `b2fa4a660409fa0f842d442a6991f359a7f0aebefec6f854c852a8b639621d7a` |

校验后替换本机引擎，旧树保留备份。三份分发 EXE 已验证 UTF-8 manifest，BUILD.json 记录修改前后摘要及上游来源。副本打包时复核这些记录，并在 PACKAGE-MANIFEST.json 中保存实际 T8 源码提交。

本机服务在 `127.0.0.1:8082` 启动，状态报告 package `0.1.40-t8.1`、engine `0.1.40`、Protocol 1；语言和视觉保持按需加载。升级前后端口、监听地址、API key、采样和全部引擎参数逐项比较相同。此状态检查不代表已运行新版 GPU 推理。

## 边界

已有 [上一版验收](VALIDATION-COMFYUI-T8.md) 的文本、视觉和实际采样证据属于旧引擎。本版 GPU 验收使用独立 ComfyUI 端口和本轮托管实例，只停止本轮创建且身份核对一致的进程，不影响用户其他程序。

首轮环境为 ComfyUI 0.38.0、前端 1.53.10、PyTorch 2.7.0+cu128、RTX 4060 Ti 16GB / 128GB RAM。实际观察到新语言和视觉进程；队列 load 和文本节点返回前均确认双进程退出，随后绘图生成一张 PNG。首图耗时 272.187 秒，不作为速度基准。下一图开始时其他程序重新占用内存，视觉节点在推理前正确拒绝低于 60GiB 的可用 RAM；本轮托管服务和独立 ComfyUI 已退出。此次部分验收不代表其余工作流已通过。

AMD 引擎随包分发，暂无 AMD 实机；Windows AMD 视觉暂不支持。上游新增实验开关维持 opt-in，没有扩大硬件性能声明。API 默认保持严格 JSON/工具检查，[兼容开关](../serve/API_COMPATIBILITY.md) 需要按请求显式启用。
