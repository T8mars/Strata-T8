## Strata-T8 0.1.39-t8.12

第五组 20 轮联合检查完成。修复嵌套 JSON/推理回放、消息与图片字段、工具命名空间、采样溢出、流式参数和共享 token 默认值；配置未完成时回滚。Schema 按资源草案检查，继承引用规则；旧草案混合 dependencies 的特定 anchor 组合受控拒绝，可使用 JSON Pointer。

更新器提前核对计划、暂存和严格 JSON；通过无覆盖提交保护预检后出现的文件，修复 PowerShell 结果原子替换及只读回滚，仅回收可确认归属的未应用旧暂存。新增 62 例，服务/整合包 461 例、setup 258 例通过。配套独立节点 1.0.4 新增 29 例、160 例通过，三类实际 GPU 绘图流程输出 4 张图并确认引擎卸载。

VisionReady 包含嵌入式 Python、锁定依赖、CUDA/HIP 引擎与运行库、固定 BF16 视觉权重和配置。两个发行包均不含主模型及 MTP；Portable-NoModels 不含任何权重。模型地址、路径和来源见 README；支持发行更新、上游同步、配置保留和失败回滚。每个 ZIP 附 SHA256 与完整清单。

逐轮证据见 docs/AUDIT-20-ROUND5-T8.md，独立节点见 https://github.com/T8mars/Comfyui-Strata-T8 。AMD 实机推理尚未验证，Windows AMD 视觉暂不支持。
