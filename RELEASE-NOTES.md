## Strata-T8 0.1.39-t8.8

第二次 20 轮联合检查：修复配置写入中断、非有限设置值、模型描述和空组件校验、bootstrap 缓存恢复、更新暂存清理及计划/清单核对、MTP 二进制权重排除、上游文档生成失败回滚。视觉配置可在设置页启用延迟加载。

独立节点 1.0.1 同步修复请求超时、JSON/schema 校验、并发服务控制、取消操作及面板错误提示。节点和检查记录见 https://github.com/T8mars/Comfyui-Strata-T8 ，Publisher t8star。

VisionReady 包含嵌入式 Python、锁定依赖、CUDA/HIP 引擎与运行库、固定 BF16 视觉权重及配置。主模型与 MTP 继续独立分发；另提供 Portable-NoModels 兼容旧更新器。两个包附文件清单、ZIP SHA256，支持按类型更新、配置保留与失败回滚。

安装、模型下载与路径见 README；AMD 实机推理尚未验证，Windows AMD 视觉暂不支持。
