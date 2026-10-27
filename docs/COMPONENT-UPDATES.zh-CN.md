# 组件更新与成功升级回退

初次安装下载完整离线包，解压后运行 `Strata-T8.exe`；Python、引擎与运行依赖随包提供，主模型和语音权重独立安装。若完整 ZIP 以 `.zip.001` 等分卷提供，下载所有同名分卷、parts.json 和 ASSEMBLE 两个脚本，放到同一目录运行 ASSEMBLE.bat；校验合成后再解压。

新 Release 提供 Components 索引时，一键更新按组件校验本地文件，只下载变化或损坏的组件。组件包括应用、Python、CUDA、HIP、视觉权重与 CPU 语音运行环境。索引绑定发行版本、类型、软件兼容信息、文件 SHA256 和压缩包 SHA256；校验失败不会切换到未校验资源。旧 Release 没有索引时继续使用完整 ZIP 更新。

在模型设置页点击“查看可回退版本”，可离线回退当前版本对应的成功升级备份。回退沿用停止服务、检查目录、逐文件校验和失败恢复流程。主模型、模型配置、ASR 设置、对白缓存和视频进度均不属于更新内容。备份默认位于 `%LOCALAPPDATA%/Strata/portable-backups`，升级记录位于运行目录 `.portable-update/history`；删除或修改备份后不能回退。

命令行入口：

```text
runtime/python/python.exe -X utf8 tools/portable_update.py --backups
runtime/python/python.exe -X utf8 tools/portable_update.py --restore <备份ID>
```

命令只准备经过校验的计划，随后由 `.portable-update/apply.ps1` 应用；网页入口会自动完成这些步骤并重新打开模型设置。

开发者在已经验证的完整解压包上运行：

```text
runtime/python/python.exe -X utf8 tools/build_components.py --package <完整解压包> --output <发行资产目录>
```

从 **0.1.42-t8.9** 起，发布工作流为两种发行类型生成索引、校验文件及共享组件资产。发布器再次将组件清单与完整离线 ZIP 逐项绑定，并校验每个组件成员；每个组件资产小于 2 GiB。初次安装请选择完整 ZIP；组件 ZIP 由更新器自动使用。

0.2.6 继续使用契约 1 和原六个组件类别。新增的 DeepSeek、RDNA2、RDNA3 引擎文件归入应用组件，CUDA 12/13 归入 CUDA 组件；这是兼容旧更新器的传输分类，具体引擎来源与硬件能力仍在元数据中独立记录。分卷只用于初次下载完整环境，不改变组件更新流程。
