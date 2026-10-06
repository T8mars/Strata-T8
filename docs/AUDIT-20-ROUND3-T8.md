# 第三组 20 轮联合检查（2026-10-06）

基线为整合包 `0.1.39-t8.8`（`425786e` 工作树）和节点 `1.0.1`。主审负责整合包和服务，独立子 Agent 负责节点并交叉复审主仓变更。前两组的检查目标及重复完整回归不计入本组轮次。

| 轮次 | 新检查目标、发现及处理 | 验证 |
| --- | --- | --- |
| R01 | 改上下文重新配置恢复默认端口并丢失 API key；保留 host/key/port，显式端口仍覆盖 | ConfigureTransactions，实际配置文件 |
| R02 | 自动视觉配置丢失 CPU 模式、token 与自定超时；保留原模式和限制，再更新本机路径 | ConfigureTransactions，CPU 模式 |
| R03 | 视觉校验失败留下重建的文字配置；恢复配置与状态原字节 | ConfigureTransactions，视觉失败 |
| R04 | 最终状态保存失败时配置已改变；恢复两份文件，首次安装清除未完成文件 | ConfigureTransactions，状态写入失败 |
| R05 | setup 返回非零留下部分配置；保留错误码并恢复快照。回滚一份遭文件锁时仍尝试另一份，明确报告未恢复文件 | ConfigureTransactions，非零返回、sharing violation |
| R06 | 状态数组、非字符串目录及外部配置路径导致异常或选择错误文件；启动前诊断 | ConfigurationPaths，类型和路径反例 |
| R07 | 错误 engine args/vision 类型到启动时才失败；先拒绝错误配置 | ConfigurationPaths，类型反例 |
| R08 | 换到 NoModels 后仍引用已删除的内置权重；关闭内置视觉，保留外置配置。记录发行类型，修复同版本切换不刷新配置；安装视觉失败保留原类型供重试 | ConfigurationPaths、EditionTransitions，真实启动器路径 |
| R09 | MTP 可经 junction 指向交付目录外；解析组件完整路径后检查范围 | LinkedDelivery，实际 Windows junction |
| R10 | 索引存在但 chat template 缺失/为空时误认为准备完成；检查索引、词表、模板非空 | PreparedPack，离线准备替身 |
| R11 | Bootstrap ZIP 允许大小写别名和父文件/子文件冲突；先检查全目录 | ArchivePreflight，实际 ZIP |
| R12 | Bootstrap 允许 Windows 设备名、尾点和控制字符；拒绝非法路径 | ArchivePreflight，解压前拒绝 |
| R13 | Bootstrap 跟随目标 junction 或遇到已有父文件后部分写入；拒绝链接及目标类型冲突 | ArchivePreflight，实际 junction；缓存官方 ZIP 目录 |
| R14 | bool 文件大小、数字权重角色、错误摘要类型、非整数 workers/chunk 被接受或晚失败；入口严格校验 | StrictMetadata，下载前拒绝 |
| R15 | 安装清单同时出现父文件和子文件；拒绝含糊清单，拒绝控制字符路径 | StrictMetadata，verify=False |
| R16 | 更新控制目录是 junction 时先删除外部 plan；检查控制目录、plan/apply 链接与范围 | ExistingUpdatePlan，实际 junction |
| R17 | 新准备失败丢旧计划；失败保留旧计划。No-op 又可能误应用旧 edition 计划；No-op 清除旧计划 | ExistingUpdatePlan，两条独立路径 |
| R18 | 应用、暂存、备份目录重叠或备份父目录是 junction；替换前拒绝 | UpdateDirectoryPreflight，真实 PowerShell |
| R19 | 已知目标目录冲突到替换中途才发现；预检全部目标及父路径，冲突时尚未创建备份 | UpdateDirectoryPreflight，真实 PowerShell |
| R20 | 服务将 const/enum 内 `$ref` 当引用，缺失引用到生成后才失败；只检查有效 Schema 关键字及引用目标 | StructuredHttp，真实 HTTP 200/400/502，Draft4/7/2019/2020 与 anchor |

新增 `tools/test_portable_round3.py` 纳入 CI、上游同步和发行构建。交叉审查修复旧 Draft 的未知 `$dynamicRef` 注解被误执行，以及解压目标父文件和 No-op 旧计划边界。合法递归可继续使用；不能成功校验的循环引用受控返回 502，不宣称所有递归问题都能在模型加载前判定。

[节点第三组 20 轮](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/AUDIT-20-ROUND3-NODES.md) 修复 13 类问题、新增 21 例，完整回归 105 例通过。官方 V3/CPU tensor/PNG 探针验证动态档案选项、图片上限及身份透传，未初始化 CUDA。

## 验证范围

回归使用独立临时目录、真实 CMD/PowerShell、微型 ZIP/HTTP、模拟下载和模拟原生引擎。选取 13 个行为回归对旧提交源码执行，确认旧版失败；没有计作额外轮次。GPU 实测环境为 Windows、RTX 4060 Ti 16GB、128GB RAM，沿用已校验模型。

20 轮是 20 个不同目标，不等于 20 个 BUG，也不能证明所有硬件和第三方组合均无缺陷。操作系统拒绝恢复的文件会被明确报告，需要解除文件锁后恢复；配置事务不保证断电时两份文件同时提交。AMD 实机、多 GPU 和 Linux 托管原生引擎未验证。

## 最终验证

整合包新增 43 个回归用例。最终源码 `2a7989fca3168a9adba57022aa11fc8167bbe7ab` 的 [CI](https://github.com/T8mars/Strata-T8/actions/runs/37448730027) 运行服务与整合包 349 例、独立节点集成 105 例、setup 258 例，均通过。源码 CI 未安装嵌入式运行库，349 例中 5 例明确跳过；发行构建另外安装真实运行库后执行相同测试。

本机较早的 344 例完整回归有 1 个 fixture 错误：隔离 Git 仓库执行 `git commit -m base` 时子进程崩溃，退出码 `0xC0000005`。随后独立重跑该同步模块 6 例全部通过；最终源码 CI 也覆盖这些测试。未将这次本机运行记作全部通过。

节点 `1.0.2` 的 [Windows/Linux CI](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37446064080) 通过；本机完整节点回归 105 例、setup 258 例通过。节点新增的 21 例包含在 105 例中，未另行累计。

实际 ComfyUI `0.38.0`、Torch `2.7.0+cu128` 和 DreamShaper 8 SD1.5 完成三个工作流：

| 工作流 | 耗时 | 图片 |
| --- | ---: | ---: |
| 文本提示词到绘图 | 24.17s | 1 |
| 图片反推提示词到绘图 | 25.22s | 1 |
| 双镜头分镜到批量绘图 | 50.22s | 2 |

实际排队 load 及重复缓存执行通过，每次交接都确认文字与视觉进程停止。本机验证档案使用空闲显存 8GiB、空闲内存 60GiB 阈值；产品默认空闲显存阈值仍为 12GiB。耗时仅代表上述硬件、模型、工作流与设置。

最终源码启动后的文字 API 返回 `STRATA_OK`，耗时 21.98s；视觉正确描述测试图红圆、蓝方块及文字，耗时 5.06s。完成后卸载两种引擎，HTTP 服务在 `127.0.0.1:8082` 保持在线。最终追加的发行类型刷新由启动器回归及这次 API 验证覆盖；三个绘图流程是在本组核心修复后、追加刷新修复前执行。

原始证据保存在本机 `.portable-build`：`audit3-pre-fix-final.log`、`cloud-v10-ci-success.log`、`audit3-root-final-full.log`、`audit3-sync-rerun.log`、`audit3-node-final.log`、`audit3-setup.log`、`audit3-graph-results.json`、`deployed-v10-results.json` 和 `node-v102-published-verification.json`。API key、个人档案、模型及原始后台日志没有纳入公开仓库。

## 正式发行

[整合包 `0.1.39-t8.10`](https://github.com/T8mars/Strata-T8/releases/tag/v0.1.39-t8.10) 由提交 `2a7989fca3168a9adba57022aa11fc8167bbe7ab` 构建。[发行工作流](https://github.com/T8mars/Strata-T8/actions/runs/37448833481) 使用内置 Python 与真实运行库，服务/整合包 349 例、节点集成 105 例、setup 258 例全部通过，无跳过；完整文件 SHA256 和 ZIP CRC 检查通过后发布。

| 发行包 | 字节数 | 文件数（不计清单） | ZIP SHA256 |
| --- | ---: | ---: | --- |
| VisionReady-NoMainModel | 1,922,420,772 | 8,020 | `c86b15b12a99666daf550eff0abaf851a31cb455c92fbe4f88af2f5b5eb33958` |
| Portable-NoModels | 1,208,734,186 | 8,019 | `64e562d5566a2e12be9278b0c14cc274e9011e4255799a7c8367a8d9cc682aed` |

发布后再次核对 GitHub 资产摘要与构建摘要、公开 SHA256 文件、两个 ZIP 的远端清单和 11 个关键源码/配置文件。只下载约 4MB 的验证范围，没有重新下载完整整合包或模型。VisionReady 包含 Python、依赖、CUDA/HIP 引擎、固定视觉权重及配置；两个版本均不含主模型和 MTP。旧正式发行 `0.1.39-t8.9` 保持原资产，追加的同版本发行类型修复在 `t8.10` 发布。

[节点 `1.0.2`](https://github.com/T8mars/Comfyui-Strata-T8/releases/tag/v1.0.2) 发布源码为 `ba2f0f0ddba8c0871ec810b90ae845d98e36d8d1`。GitHub ZIP SHA256 为 `4abe8673e3f5a33b900dc0ea81513d934e6422ea2a2a4664166763a474b6ce7d`，Registry ZIP SHA256 为 `5afb87131bf6872d1342cb5d75830420a766ea0c6db58683226790baebdd6509`；两份包内代码逐字节匹配发布提交，10 个节点可导入，均不含模型和个人档案。

Publisher `t8star` 的 [官方发布工作流](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37446064716) 成功，Registry CDN 包可取得。[Registry 版本接口](https://api.comfy.org/nodes/strata-t8/versions/1.0.2) 在本次核查时仍为 `NodeVersionStatusPending`；等待官方审核，不能据此确认 ComfyUI-Manager 已能检索。审核期间可以使用 GitHub Release 安装。

发行证据：`.portable-build/cloud-v10-release-success.log`、`release-v10-published.json`、`node-v102-published-verification.json`、`registry-v102-final-status.json`。本报告补充发生在正式发布后，未替换已经发布的版本或资产。
