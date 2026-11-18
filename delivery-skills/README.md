# 从提交到转测，按需装四个 skill

“帮我看看这批 bug 哪些能转测”只需要 TAPD 查询能力，不需要先装 Jenkins。“这两单提交并推送，先别提 MR”走交付和 QE 分支规则，不会触发部署或转测。

| 入口 | 拿来做什么 | 真正执行时需要 |
|---|---|---|
| [ship](ship/SKILL.md) | 组织提交、推送、MR、审查、打包、交付，守住用户指定的终点 | 项目的 Git 工作树；迭代推送/MR 需要 QE |
| [tapd-wrapup](tapd-wrapup/SKILL.md) | 评论含真实证据、逐单转测、保留处理人、建子任务、核算工时 | TAPD MCP；迭代建任务需 QE，图片上传器另需 Python 3 |
| [jenkins-deploy](jenkins-deploy/SKILL.md) | 找准实例/job/分支，通过 QE 发布并核对实际构建和部署 | QE 安装包的 Jenkins 驱动、Python 3、本机授权实例 |
| [qe](qe/SKILL.md) | 将原始需求或缺陷交给 QE 判断分支归属，让平台办理 MR 等迭代动作 | 单独获授权并安装的企业 `qe` CLI |

## 安装

**已有企业 CLI：** 优先按其安装说明用 `qe setup` 安装四个 skill，以该版本的 `qe guide` 为准。Jenkins 驱动由它装在共享技能目录，真实实例配置只保存在本机。已有技能不必再复制；升级先对比当前版本并保留自己的项目规则，不用强制覆盖。

**只装公开版：** 从本目录按需复制整个技能目录（含其 `references/`、`scripts/` 等原有文件）到当前宿主的技能目录。例如从本目录给 Claude Code 安装 TAPD 收尾：

```powershell
$dest = Join-Path $HOME '.claude/skills'
$skill = 'tapd-wrapup'
if (Test-Path -LiteralPath (Join-Path $dest $skill)) { throw "技能已存在，请先比较并合并，勿覆盖已有安装" }
New-Item -ItemType Directory -Path $dest -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PWD $skill) -Destination (Join-Path $dest $skill) -Recurse
```

其他三个 skill 替换 `$skill` 即可。Codex 的宿主目录通常为 `$CODEX_HOME/skills` 或 `~/.codex/skills`，按实际安装选择。若后来改用 `qe setup`，先检查现有目录是否已链接到它的共享目录 `~/.agents/skills`；同名独立副本先备份、对比合并，再选一个入口，避免宿主继续加载旧副本。不要把已有 Jenkins `config.json` 或私有项目表拷进本仓库。

只复制本目录不会安装 QE CLI、Jenkins 底层驱动或 TAPD 登录态。没有企业连接时仍可用 `ship` 组织本地提交，用模板起草评论；涉及平台写入时等依赖齐全再办。
