## Strata-T8 0.1.39-t8.14

完成第七组 20 轮联合检查。修复工具重名预检、生成函数名、缺外层标签时的最终参数丢失和关闭后参数混入；图片输入严格检查 base64 URI并限制为 64MiB，读取后取消不再转换。导入复核模型分片大小，启动脚本与最终端口保持一致并参加失败回滚，视觉 catalog 和下载 sidecar 限制在权重目录。

更新器绑定安装清单和 metadata，延迟执行独立检查版本、edition及清单变化，备份和回滚使用精确文件目标。上游同步不再改写共享 merge driver 或依赖可被抢换的 FETCH_HEAD；失败先恢复本次 merge，诊断分支绑定真实 commit。新增 68 方法，596 例完整回归、258 例安装器测试通过。独立节点 1.0.6 新增 23 方法，211 例通过。

VisionReady 包含 Python、锁定依赖、CUDA/HIP 引擎和运行库、BF16 视觉权重及配置；两版均不含主模型和 MTP，Portable-NoModels 不含任何权重。模型地址、安装路径、来源和致谢见 README。每个 ZIP 附 SHA256 与完整清单，支持发行更新与上游同步。

逐轮证据见 docs/AUDIT-20-ROUND7-T8.md，节点见 https://github.com/T8mars/Comfyui-Strata-T8 。AMD 实机尚未验证，Windows AMD 视觉暂不支持。
