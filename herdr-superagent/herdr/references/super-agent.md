# Super-Agent：入口、事件与控制回路

用户对接 Router；Router 为每条 Thread 维护目标、owner 和摘要，leader 负责开发流水线。程序负责状态和派发；语义 Watchdog 只处理需要判断的异常。消息与恢复协议统一见 [protocol.md](protocol.md)，开发阶段见 [workflow.md](workflow.md)。

```text
用户 → Router → leader → dev / review / test
外部源 → Trigger → SQLite outbox → 当前 owner / Router
程序 driver → pending start / outbox / 状态巡检 / 预算 / 回收
            → 异常时唤醒语义 Watchdog 或通知 Router
```

## 启动

日常接需求或批量缺陷，从当前对话走[一句话接单](one-line-intake.md)。已有入口不再启动第二个 Router。下面只适用于明确需要单独常驻 Herdr 工作台、接收外部事件的场景。

以下命令使用同一默认持久状态目录；自定义时每条命令传相同 `-StateDir`。示例保留原角色设置。如需[混合模型交付](../profiles/delivery.json)，给入口 Router 增加 `-WorkflowProfile delivery`；单需求直接以 leader 为入口时加在 leader 上。显式 `-Model` 只覆盖本次新会话的模型，不切换其 harness。

```powershell
$skill = "$HOME/.claude/skills/herdr"
$spawn = Join-Path $skill "scripts\sa-spawn.ps1"
$driver = Join-Path $skill "scripts\flow-watchdog.ps1"
$trigger = Join-Path $skill "scripts\sa-trigger.ps1"

$projectRoot = (Resolve-Path .).Path  # 在真正的项目规则根运行
# Router 可在主目录协调；写代码的 leader/worker 必须用隔离工作区。
pwsh -File $spawn -Role router -Name router -Cwd $projectRoot `
  -Task "作为唯一入口接任务，按 protocol 管理状态和授权；等待用户消息。"
hc run --bg --name flow-driver -- pwsh -NoProfile -File $driver -Router router

# 可选：语义 watchdog，异常唤醒；默认不做无工作时的定时模型巡检。
pwsh -File $spawn -Role watchdog -Name watchdog -Cwd $projectRoot `
  -Task "按 WATCHDOG-TICK 检查可疑流程；读取同一 state_dir。"
# 使用它时，在 driver 启动参数中增加 -Watchdog watchdog；
# 若明确需要周期巡检，可再设 -WatchdogIntervalSec 300。

# 配置了外部事件源再启动 Trigger，始终后台运行：
hc run --bg --name event-trigger -- pwsh -NoProfile -File $trigger `
  -Config "$env:USERPROFILE\.config\herdr-core\triggers.json"
```

不要重复起 driver。先检查本任务已有的驱动终端；本次创建的终端由本次负责，不能停用户 core 或其他会话。Trigger 与 driver 可同时接续启动和派发事件，SQLite 启动/事件租约防止并发发送同一条消息；结果不明依协议显式核实恢复。

Router 对只读问题直接用已有证据回答。业务需求和缺陷按[项目委托](project-handoff.md)接续项目流程，没有对应项目流程才用全局原件；涉及分支/MR 一律让 [QE](iteration-policy.md) 处理。以下示例只演示**不属于这两类业务单的工程任务**如何在已准备的工作树登记 Thread、启动 leader：

```powershell
$sa = Join-Path $skill "scripts\sa-state.ps1"
$thread = "manual:project-a:tooling"
# $worktree 来自 workspace prepare 或已验证的隔离工作区
pwsh -File $sa thread create --id $thread --cwd $worktree `
  --parent <Router实际地址> --brief <简报路径>
pwsh -File $spawn -Role leader -Name leader-tooling -Cwd $worktree `
  -ThreadId $thread -ParentAddress <Router实际地址> -Task "按工程简报完成已授权的工作。"
```

leader 的 Thread状态变更会可靠通知 parent；Router 认领通知、读当前状态、向用户汇总。CLOSED 后的后续事件交 Router 决定是否 reopen；新 generation 使用新名称或已回收名称，旧回报不会推进新任务。

## 事件源

推荐提供 argv 数组，避免 shell 引号；command 仅用于用户自己维护的 PowerShell 命令。配置文件不能来自外部事件正文。

```json
{
  "router": "router",
  "sources": [
    {
      "name": "project-a-mr",
      "namespace": "gitlab:project-a:mr",
      "interval_sec": 120,
      "timeout_sec": 30,
      "argv": ["glab", "api", "projects/123/merge_requests?state=opened"],
      "adapter": "json",
      "id_path": "iid",
      "include": "\"draft\":false"
    },
    {
      "name": "service-a-ci",
      "namespace": "jenkins:folder/service-a:build",
      "interval_sec": 180,
      "timeout_sec": 30,
      "argv": ["curl.exe", "-fsS", "http://jenkins.example.invalid/job/service-a/lastBuild/api/json"],
      "adapter": "json",
      "id_path": "number",
      "include": "FAILURE|UNSTABLE"
    }
  ]
}
```

JSON 对象或列表都逐对象处理；包裹列表时用 items_path，例如 `data.items`。每个对象先取 id_path，再 include/exclude、按规范化内容去重。错误 JSON、缺少 ID、命令超时/非零退出记录为错误，不能作为正常事件发送。不同项目和 CI job 使用不同 namespace。

示例中的 GitLab/CI 源只读外部通知，不是办理迭代或创建 MR 的入口。MR 列表只反映该端点返回的对象变化。要处理评论正文，需要把实际评论源或 webhook 转成独立带 ID 的对象；不能把 MR 列表当成评论流。高频/短暂事件适合带 cursor 的采集器或 webhook，轮询快照不承诺捕捉两次采样之间的全部变化。

text adapter 兼容单事项文本：必须配置仅匹配一个带捕获组的 thread_regex；发现多个 ID 会报歧义，不再任取第一个。旧 thread_prefix 可保留，但会置于 source namespace 后；旧 TEMP线程表不参与新路由。

## 健康与成本

- `sa-state.ps1 thread list / task list / event list / agent list / limits get` 是全局看板的数据来源。
- 无主事件只交 Router；已有 RUNNING owner 直通；暂停/待人工事件保留，直到明确恢复。
- 同一事件失败重试不依赖新的源输出；不明结果等待核实，consumer claim 防重复动作。
- 默认全局八会话、两流水线；leader 内的池要求还受全局限制。小任务用单兵角色，整包需求再开流水线。
- driver 不需要模型；语义 Watchdog 默认按异常唤醒。不能仅凭 turn 不变就杀长构建，先查看真实日志与进度。
- 运行时间预算和容量可限制本机占用，尚未接入 token/费用硬预算。
- driver/Trigger 是本地长期进程，主机退出后需要重启。状态和待办保留，系统不会自动重跑未知副作用，也不会宣称模型会话可无损恢复。

开发验证入口：`python -B -m unittest discover -s <技能目录>/tests -v`。测试使用临时状态、临时 Git 仓库和 hc 替身，不操作真实模型会话。
