# Strata-T8 更新机制

## 用户整合包

运行 UPDATE-PORTABLE.bat 即可更新，无需 Git 或系统 Python。启动时的版本检查只提示新版本，超时或离线不阻塞启动。

1. 检查本目录内的 Python/引擎进程；有则要求先退出。
2. 查询 T8mars/Strata-T8 最新正式 Release，比较 `上游版本-t8.修订`。
3. 下载完整包及 SHA256，核对大小、压缩包哈希、文件清单、每个文件的哈希和版本。
4. 拒绝路径越界、符号链接、模型目录、用户配置及未列入清单的文件。
5. Python 退出后由 PowerShell 再次检查进程、暂存清单哈希与新增文件冲突，再替换受管程序文件，删除旧版本受管的过期文件。
6. 原文件移动到独立备份目录；替换失败逆序恢复。结果和备份路径写入 `.portable-update/result.json`。

主模型、MTP、配置、日志和其他自建文件不在受管清单内。VisionReady 的指定 mmproj 是受管资产，更新时按固定角色、路径、大小与 SHA256 校验；其余权重仍拒绝。新版本程序与自建文件重名会停止更新。网页聊天记录保存在浏览器，保持相同浏览器和服务地址即可沿用。SHA256 检测下载损坏，发行来源是此 GitHub 仓库。

同一电脑、同一模型目录升级后的首次启动不重新运行安装默认配置；保留端口、API key、采样和手动引擎参数，刷新所需运行库路径并执行上游兼容迁移。换电脑、移动模型或主动指定上下文/后端时重新配置。[发布验收记录](VALIDATION-T8.md)

## 上游同步

`Sync upstream` 每日检查 Niko1221/Strata 最新正式 Release，保留 Git 历史进行合并，运行服务器、安全、更新和安装测试，通过后推送 main，并调用自动打包工作流。

在 Actions 手动运行，选择 `main` 将上游最新源码合入 `codex/upstream-main-*` 验证分支，测试通过后推送该分支；稳定 main 分支仍跟随正式版，此模式不自动发布整合包。README.md 是 T8 入口，合并时保留；上游 README 更新到 README-UPSTREAM.md。其他源码冲突时终止合并，保留指向上游提交的 `codex/upstream-*` 分支及冲突诊断 artifact，不覆盖 T8 修改。

源码用户也可在干净的 Git 工作树运行（需要 Git、Python 与已登录 GitHub CLI）：

```text
python tools/sync_upstream.py --source release
python tools/sync_upstream.py --source main
```

上游 Release 可以使用四段热修复版本（例如 `v0.1.40.1`），源码及引擎仍使用三段 `0.1.40`，T8 发行版本为 `0.1.40-t8.1`。meta.json 分别记录 `upstream_release_tag`、真实 `upstream_commit`、`engine_version` 和官方引擎资产 SHA256；只有 Release 前三段与源码版本一致时才允许发布。正式版自动刷新资产摘要，没有源码变化时也核对摘要。main 验证分支不沿用旧资产摘要，同时保留 `upstream_stable_release_tag` 作为正式版下限；旧版本或旧于当前 main 的正式提交不能重新标记为新版。

上游重写历史时，仅在已导入的上游提交仍属于本仓历史，且新历史包含完全相同的文件树时建立历史连接。找不到此锚点则停止并保留诊断，交由人工审查；不会清空或强制重置 T8 历史。v0.1.40.1 同步已连接新旧相同的 v0.1.39 基准。

Git 克隆副本使用上游 START-HERE.bat / UPDATE.bat 安装更新；整合包的 UPDATE.bat 打包时映射到 UPDATE-PORTABLE.bat。整合包更新器拒绝覆盖 Git checkout。

## 自动打包与发布

`Build portable release` 可由同步工作流调用，也可手动对指定提交运行。同步工作流传入通过测试的完整提交 SHA。Windows x64 runner 读取 meta.json 和 CMakeLists.txt，下载固定 SHA256 的嵌入式 Python，安装锁定依赖与 CUDA wheels，从记录的上游 Release 标签获取 CUDA/HIP 引擎，核对资产 SHA256、来源提交与 BUILD.json。为未签名的 strata.exe 与 strata-vision.exe 添加应用 UTF-8 manifest，保留其他资源及权限；记录原始/分发二进制 SHA256，并在打包清单中记录实际修改后的引擎。已签名资产需要由上游构建支持后再发行。

缺少匹配引擎、依赖不兼容或测试失败均停止发布。打包采用明确文件列表，只允许固定视觉权重，禁止主模型、MTP、用户模型目录和本地规划文件。清单记录 T8 源码 SHA、上游标签/提交、引擎版本、依赖来源及每个文件的摘要。

发布器再次逐文件校验两版 ZIP、SHA256、清单和引擎来源，只上传本版本的四个资产。发布前确认远端标签指向实际测试 SHA；无标签的草稿须绑定此 SHA，已有标签则以其实际提交为准。上传后核对四个资产的名称、大小和 GitHub 摘要，再公开 Release。网络或鉴权失败不能当作“Release 不存在”；同标签的已发布资产保持不变，标签指向其他提交时拒绝发布。

GitHub runner 没有目标 GPU，自动检查不代表所有 GPU 的推理验收。AMD 验证状态记录在 features.json。升级 Python 大版本时需同步路径配置并重新验收。

## 发行类型迁移

每版发布 VisionReady-NoMainModel 和 Portable-NoModels，分别附 SHA256。旧 t8.4 更新器仍能获取新 NoModels 包；升级后 INSTALL-VISION.bat 可在相同版本切换类型。后续 UPDATE-PORTABLE 默认沿用当前类型，更新器仍拒绝降级版本。

视觉配置在启动时迁移到新运行目录，保留 GPU/CPU 编码方式及 token 设置。切换 VisionReady 不下载主模型或 MTP。节点在 [独立仓库](https://github.com/T8mars/Comfyui-Strata-T8) 使用语义版本，通过 ComfyUI-Manager/Registry 或 Git 更新并重启；本机档案在节点目录外保存。整合包自动同步 Strata 上游；节点版本由其 pyproject.toml 管理，通过官方发布 Action 分别发布 GitHub Release 与 Registry。
