# 开发流水线

本页是 Herdr 的工程调度方法。**业务需求和缺陷不以这七步兜底**：先用项目已有的适用流程；确实没有，才按类型用全局需求或缺陷闭环。按[项目委托](project-handoff.md)接手原阶段，不重复排审查或自测。状态写入、ACK 和事件认领仍按[协议](protocol.md)。

leader 将需求整理为目标、工程/文件范围、验收清单和授权范围；能从工程与已有材料确定的事项自行处理，产品或权限决策集中交给 Router/用户。小改动可以直接交单兵角色，不必启动完整池。

1. 涉及迭代分支时按[迭代规则](iteration-policy.md)将原单交 QE 判定。已有批准的分支才可挂接到独立工作树，优先复用项目映射。主工作区、用户未提交内容和其他 Thread 保持独立。
2. 登记 Thread、保存简报。拆出的每个任务有 task_id、验收标准、独立工作区和代码版本；启动 worker 时传 `-ThreadId`、`-TaskId`、parent 与简报路径。
3. worker 先启动 ACK，仅 accepted=true 执行当前任务，false 不重复执行。开发实际跑编译和必要单测，记录执行数量/结果；代码有新提交时绑定新 revision/attempt，然后报告 DONE。
4. 复审按队列消费固定版本。正确性、安全、验收、明确回归、真实数据一致性问题可以阻断；风格、命名和可选优化不阻断。
5. APPROVED 后构建并运行/部署获授权的环境，核实运行版本，保存 deployment JSON，再报告 DEPLOYED。只编译成功不能代替部署或功能验收。
6. 测试按当前版本和验收操作，保存截图/请求响应/断言等真实证据，报告 PASS/FAIL。修复后更新 attempt 和受影响门禁。
7. leader 终审逐条核验，写交付报告，CLOSED。状态事务自动排入 Router 通知；driver 回收登记会话，原始证据保留。

消息用于提醒，阶段以 CLI 返回为准。leader 每次收到事件先 claim，再读当前 Thread/task；过期 attempt/spec/code 的结果不能推进。已处理消息不重复执行。

并发受全局 flow/agent/role 上限限制；同一 worktree（含子目录）一个 writer，review/test 按代码版本、账号、数据和浏览器会话隔离。空闲 worker 可以继续承担同流程下一任务，换任务时消息包含完整当前版本和简报。需要真正并行写入时准备多个已批准分支的独立 worktree，最后在集成 SHA 上验收。

复审一次至多列出五条最重要阻断项，建议最多五条。第一轮退回后增量复审；第二次仍拒绝则明确裁决并记录文件：误报可带裁决通过；真阻断修复后由 leader 核实再带裁决过门禁；无法解决则 BLOCKED/WAITING_HUMAN，不强制进入测试。

常驻 leader 处理完事件继续 `hc wait --children any`。允许结束回合的情况：流程终态、已写 WAITING_HUMAN/PAUSED/BLOCKED 或用户要求停止。人修改验收先更新 brief 与 spec_rev，再下发新版本；人叫停先写 PAUSED/CANCELED，再由 driver 中断/回收。

driver 运行在独立后台终端：

```powershell
hc run --bg --name flow-driver -- pwsh -NoProfile -File `
  "$HOME/.claude/skills/herdr/scripts/flow-watchdog.ps1"
```

所有组件使用同一默认状态目录，或明确传入同一 `-StateDir`。driver 每轮推进 pending start、outbox，检查登记会话和时间预算。一次观察失败不会停用目标；WAITING_HUMAN/PAUSED/BLOCKED 不因 idle 被唤醒；任意文本出现 CLOSED 不再作为结束条件。语义 watchdog 可选，普通巡检不需要额外模型回合。

权限默认保留交互确认。blocked 读屏幕后升级人，不代答权限、不用 --force；需要部署、发布、提交平台状态时，按既有项目技能和用户授权执行。
