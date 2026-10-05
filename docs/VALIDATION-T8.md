# Strata-T8 发行验收

验收日期：2026-10-05。正式版本：[v0.1.39-t8.4](https://github.com/T8mars/Strata-T8/releases/tag/v0.1.39-t8.4)，上游源码及 CUDA/HIP 引擎均为 0.1.39；发行源码提交为 `89c6ba6cbe87d49e800c73293c9d484df78162aa`。

## 自动检查与发布

| 验收 | 结果与证据 |
| --- | --- |
| Windows 应用、服务器、安全、更新及同步测试 | 235 项通过；安装回归测试 258 项通过，[发行工作流](https://github.com/T8mars/Strata-T8/actions/runs/37307839051) |
| 独立 CI | 235 项中通过 234 项，跳过 1 项需嵌入式 Python 的入口测试；258 项安装测试通过，[检查工作流](https://github.com/T8mars/Strata-T8/actions/runs/37307778661)。发行工作流具有完整运行库，未跳过该入口测试 |
| 上游正式版本同步 | [实际运行通过](https://github.com/T8mars/Strata-T8/actions/runs/37298930006)，当时无新版本 |
| 上游 main 同步 | [实际运行通过](https://github.com/T8mars/Strata-T8/actions/runs/37301167931)，当时无新提交；有更新时使用验证分支 |
| Git 合并与冲突 | 临时 Git 仓库测试覆盖正常合并、无更新、README 保留、版本递增、脏工作树和冲突终止；原仓库与目标仓库原有提交均保留为祖先 |

同步、检查及发行工作流均已启用。每日检查上游正式版本；出现合并冲突、测试失败或缺少匹配引擎时停止自动发布。

## 分发包

文件：`Strata-T8-0.1.39-t8.4-Windows-x64-Portable-NoModels.zip`，大小 **1,208,678,536 字节**。公开资产已下载并核对 SHA256、ZIP CRC 和 8005 个受管文件的大小与哈希。

```text
6f2c1f7d40f22506884703210eb9bb979b8386dfdd13024cfff6ed69eef61deb
```

内置 Python 3.12.10、锁定 Python 依赖及同版本 CUDA/HIP 引擎和运行库。包含 112 个许可证或版权说明文件；没有 GGUF、MTP、其他模型权重、用户配置或本机模型目录。主模型和 MTP 从 ModelScope 国内 CDN 获取，与锁定的官方版本及张量校验值核对；模型不进入 Release。

## 完整升级与本机推理

实际通过旧包的 `UPDATE-PORTABLE.bat` 从 t8.3 升级到 t8.4，使用公开 Release 下载地址，更新器及入口随包一并替换，退出码为 0。更新前后的模型位置、模型配置、自建 JSON、笔记和日志逐文件哈希一致；升级后全部 8005 个受管文件校验通过。

升级后的程序在含中文及空格的目录下启动。外部 HTTP/HTTPS 代理设为不可用地址，本地地址保持直连，用于验证网络不可用时的启动；普通启动的禁止模型下载逻辑另有回归测试。首次启动仍保留端口 8081、测试 API key、32K 上下文、7 个工作线程、KV 设置及采样参数；未提供 API key 的推理请求返回 401。

在 NVIDIA RTX 4060 Ti 16GB、128GB 内存、Intel Core i7-14700KF 上，OpenAI Chat Completions、Anthropic Messages 和 Responses API 均对 `21 × 2` 返回 `42`；网页返回正常。本机默认服务随后恢复到 `127.0.0.1:8080`，64K 上下文，健康检查 `loaded=true`，再次完成实际推理。

AMD 引擎及运行库已经包含并校验；本次没有 AMD 硬件，未进行 AMD 实机推理验收。以上推理结果是所列硬件及模型的验收，不作为其他硬件的性能数据。
