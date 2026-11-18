# 一句话接需求，按项目流程交付

在 Claude Code 或 Codex 中说：

> 启用 SuperAgent 模式，修附件 Excel 第二个工作表里的 bug。做到可提交，不推送。

也可以贴一条或多条 TAPD 链接，并指定“只出方案”“仅改前端”等停止点。这里有两组可分别安装的技能：

| 想做什么 | 从这里开始 |
|---|---|
| 接一条需求，或逐条定位、修复和复测缺陷 | [工作闭环](work-closeloop/README.md)：`req-closeloop` 和 `bug-closeloop` |
| 用一句话收单，把多个任务交给开发、复审和浏览器测试角色 | [Herdr SuperAgent](herdr-superagent/README.md)：对话入口、运行时和角色配置；另有可下载的 [HTML 使用指南](herdr-superagent/docs/herdr-workflow-guide.html) |

**先读项目规则，再选流程。** 有适用的项目工作流就接续现有阶段；没有才用全局需求或缺陷闭环。涉及迭代时，分支归属由 QE 根据原单判定，建分支和 MR 只走 QE；需要写入的平台操作，先看本次预览再确认。

只用全局闭环，安装第一组即可。要启用一句话模式并保留全局兜底，两组都装。TAPD 连接、项目映射、测试账号和 `qe-cli` 均由使用者在自己的环境配置；本仓库不分发企业客户端或登录状态。
