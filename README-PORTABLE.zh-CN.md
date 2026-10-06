# Strata-T8 Windows 整合包

内置独立 Python、锁定 Python 依赖、NVIDIA CUDA / AMD HIP 引擎及运行库。无需安装 Python、Git、编译器或 CUDA Toolkit；显卡驱动由电脑提供。VisionReady 包含视觉权重与配置模板，主模型和 MTP 独立分发。

## 开始使用

1. 解压到可写的 SSD 目录，不要在压缩包内运行。
2. 将完整 `Strata-data` 放到 `START-HERE.bat` 同一目录，或程序文件夹旁。
3. 运行 `CHECK-ENV.bat`，再双击 `START-HERE.bat`。
4. 浏览器打开 `http://127.0.0.1:8080`。按 Ctrl+C 或关闭启动窗口停止服务。

模型放在其他磁盘时运行 `IMPORT-MODEL.bat`，输入完整数据目录。也可执行：

```bat
IMPORT-MODEL.bat --data-dir "D:\模型\Strata-data"
START-HERE.bat --port 8081
START-HERE.bat --context 32768
START-HERE.bat --backend hip
```

首次启动或换电脑时自动配置；配置保存在 `strata-*.json`，模型位置在 `portable-settings.json`。同一电脑升级程序后保留端口、API key、上下文及手动引擎参数，仅更新运行库路径和必要的上游兼容设置。主动配置或更换硬件时重新生成引擎参数，保留端口和 API key；配置失败恢复原文件，恢复被文件锁阻止时明确报告。

## 独立模型目录

```text
Strata-data/
  portable-model.json
  models/IQ3_S/       两份 GGUF，合计 83.62GB
  packs/iq3_s/        索引、dense.bin、tokenizer
  mtp/               MTP 辅助权重及运行数据
```

接收完整目录后导入即可。只有 GGUF 时需运行 `PREPARE-MODEL.bat`，按提示下载缺少的 MTP 张量并完成预处理；不下载整个原始 checkpoint。

```bat
PREPARE-MODEL.bat --data-dir "D:\模型\Strata-data"
```

下载顺序：ModelScope、hf-mirror、Hugging Face，固定版本与 SHA256 校验。下载入口：[ModelScope GGUF](https://modelscope.cn/models/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF)、[ModelScope MTP checkpoint](https://modelscope.cn/models/Qwen/Qwen3.8-Flash-Next)、[国内镜像](https://hf-mirror.com/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S)、[Hugging Face](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S)。普通启动、检查及导入不下载模型。

## 程序更新

退出 Strata 后运行 `UPDATE-PORTABLE.bat`（`UPDATE.bat` 同样可用）。它从 T8mars/Strata-T8 最新正式 Release 下载完整包，检查 SHA256 和文件清单，保留模型、配置、日志及浏览器聊天记录。新文件与自建文件重名时拒绝覆盖；替换失败回滚。

结果保存在 `.portable-update/result.json`；其中记录备份路径。备份保存在系统临时目录，确认更新正常后可以自行删除。首次发布前或 GitHub 不可达时检查失败不影响本地启动。当前采用完整包更新，下载大小按发行类型而定。旧 NoModels 包先更新，再运行 INSTALL-VISION.bat 切换至 VisionReady。

聊天记录由上游网页保存在浏览器；继续使用相同的浏览器、地址和端口即可。异步检查不会强制升级或打断推理。

## 兼容与完整性

Windows 10 1903+/11 x64、AVX2 CPU、兼容显卡与驱动。NVIDIA 建议 12GB 以上显存、驱动 580+；IQ3_S 建议 96GB 以上内存。AMD 兼容列表见 `docs/AMD_HIP.md`。VisionReady 在导入兼容 Qwen 主模型后自动配置视觉，NoModels 默认为文本模式；Windows AMD 视觉暂不支持。RTX 4060 Ti 16GB / 128GB 内存已验收；AMD 实机推理尚未验收。

OpenAI API：`http://127.0.0.1:8080/v1`；默认仅监听本机。

`PACKAGE-MANIFEST.json` 记录每个程序文件的大小与 SHA256；运行 `VERIFY-PACKAGE.bat` 可检查。包内保留 Strata、Python、gguf-py、Python 依赖和 CUDA/ROCm 的随附许可证。模型另遵循自己的许可证。

[项目与最新 Release](https://github.com/T8mars/Strata-T8) · [上游 Strata](https://github.com/Niko1221/Strata)

## 视觉与 ComfyUI

VisionReady 内置固定 BF16 mmproj、模板、来源和许可证；主模型/MTP 不包含。可用 START-HERE.bat --vision gpu、--vision cpu、--vision no 切换，--vision-tokens 指定图片 token 预算。更新保留用户编码方式和 token 设置。

ComfyUI 节点在 [Comfyui-Strata-T8](https://github.com/T8mars/Comfyui-Strata-T8) 独立维护，可通过 Manager 安装与更新；[安装、模型路径与工作流](https://github.com/T8mars/Comfyui-Strata-T8#readme)。
