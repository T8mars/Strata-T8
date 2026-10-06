## Strata-T8 0.1.39-t8.7

整合包与 ComfyUI 节点拆分为两个独立仓库。整合包继续在 Strata-T8 发行，节点迁至 https://github.com/T8mars/Comfyui-Strata-T8，使用独立版本与 ComfyUI Registry Publisher t8star。

20 轮联合审查后修复视觉安装入口、batch 错误码、配置端口转发、下载状态损坏恢复，以及更新时的暂存清单和用户新建同名文件保护。打包仅收录受 Git 管理的应用源码，排除本机私密文件。原生启动失败现在返回明确的 JSON 503，覆盖 OpenAI、Responses 和 Anthropic 生成接口。

VisionReady 包含嵌入式 Python、锁定依赖、CUDA/HIP 引擎与运行库、固定 BF16 视觉权重及配置。主模型与 MTP 继续独立分发；另提供 Portable-NoModels 兼容旧更新器。两个包附文件清单、ZIP SHA256，支持按类型更新、配置保留与失败回滚。

安装、模型下载与路径见 README；AMD 实机推理尚未验证，Windows AMD 视觉暂不支持。
