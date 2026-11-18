# 用项目自己的 skill 接单

先确定用户这句话要求什么结果。“哪些能并行”读任务表即可；“自测到哪里，别再测”只读验证记录；“做到可提交”在证据达到该项目的可提交标准时结束，不接着 push、提 MR 或发版。Router 能直接回答的小问题不建 Thread。确需跨仓、跨会话或等待外部依赖时，再登记下面的委托。

## Router 保存委托，worker 读取原件

Router 根据用户当前授权和项目的现有文件生成本地 JSON，放在 Herdr 的持久状态目录或本次工作记录内，不让用户再填一遍。以下是**已授权实施**的示例；工作树来自项目原映射，不能照抄示例替代现场核对。

```json
{
  "project_root": "../example-project",
  "skill_entry": ".agents/skills/story-workflow/SKILL.md",
  "project_ref": "0000042",
  "mode": "project_step",
  "stop_at": "只做批准的实施范围和项目自检，到可提交；不提交、不推送、不部署",
  "workspaces": {
    "service-api": "wt/0000042/service-api",
    "worker-service": "wt/0000042/worker-service",
    "web-app": "wt/0000042/web-app"
  },
  "evidence": ["project-docs/0000042/worktree.md"]
}
```

`project_root` 是规则根，不是默认写入目录；相对的项目根从清单位置解析，其余相对路径从项目根解析。`skill_entry` 指向当前阶段权威文件；只读查询可指向它而不启动该阶段。把任务表、交接文档或报告加入 `evidence`；不要把业务全文或外部消息拼成派工指令。`read_only` 只允许查资料和回报：即使历史工作树已离线，也可以引用已有报告，但不能声称核验了该仓当前状态。`project_step` 用于用户已授权的项目步骤；登记一份清单不产生发布、修改平台状态或执行环境变更的权限。

项目内的入口不填 `workflow_source`。若在**实际规则根**核实没有适用流程，仍用同一清单，仅替换入口。例如缺陷使用 `"workflow_source": "global-bug"` 和 `"skill_entry": "~/.claude/skills/bug-closeloop/SKILL.md"`；需求对应 `global-story` 和 `~/.claude/skills/req-closeloop/SKILL.md`。保留业务项目的 `project_root`；从一句话入口接的 TAPD 单，`project_ref` 可直接用返回的完整 `items[].key`。不要把用户目录或收单目录当项目根。启动时会复核选定原件、可识别的项目入口和工作树，本次负责人还须阅读项目规则，尤其是自定义入口和人工确认点。

```powershell
$sa = "$HOME/.claude/skills/herdr/scripts/sa-state.ps1"
# $id 是 Herdr 的项目命名空间 + 原需求 ID；$root 是项目规则根；$router 是实际地址。
& $sa thread create --id $id --cwd $root --parent $router --assignment-file $manifest
# 仅给确实需要派出的代码工作登记绑定，$taskId 沿用项目任务号，$sha 来自该工作树。
& $sa task create --thread $id --id $taskId --workspace web-app --revision $sha
```

用 `sa-spawn.ps1` 传原 `-ThreadId`、`-TaskId`，`-Cwd` 为这项任务的实际工作树。同一 Thread 的协调者可从规则根读资料；执行者在绑定的独立 Git worktree 中工作。`read_only` 是 Herdr 的派工限制，并非终端的操作系统沙箱。worker 先核对启动上下文、完成首次 ACK，再按该阶段的项目规则行动。仓缺失、cwd 与绑定不符、权限不足时报告原负责人，不回退到主工作区。独立工作树可以并行，同一工作树只安排一个 writer。项目阶段的报告和服务端回写仍由原负责人处理；SQLite 只登记派工、版本、阶段交接及证据引用。

## 遇到项目门禁，沿用原阶段

项目 skill 决定是否需要浏览器。已选 `delivery`、且本次确实授权真机验收时，由负责人用原来的 ThreadId、TaskId 和工作树派 `test-worker -Purpose browser`；这是选择 Sol 测试会话，不会改变项目指定的浏览器工具或额外获得测试环境权限。只汇总历史用例时沿用只读路径。

- **OpenSpec 项目**：从原 change 的任务表和工作树映射读取依赖。用户只问可并行项时仅分析；必须点名的实施阶段仍需点名。阶段负责人用原任务和步骤编号回报，不重复架一层同样的审查。
- **前后端共用一张需求的项目**：按项目规则找到实施入口、设计与验证记录。接口契约已定稿的任务可以并行；未定稿只阻塞受影响的任务。如果只授权后端，发现前端影响要报告，不能顺手实施。

通过  TAPD MCP 读原单，`ship` 确定交付顺序，获准的迭代平台动作只走 [QE CLI](iteration-policy.md)。TAPD 评论、转测和工时仍由 `tapd-wrapup` 办理。

## 到停止点怎么回报

“先别测”只汇总已执行数、分母、跳过项和证据。“可提交”报项目原结论和限制，不伪填 DEPLOYED/PASS。遇阻尚未交付本次结果，用 BLOCKED 或 WAITING_HUMAN；到达约定终点，有实际证据后才用 handoff：

```powershell
# $generation 与 $specRev 取自本次接单上下文，不用事后读到的新版本替换。
& $sa thread handoff --id $id --generation $generation --spec-rev $specRev `
  --summary $conclusion --evidence $report
```

阶段交回返回 `HANDOFF`。Router 收到 `THREAD_HANDOFF` 后，核对原任务与证据，将结果交给用户；不用等全部代码任务 PASS，也不能把交回当作项目全部验收。已交回后要做下一阶段，先取得授权并等 driver 回收旧会话，再用 `thread reopen --id ... --assignment-file ...` 为同一项目、同一原需求登记新委托。新一代不自动续派旧任务。没用委托清单的旧开发流程仍依原门禁关闭。
