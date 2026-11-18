# 按需安装的配套技能

| 用在什么时候 | 选哪个 |
|---|---|
| 已经定位问题，要把证据交给后端、产品或测试 | [handoff-doc](handoff-doc/SKILL.md)：按接收人的下一步整理请求样例、待决项或复测步骤 |
| 准备打开业务页面复现或验收，需要确定工具和登录方式 | [browser-routing](browser-routing/SKILL.md)：按项目要求、会话和环境选浏览器；不包含浏览器客户端或登录脚本 |
| 修改采用本套约定的达梦项目表结构 | [sql-rule](sql-rule/SKILL.md)：核对 DDL 的字段单位、主键、索引、注释和审计列 |

前两项是需求与缺陷闭环可调用的配套能力；第三项只用于相应数据库项目。交接和 SQL 检查可以离线使用。需要生成产品 Excel 时准备本机电子表格工具；浏览器验收另需项目认可的浏览器连接及环境权限。

从**仓库根目录**在 PowerShell 运行以下示例；只装需要的名字。目标填当前宿主的技能根（例如 Claude Code 的 `~/.claude/skills`，或 Codex 的 `$CODEX_HOME/skills`）。

```powershell
$dest = Join-Path $HOME '.claude/skills'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
foreach ($name in 'handoff-doc', 'browser-routing', 'sql-rule') {
    $target = Join-Path $dest $name
    if (Test-Path -LiteralPath $target) { Write-Warning "已有安装，跳过：$target"; continue }
    Copy-Item -LiteralPath (Join-Path 'support-skills' $name) -Destination $target -Recurse
}
```
