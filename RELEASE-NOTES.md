## Strata-T8 0.1.40-t8.2

同步 [上游 v0.1.40.2](https://github.com/Niko1221/Strata/releases/tag/v0.1.40.2)，更新至 0.1.40.2 CUDA/HIP 引擎。主模型、MTP 和既有视觉权重可直接复用，ComfyUI 节点 1.0.6 与 Protocol 1 保持兼容。

同步引擎的 KV、resident experts、多 GPU 和文件缓存修复；服务新增 READY/ENC 超时、分块 HTTP 请求、EXIF 图片方向校正、多图缓存保护、Responses JSON/工具校验和 Prometheus 输出。保留 T8 的取消清理、双引擎释放、严格 JSON/工具参数检查及更新回滚。实验性参数继续按需显式开启。

自动同步、构建和发布支持四段源码/引擎版本，发行包保持 0.1.40-t8.2，以兼容旧更新器；元数据和 SHA256 记录准确来源。补齐新运行文档，安装器测试隔离于本机安装，Docker 的 POSIX 回归由 Linux CI 执行。

两版均内置 Python 3.12.10、锁定依赖及 NVIDIA/AMD 运行库，不含主模型、MTP 或用户配置。VisionReady 另含固定 BF16 视觉权重、配置和许可证；Portable-NoModels 不含任何权重。附 SHA256 和逐文件清单。[下载与模型路径](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.2/README.md)

严格回归通过 1190 项应用、397 项安装器、219 项节点和 8 项分词器测试，无失败或跳过。RTX 4060 Ti 16GB / 128GB RAM 完成文本、图片、结构化批量到实际绘图，以及 API 十项生命周期、双引擎退出和显存恢复复验。AMD、Intel 无本机硬件验收；Windows AMD 视觉暂不支持。节点 Registry 审批仍待确认。[本版验证范围](https://github.com/T8mars/Strata-T8/blob/v0.1.40-t8.2/docs/VALIDATION-UPSTREAM-01402-T8.md)
