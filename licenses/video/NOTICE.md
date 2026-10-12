# 视频解码依赖

整合包包含 PyAV 15.0.0 及其 Windows wheel 附带的 FFmpeg 7.1.1 动态库。PyAV 源码采用 BSD-3-Clause；其完整许可保存在 Python 的 `av-15.0.0.dist-info/licenses`。FFmpeg 与随附编解码器各自保留独立许可，不能用 PyAV 的许可概括全部二进制组件。

本目录保留 FFmpeg 的版权说明及 LGPL/GPL 文本；固定来源、wheel SHA256 见 `sources.json`。构建时另生成 `runtime/VIDEO-DEPENDENCIES.json`，记录实际动态库文件摘要、FFmpeg 编译配置与库自报许可。这些动态库保持原样，位于 `runtime/python/Lib/site-packages/av.libs`，可用兼容二进制替换；接收者无需安装外部 ffmpeg 命令。

源码与构建入口：[PyAV 15.0.0](https://github.com/PyAV-Org/PyAV/tree/v15.0.0)、[FFmpeg 7.1.1](https://github.com/FFmpeg/FFmpeg/tree/n7.1.1)、[PyAV 二进制构建项目](https://github.com/PyAV-Org/pyav-ffmpeg)。随附独立编解码器还包括 [x264](https://code.videolan.org/videolan/x264) 与 [x265](https://bitbucket.org/multicoreware/x265_git)。视频依赖不包含主模型、MTP 或 ASR 权重。
