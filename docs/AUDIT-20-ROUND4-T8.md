# 第四组 20 轮联合检查（2026-10-06）

整合包基线为 `0.1.39-t8.10`，主仓提交 `5c8e53c019d4d3fd45ce6aebefa8585cb5595f69`；节点基线为 `1.0.2`。本组新增 20 个不同检查目标，前三组及重复完整回归不计轮数。主审负责服务、配置与模型准备，两名独立子 Agent 分别审查节点和更新器，并交叉检查变更。

| 轮次 | 新检查目标、发现及处理 | 验证 |
| --- | --- | --- |
| R01 | 负数、重复 Content-Length 和 Transfer-Encoding 可造成含糊请求；在任何操作前拒绝 | RequestBoundaries，实际 socket |
| R02 | 有效 JSON 的实际字节少于声明长度仍执行；必须读到完整声明长度 | RequestBoundaries，半关闭 socket、load 未调用 |
| R03 | 请求长度没有上限；生成限制 64MiB，设置和控制限制 64KiB，读取超时 30s | RequestBoundaries，仅请求头即可返回 413 |
| R04 | NaN、指数溢出、重复 JSON 成员及过深 JSON 未受控拒绝；统一严格解析并返回 400 | RequestBoundaries，实际 HTTP；随后 health 正常 |
| R05 | 转义的孤立 Unicode surrogate 到编码时才失败；加载前验证 UTF-8 | RequestBoundaries，load 未调用 |
| R06 | 模型结构化输出含孤立 surrogate 或过深 JSON 时抛裸异常；转为受控 StructuredOutputError | OutputEncoding，直接验证模型输出，API 既有 502 映射 |
| R07 | 错误 token 预算可先加载模型或抛类型异常；三个生成 API 提前检查整数与范围 | RequestBoundaries，20 个预算反例，load 未调用 |
| R08 | 错误 Anthropic messages、role、block、image source 及模板 kwargs 先加载或裸异常；先验证形状 | RequestBoundaries，实际 HTTP 400 与未加载断言 |
| R09 | 错误 reasoning_budget_tokens 在加载后才检查；两个聊天 API 均提前验证 | RequestBoundaries，OpenAI/Anthropic |
| R10 | 更新后的版本/发行类型刷新在状态提交失败时留下改变的配置；恢复两份文件及内存状态 | RefreshTransaction，原字节快照 |
| R11 | 创建不同模型的托管档案调用全局 configure，改动网页安装配置/状态/启动脚本；捕获 setup 结果到内存后只写目标档案 | ManagedProfiles，配置、状态、脚本保持原字节 |
| R12 | 托管上下文超界到 setup 才处理；CLI 在环境检查前拒绝 1024..131072 以外的值 | ManagedProfiles，环境检查未调用 |
| R13 | 重复 batch/slots/context 参数残留使串行档案含糊；移除所有受控参数后加入一个上下文值 | ManagedProfiles，分离值及等号形式 |
| R14 | pack 缺少 dense/index 或工具成功但无产物仍发布完成描述；检查全部必要非空产物并写入 required_files | PreparedArtifacts，准备工具替身，禁止发布不完整描述 |
| R15 | meta 与清单版本、发行类型、权重角色可矛盾；核对一致性，保留旧 NoModels 缺少新字段的兼容 | 更新器 UpdateMetadata |
| R16 | result.json 写失败后再次写失败掩盖回滚结果；原子写结果，报告失败仍明确输出恢复状态 | 真实 PowerShell、锁文件 |
| R17 | 新计划发布失败覆盖旧 apply 执行器；失败恢复旧执行器，使旧计划仍能执行 | 旧计划实际 PowerShell 应用 |
| R18 | 合法托管文件与目录互换无法更新；先备份旧文件、再安装新文件；保护用户文件和自建空目录，失败恢复目录拓扑 | 微型包、真实 PowerShell，两种迁移及失败回滚 |
| R19 | ZIP 根文件、父文件/子文件冲突或缺少清单到解压中途才发现；全目录和目标拓扑预检后才写入 | 实际 ZIP，失败时无部分写入 |
| R20 | 复制到别处的 plan 仍能修改原安装；执行前将计划路径绑定安装控制目录，兼容 Windows 长短路径 | 真实 PowerShell，修改前拒绝 |

新增 `tools/test_portable_round4.py` 20 例、`tools/test_portable_update_round4.py` 22 例。独立结构化模块的既有 8 例也加入完整 CI、同步和发行测试列表，补足直接输出校验覆盖。42 个新增用例及 8 个既有用例均包含在完整测试数中，不重复累计。

[节点第四组 20 轮报告](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/AUDIT-20-ROUND4-NODES.md) 记录 9 类修复、26 个新增用例及 131 例完整回归。20 个目标不等于 20 个 BUG；草案或同一事务的多个反例按根因合并。

## 验证方法与范围

本组用临时目录、实际 socket/HTTP、真实 CMD/PowerShell、微型 ZIP 和文件锁复现；下载、setup 产物和原生推理在单元回归中使用替身。整合包的首批 19 个新方法在修复前产生 38 个失败及 4 个错误（含 subtests）；更新器初始 15 个新方法产生 11 个失败及 3 个错误。节点选取 13 个缺陷方法对不可变基线执行，全部复现失败。这些失败日志用于证明反例存在，不计入最终通过数。

额外选取深度 4000 的请求/输出及错误模板、Anthropic block 三个方法对不可变基线执行，均复现失败；与首批方法有重合，不另累计轮次或方法数。

本机最终回归：服务/整合包 **399 例通过，180.507s**；独立节点 **131 例通过，15.484s**；setup **258 例通过，21.640s**，均无跳过。更新器相关组合的 106 例已包含在整合包测试中，不能另行累计。证据为忽略目录 `.portable-build/audit4-root-final-full.log`、`audit4-node-final-full.log`、`audit4-setup.log`、`audit4-extra-baseline.log` 及两个子 Agent 的复现日志。

Windows 文件锁导致无法恢复时会明确报告未恢复的文件；配置和更新事务不保证断电时多文件同时提交。AMD 实机、多 GPU 及 Linux 托管原生引擎未验证。实际 GPU 与正式发行证据见下文。

## 本机 GPU 工作流

全部功能修复后的工作树在实际 ComfyUI `0.38.0`、Torch `2.7.0+cu128`、DreamShaper 8 SD1.5 上执行；Windows、RTX 4060 Ti 16GB、128GB RAM，沿用已校验模型，没有重新下载。

| 工作流 | 耗时 | 图片数 |
| --- | ---: | ---: |
| 文本提示词到绘图 | 28.28s | 1 |
| 图片反推提示词到绘图 | 28.45s | 1 |
| 双镜头分镜到批量绘图 | 54.38s | 2 |

实际排队 load、缓存重复执行通过；每次返回下游前确认文字和视觉进程停止。使用独立验证档案，门槛为空闲显存 8GiB、内存 60GiB；产品默认空闲显存门槛仍为 12GiB。耗时只代表上述硬件、模型与设置。环境中未加载的 GLSL 扩展不在这三条工作流内。

`.portable-build/audit4-graph-results.json` 记录工作流 ID、产物、释放状态及六份相关功能源码 SHA256；与最终发布源码逐一核对。初次探针在发出任何队列请求前因探针变量名错误退出，修正后完整执行上述三条流程；没有把失败探针计作通过。


## 最终源码与正式发行

提交 `9e20fe5ab53b601646373407c6dd02a8172e0fbe` 的 [源码 CI](https://github.com/T8mars/Strata-T8/actions/runs/37452989952) 通过服务/整合包 399 例、独立节点 131 例、setup 258 例。源码 CI 没有嵌入式运行库，399 例中 5 例明确跳过；正式发行构建安装真实运行库后执行同一列表。

[整合包 0.1.39-t8.11](https://github.com/T8mars/Strata-T8/releases/tag/v0.1.39-t8.11) 的 [发行构建](https://github.com/T8mars/Strata-T8/actions/runs/37453418848) 运行服务/整合包 399 例、节点集成 131 例、setup 258 例全部通过，无跳过；完整文件 SHA256 与 ZIP CRC 检查后正式发布。

| 发行包 | 字节数 | 文件数（不计清单） | ZIP SHA256 |
| --- | ---: | ---: | --- |
| VisionReady-NoMainModel | 1,922,430,308 | 8,021 | `d67c076ed13d24fedf1a8c6a6cb1c57554d02bb0c869ae185a413663a91a1f86` |
| Portable-NoModels | 1,208,743,706 | 8,020 | `7e964f77defc26ddcf2d19d3631c4cc5241d7a97609d8c5ba42a3228d7230abc` |

发布后再次验证 GitHub 资产摘要与构建输出、公开 SHA256 文件、远端 ZIP 清单及 14 份关键源码/配置。只读取约 4MB 的验证范围，没有重新下载完整整合包或模型。VisionReady 含 Python、依赖、CUDA/HIP 引擎、固定视觉权重及配置；两个版本均不含主模型和 MTP。

最终提交的本机文字推理返回 STRATA_OK，16.88s；视觉正确识别红圆、蓝方块及测试文字，3.81s。完成后卸载文字和视觉引擎，HTTP 在 `127.0.0.1:8082` 保持在线。三个绘图流程的六份相关功能源码 SHA256 与最终源码逐一匹配。

[节点 1.0.3](https://github.com/T8mars/Comfyui-Strata-T8/releases/tag/v1.0.3) 源码为 `4203df3caef5495d998a8a9d6f9721c12a885cdc`。[Windows/Linux CI](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37452842843) 通过；Windows 131 例全部执行，Linux 131 例中明确跳过 1 个 Windows 专属用例。[官方发布工作流](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37452843332) 成功。GitHub ZIP SHA256 为 `ea7a8b4b7550bc70f104c8d54f00fa0088963cc36211eb52c327616bf0e1a155`，Registry ZIP SHA256 为 `64703daad0c015232710bb96c23a692cf0032f066e015145fed8827f3d296f93`；两份包代码逐字节匹配发布提交，10 个节点可导入，没有模型或个人档案。

Publisher 为 `t8star`。[Registry 版本接口](https://api.comfy.org/nodes/strata-t8/versions/1.0.3) 本次核查状态为 `NodeVersionStatusPending`；CDN 包可取得，审核期间使用 GitHub Release 安装。发布成功不等于确认 Manager 已可检索。

本机证据：`.portable-build/cloud-v11-ci-success.log`、`cloud-v11-release-success.log`、`release-v11-published.json`、`node-v103-published-verification.json`、`registry-v103-final-status.json`、`deployed-v11-results.json` 和前述回归/GPU 日志。原始后台日志、API key、个人档案和模型未纳入公开仓库。本段追加于正式发布后，未替换已经发布的版本或资产。
