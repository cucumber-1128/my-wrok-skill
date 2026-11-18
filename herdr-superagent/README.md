# Herdr SuperAgent：一句话收单，按项目工作流交回

在已装好技能的 Claude Code 或 Codex 里发送：

> 启用 SuperAgent 模式，修一波 bug，链接在附件 Excel 的「待修」表里。做到可提交，不推送。

也可以直接贴一条或多条 TAPD 需求/缺陷链接。当前对话负责接单和汇总；项目有自己的工作流就从原阶段接续，没有才使用全局需求或缺陷闭环。建分支、提 MR 只走 QE CLI，是否复用关联需求分支由 QE 判定。需要确认的写操作先展示本次预览，等待用户回复。

📖 [打开离线 HTML 使用说明](docs/herdr-workflow-guide.html)（GitHub 页面可下载后用浏览器打开）

## 首次安装

本目录提供两个技能：`herdr/` 是运行时，`superagent/` 是对话入口。以下为 **Windows PowerShell 7** 首次安装示例，从本目录运行，遇到已存在的目标会停止；升级已有安装时先对比内容并备份，不覆盖本机定制。

```powershell
$ErrorActionPreference = 'Stop'
$homeSkills = Join-Path $HOME '.claude/skills'
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
$codexSkills = Join-Path $codexHome 'skills'
$runtime = Join-Path $homeSkills 'herdr'
$entry = Join-Path $homeSkills 'superagent'
$codexEntry = Join-Path $codexSkills 'superagent'
foreach ($path in $runtime, $entry, $codexEntry) {
    if (Test-Path -LiteralPath $path) { throw "已有安装，请先对比：$path" }
}
New-Item -ItemType Directory -Force -Path $homeSkills, $codexSkills | Out-Null
Copy-Item -LiteralPath ./herdr -Destination $runtime -Recurse
Copy-Item -LiteralPath ./superagent -Destination $entry -Recurse
New-Item -ItemType Junction -Path $codexEntry -Target $entry | Out-Null
```

如只用 Claude Code，安装 `herdr`、`superagent` 到 `~/.claude/skills/` 即可，不需要 Codex 的入口。Codex 发现技能的位置以当前宿主为准；如果使用别的技能根，把上例的 `$codexSkills` 替换为实际目录。安装后在能发现新技能的会话中使用。

### 哪一步需要什么

- **先试收件**：需要 PowerShell 7 和 Python 3.10+（标准库，无需 pip）。下面只预览，不建待办、不连 TAPD，也不启动模型：

  ```powershell
  $start = "$HOME/.claude/skills/herdr/scripts/sa-start.ps1"
  & $start -Text '启用 SuperAgent 模式，修复导出后金额列为空的问题。' -Kind bug -Preview
  ```

- **办真实单**：在宿主配置有权限的 TAPD MCP、可用的 QE CLI 和项目规则根。全局闭环原件的路径为 `~/.claude/skills/req-closeloop/SKILL.md` 和 `~/.claude/skills/bug-closeloop/SKILL.md`。如果已有私有项目资料库，`WORK_SKILLS_LIBRARY` 可指向资料库根，Herdr 会识别其 `映射.json` 中 `projects` 各项列出的项目根；相对路径基于该文件所在目录。以项目卡/规则核对 TAPD 工作区，不把这份目录映射当成工单或授权。自定义阶段入口请写入本项目 `AGENTS.md` 或 `CLAUDE.md`；不要提交真实映射、工单或账户数据。
- **派出独立角色**：安装 Git、Herdr 的 `hc`，以及已配置的 Claude Code / Codex 客户端；Codex 路径使用支持 `--no-daemon --model` 的兼容客户端。图形工作台操作另外需要 `herdr-app`。仓库不分发这些程序或登录凭据。
- **匹配模型**：随附的 [delivery.json](herdr/profiles/delivery.json) 是一套具体的 Magpie 路由示例。模型 ID 必须能被对应客户端解析；使用其他提供方时，把**已安装副本**中的模型 ID 换成本机可用别名。开发与复审可互换；浏览器测试才选 `-Purpose browser`。测试环境及浏览器工具仍由项目提供。

## 机制与自测

- [一句话收件与批次恢复](herdr/references/one-line-intake.md) · [QE 约束](herdr/references/iteration-policy.md) · [项目委托和停止点](herdr/references/project-handoff.md)
- [启动、ACK、事件、版本和恢复协议](herdr/references/protocol.md) · [可选的后台驱动](herdr/references/super-agent.md)

从本目录运行 `python -B -m unittest discover -s herdr/tests -v`。用例使用临时 SQLite、Git 仓库、合成编号和替身终端；不操作真实业务单或模型账户。
