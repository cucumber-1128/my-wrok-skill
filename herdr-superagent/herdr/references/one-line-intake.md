# 一句话接需求、修一批缺陷

“启用 superagent 模式做需求，〈链接一〉、〈链接二〉”与“启用 superagent 模式，修一下附件 Excel 里的 bug”都是完整的启动方式。用户只需给对象和这次要做的事；“只分析”“仅后端”“不要发版”等限制原样传下去。**当前接到消息的会话充当 Router**，不改变它自己的模型；实际由 Herdr 派出的会话才使用相应模型配置。

## 1. 接收这张清单

- 用本机 `scripts/sa-start.ps1` 处理用户的原文和附件本地路径。已在聊天中可读的附件由宿主作为文件传入，不让用户再抄路径、导出 JSON 或逐行复制。用户只说做需求用 `story`、修缺陷用 `bug`；明确同时交办两类用 `auto`。不要把模拟例句、引用资料或历史消息当成新工单。
- 单条、多条及文本＋工作簿走同一路径。按 workspace、对象类型、完整 ID 去重，保留表名/单元格或原文位置。`.xlsx` / `.xlsm` 只取这次范围内的链接，不执行公式或宏，不写回原表；旧二进制 `.xls` 请另存为 `.xlsx` 或粘贴链接。用户只点名某 sheet/行时只派范围内的项。缺附件、只有短号/列表页、类型不符或无法读取的部分，保留为待补项；不要猜 ID、放宽到整个迭代，或让一条坏链接挡住其余项。
- 默认 `delivery`；用户说“开发和复审互换”用 `delivery-swapped`。已启动的工作保持原路由。若用户只是问详情/进度，用预览和只读证据回答，不占一个执行会话。

**给宿主的实际入口（不是让用户运行）：** 用参数传值，不把用户消息或单元格内容拼进 shell 命令；PowerShell 用数组传多文件。只有附件时，消息中不必重复文件名：

```powershell
$start = "$HOME/.claude/skills/herdr/scripts/sa-start.ps1"
$batch = & $start -Text $userMessage -Kind bug -File $attachmentPaths | ConvertFrom-Json
# 需求消息使用 -Kind story；没有附件就省略 -File。指定范围时传 -Sheet、-FirstRow、-LastRow。
```

用返回的 `batch_id` 与 `items[].key` 处理；只读预览加 `-Preview`，不产生待办。`unresolved` 含无法确认的位置；零个可办对象、无链接又无具体描述时索要缺少的资料，不建一个空流程。有多个清楚的文字需求但没有 TAPD 链接时按用户原列项分别收件，不拆碎单个需求的验收清单。文字本地编号不冒充 TAPD 编号。

## 2. 读 TAPD，认对项目，再做事

1. 用**配置好的 TAPD MCP**按工作区、对象类型和完整 ID 读取条目（需求 `get_stories_or_tasks` 的 stories 类型；缺陷 `get_bug`）。ID 用原始字符串；同工作区的需求可批读，逐条核对返回的 ID 与 workspace。按需取评论、附件、原型和复现证据。没有权限、返回空或匹配不上，阻塞该项并说明原因，不从标题臆测。不要用浏览器读 TAPD。
2. 原需求/缺陷是独立交付项，同一批次不等于同一个项目。按返回的 workspace 与本机项目卡和映射核对，再读目标项目规则；不能把质效平台的 ID 当成 TAPD workspace，不能根据当前目录把另一项目的单派到这里。未知映射只问涉及的项。
   **领取后、分派前读取回执的 `iteration_policy_file`（[迭代与流程规则](iteration-policy.md)）**：从真实项目规则根选项目入口，缺少适用流程才按类型绑定全局闭环。若需要分支/MR，将本条原单、仓库交 QE CLI，不给 worker 下发“自己建分支”指令；不得从 Excel 中的分支名代替 QE 判断。不同仓的复用/缺失结果分别处理。
3. 新需求从项目**当前阶段**接续：若已有方案、任务表、工作树与结论，直接复用。对缺陷使用项目原缺陷流程，按确切清单取证、定责、修复和验证。只把独立工作派到空闲且已经核准的工作树；共享写目录、同一故障或相同环境数据的项串行。多个缺陷共用一个修复时，每个 ID 都给出其验证结果或阻塞。收件人做得完的小任务直接办。

项目卡没有本次 TAPD 工作区的映射时，只挂起对应条目；向用户确认规则根，再检查原工作流。工作簿里的链接是一批在线对象，不意味着可以离线修复或处理整个需求的所有缺陷。

复杂实施或需跨仓时，以本条 TAPD key 作为 Herdr Thread ID，用[项目委托](project-handoff.md)登记选定工作流、原任务、实际工作树和本轮终点，然后用 `scripts/sa-spawn.ps1` 指定该 Thread 和批次返回的 `-WorkflowProfile` 启动或接续 leader。没有项目流程，也在同一委托上绑定全局原件；不因此改跑一套 Herdr 通用业务流程。浏览器验收才加 `-Purpose browser`。当前对话收单不用另开 driver；确需长时间托管已启动的 worker 时由宿主按[后台驱动约定](super-agent.md)检查、复用或启动驱动。

## 3. 做到本轮终点

有写入、派工或占用工作树前先原子领取。宿主保存批次号和领取凭据；不把后者写到对外汇报、TAPD 或项目说明中。示例使用同一状态目录：

```powershell
$claim = & $start -Batch $batch.batch_id -Item $item.key -Claim | ConvertFrom-Json
if (-not $claim.claimed) { return }  # 单项处理函数内：引用原进度，本批继续其他项
# 需要独立 Thread 时，创建后立即登记对应关系（ThreadId 用本条 TAPD key），再派 worker。
# & $start -Batch $batch.batch_id -Item $item.key -Status CLAIMED -ClaimToken $claim.claim_token -ThreadId $item.key -Note '已绑定本条项目委托'
# 完成本次实质工作并打开证据后：
& $start -Batch $batch.batch_id -Item $item.key -Status HANDOFF -ClaimToken $claim.claim_token -Note $conclusion -Evidence $evidencePath
& $start -Batch $batch.batch_id   # 汇总当前批次，查询不是重新执行
```

同项已被别批认领：引用原处理，别再开一次。有历史结论先核对本次范围、实际版本和证据，不能直接把上次通过当成本次通过。确实受阻则用相同凭据回报 `BLOCKED` 和原因，再处理独立项。复核阻塞已解除才 `-Retry -Note ...`；若原负责人中断，先确认已停止、核对副作用，留存决定证据后用 `-TakeOver -PriorClaim ... -ConfirmedStopped -Note ... -Evidence ...` 显式接手。不要盲重派。

“做需求/修 bug”授权推进本地步骤至本次约定终点；不能代替产品确认、QE 对本次预览的确认，或顺带推送、提 MR、发版、评论和转测。一个有问题的项等待人，不打断其余可做项。显示**去重后总数、已交回/在做/待办/受阻及未识别输入**；逐项给出标题或 ID、项目、实际结果、证据/下一步。只入队、已启动、获得 ACK、已修复与已验证不能混为一谈。
