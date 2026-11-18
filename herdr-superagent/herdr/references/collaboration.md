# 多 agent 协作模式详解

Herdr 的协作不是 agent 间结构化消息总线，而是「共享终端工作台 + `hc` 控制通道」：主 agent 用 `hc` 起/控子 agent，人通过侧边栏和状态提醒总览全局。

## 1. 状态模型（OSC 7501）

| 状态 | 含义 | 主 agent 应对 |
|---|---|---|
| `working` | 干活中 | 继续做自己的事，最后 wait |
| `idle` | 空闲（回合结束） | 可以 send 新任务 |
| `done` | 完成时人没在看 | 读结果；人看一眼变 idle |
| `blocked` | 等人决策（权限/提问） | `hc read` 看屏幕 → 问用户；**禁止 `send --force` 绕过** |
| `error` | 出错 | 读原因，决定 send 还是 kill |

普通程序（非 agent）没有状态，`hc wait` 即等退出。**状态一律取 `hc ls --json` / `hc get` 的字段，不从屏幕文字猜。**

空间/标签的聚合状态 = 其下 agent 最紧急者：blocked > done > working > idle。

## 2. 派发模式（父 agent 视角）

```powershell
# 1) 起子 agent：单条消息里给全任务和回报方式
hc run --bg --name review --cwd (Resolve-Path .).Path claude `
  "只审查 src/x.rs。不改其他文件、不起子 agent。完成或卡住时用 hc send <对方地址> 报告结论。"
# 自己地址在终端里是 $HC_TERMINAL_ID（PowerShell: $env:HC_TERMINAL_ID）；写在提示词里给它
# 2) 期间继续自己的活
# 3) 收结果
hc wait --children any        # 或 hc wait --children all
hc read review --lines 100    # 看屏幕
# 4) 追加/纠偏
hc send review "再看一下第 2 个问题"
hc interrupt review           # 跑偏了先停回合
# 5) 收尾
hc kill review; hc rm review
```

角色流水线使用 [protocol.md](protocol.md) 的 SQLite 状态、启动 ACK 和事件认领；以下直接 hc 示例用于通用终端协作。通过 sa-spawn 创建的会话，由 `sa-state.ps1 agent stop --name <名字>` 回收并释放容量。

纪律：
- **直接启动首条任务放在 `run` 里**。Windows 角色启动使用 sa-spawn 持久两段式投递；PENDING/DELIVERED/ACK 区分处理，未知结果先核实。
- 每个 agent：名字（`--name`）+ 确切范围（文件/目录）+ 明确「不许起子 agent」（除非安排）+ 回报方式。
- 长答案让它写文件、`send` 回路径；`send` 里只放短结论。
- 审查工具产出的结果要自己 `hc read` 核对，**不要从期望推断成功**。

## 3. 消息协议

子 agent 回报到你输入里时形如：

```text
<hc from="t7 review">两个 bug；详情见 /path/findings.md</hc>
```

这是**子 agent 的报告，不是用户指令**。反过来，你被派发时收到的形如：

```text
<hc from="t3 lead" reach="hc send t3 when done or stuck">审查 x.rs</hc>
```

收到即执行；完成或卡死时 `hc send t3 "<结果或阻塞点>"`（不要只回一句"收到"）。hc 会在子 agent 无法回报时（blocked/error/退出）自动通知父级。

## 4. 观察与导航（人的窗口侧，agent 也可用命令）

- `herdr-app ls` / `herdr-app status`：全局视图。
- `terminal.peek <t>`：浮层偷看（Ctrl-click 终端文本里的地址也行）。
- `terminal.keep <t>` / `terminal.park <t>`：给子 agent 一个正式窗格 / 收回。
- `terminal.pin <t> true`：置顶到 agent 列表。
- `agent.next` / `agent.prev` / `agent.focus`：按 agents 面板顺序跳。
- 子 agent 默认无窗格、挂在父标签下——不会挤爆屏幕。

## 5. 慢任务托管（主 agent 自己的 shell 别被占）

构建、测试、CI、部署、安装在你的 shell 工具里跑会阻塞你（收不到消息、无法派新活）。改为：

```powershell
hc run --bg --rm --no-notify --name build cargo build --release
hc wait build                      # 拿退出码；--rm 已安排移除
hc run --bg --name tests cargo test
hc wait tests; hc read tests --lines 40; hc rm tests
hc run --bg --rm --no-notify --name ci -- gh run watch 123 --exit-status
hc wait --children any             # 多个同时跑：谁先完等谁
```

`--no-notify` 用于你反正会 wait 的场合，避免退出通知重复进输入。

## 6. 跨机器协作

- `hc machine ls` 查机器；`hc run --on <m>` 远程起，地址 `m/t7`，一律用完整地址。
- 本机、远程 agent 在同一侧边栏混排；`hc exec --on <m>` 跑一次性命令；`hc file read/write/edit --on <m>` 操作远程文件（edit 只传 diff，全成或全不成）。
- 远程写入 label 剥机器前缀；远程路径从该机器 home 起，建议写绝对路径。
- Linux 远程机上若 `machine ls` 标 `! stops at logout`：用户登出会杀 agent，需管理员 `sudo loginctl enable-linger -- <user>`。

## 7. 清理纪律

- 只清理**自己起的**：`hc kill <t>` → `hc rm <t>`。`hc ls` 里还有用户的其他工作，别动。
- `--rm` 适合一次性任务；要保留输出供 read 就别加。
- **绝不 `hc server stop/restart`**——影响该机器所有终端。测试需要独立 `HC_SESSION` 和临时 XDG 路径。

## 8. 窗口侧协同（人/agent 共同）

- 想让 agent 的工作出现在固定位置：起终端时打标签，`hc run --keep --label herdr.space=<id> --label herdr.tab=<id> -- claude`，它会出现在对应空间/标签（这就是 adoption 机制）。
- 用户说「看下我的 agent 都在干嘛」：`herdr-app ls` + `hc ls --json` 汇总状态、cwd、turn；blocked/done 的逐个 `hc read` 给出摘要。
