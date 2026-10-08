# Strata-T8 0.1.40-t8.4 验证

同步上游 [v0.1.40.4](https://github.com/Niko1221/Strata/releases/tag/v0.1.40.4)，源码 `6674a0065fb96bacde33e3eb10f91a1df86f95f2`，CUDA/HIP 引擎均为 `0.1.40.4`。发行版本为 `0.1.40-t8.4`，独立节点保持 `1.0.7` 和 Protocol 1。

## 同步范围

完整合入 0.1.40.3 和 0.1.40.4 的累计变化：Windows AMD 自动专家缓存保留显存、HIP 完整 DLL 依赖与运行库诊断、网页空回答/思考历史、Tokenizer 并发、Docker CONFIG 与已有配置链接、MTP 路由 guard，以及 Linux Intel Arc 安装和 A 系列修复。

保留 T8 的请求绝对总时限、启动失败清理、视觉缓存私有快照、严格 JSON/工具校验、取消和双引擎释放、离线模型导入与更新回滚。将上游依赖作者本机旧 ZIP 的 HIP 集成测试改为读取当前匹配引擎，核对实际 PE 依赖及 EXE 旁 DLL 的完整摘要；正式发行不得跳过。

本整包提供 CUDA 13 和 Windows HIP 引擎。上游 .4 的 Pascal 修复位于其 CUDA 12 资产与 Linux 源码；本包显卡范围仍为 README 中的 RTX 20/30/40/50。AMD、Intel 和 Pascal 不作为本机硬件验收结果。

## 验证记录

本轮结果将在严格 CPU 回归、真实工作流、API 生命周期与整包审计完成后填写。主模型、MTP、视觉和配置复用，测试不下载模型。
