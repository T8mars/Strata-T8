## Strata-T8 0.1.39-t8.11

第四组 20 轮联合检查：提前拒绝截断、含糊或超限 HTTP 请求及无效 JSON/token 预算；模型结构化输出异常受控返回。创建 ComfyUI 托管档案只写独立配置；更新后的配置刷新失败恢复原文件。模型准备检查完整必要产物；更新器支持托管文件与目录迁移，保护用户文件，修复旧计划执行器恢复、结果报告与计划路径绑定。

新增 42 例回归，服务/整合包 399 例、setup 258 例通过；独立节点 1.0.3 修复 9 类问题、新增 26 例，131 例完整回归通过。逐轮记录见 docs/AUDIT-20-ROUND4-T8.md，节点见 https://github.com/T8mars/Comfyui-Strata-T8 。

VisionReady 包含嵌入式 Python、锁定依赖、CUDA/HIP 引擎与运行库、固定 BF16 视觉权重及配置。主模型与 MTP 独立分发；另提供 Portable-NoModels 兼容旧更新器。两个包附文件清单与 ZIP SHA256，支持按类型更新、配置保留与失败回滚。

安装、模型下载与路径见 README；AMD 实机推理尚未验证，Windows AMD 视觉暂不支持。
