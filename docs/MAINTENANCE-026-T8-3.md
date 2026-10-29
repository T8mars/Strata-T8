# 0.2.6-t8.3 维护修复

本版基于上游 v0.2.6，修复 Terminal 和源码更新脚本中的四类问题。原生引擎、模型格式、T8 网页及 ComfyUI 协议保持兼容；节点继续使用 1.3.3。

| 修复 | 触发与结果 | 来源 |
|---|---|---|
| 停止失败状态 | `strata launch --stop` 全部失败或部分失败时返回 1，并显示失败原因；不再同时提示没有运行项 | [PR4568](https://github.com/Niko1221/Strata/pull/4568) / [Issue4398](https://github.com/Niko1221/Strata/issues/4398) |
| 旧 API Key 查找 | 损坏档案排在有效档案之前或之后，均不影响继续查找已有 Key；原配置不改写 | [PR4563](https://github.com/Niko1221/Strata/pull/4563) / [Issue4396](https://github.com/Niko1221/Strata/issues/4396) |
| 运行状态隔离 | 档案读取失败时不再访问未绑定变量，也不沿用上一行的配置路径；使用该行的空配置及默认地址 | [PR4562](https://github.com/Niko1221/Strata/pull/4562) / [Issue4395](https://github.com/Niko1221/Strata/issues/4395) |
| 源码更新判断 | 无 `origin/main` 的 fork 拉取失败时正常报错，保留本地提交和私有文件；真正无共同历史的迁移仍保存备份 | [PR4571](https://github.com/Niko1221/Strata/pull/4571) / [Issue4390](https://github.com/Niko1221/Strata/issues/4390) |

已补充行为复现和修后测试。相关七个测试文件在 Windows 上 202 项通过、10 个子场景通过；1 项 POSIX 文件权限测试按平台跳过。该范围包含新增 8 个测试方法，不重复加总。更新脚本使用真实临时 Git 仓库、Windows 批处理 CALL 和 Git Bash，覆盖无 main、普通分歧、真正旧历史三类场景，并核对 HEAD、备份、安装是否执行和私有假数据是否保留。

源码更新脚本用于 Git checkout。整合包的 `UPDATE.bat` 仍映射到 `UPDATE-PORTABLE.bat`，继续通过带 SHA256 和文件清单的组件更新。模型、设置与已有语音/视频功能沿用原有保护。主模型、MTP、语音权重和私有 roadmap 不进入发行包；VisionReady 保留视觉权重与配置。

本轮没有重新测量大模型速度、显存或语音准确率。上游 v0.2.7 涉及较大源码与默认策略变化，尚未整体迁入本版；不能将上游报告的性能数字解释为本项目已经测得的结果。发行包及更新入口的发布前检查结果见本版 Release。
