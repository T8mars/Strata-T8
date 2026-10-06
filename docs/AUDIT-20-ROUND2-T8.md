# 第二次 20 轮联合检查（2026-10-06）

基线为整合包 `0.1.39-t8.7` 与独立节点 `1.0.0`。主审检查整合包和服务，独立子 Agent 检查节点。每轮对应不同目标。第一次记录见 [AUDIT-20-T8.md](AUDIT-20-T8.md)，没有将旧检查或重复运行完整测试计入本轮。

| 轮次 | 本轮目标及结果 | 证据 |
| --- | --- | --- |
| R01 | 配置直接覆盖在磁盘写满时截断旧文件；改为完整写入、fsync 后原子替换 | test_portable_round2.ConfigurationPersistence，部分写入 |
| R02 | 替换失败留下凭据临时文件；清理临时文件，保留旧配置和 Settings 备份 | ConfigurationPersistence，替换失败 |
| R03 | 数组、错误字段类型或缺少目录的模型描述导致原始 Python 异常；改为提前诊断 | DeliveryValidation，多种错误描述 |
| R04 | 零字节主模型/MTP 被当作完整数据；导入拒绝空文件，准备器重建空 MTP 层 | DeliveryValidation，四种空组件 |
| R05 | Settings 接受 NaN/Infinity 或因巨整数崩溃；保存前拒绝非有限/不可表示值 | serve.test_runconfig.Apply |
| R06 | 复核非有限值通过 Settings HTTP 返回 400，配置字节和备份状态保持 | serve.test_runconfig.Http，真实 HTTP |
| R07 | 服务支持视觉 lazy loading，设置页仍拒绝；删除旧限制并更新帮助 | serve.test_runconfig.Apply |
| R08 | 下载失败留下大型更新暂存目录；核对绝对路径后仅清理本次新建目录 | FailedUpdateStaging，下载中断 |
| R09 | apply 脚本复制失败后仍有计划；复制完成后才原子发布计划 | FailedUpdateStaging，复制失败 |
| R10 | 成功准备更新保留重复 ZIP；核对后删除压缩包，保留已验证展开文件 | FailedUpdateStaging，成功路径 |
| R11 | 新清单与计划摘要/数量未绑定，改计划可接受坏文件或遗漏必需文件；提前核对 | ApplyManifestBinding，真实 PowerShell |
| R12 | 旧计划未绑定安装清单；增加核对，拒绝误删用户自建文件 | ApplyManifestBinding，真实 PowerShell |
| R13 | 复核新增清单检查不破坏文件锁回滚、中文路径和用户配置保留 | tools.test_portable_update，真实 PowerShell |
| R14 | 损坏 bootstrap 缓存只报错；重新获取并核对固定 SHA256 | BootstrapCache，损坏缓存 |
| R15 | bootstrap 中断留下最终缓存文件；临时下载并验证后替换 | BootstrapCache，流读取异常 |
| R16 | NoModels 漏检 MTP .bin；排除未知二进制权重，保留六种上游小型路由统计/词表掩码 | BinaryWeightPolicy、test_portable_weights |
| R17 | 复核已完成块复用、尾块精确范围和短读重试 | ResumedModelRanges，微型固定摘要数据 |
| R18 | 复核最终 SHA 失败清空完成块，下一次重新获取；国内优先来源保持 | ResumedModelRanges、test_model_sources |
| R19 | 上游文档生成失败后 abort 未恢复 README-UPSTREAM；恢复受本工具管理的原文件 | test_sync_upstream，真实 Git |
| R20 | 两仓完整回归、官方 ComfyUI API、三类实际绘图流程和发行文件核对 | 最终验收记录 |

新增回归位于 `tools/test_portable_round2.py`、`serve/test_runconfig.py` 与 `tools/test_sync_upstream.py`。CI、上游同步和自动发布均执行新回归。[节点第二次 20 轮记录](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/AUDIT-20-ROUND2-NODES.md)。

## 验证范围

实机为 Windows、RTX 4060 Ti 16GB、128GB RAM；模型沿用已校验的本机 IQ3_S/MTP 和视觉编码器。回归使用临时目录、模拟下载和模拟引擎，不下载模型。AMD 硬件和其他第三方节点组合仍未实机验证。

最终本机回归：整合包/服务 **305 例**、安装器 **258 例**、独立节点 **84 例**，全部通过，无跳过。安装器分组与整合包分组共享部分用例，数量不相加宣称互不重复。节点新增 29 例及 1 例发行权重检查，修复 14 类问题；官方 API/CPU tensor 探针另行执行。

最终实机 ComfyUI 0.38.0 / Torch 2.7.0+cu128 / DreamShaper 8 SD1.5：文本绘图 27.27s（1 张），图片反推绘图 27.16s（1 张），两镜头分镜批量绘图 51.34s（2 张）。Control(load) 经真实队列执行，重复请求命中缓存，每次下游绘图前确认语言/视觉进程均停止。计时为本次小型工作流总耗时，不能当作模型吞吐量。验收档案使用 8GiB 空闲显存门槛，默认配置仍为 12GiB。
