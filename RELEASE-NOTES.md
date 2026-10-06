## Strata-T8 0.1.40-t8.1

完整同步 [上游 v0.1.40.1](https://github.com/Niko1221/Strata/releases/tag/v0.1.40.1)，使用 0.1.40 CUDA/HIP 引擎，保留 T8 Protocol 1、视觉按需加载、同卡资源交接和原有更新保护。现有主模型、MTP 与 BF16 视觉权重继续使用。

修复引号、代码块和 thinking 示例被误识别为工具调用的问题；同步正常结束判断、stop、tool_choice、工具别名与 reasoning rescue。引擎重启唤醒等待请求，及时报告致命错误和长提示词失败。保留 T8 对 JSON、工具参数、图片预算、请求取消及进程归属的检查。宽松 JSON/历史兼容需要显式启用，见 [API 兼容说明](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.1/serve/API_COMPATIBILITY.md)。

同步上游引擎、安装器与 pack 工具；补齐 pack 读取文件关闭和运行依赖。自动同步支持四段热修复标签，连接上游重写后的相同历史，记录准确的源码/引擎来源和固定 SHA256，阻止版本降级及摘要异常。正式打包前要求应用、安装器与节点测试非零执行且无失败、错误或跳过；发布核对实际测试提交、草稿标签和四个发行资产。

VisionReady 包含独立 Python、锁定依赖、NVIDIA/AMD 引擎及运行库、907,543,008 字节 BF16 视觉权重和配置；Portable-NoModels 不含任何权重。两版均不含主模型、MTP 或用户配置，附 SHA256 和逐文件清单。下载、模型路径、来源和致谢见 [README](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.1/README.md)，更新策略见 [更新说明](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.1/docs/UPDATING-T8.md)。

[独立 ComfyUI 节点 1.0.6](https://github.com/T8mars/Comfyui-Strata-T8) 保持兼容。RTX 4060 Ti 16GB / 128GB RAM 已通过文本、视觉、结构化批量到实际绘图，以及 API 加载/预填充/生成取消、恢复、双引擎卸载与重载验证。节点 Git/Release 可安装，Registry 人工复核申请已提交，官方审批仍待确认。验证范围及测量条件见 [本版验收记录](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.1/docs/VALIDATION-UPSTREAM-0140-T8.md)。AMD 无本机硬件验收，Windows AMD 视觉暂不支持。
