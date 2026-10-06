## Strata-T8 0.1.39-t8.9

第三组 20 轮联合检查：修复重新配置时端口和 API key 丢失、视觉参数保留、配置失败回滚、交付目录及 tokenizer 检查、ZIP 路径预检、清单类型、更新控制目录和备份路径、旧计划误应用及 Schema 引用校验。

独立节点 1.0.2 修复 13 类问题、新增 21 例，完整节点回归 105 例通过。覆盖托管归属、启动回滚、卸载兜底、HTTP 截断响应、极窄图片、面板草稿及 API key 管理。节点和逐轮记录见 https://github.com/T8mars/Comfyui-Strata-T8 ，Publisher t8star。

VisionReady 包含嵌入式 Python、锁定依赖、CUDA/HIP 引擎与运行库、固定 BF16 视觉权重及配置。主模型与 MTP 继续独立分发；另提供 Portable-NoModels 兼容旧更新器。两个包附文件清单、ZIP SHA256，支持按类型更新、配置保留与失败回滚。

安装、模型下载与路径见 README；AMD 实机推理尚未验证，Windows AMD 视觉暂不支持。
