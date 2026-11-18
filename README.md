# 一句话接需求，按项目流程交付

在 Claude Code 或 Codex 中说：

> 启用 SuperAgent 模式，修附件 Excel 第二个工作表里的 bug。做到可提交，不推送。

也可以贴一条或多条 TAPD 链接，并指定“只出方案”“仅改前端”等停止点。单独说“提 MR”“转测并写评论”时，可以直接用对应技能，不必启动 SuperAgent。

| 想做什么 | 从这里开始 |
|---|---|
| 接一条需求，或逐条定位、修复和复测缺陷 | [工作闭环](work-closeloop/README.md)：`req-closeloop` 和 `bug-closeloop` |
| 用一句话收单，把多个任务交给开发、复审和浏览器测试角色 | [Herdr SuperAgent](herdr-superagent/README.md)：对话入口、运行时和角色配置；另有可下载的 [HTML 使用指南](herdr-superagent/docs/herdr-workflow-guide.html) |
| 提交、推送、提 MR，或只做指定的交付步骤 | [ship](delivery-skills/ship/SKILL.md) 管交付顺序；[qe](delivery-skills/qe/SKILL.md) 调用质效平台，按原始工单判定新建或复用分支 |
| 构建、部署，核对线上跑的是哪次构建 | [jenkins-deploy](delivery-skills/jenkins-deploy/SKILL.md) 核对实例、任务、分支、构建结果与部署状态 |
| 评论、转测、补处理人或填工时 | [tapd-wrapup](delivery-skills/tapd-wrapup/SKILL.md) 按实际证据回写；工时挂任务，不挂需求或缺陷 |
| 真机验收、写交接、按项目规范审 SQL | [browser-routing](support-skills/browser-routing/SKILL.md) 选浏览器及登录方式；[handoff-doc](support-skills/handoff-doc/SKILL.md) 写给接手人；[sql-rule](support-skills/sql-rule/SKILL.md) 用于采用该约定的项目 |

**先读项目规则，再选流程。** 有适用的项目工作流就接续现有阶段；没有才用全局需求或缺陷闭环。涉及迭代时，分支归属由 QE 根据原单判定，建分支和 MR 只走 QE；需要写入的平台操作，先看本次预览再确认。

**安装时怎么选：** 做需求或修 bug，从[工作闭环](work-closeloop/README.md)开始；要一句话分派任务，再装 [Herdr](herdr-superagent/README.md)。团队交付按[交付技能安装说明](delivery-skills/README.md)选择公开技能或私有 CLI 随包安装，不重复覆盖本机定制。[辅助技能](support-skills/README.md)按需安装。

本仓库公开的是技能和可分发脚本。企业 `qe-cli` 与随包的四套交付技能放在[单独的私有仓库](https://github.com/cucumber-1128/qe-cli)，有权限的成员可从那里安装。TAPD 连接、项目映射、审批人和环境凭据留在各自的项目环境。
