# ComfyUI Strata-T8

独立 Strata 服务的 ComfyUI 节点：文本与提示词、分镜 JSON、图片描述/反推/OCR、批量处理和托管服务。节点包不含主模型或运行环境。

1. 将此目录放入 ComfyUI/custom_nodes，用 ComfyUI 的 Python 安装 requirements.txt，重启。
2. 下载 [VisionReady](https://github.com/T8mars/Strata-T8/releases/latest) 并导入主模型。
3. 在 Strata-T8 侧栏保存本机档案，指定运行包与模型目录，刷新后选择档案。
4. 导入 examples/*.api.json，替换档案与绘图模型。

同卡模式确认语言/视觉引擎退出再返回下游；API key 只在本机档案。更新时退出 ComfyUI，覆盖节点目录并重启，档案保留。

[完整说明](https://github.com/T8mars/Strata-T8/blob/main/docs/COMFYUI-T8.md) · [实机验收](https://github.com/T8mars/Strata-T8/blob/main/docs/VALIDATION-COMFYUI-T8.md)
