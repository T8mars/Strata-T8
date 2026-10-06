# 第七组 20 轮联合检查（2026-10-06）

整合包基线 `0.1.39-t8.13`、不可变提交 `542e080`；节点基线 `1.0.5`、不可变提交 `8c69d89`。主审负责 R01–R09，交付与更新子 Agent 分别负责 R10–R14、R15–R20，节点子 Agent 另行完成 20 个新焦点。交付子 Agent 交叉审查主审和节点修复。旧回归另记，不充作新轮次。

| 轮次 | 新检查目标、发现与处理 | 验证 |
| --- | --- | --- |
| R01 | OpenAI/Anthropic 重复工具名称使 Schema 后项覆盖前项；加载前拒绝重名 | 实际 HTTP 400，load 未调用 |
| R02 | 模型生成空白或无效 Unicode 函数名仍成为成功调用；统一名称校验与生成失败 | 完整/逐字符解析及三个 API 普通/流式失败路径 |
| R03 | 缺外层结束标签时最终参数丢失；按完整内层函数重新解析并保留 call ID。交叉审查另发现函数结束后的参数混入最终值，现受控拒绝 | Responses custom 原始输入实际 HTTP；流式/最终参数及 ID 一致；四种尾项组合失败 |
| R04 | 缺逗号 data URI 抛 IndexError，非法 base64 字符被忽略；要求带逗号的 base64 URI并严格解码 | 缺分隔符、非 base64、非法字符均 ValueError |
| R05 | HTTP、本地文件与 data URI 没有图片字节上限；输入最多 64MiB，流最多读取上限加 1 字节 | 临时文件、真实微型 HTTP（有/无 Content-Length）、编码/解码后边界 |
| R06 | 自定义脚本中的 Unicode、换行及字面 XML 标签在最终调用中保留 | 三种分块大小、外层完整/缺失；检查通过 |
| R07 | 多个工具调用的后一调用截断时，不能沿用前一完整调用或产生可执行结果 | 自定义输入截断、两个不同 call ID；检查通过 |
| R08 | 不同请求线程清理不能删除另一线程的图像嵌入文件 | 实际两线程与临时文件；检查通过 |
| R09 | 图片读取后取消仍执行转换；读取完成后先检查取消 | 已取消时不读取，读取后取消时不 normalize 或写原生编码命令 |
| R10 | 旧 ready 描述符的导入忽略 catalog 文件大小，截短分片仍被接受；导入核对大小、名称及重复项 | 临时小模型反例；无 catalog 的旧描述符兼容 |
| R11 | 托管配置复用的凭据隔离、串行生命周期和参数保留 | 缓存配置与保存失败字节快照；检查通过 |
| R12 | 最终配置保留端口，但 companion launcher 仍用 setup 默认端口；失败没有恢复脚本 | 按最终配置重写脚本，三文件回滚、链接预检；真实 CMD 核对传入端口 |
| R13 | 视觉 catalog 与下载 sidecar 可引用权重目录外路径；校验来源字段、名称和解析后路径 | 临时目录/真实链接、合法离线 SHA 校验 |
| R14 | 国内优先、固定 revision、错误内容后回退镜像 | 真实微型 Range HTTP，SHA 拒绝首来源并成功使用镜像；检查通过 |
| R15 | PowerShell Move-Item 将旧文件吞入竞争同名目录，回滚可误报成功；精确文件移动、拒绝覆盖、复核路径 | 实际 PowerShell；不能恢复则保留备份并报告 rollback_errors；同名文件本来拒绝，记正例 |
| R16 | 安装身份在准备和延迟执行间变化，旧计划仍可发布；绑定旧清单 SHA、metadata/edition 与数值版本 | 实际下载期间升级、PowerShell 延迟降级/同版重装拒绝；数字 revision 顺序正例 |
| R17 | 同步器永久改写共享 merge driver 配置；仅本次 merge 使用 -c | 真实 Git/linked worktree 的 no-op、失败、成功均保留 config 字节 |
| R18 | FETCH_HEAD 被另一 fetch 抢换，annotated tag 未解析真实 commit；独立临时 ref 并 peel | 两次真实 fetch/tag；保留 FETCH_HEAD，最后清理临时 ref |
| R19 | 诊断写失败时自己的 merge 仍暂停；先核对并 abort 本次 MERGE_HEAD 再写报告 | 真实 Git 和诊断父文件/写入失败；用户替换的操作保留 |
| R20 | 诊断分支同名但指向错误提交；验证提交并选空闲后缀 | 真实 Git 短名/完整 SHA 碰撞，原分支保留；同提交分支正向复用 |

20 个目标不等于 20 个独立 BUG。同一根因的反例合并记录，正向项明确标注。[节点第七组报告](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/AUDIT-20-ROUND7-NODES.md) 另列节点的 20 个焦点。

## 基线与修复证据

主审新增 21 方法，对 `542e080` 的相关源码归档显式前置 sys.path，日志打印实际导入路径：31 个失败子案例、1 个错误；修复后 21 例通过，1.256s。第一次增补后的全仓库 ZIP 解压发生 CRC 错误，未作为最终基线；重新归档所需源码到独立 final 目录并完整执行。最终证据为 `.portable-build/audit7-main-before.log`。交叉审查尾参数的四种组合及参数内字面 `</function> 你好`，21 例再次通过，1.240s。

交付新增 21 方法：不可变基线 14 方法失败、7 方法原通过，24 个失败子案例、0 errors；修复后 21 例通过，1.285s，相关 166 例通过，31.139s。日志为 `audit7-delivery-baseline.log`、`audit7-delivery-after-final.log`、`audit7-delivery-related-final.log`。未改旧 fixtures。

更新新增 26 方法：不可变基线 19 方法失败、2 错误、5 原通过，44.543s；最终证据为 `audit7-update-immutable-before-final.log`。此前两个注入用例受 Windows 长短路径比较影响的日志不作为证明。真实 PowerShell/Git 验证，不下载模型。

节点新增 23 方法，完整 211 例通过，27.857s，无跳过；13 个选定缺陷方法对不可变基线全部失败，27 failures。官方 ComfyUI V3/CPU、真实 socket 与 Node.js 探针通过，未初始化 CUDA。

## 限制

图片上限检查输入字节，不是解码后的像素预算；base64 URI 使用连续标准 base64，MIME 折行须由客户端去掉。HTTP 源仍使用既有 60s socket timeout，本组不宣称慢流下载可即时取消。主模型导入复核大小，不重新哈希约 84GB 主权重，首次下载由下载器执行 SHA256。配置多文件回滚不能保证断电时同时提交。AMD 实机推理未验证，Windows AMD 视觉仍不支持。


## 冻结源码 GPU 验收

ComfyUI 0.38.0 / Torch 2.7.0+cu128 / RTX 4060 Ti 16GB / 128GB RAM / DreamShaper 8：text-to-image 23.17s，1 图；image-to-image 26.17s，1 图；storyboard-to-images 56.47s，2 图。队列 load、缓存重复及每次文字/视觉引擎释放均通过，最后停止自有托管服务。8 份相关功能源码 SHA256 与冻结文件匹配。

首轮三类流程已经通过，交叉审查补充尾参数修复后重启 ComfyUI并完整重跑全部三个流程；只将上述最终数字作为冻结验收。验收档案显存阈值 8GiB，产品默认 12GiB。可选 GLSL 模块未安装且未参与测试。未停止用户其他服务，未下载主模型。证据为 audit7-graph-results.json 与 audit7-comfy-graphs-final.log。


## 完整本机回归

新增 68 个方法，完整整合包 596 例通过，297.241s；节点升版 1.0.6 后 211 例通过，27.629s；安装器 258 例通过，18.505s，均无跳过。最终日志为 audit7-root-all-final-second.log、audit7-node-v106-final.log、audit7-setup.log。更新相关集最终 148 例通过，185.180s，无跳过。

首轮整合包 596 例有 4 个 Git 原生进程错误，Windows 事件日志记录 ntdll/msvcrt 中的原生异常；没有把这一轮计作通过。更新相关集首遍有一个 PowerShell 空 stdout，随后单例、连续 10 次执行和完整重跑通过。停止并行 GPU/其它测试后，完整 596 串行重跑成功，未为这些本机瞬态添加重试或修改功能。证据 audit7-external-process-events.json 保留。
