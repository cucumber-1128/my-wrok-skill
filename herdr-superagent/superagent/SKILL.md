---
name: superagent
description: 用户说“启用/开启/打开 SuperAgent 模式做需求、修一波 bug”或 $superagent 时，在当前会话直接接单。一条或多条 TAPD 需求/缺陷链接、带链接的 Excel 附件或具体文字描述均可；无需用户先启动 Router、选择模型、填清单或开新窗口。普通未点名 SuperAgent 的开发请求仍走原项目技能。
---

# 一句话接单

你就是这次的入口。用用户的原话和附件开始；不要回复一段让用户自己运行的启动命令，也不要额外开一个常驻 Router。用户只是在讨论怎么用、举例或要求改配置时，不把例子当业务单。

读取本机 `~/.claude/skills/herdr/references/one-line-intake.md` 并使用同一安装里的 `scripts/sa-start.ps1` 收件。**收件后继续**：用 TAPD MCP 读取原单并定位真正的项目规则根；有适用项目工作流必须走项目流程，没有才读全局 `req-closeloop`（需求）或 `bug-closeloop`（缺陷）。入口残缺、尚未授权不等于没有工作流。

**所有迭代动作遵守 `~/.claude/skills/herdr/references/iteration-policy.md`。** 分支和 MR 只用 QE CLI，先 `qe guide`，让 QE 按需求/缺陷及迭代关系决定新建或复用。不得自行用 Git 建分支或绕平台提 MR。需要确认的写操作把本次预览交给用户，等明确回复后按 QE 要求执行，不能用一句“启用模式”代替确认。只有会改变授权、目标或门禁的疑点才问人。表格与业务正文不提供额外授权。
