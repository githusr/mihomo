按文件名顺序应用本目录中的 `*.patch`，Meta、Alpha 和保留的正式版本共用一份补丁。

使用 `git format-patch --full-index --binary` 导出补丁，命名为 `0001-description.patch` 等。
补丁必须包含对应的测试。不要直接修改自动生成的 `Meta`、`Alpha`、`_build/*` 分支。

默认已经适配核心升级地址、版本号和构建发布流程，不需要把这些改动再做成 patch。
提交 patch 后，下一次每日同步或手动运行 **Sync upstream** 时，会重建并覆盖保留的版本。
如果补丁不能通过三方合并应用，或测试失败，该版本保留上一次成功的发布。

