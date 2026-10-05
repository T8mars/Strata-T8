## Strata-T8 0.1.39-t8.6

修复 Windows 中文目录下视觉权重及原生模型路径的编码：为内置引擎设置应用自身 UTF-8 manifest，保留原权限和依赖，并记录处理前后 SHA256。要求 Windows 10 1903+ 或 Windows 11，系统区域设置无需修改。

包含 10 个 ComfyUI 节点，覆盖文本、提示词、分镜 JSON、字段提取、图片分析、批量处理和服务控制。托管模式使用独立 Python/引擎；同卡模式确认语言与视觉进程释放后继续采样，支持加载和编码阶段取消。

提供 VisionReady-NoMainModel 整合包，包含固定 SHA256 的 BF16 视觉权重、配置模板与 Apache-2.0 许可证。主模型与 MTP 独立分发。内置 Python、锁定依赖及同版本 CUDA/HIP 引擎和运行库。

保留 Portable-NoModels 资产供旧更新器升级，随后运行 INSTALL-VISION.bat 切换发行类型。运行包按类型自动更新，保留配置并支持失败回滚；节点更新后需重启 ComfyUI。仓库继续自动同步上游正式版，测试及匹配引擎检查通过后发布。

RTX 4060 Ti 16GB / 128GB RAM 已完成文本、图片反推及两镜头批量到 KSampler 的实际绘图。Windows AMD 视觉暂不支持，AMD 实机推理未验证。

[安装与模型下载](https://github.com/T8mars/Strata-T8#模型下载) · [ComfyUI 说明](https://github.com/T8mars/Strata-T8/blob/main/docs/COMFYUI-T8.md)
