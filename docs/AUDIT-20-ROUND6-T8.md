# 第六组 20 轮联合检查（2026-10-06）

整合包基线 `0.1.39-t8.12`、提交 `a9318b9`；节点基线 `1.0.4`、提交 `83d0286`。本组重新设置 20 个检查目标，不计入前五组或重复回归。主审负责 R01–R09，配置和更新子 Agent 分别负责 R10–R14、R15–R20；另一子 Agent 完成节点的独立 20 项检查，并交叉审查修复。

| 轮次 | 新检查目标、发现与处理 | 验证 |
| --- | --- | --- |
| R01 | 工具 parameters/input_schema、properties 或 description 类型错误，直到生成时才失败；先检查模板及解析器消费的字段 | 三个 API 实际 HTTP 400，load 未调用 |
| R02 | 合法的布尔属性 Schema 被当作对象读取；无类型提示的属性按普通参数解析 | 逐字符流式、完整调用及三个 API 流式/普通响应保持一致 |
| R03 | OpenAI/Anthropic 历史工具调用名称和 Anthropic input 类型错误；加载前检查 | 空名、非字符串名、非对象 input，HTTP 400 |
| R04 | Anthropic 未知 role 和 falsey 非内容值被忽略或留到模板；先检查支持的角色和内容容器 | HTTP 400，不加载模型；保留已有扩展角色 |
| R05 | 同一 Anthropic user 回合包含 tool_result 时图片丢失，嵌入工具结果的图片也丢失；保留文本/图片顺序和图像来源 | 正向消息转换、非法图片源预检；模型推理不用于此单元测试 |
| R06 | Responses custom input、falsey 非字符串 namespace 被改成空值；严格检查叶类型 | 实际 HTTP 400，原字符串回放保留 |
| R07 | 模型生成 NaN/Infinity/溢出数字，被写成不标准的工具 JSON；不能严格解析的参数保留原文本 | 三个 API 普通/流式 wire JSON及工具 arguments 均可严格解析 |
| R08 | 模型生成重复成员、过深对象或无效 Unicode 的工具参数；严格解析失败后保留原文本 | 逐字符输出与完整调用一致，不丢原始参数 |
| R09 | 工具 Schema 和字符串参数兼容性、token count 的无模型行为 | 布尔 Schema、声明 string 的 NaN 文本、合法嵌套值；计数不调用 load，检查通过 |
| R10 | 同机器 setup no-op 吞掉显式新 context/backend/port；要求结果匹配请求 | 临时配置/状态字节快照，失败恢复；相同设置仍接受 |
| R11 | 托管输出可覆盖网页配置、settings、模型或应用文件；拒绝保留路径及其解析后的别名 | 临时输出和真实 Windows junction；独立自定义配置仍可写 |
| R12 | 准备期间 GGUF 消失或截短仍发布 ready；发布描述符前重新核对所需文件大小 | 临时小模型与准备工具替身；不下载模型 |
| R13 | 模型 catalog 文件名和组件目录 junction 可写出模型交付目录；执行前、发布前检查最终路径 | 路径、大小写碰撞和真实 junction，外部内容保留 |
| R14 | 生成的配置缺失 args，exe/port/backend 等类型错误仍记录完成；完成前检查消费字段 | 配置与状态回滚，合法配置保持兼容 |
| R15 | 暂存预检后变更或复制损坏的字节被安装；核对实际临时副本后再提交 | 实际 PowerShell 注入变更，包含清单副本；失败回滚 |
| R16 | 延迟执行器缺少 metadata/version/edition/权重角色绑定；单元素数组可被 PowerShell 展开成对象 | 实际 PowerShell 修改前拒绝，两个合法 edition 正例 |
| R17 | 已有 clean paused Git 操作可能被同步器 abort；同步前检查 Git 操作状态 | 临时真实 Git checkout；既有操作保留 |
| R18 | 上游合并覆盖 ignored 本地文件/目录；预检拓扑碰撞，保留未开始 merge 的失败诊断 | 临时真实 Git，保护文件、目录迁移和原 checkout |
| R19 | 新同步失败或 no-op 后仍留旧冲突描述符，工作流可能推旧分支；每次调用先清理自有描述符 | 实际 Git/失败入口；拒绝链接诊断路径 |
| R20 | 不完整稳定 tag、tag/CMake 不一致和上游版本回退；完整版本语法及一致性检查 | 临时真实 Git，失败恢复 metadata 和 README |

20 个目标不等于 20 个独立 BUG。同一根因的反例合并记录；R09 为正向兼容性检查。工具 Schema 检查仅覆盖本地模板和解析器消费的结构，不宣称对工具输入执行完整 JSON Schema 校验。[节点第六组报告](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/AUDIT-20-ROUND6-NODES.md) 单独记录节点侧的 20 个目标。

## 基线与回归证据

主审新增 `tools/test_portable_round6.py` 20 个方法，对不可变 `a9318b9` 副本显式指定模块路径执行，含 subtests 为 53 个失败、19 个错误；日志打印实际源码路径。嵌入式 Python 默认路径曾导致一次检查读到当前修复副本，该次不计作基线证明；最终证据为 `.portable-build/audit6-main-before.log`。修复后 20 个方法通过；交叉审查补强重复 XML parameter 的完整/流式一致性，失败返回 502 或流式失败事件，不产生成功工具调用。此前服务/API 相关 200 例回归通过。

配置新增 22 个方法，不可变基线 3 个通过、18 个方法失败和 1 个方法错误（36 个失败子案例、1 个错误）；相关回归 119 例通过，19.467s。更新新增 25 个方法，不可变基线 4 个通过、21 个方法含失败/错误（26 个失败子案例、2 个错误）；相关回归 93 例通过，111.895s。最终基线日志为 `audit6-config-baseline.log`、`audit6-update-immutable-before-final.log`，组件数字不另加进完整回归。

两处旧准备测试的 download 替身改为产生 catalog 对应大小的 1 字节 GGUF；保留原测试断言，以符合“准备成功必须已有全部主分片”的契约。配置复核检查大小，首次下载的 SHA256 校验仍由下载器负责。完整回归、实际 GPU 与正式发行证据随后记录。

## 冻结源码本机验收

服务/整合包 **528 例通过，258.492s**，新增 67 个方法；setup **258 例通过，18.747s**。节点新增 28 个方法，完整 **188 例通过，24.113s**；升版 1.0.5、最低托管版本 t8.13 后再次 **188 例通过，25.736s**。均无跳过。日志为 `audit6-root-all-final.log`、`audit6-setup.log`、`audit6-node-full-second.log`、`audit6-node-v105-final.log`。

ComfyUI 0.38.0 / Torch 2.7.0+cu128 / RTX 4060 Ti 16GB / 128GB RAM / DreamShaper 8：实际队列文字提示词→绘图输出 1 图，28.16s；图片理解→绘图输出 1 图，35.22s；两镜头分镜→Batch→下游绘图输出 2 图，58.34s。另验证队列 load、缓存重复和每次文字/视觉进程释放，最后停止自有托管服务。`audit6-graph-results.json` 的 8 份相关功能源码 SHA256 与冻结文件一致。

首次派发发生在 ComfyUI 启动完成前，连接被拒绝；没有计作通过。辅助脚本增加 readiness 检查后，上述三图重新完整执行成功。可选 GLSL 模块缺失，未用于上述工作流；没有停止用户的其他服务，没有重新下载模型。节点官方 V3 CPU 探针独立验证列表/Unicode/缓存和透传，未初始化 CUDA，见 `audit6-node-official-probe.log`。


## 正式发行核对

最终源码 `d0a3c5ab0e1775583aba7a7b15c79d6b502f5947` 的[源码 CI](https://github.com/T8mars/Strata-T8/actions/runs/37470044887) 成功：528/188/258 例。源码 CI 没有嵌入式运行库，其中 5 个运行库用例明确跳过；[正式发行构建](https://github.com/T8mars/Strata-T8/actions/runs/37470047582) 安装真实运行库后，同一列表 **528/188/258 例全部通过，无跳过**。

[整合包 0.1.39-t8.13](https://github.com/T8mars/Strata-T8/releases/tag/v0.1.39-t8.13) 已正式发布并为 latest。清单绑定上述提交；构建端核对完整文件 SHA256 和 ZIP CRC。

| 发行包 | 字节数 | 文件数（不计清单） | ZIP SHA256 |
| --- | ---: | ---: | --- |
| VisionReady-NoMainModel | 1,922,449,722 | 8,023 | `759f2cb614b7e2b83269d1e29ab1c6ad06f030eabf6e168f4701e69e9ba44d1b` |
| Portable-NoModels | 1,208,763,091 | 8,022 | `c32fcf20f4b68de7275b9ca5ebc0a8a3589d828fa48478c27b51b9f0c3fe0505` |

再次核对 GitHub 资产摘要、公开 SHA256 文件、两个远端 ZIP 的清单及 19 份关键源码/配置。公开范围验证合计读取 4,221,653 字节，没有重新下载整合包或主模型。VisionReady 含 Python、依赖、CUDA/HIP 引擎、固定视觉权重及配置；两个包均不含主模型及 MTP，NoModels 不含任何权重。

最终提交的本机文字推理返回 STRATA_OK，22.19s；视觉正确识别红圆、蓝方块和测试文字，5.16s。引擎已卸载，HTTP 在 `127.0.0.1:8082` 保持在线。三个 GPU 流程的 8 份相关功能源码 SHA256 与当前文件匹配。

[节点 1.0.5](https://github.com/T8mars/Comfyui-Strata-T8/releases/tag/v1.0.5) 源码 `0452eb109136fe0c1958d5133612a5af7f31df0e`，[Windows/Linux CI](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37469986731) 及[官方发布工作流](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37469987350) 成功。Windows 188 例全执行，Linux 188 例中明确跳过 1 个 Windows 专属用例。GitHub ZIP SHA256 `71717f623abebe949b72164ab8a378ab3d66474cd329cd1c6adc07eb843b47d8`，Registry ZIP SHA256 `ea72c2668725d6fa0de39eb17ff8f5ec35690c7ac41ddcbb1147308aeba743b9`；代码逐字节匹配发布提交，10 个节点可导入，没有模型或个人档案。

Publisher `t8star`；[Registry 版本接口](https://api.comfy.org/nodes/strata-t8/versions/1.0.5) 最终核查为 `NodeVersionStatusPending`，CDN 包可取得。审核期间使用 GitHub Release，未把提交成功说成 Manager 已可检索。

证据位于忽略目录 `.portable-build/`：`audit6-cloud-root-ci.log`、`audit6-cloud-node-ci.log`、`audit6-cloud-node-publish.log`、`cloud-v13-release-success.log`、`release-v13-published.json`、`node-v105-published-verification.json`、`registry-v105-final-status.json`、`deployed-v13-results.json` 和前述回归/GPU 日志。本段是发布后的文档补充，没有替换不可变版本及资产。
