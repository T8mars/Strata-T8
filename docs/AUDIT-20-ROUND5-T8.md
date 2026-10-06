# 第五组 20 轮联合检查（2026-10-06）

整合包基线 `0.1.39-t8.11`、提交 `ef47f215`；节点基线 `1.0.3`、提交 `46437e9`。本组为新一组 20 个检查目标，前四组及重复回归不计轮数。主审负责请求和配置；三个子 Agent 分别负责更新器、节点和服务端 Schema，并交叉审查修复。

| 轮次 | 新检查目标、发现与处理 | 验证 |
| --- | --- | --- |
| R01 | 错误 `$schema` 类型可抛裸异常、未知 URI 被默认草案接受；只接受已支持的草案声明 | SchemaDialects、SchemaHTTP；两个 API 在加载前返回 400 |
| R02 | 父草案对整个树进行 meta 检查，误拒合法跨草案 `$id` 子资源；每个资源使用声明或继承的草案，正确枚举旧 dependencies | SchemaDialects、SchemaHTTP；2020-12 与 Draft7 数组元组，混合属性/Schema dependencies，正反输出 |
| R03 | 孙级引用使用根草案、旧草案被忽略的 `$ref` sibling 仍被扫描；继承当前草案、按有效关键字扫描 | SchemaDialects；旧草案注解、忽略 sibling、引用使 sibling 生效及现代草案反例 |
| R04 | Responses 推理回放的 text 类型、Unicode 或 JSON 不受控，直接 reasoning content 可抛类型异常；严格解析并检查字符串 | RequestPayloads；实际 HTTP 400、load 未调用，兼容外部 opaque token |
| R05 | 双重编码 messages、工具 arguments 可绕过外层严格 JSON；重复应用唯一成员、有限数和 UTF-8 检查，所有请求 JSON 最多 128 层 | RequestPayloads；实际 HTTP、600 层参数，保留 Responses 截断 arguments 的文本回放 |
| R06 | 非字符串 call_id 导致工具结果排序崩溃；提前检查类型 | RequestPayloads；非法 ID 与合法逆序结果归位 |
| R07 | 巨大整数采样值在原生参数浮点转换时溢出；加载前运行采样转换检查 | RequestPayloads；两个 API 的巨大整数、Anthropic tune，保留 top_k 的既有截断规则 |
| R08 | 缺失或无效 OpenAI role 到模板阶段才出错或被忽略；先验证支持的角色 | RequestPayloads；保留 developer 和 assistant 空工具回合 |
| R09 | 文本和 thinking block 的数字/对象/list 触发 join 或模板异常；文本叶必须为字符串 | RequestPayloads；三个 API 与合法图文顺序 |
| R10 | image_url、base64 data/media_type 等叶类型错误在加载后才失败；在消息转换阶段检查 | RequestPayloads；三个 API，load 未调用 |
| R11 | namespace 元数据和成员类型可引发异常，展开后的重复工具名称含糊；先验证并拒绝碰撞 | RequestPayloads；合法命名空间保持原映射 |
| R12 | 字符串 stream 被当成开启、错误 stream_options 可在输出阶段失败；检查布尔值与对象 | RequestPayloads；非法输入 400、合法 SSE 完整终止 |
| R13 | setup 返回成功但没有配置仍记录完成，旧配置可误记为新数据；要求实际配置文件和对象，未更改配置只允许匹配原模型和机器，失败恢复配置及状态 | ConfigurationCompletion；缺失/非对象、旧配置 no-op 与字节快照；相同模型 no-op 保持兼容 |
| R14 | 共享默认值覆盖显式 `max_tokens:0` 或 `max_completion_tokens:0`；仅在缺失/None 时继承 | RequestPayloads；真实 HTTP 回答超过共享一 token，缺省仍继承 |
| R15 | 更新包、API、meta 与旧清单 JSON 可有重复成员或非标准数字；严格解析 | 更新器 Round5；实际微型 ZIP 与 API 替身 |
| R16 | PowerShell 计划字段、文件数组/整数及 Windows/用户保留路径缺少独立检查；应用前预检 | 更新器 Round5；实际 PowerShell，修改前拒绝 |
| R17 | 延迟应用时暂存可能新增未列文件，清单可缺少 meta；核对完整暂存与必要元数据 | 更新器 Round5；额外文件、缺失 meta；非目录根原已受控拒绝 |
| R18 | 预检后新出现同名文件会被覆盖，复制失败时也会登记为已安装并误删；同目录临时复制、无覆盖移动，成功后登记 | 更新器 Round5；实际 PowerShell 注入竞争文件，失败后用户内容保留 |
| R19 | PowerShell 5.1 的 null 备份参数使既有结果文件替换失败；使用 NullString，修正失败报告和只读新文件回滚 | 更新器 Round5；既有结果、计划删除失败、只读回滚 |
| R20 | 重复准备遗留未应用暂存；仅回收匹配所有权、完整摘要与文件集合的旧暂存 | 更新器 Round5；保留备份、用户新增内容、链接及无标记目录 |

20 个检查目标不等于 20 个独立 BUG。同一解析器或事务中的多个反例按根因合并；原已通过的边界明确记录。单元回归不下载模型，原生推理以替身测试；实际 GPU 流程单独记录。

[节点第五组报告](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/AUDIT-20-ROUND5-NODES.md) 记录独立节点的 20 个检查目标。

## 基线复现

`tools/test_portable_round5.py` 21 个新方法对不可变 `ef47f21` 副本执行，含 subtests 共 38 个失败、14 个错误。更新器 21 个新用例中，15 个在不可变基线失败、6 个原已通过。Schema 用例另记录错误草案、跨草案和引用继承的最小反例。失败数用于证明反例存在，不计入最终通过数。

证据位于忽略目录 `.portable-build/audit5-main-before.log`、`audit5-update-immutable-before.log`、`audit5-update-immutable-protected-before.log`、`audit5-schema-before.log`。更新器首轮调用漏用产品入口的 UTF-8 参数，造成旧中文 fixture 解码错误；该次运行未记作通过，随后按实际入口重跑。

交叉审查又复现深层工具参数和旧配置 no-op 的遗漏；增加 3 个方法，其中 2 个在不可变基线失败/错误、1 个原已通过。`tools/test_portable_round5.py` 最终 24 个新方法、Schema 模块 17 个、更新器 21 个，共 62 个新增方法。原有端口传递测试的 setup 替身改为实际写出配置，保留原断言；前两次完整运行的 fixture 错误不计作通过。

旧 Draft4/7 的混合 dependencies 在 schema 项排首位且使用本地 anchor 时，第三方引用库仍有兼容限制。现在提前受控拒绝，两个 API 返回 400 且不加载模型；可改用 `#/definitions/target` 等 JSON Pointer 引用。一般本地 pointer、anchor、2019 recursive 和 2020 dynamic 的既有回归继续执行，未宣称支持所有旧草案组合。
