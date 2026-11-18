# 业务工作闭环

项目没有自己的开发流程时，用这两份 skill 接原单。它们可以独立工作，也可以由 Herdr SuperAgent 作为全局兜底：有适用项目流程就接续原阶段，不另造一套审批。

| 你发来的话 | 实际结果 |
|---|---|
| “这个需求先出方案，暂时别开发。” | [req-closeloop](req-closeloop/SKILL.md) 读原单、图片和评论，给出可观察的验收项与待确认问题，停在方案。 |
| “我已经做完，帮我验一下。” | 从真实变更反推改动点，逐项对照需求，分开列出漏做、范围外和未测项。 |
| “处理这张表里的 bug，只看第二个 sheet。” | [bug-closeloop](bug-closeloop/SKILL.md) 只收指定范围，逐单取证；前端修并实测，后端问题交接到接口和字段。 |

## 安装两个目录

将本目录的 `req-closeloop/`、`bug-closeloop/` 分别复制到用户的 `~/.claude/skills/`，保留内部的 `references/`。在 Windows PowerShell 可从**仓库根目录**运行：

```powershell
$dest = Join-Path $HOME '.claude/skills'
New-Item -ItemType Directory -Path $dest -Force | Out-Null
foreach ($name in 'req-closeloop', 'bug-closeloop') {
  $to = Join-Path $dest $name
  if (Test-Path -LiteralPath $to) { Write-Warning "已存在，未覆盖：$to"; continue }
  Copy-Item -LiteralPath (Join-Path 'work-closeloop' $name) -Destination $to -Recurse
}
```

如果使用 Herdr，这是它查找两份全局闭环的位置。若只用其他支持 `SKILL.md` 的宿主，也可复制到该宿主配置的技能根。不同宿主的工具命名空间可能不同，以实际连接为准。

## 接自己的项目，不上传项目资料

- 有项目卡、工作树映射或会话 hook 的，沿用已有配置。否则可设置环境变量 `WORK_SKILLS_LIBRARY` 指向**业务仓库之外**的私有资料库。其 `映射.json` 示例为 `{"projects":{"示例项目":["/work/example-project"]}}`；将示例路径换成本机真实规则根，在 `示例项目/项目卡.md` 记录分支约定、验证环境及登录渠道。只有要用到的资料才需要准备。
- **TAPD MCP** 负责读取原单及图片；浏览器只用于业务页面复现与验收，遵循项目规定或本机 `browser-routing`。缺少真实数据或权限时明确标「未验证」。
- **迭代操作只走 QE CLI**，先读本机 `qe guide`。分支与 MR 都把原单交给 QE，不自行建分支；写操作按当前 CLI 预览、确认。交付次序使用 `ship`，TAPD 评论、流转及工时使用 `tapd-wrapup`；需要时由项目的工具安装包另行配置。只读分析不用先安装所有交付工具。
- 给后端的交接可以配合 `handoff-doc`，字段要求在缺陷 skill 内已列明。两套 skill 不内置环境账号、私有端点或生产配置；这些只在各自的项目环境中维护。
