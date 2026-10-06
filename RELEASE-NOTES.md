## Strata-T8 0.1.39-t8.13

第六组 20 轮联合检查完成。修复工具 Schema/历史参数的预检、Anthropic 工具结果中的图片丢失、工具参数流式一致性；模型生成的非标准 JSON 参数保留原文，重复参数受控失败。配置完成时核对显式设置与消费字段；托管配置拒绝覆盖应用/网页/模型文件，模型准备检查组件路径与最终主分片。

更新器校验实际复制字节以及延迟 metadata/edition 声明；上游同步保护已开始的 Git 操作和 ignored 用户文件，清除旧诊断，并核对稳定 tag、源码版本及禁止回退。新增 67 个方法，528 例完整回归、258 例安装器测试通过。配套独立节点 1.0.5 新增 28 个方法、188 例通过；三类实际 ComfyUI GPU 绘图流程共输出 4 张图并确认引擎释放。

VisionReady 包含嵌入式 Python、锁定依赖、CUDA/HIP 引擎和运行库、固定 BF16 视觉权重及配置。两个包均不含主模型及 MTP；Portable-NoModels 不含任何权重。模型地址、安装路径、来源与致谢见 README；支持发行更新、上游同步、配置保留和失败回滚。每个 ZIP 附 SHA256 与完整清单。

逐轮证据见 docs/AUDIT-20-ROUND6-T8.md，独立节点见 https://github.com/T8mars/Comfyui-Strata-T8 。AMD 实机推理尚未验证，Windows AMD 视觉暂不支持。
