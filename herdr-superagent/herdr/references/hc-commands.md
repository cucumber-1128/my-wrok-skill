# hc 命令参考

`hc` = herdr-core CLI。窗口关了终端照跑。`<t>` 是地址：`t7`（本机）、名字（`hc run --name review` 起的）、或 `machine/t7`（远程）。管道输出默认 JSON（`run`/`exec`/`read`/`file read` 除外）。以 `hc --help` 和 `hc <命令> --help` 为准。

## 终端生命周期

| 命令 | 用法 |
|---|---|
| `hc run [命令行]` | 起程序并像 shell 一样等它（流式输出+退出码）。选项见下 |
| `hc run --bg ...` | 后台起、立刻返回（agent、服务器、多个 worker） |
| `hc ls [--all]` | 列存活终端（`--all` 含已结束） |
| `hc get <t>` | 看单个终端的观测状态（cwd/status/turn/foreground.cwd） |
| `hc attach [t]` | 把当前终端接管过去（`ctrl+\` 脱离） |
| `hc kill <t>` | 结束其程序 |
| `hc rm <t>` | 移除已结束终端 |
| `hc label <t> k=v...` | 设置标签 |
| `hc keep <t> [--off]` | 程序丢失时用 shell 原地恢复（`run --keep` 同效），永不重跑原任务 |
| `hc server start\|stop\|status\|restart` | 核心服务管理。**不要对用户的 core 执行 stop/restart** |

`hc run` 选项：
- `--name <>`：名字，1–32 位 `a-z 0-9 - _`，存活终端间唯一
- `--cwd <>`：工作目录（默认当前目录；`--on` 时默认远程 home）
- `--on <machine>`：远程起，地址 `machine/t7`
- `--label k=v`（可重复）、`--parent <>` / `--no-parent`（默认父=你）
- `--no-notify`：不往父终端打字通知
- `--outlive-parent`：父程序结束后仍存活（默认子随父亡）
- `--rm`：退出后自动移除（父拿到退出码之后）
- `--keep`：丢失时恢复 shell
- `--bg`：后台起立刻返回
- `--timeout <秒>`：`run` 自身超时（默认 180），超时打印 `[hc] still running as t3` 并以 124 退出，**程序继续跑，别重起，用返回地址去 wait/read**

`hc run` 带模板名与任务：`hc run reviewer "review src/x.rs"`——模板合并指令与任务作为首条消息。

## 对话与干预

| 命令 | 用法 |
|---|---|
| `hc send <t> <文本>` | 粘贴文本并回车。`--force` 在 blocked 时也打（**绕过权限，禁止**）；`--if idle\|working` 状态匹配才打；`--wait` 等这次发出的回合；`--timeout` |
| `hc keys <t> enter esc up ctrl+c f5 a ...` | 按具名键（处理信任对话框等：`hc keys <t> down enter`） |
| `hc interrupt <t>` | 按停止键、报告效果（`--timeout` 默认 10s）——停当前回合，不杀进程 |
| `hc wait <t>` | 等它停止工作（变 idle/done/blocked/error）或退出 |
| `hc wait <t> --output X` | 等本次 wait 开始后打印出 X（纯文本、剥转义），用于服务器就绪 |
| `hc wait --children all` | 等所有子 agent 都不 working |
| `hc wait --children any` | 等下一个子 agent 的新闻或发给你的人消息 |
| `hc put <t> <文件>` | 把本地文件送到该终端所在机器，打印落点 |

wait 的返回 `reason`：`message`（有消息给你，作为下一条输入，处理完可再 wait）、`timeout`（签到，接着 wait）、`not_started`（`send --wait` 尚未开始，先 read）。

## 无终端

| 命令 | 用法 |
|---|---|
| `hc exec [--on m] <argv...>` | 直接跑一条命令：输出原样、退出码原样。`--cwd`、`--env k=v`、`--timeout`（默认 60，上限 600）、`-i` 喂 stdin |
| `hc file read [--on m] <path> [--offset N --limit N]` | 远程/本地读文件 |
| `hc file write [--on m] <path>` | stdin 内容整体写（建父目录） |
| `hc file edit [--on m] <path> --old 'x' --new 'y'` | 精确替换（old 须唯一；可多对，全成或全不成；失败报 `edit_failed`） |

## 其他

| 命令 | 用途 |
|---|---|
| `hc templates` | 列模板（Markdown 在 `~/.config/herdr-core/templates/<name>.md`，Windows 为 `%APPDATA%\herdr-core\templates\`；frontmatter 有 name/description/command）。**本机角色文件不放这里**——放技能 `roles/`（hc 模板启动在 Windows 上缺 `sh` 用不了，统一用 `scripts/sa-spawn.ps1`） |
| `hc events` | 实时跟踪变化 |
| `hc machine add\|ls\|login\|update\|rename\|rm` | 机器管理（ssh / `--via '<cmd>'`）；`needs_login` 时 `hc machine login <name>` |
| `hc connect` | stdio 上的 API（`ssh box hc connect`） |
| `hc schema` | API 的 JSON Schema |
| `hc update [--version X\|--from 文件]` | 更新（终端保持运行） |
| `hc --skill` | 打印给 agent 的完整手册（本文件的来源） |

## 配置 `~/.config/herdr-core/config.toml`（Windows: `%APPDATA%\herdr-core\config.toml`）

- `notes = false`：hc 不主动打字进父终端（无父级注记、`send` 不加 `<hc>` 标签）
- `shell = ["nu"]`：覆盖新终端与恢复用 shell（每项一个参数）
- `draft_wait = 60`：人正在输入草稿时 send 最多等多久（秒），`0` 只等安静停顿
- `restore_screen = true`：恢复终端时先显示丢失程序的最后屏幕
- `wait_timeout`：wait 默认超时

## 环境与身份

- `$HC_TERMINAL_ID`：你所在终端的地址。子 agent 回报用 `hc send $HC_TERMINAL_ID "..."`。
- 你起的终端默认是你的子终端，**父程序结束它们也结束**（`--outlive-parent` 例外）。Codex 必须 `--no-daemon` 运行。
- 错误形状 `{error: {kind, message, hint}}` 在 stderr；退出码：`3` not_found、`7` needs_target、`9` blocked、`10` status_mismatch、`11` finished。
