# Strata-T8 更新机制

## 用户整合包

运行 UPDATE-PORTABLE.bat 即可更新，无需 Git 或系统 Python。启动时的版本检查只提示新版本，超时或离线不阻塞启动。

1. 检查本目录内的 Python/引擎进程；有则要求先退出。
2. 查询 T8mars/Strata-T8 最新正式 Release，比较 `上游版本-t8.修订`。
3. 下载完整包及 SHA256，核对大小、压缩包哈希、文件清单、每个文件的哈希和版本。
4. 拒绝路径越界、符号链接、模型目录、用户配置及未列入清单的文件。
5. Python 退出后由 PowerShell 再次检查进程并替换受管程序文件，删除旧版本受管的过期文件。
6. 原文件移动到独立备份目录；替换失败逆序恢复。结果和备份路径写入 `.portable-update/result.json`。

模型、MTP、配置、日志和其他自建文件不在受管清单内。新版本程序与自建文件重名会停止更新。网页聊天记录保存在浏览器，保持相同浏览器和服务地址即可沿用。SHA256 检测下载损坏，发行来源是此 GitHub 仓库。

## 上游同步

`Sync upstream` 每日检查 Niko1221/Strata 最新正式 Release，保留 Git 历史进行合并，运行服务器、安全、更新和安装测试，通过后推送 main，并调用自动打包工作流。

在 Actions 手动运行，选择 `main` 将上游最新源码合入 `codex/upstream-main-*` 验证分支，测试通过后推送该分支；稳定 main 分支仍跟随正式版，此模式不自动发布整合包。README.md 是 T8 入口，合并时保留；上游 README 更新到 README-UPSTREAM.md。其他源码冲突时终止合并，保留指向上游提交的 `codex/upstream-*` 分支及冲突诊断 artifact，不覆盖 T8 修改。

源码用户也可在干净的 Git 工作树运行（需要 Git、Python 与已登录 GitHub CLI）：

```text
python tools/sync_upstream.py --source release
python tools/sync_upstream.py --source main
```

Git 克隆副本使用上游 START-HERE.bat / UPDATE.bat 安装更新；整合包的 UPDATE.bat 打包时映射到 UPDATE-PORTABLE.bat。整合包更新器拒绝覆盖 Git checkout。

## 自动打包与发布

`Build portable release` 可由同步工作流调用，也可手动对指定提交运行。Windows x64 runner 读取 meta.json 和 CMakeLists.txt 的版本，下载固定 SHA256 的嵌入式 Python，安装锁定依赖与 CUDA wheels，获取同版本上游 CUDA/HIP 引擎，核对资产 SHA256 与 BUILD.json。

缺少匹配引擎、依赖不兼容或测试失败均停止发布。打包采用明确文件列表，禁止权重文件和模型目录；生成文件清单、ZIP 和 SHA256，完成 CRC 检查后上传草稿，资产完整后发布。已有正式 Release 不替换资产。

GitHub runner 没有目标 GPU，自动检查不代表所有 GPU 的推理验收。AMD 验证状态记录在 features.json。升级 Python 大版本时需同步路径配置并重新验收。
