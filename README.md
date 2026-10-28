# mihomo personal fork

自动跟随 [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo)，在其精确源码上应用个人 patch，使用上游构建矩阵重新发布。

## 使用

- 每天 **03:17 UTC（北京时间 11:17）** 自动运行，也可在 Actions → **Sync upstream** → **Run workflow** 手动运行。
- 将 `git format-patch --full-index --binary` 导出的补丁放入 [`patches/`](patches/README.md)，按文件名顺序应用。patch 应包含自己的回归测试。
- 只编辑默认分支 `automation`。`Meta`、`Alpha`、`_build/*` 和发布 tag 都由机器人管理，可以被重写。
- 默认保留 **Prerelease-Alpha + 最近两个正式版**，共三个 Releases。上游 SHA、patch 或构建器改变时，重建保留的版本并覆盖同名 Release/tag。
- Release/tag 名称跟随上游；二进制版本和文件名附带构建指纹，让核心升级能发现同一个正式版下的 patch 更新。内置核心升级地址已指向本 fork。

## 工作方式

`automation` 的同步器获取上游 Meta、Alpha 和 tag 的精确提交，将功能 patch 和必要的 fork 适配提交到 `_build/*` 暂存分支，然后显式触发构建。

每个版本从自己的上游源码读取 `.github/workflows/build.yml`，完整保留 `build` job 的平台矩阵、工具链、测试和打包步骤。只替换版本生成、checkout 凭据与上传设置，发布部分由本仓库管理。Docker 发布、Android 仓库通知和其他上游触发器不会被带入。

正式版使用同名上游 tag 的源码。Alpha 跟随上游 Alpha 分支。Meta 与最新正式 tag 不同时单独构建验证，仅更新 Meta，不新增 Release。补丁应用或任意构建失败时，不发布该次构建；其他版本仍可独立成功。只有保留范围内的版本都有可用发布后，才清理超额的、由本自动化创建的 Releases，避免新版本失败时删除仍可用的旧版本。

发布时先上传新构建文件，之后更新 tag 和 `version.txt`，最后清理旧附件。`fork-build.json` 记录上游 SHA、构建指纹和实际源码提交；`checksums.txt` 校验本 fork 的附件。保留版本都会使用最新 patch，不提供固定版本不可变的承诺。

## 认证与设置

- Actions secret `FORK_DEPLOY_KEY`：仅本仓库可写的 SSH deploy key，用于提交生成分支、工作流和 tag。私钥不在 Git 中。
- Actions 自动提供的 `GITHUB_TOKEN`：触发构建和管理本仓库 Releases。无需个人 PAT、外部服务或 Docker Hub 凭据。
- 默认分支必须为 `automation`，启用 Actions，并关闭 immutable releases。不要为生成分支/tag 配置禁止机器人更新的规则。
- 同步器每 30 天向控制分支写入一次活动提交，避免长期无上游变化导致定时任务停用。定时运行仍可能因 GitHub 调度延迟而晚于计划时间。

## 本地检查

需要 Python 3.11+、Git、已登录的 GitHub CLI，以及 `PyYAML==6.0.2`。

```sh
python3 -m unittest discover -s tests -v
python3 scripts/fork.py sync --dry-run
```

dry run 会获取上游、在临时 worktree 应用 patch 并生成构建提交，但不推送、不发版、不清理远端。

覆盖附件和移动 tag 不是原子操作；已有下载链接或固定校验和可能失效。发布中断会在下一次运行重试，遇到补丁冲突需修正 `patches/` 后重跑。旧正式版无法应用最新补丁时会保留它的上一次成功产物。
