# Strata-T8 0.1.40-t8.2 验证

同步上游 [v0.1.40.2](https://github.com/Niko1221/Strata/releases/tag/v0.1.40.2)，源码 `e8ca9afd03d839d4f8dbbe82dffce7f8a3bafd7a`，CUDA/HIP 引擎均为 `0.1.40.2`。发行版本保持 `0.1.40-t8.2`，节点保持 `1.0.6` 和 Protocol 1。

## 代码与依赖

包含上游引擎、服务、安装器、实验 Intel Linux 后端及测试更新。T8 保留严格 JSON/工具参数校验、取消清理、双引擎资源释放和更新回滚。新增分块 HTTP 解码拒绝 Content-Length 与 Transfer-Encoding 同时出现、异常块大小和未终止的 trailers。关闭工具恢复时拒绝一层 wrapper 中混合多个函数；需要宽松恢复时仍须显式启用。

嵌入式 Python 3.12.10 的依赖检查通过。本机严格门槛完成 1190 项应用（476.000 秒）、397 项安装器、219 项独立节点和 8 项实际 tokenizer 测试，均无失败、错误或跳过。正式 ZIP 和发布提交由发行审计再次核对。Linux Docker 入口三项测试单独执行，不计入 Windows 回归。

实际子进程验证了 engine READY、vision READY 和 ENC 超时后的进程退出；超时设为 0 时仍支持请求取消。多图测试确认一次请求超过 64 张图时不会提前删除正在使用的 embeddings。

## 原生引擎来源

| 官方资产 | SHA256 |
| --- | --- |
| strata-windows-x64.zip | `02901f0cd0691ab33ec827e354ffa72e1ab4f19a093f0280c6deb7327b26eb30` |
| strata-windows-x64-hip.zip | `f843554aa66c5047ca010a1ca7650ee1bfe6237bd92b140d5bb62f6ee314cf75` |

下载后核对摘要、BUILD.json 与源码提交，给分发 EXE 加入 UTF-8 manifest，记录修改前后 SHA256。本机旧引擎保留备份；原有主模型、MTP、视觉权重和配置复用，没有重新下载这些权重。

## ComfyUI 实机

2026-10-07，Windows、RTX 4060 Ti 16GB、128GB RAM；ComfyUI 0.38.0、前端 1.53.10、PyTorch 2.7.0+cu128。独立回环端口 18488、全新本机档案、32768 context、60GiB 可用 RAM/12288MiB 可用 VRAM 门槛；复用既有 IQ3_S、MTP、BF16 mmproj 和 DreamShaper 8。

| 工作流 | 输出 | 本轮墙钟 |
| --- | --- | --- |
| 文本提示词 → SD 绘图 | 1 张 384×384 PNG | 36.735 秒 |
| 图片反推 → SD 绘图 | 1 张 384×384 PNG | 26.203 秒 |
| 两镜头分镜 → 顺序批量 → SD 绘图 | 2 张 384×384 PNG | 51.047 秒 |

先通过真实队列的服务加载节点，再依次执行上述工作流。后两类复用同一 ComfyUI CUDA 上下文和部分节点缓存，这些数值不是冷启动性能基准。每个 Strata 节点返回下游前核对 language/vision 的 running、starting、loaded 均为 false；按 PID/创建时间确认原生进程退出，WebSocket 记录执行边界。完成后仅退出本轮自有托管服务与 ComfyUI。

## 本机 API 与限制

`127.0.0.1:8082` 使用更新后的原生引擎，保持按需加载。实际完成加载中断、取消后加载、文本、图片、预填充中断、生成中断、取消后恢复、卸载、新进程重载、重载后生成十项功能检查；最终双引擎进程退出、进行中请求数为零。

第一轮结束时其他程序新增 GPU 占用，整机空闲显存从 14970MiB 降至 10141MiB，显存基线恢复断言未通过，原始记录保留。

资源释放后重新部署最终服务源码，第二轮十项检查和最终显存恢复全部通过：可用显存从 14424MiB 回到 15117MiB。加载、预填充、生成取消后的恢复等待分别为 0.703、5.860、0.391 秒，属于该轮请求的墙钟测量，不代表通用性能。服务最终保持按需可用，双引擎未驻留。

ComfyUI 工作流完成后调整了拒绝混合 Content-Length/Transfer-Encoding 请求的读尾处理；最终严格 HTTP 回归和第二轮 API 实测覆盖最终服务版本。原生引擎、节点代码及文本/视觉生成流程保持同一版本。

AMD 引擎随包分发，尚无 AMD 实机验收；Windows AMD 视觉暂不支持。Intel 更新属于上游实验性 Linux 源码，本 Windows 包不提供 Intel 预编译引擎。节点 1.0.6 的 Registry 人工复核仍待官方确认，当前使用 Git 或其已有 GitHub Release 安装。
