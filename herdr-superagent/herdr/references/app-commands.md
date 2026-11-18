# herdr-app 命令参考

`herdr-app do <命令> [--参数 值 ...]` 改变窗口结构；`herdr-app commands` 打印实时全表（唯一权威）。目标 id 一律字符串。必填参数缺失会报错（不会弹对话框）。

## 读取与调试

| 命令 | 用途 |
|---|---|
| `herdr-app ls` | space/tab/terminal 树（子 agent 挂在父下） |
| `herdr-app status` | 屏幕现状：布局、焦点、弹层、zoom、文件与 mod 错误 |
| `herdr-app commands` | 全部命令+参数+说明（无需开窗口） |
| `herdr-app do ...` | 执行命令；打印 `{space,tab,terminal}` 或 `{pane,terminal}`，失败 `{error}` 退 1 |
| `herdr-app shot [file.png]` | 截当前窗口为 PNG，打印绝对路径 |
| `herdr-app check <mod目录>` | 加载并渲染一次 mod，报错退出非 0 |
| `herdr-app --skill` | 驱动窗口的完整手册 |

## Space

| 命令 | 参数 |
|---|---|
| `space.new` | `[name] [folder] [tab] [argv] [task] [machine] [focus]` |
| `space.close` | `[id] [confirmed]` |
| `space.rename` | `[id] [name]`（空名=回到按所在目录命名） |
| `space.move` | `<id> <index>`（1=第一） |
| `space.select` | `[index] [id]` |
| `space.next` / `space.prev` | — |

要点：`machine` 取 `hc machine ls` 的机器；`argv`+`task` 直接在新空间起 agent 并给首条消息；`focus:false` 保持当前显示不跳过去。

## Tab

| 命令 | 参数 |
|---|---|
| `tab.new` | `[name] [space] [folder] [argv] [task] [mod] [props] [focus]` |
| `tab.close` | `[id] [confirmed]` |
| `tab.rename` | `[id] [name]` |
| `tab.move` | `[id] [index] [by]`（by 为负数向左、正数向右，环绕） |
| `tab.select` | `[index] [id]` |
| `tab.next` / `tab.prev` | 跳过 settled 标签 |
| `tab.settle` / `tab.unsettle` | `[id]` 收起但仍运行（mod 可隐藏 settled） |

`tab.new` 用 `mod`+`props` 时创建「只含一个 mod 面板」的标签（看文件、看板类）。

## Pane

| 命令 | 参数 |
|---|---|
| `pane.split` | `<direction: right\|down> [pane] [argv] [mod] [props]` |
| `pane.close` | `[pane]`（连带结束终端） |
| `pane.zoom` | `[pane]`（再执行取消；同时把该窗格设为标签焦点） |
| `pane.focus` | `[pane] [terminal]` |
| `pane.go` | `<direction>`（优先最近聚焦的窗格） |
| `pane.swap` | `[direction] [pane] [target]`（direction 与 target 二选一；换位置不换焦点） |

## Terminal（终端与剪贴板）

| 命令 | 参数 |
|---|---|
| `terminal.open` | `[argv] [folder] [place] [name]` |
| `terminal.kill` | `<terminal>` |
| `terminal.keep` | `<terminal> [place]`（给无窗格终端一个窗格/恢复） |
| `terminal.park` | `<terminal>`（移除窗格并关 keep） |
| `terminal.peek` | `<terminal>`（浮层偷看，可转为标签或关闭） |
| `terminal.show` | `[terminal] [pane]` |
| `terminal.pin` | `<terminal> <pinned>`（置顶到 agent 列表，读作 `agents()` 的 `pinned`） |
| `terminal.search` | 聚焦终端内查找条（屏幕+回滚，`ctrl-shift-f`） |
| `terminal.select` / `select_all` / `adjust_selection` / `clear_selection` / `copy` / `paste` / `paste_image` | 选区与剪贴板 |
| `terminal.scroll` | `[lines] [pages] [to: top\|bottom] [terminal]`（滚历史视图，负值向上；备用屏无历史） |
| `terminal.right_click` | `<to: program\|menu> [terminal]` |

## Agent

| 命令 | 用途 |
|---|---|
| `agent.next` / `agent.prev` | 按当前 agents 面板的 `setAgentOrder` 顺序跳 |
| `agent.focus` | `<terminal>` 聚焦某 agent |

## Layout

| 命令 | 参数 |
|---|---|
| `layout.switch` | `[name]`（默认键 `ctrl-shift-l` 轮换） |
| `layout.toggle` / `layout.show` / `layout.hide` | `<id>` |
| `layout.resize` | `<id> [px] [weight]` |
| `layout.select` | `<stack> <child>` |
| `layout.set` | `<layout>`（个人树编辑；官方布局只允许改 click/look 字段） |
| `layout.clone` | `<name> [as]`（默认 `<name>-copy`，克隆后自动切换） |
| `layout.reset` | `[name]`（重置官方布局与点击状态） |

## Mod / App / Window

| 命令 | 用途 |
|---|---|
| `mod.clone` | `<name> [as]` 复制 mod 再改 |
| `mod.open` | `<name>`（再执行关闭） |
| `overlay.close` | `[id]` |
| `app.help` / `app.welcome` / `app.palette` / `app.settings [section]` / `app.whats_new` / `app.feedback` | 应用面板（settings 分区：look/machines/alerts/updates/privacy） |
| `app.theme <name>` / `app.zoom_in` / `app.zoom_out` / `app.zoom_reset` | 外观 |
| `app.check_updates` / `app.update_core` / `app.update` / `app.reset` | 更新与重置 |
| `window.minimize` / `window.maximize` / `window.close` | 窗口 |
| `app.quit` / `app.hide` / `app.hide_others` / `app.show_all` | macOS |

## 命令行示例

```powershell
herdr-app do tab.new --name "fix login" --argv '["claude"]' --task "look at the login bug"
herdr-app do pane.split:right                      # 冒号=填第一个参数
herdr-app do space.new --name work --folder (Resolve-Path .).Path --machine vps
herdr-app do space.close --id s3 --confirmed true  # 注意：用 --id，不是 --space
```

未知参数名报错并列出合法名，**不会回退到当前目标**。别用错的字段名猜。
