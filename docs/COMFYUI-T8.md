# ComfyUI Strata-T8

## 安装与配置

下载 [Release](https://github.com/T8mars/Strata-T8/releases/latest) 的节点 ZIP，把 `comfyui-strata-t8` 放入 `ComfyUI/custom_nodes`。使用 ComfyUI 自身 Python 安装该目录的 `requirements.txt`（仅 jsonschema），重启。Strata 使用独立 VisionReady 运行包的 Python、CUDA/HIP 和引擎；导入节点不会启动引擎、下载权重或执行 pip。ComfyUI 与绘图模型由已有环境提供。

打开侧栏 **Strata-T8**，填写配置名称并保存连接 JSON，刷新页面后选择配置。API key 在独立密码框填写，留空保留；新托管配置自动生成。配置默认在 `%LOCALAPPDATA%/Strata-T8-ComfyUI`，可用 `STRATA_COMFY_HOME` 指定；此目录不应分发，工作流仅存配置名称。

```json
{
  "mode": "managed",
  "runtime": "D:\\Strata-T8",
  "data_dir": "D:\\Strata-data",
  "port": 8082,
  "vision": "gpu",
  "context": 32768,
  "same_gpu": true,
  "min_free_vram_mib": 12288,
  "min_free_ram_gib": 60,
  "timeout_s": 1800,
  "cleanup_timeout_s": 90
}
```

`vision` 可设 gpu、cpu 或 no。视觉需要兼容的 Qwen 主模型；Windows HIP 文字档案设 vision:no、same_gpu:false，未做 AMD 实机验证。

连接已有服务使用 mode:external、url:http://127.0.0.1:8080。默认 allow_lifecycle:false、same_gpu:false，只请求推理；远程服务保留 same_gpu:false。本机同卡共享时明确设两者 true，服务须为 Strata-T8 协议 1、parallel:1。节点不结束外部服务进程。

## 功能

| 节点 | 输入与输出 |
| --- | --- |
| Connection | 按名称读取本机档案，输出连接 |
| Text | 系统提示、用户文字及 JSON 历史；最终文字、思考、用量 |
| Prompt | 扩写、翻译、改写、图像/视频、正负提示词；可编辑模板 |
| Structured | JSON Schema 或默认分镜；校验 JSON、原生 STRING 列表、批量节点用列表 |
| Extract / Number | JSON Pointer 字段提取；文字/列表或 FLOAT，类型错误报错 |
| Vision | 1–8 图描述、反推、OCR、问答、主观评价，可选 JSON Schema |
| Batch / Image Batch | 最多 64 条文字或 8 图，顺序推理、一次载入；逐项索引和用量 |
| Control | 状态、启动、加载验证、卸载、停止自有服务；可透传文字/图片建立依赖 |

refresh 改变时重新生成，其他相同输入使用 ComfyUI 缓存；Control 每次执行。top_k 范围 1–64，top_p >0；seed 不承诺自适应专家模式下逐字一致。结构化输出是生成后校验，可设 0–2 次修复，格式失败明确报错。

面板“加载”进入 ComfyUI 队列。同卡 Control 完成加载验证后释放资源再返回，HTTP 服务继续待命；非同卡可显式保持载入。原生 STRING 列表使下游逐项执行，STRATA_TEXT_LIST 将整批交给 Batch。

## 三个示例

把节点包 examples 中的 API JSON 拖入 ComfyUI，替换 Connection 的配置名称和 CheckpointLoader 的 SD1.5 模型名：

- text-to-image.api.json：提示词助手 → CLIP → KSampler → 保存图像。
- image-to-image.api.json：图片反推 → CLIP → KSampler；先把附带 strata-vision-fixture.png 复制到 ComfyUI/input。
- storyboard-to-images.api.json：两镜头分镜 → Batch → 原生 STRING 列表 → 两次采样。

Strata 输出文字，绘图由下游模型完成；抽帧图片分析不称为原生视频理解。

## 显存、取消与故障

同步节点先把图片转 CPU 数据，释放 ComfyUI 模型，测量空闲 VRAM/RAM，再载入 Strata。完成、错误或取消后等待请求结束，确认语言/视觉进程退出及显存回收才输出；释放失败中止下游。托管模式仅清理记录的自有 PID/创建时间/路径。

默认空闲门槛 12GiB VRAM、60GiB RAM。本机因其他常驻程序设为 8GiB VRAM 并实测通过；降低门槛须依据实际占用。存活 GPU tensor、第三方后台任务和其他异步分支无法保证释放，发现冲突报错；示例用依赖线保持串行，多 GPU 调度尚未验收。

端口占用时换端口。缺主模型/MTP 时导入完整数据，mmproj 校验失败时重装已校验的 VisionReady 包。原生图像协议要求无空格路径，程序选择 Windows 短路径或包内临时目录；无法找到时设置可写 ASCII TEMP/TMP 后重启。

## 版本与更新

节点与运行包同版本，协议为 1，节点 ID 保持稳定。运行包退出后用 UPDATE-PORTABLE 自动获取正式版；旧无权重包先更新后用 INSTALL-VISION 切换，模型目录及视觉编码方式/token 设置保留。

节点更新：退出 ComfyUI，使用新 Release 节点目录覆盖旧程序文件并重启；本机档案位于独立目录，会保留。已验证 ComfyUI 0.38.0 V3 执行器、前端 1.53.10；旧 API 有适配，其他版本未做完整绘图验收。

[验收](VALIDATION-COMFYUI-T8.md) · [权重来源](../vision/catalog.json)
