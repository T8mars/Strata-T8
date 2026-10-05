Windows x64 整合版，基于对应版本的 Niko1221/Strata。

- 内置独立 Python、锁定依赖、NVIDIA CUDA 与 AMD HIP 引擎及运行库。
- 不含 GGUF、MTP 或其他模型权重；模型独立分发。
- 解压运行 START-HERE.bat，完整模型目录用 IMPORT-MODEL.bat 导入。
- PREPARE-MODEL.bat 支持 ModelScope 优先、国内镜像及 Hugging Face 兜底。
- UPDATE-PORTABLE.bat 自动更新正式版，保留模型和配置，失败回滚。
- 修复升级后首次启动重置手动设置的问题；保留端口、API key、上下文及引擎参数。
- 仓库定时同步上游正式版本，通过测试后自动打包发布。

下载 `Portable-NoModels.zip` 和对应 `.sha256`。Source code 压缩包不包含运行环境。

Windows 10/11 x64、AVX2 CPU、兼容显卡和驱动。NVIDIA 驱动 580+，IQ3_S 建议 96GB 以上内存。AMD 引擎包含，尚未完成本机 AMD 硬件推理验收。

[使用与模型下载](https://github.com/T8mars/Strata-T8#readme) · [上游项目](https://github.com/Niko1221/Strata)
