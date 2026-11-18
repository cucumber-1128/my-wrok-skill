# Herdr 定制：布局 / mod / 键位 / 主题

配置根：`~/.config/herdr-app/`（Windows: `$HOME/.config/herdr-app/`）。所有文件**实时生效**；无效时保留上一个好版本并写 `<name>.error`（`layout.error`、`keys.error`、`settings.error`、`programs.error`）。`AGENTS.md`、`gpui-kit.d.ts`、`herdr.d.ts` 由应用每次启动重写，**不要编辑**。

| 文件 | 管什么 |
|---|---|
| `layouts/<name>.json` | 一整个窗口：节点树、主题、字体、背景、窗格框 |
| `keys.json` | 键位 |
| `settings.json` | 人：zoom、sounds、字体、worktree 目录、clipboard 等 |
| `mods/<name>/` | 面板（JS） |
| `programs.json` | 程序识别表（名字/图标/色块，内置 124 项） |
| `themes/<name>.json` | 终端主题 |
| `frame/`、`icons/`、`view.json`、`shared.json` 等 | 应用写入，不要手改 |

## 纪律

- **官方布局/mod 只读**，每次启动被覆盖。改前克隆：
  `herdr-app do layout.clone '{"name":"main","as":"my-layout"}'`、`herdr-app do mod.clone '{"name":"files","as":"my-files"}'`。
- 只调外观先走 `settings.json`（主题、字体、颜色、zoom），别克隆。
- mod 写完必须 `herdr-app check <mod目录>` 通过；UI 调试用 `herdr-app shot` 截图。
- 兜底：`ctrl-alt-shift-escape` 永远恢复默认 `main` 布局，不可重绑定。

## 布局：`layouts/<name>.json`

节点类型：
- `block`：内容 `{"mod": "<文件夹>", "props": {...}}` | `{"terminal": "t7"}` | `{"tab": "active"}` | `{"agent": "focused"}`
- `row` / `column`：横排/竖排子节点
- `stack`：叠放，`"active": "<子id>"` + 可选 `"header"`（`"tabs"` 或一个 mod）

通用属性：`id`、`size: {px}` 或 `{weight}`、`min`/`max`、`hidden`、`background`（颜色/渐变/图片）、`border`、`radius`、`shadow`、`padding`、`gap`、`divider`、`resizable`、`movable`（拖拽窗格，可继承）、`floating`（浮动块/抽屉 `peek`）、`window_drag`。

仅 root：`"titlebar": "none"`（自绘标题栏）、`"window_blur": true`（毛玻璃）、`"preview": "layouts/x.svg"`（设置页预览图）、`"terminal_menu": "<mod>"`（右键菜单 mod）、`"toast": "<mod>"`。

外观（任意节点，就近覆盖；目前只有 root 生效）：`theme`（`graphite`/`ink`/`moss`/`paper`/`{light,dark}`）、`colors`（按主题色名覆盖）、`font`/`font_size`/`ui_font`、`font_weight`、`terminal_line_height`、`terminal_cell_width`、`terminal_scroll_speed`、`terminal_opacity`、`terminal_padding`。

窗格框（tab 块上的 `pane`）：`{ "gap": 6, "radius": 6, "border": {...}, "focus": {...}, "header": { "content": { "mod": "pane" }, "height": 30, "position": "top\|bottom", "reveal": "always\|hover" }, "lone": false }`。

自带布局：`main`（默认）、`terminals`、`win98`。切换键 `ctrl-shift-l`。

## Mod：`mods/<name>/main.js` + `mod.json`

`mod.json`：`{ "place": "right\|left\|top\|bottom\|none", "size": 320, "focusable": true?, "overlay": {width,height}? }`。`place:"none"` = 只由布局/弹层放置。

语言 = **gpui-kit**：JS 描述 GPUI 元素（`div()`、`h_flex()`、`v_flex()`，链式 snake_case），Rust 原生 GPU 绘制。**没有 DOM/HTML/CSS/WebView/Node API/setTimeout**。类型真相：同目录 `gpui-kit.d.ts`、`herdr.d.ts`。

```js
import { View, div } from "gpui-kit";
import { exec, spaces, theme, run } from "herdr";

export default class Branches extends View {
  init(_props, cx) {
    cx.timer.every(3000, (cx) => this.refresh(cx));   // 只用于数据，展示动画别用定时器
  }
  render(cx) { /* 每次 cx.notify() 或 herdr 数据变化时重跑 */ }
}
```

`herdr` 模块要点：
- 实时数据：`spaces()` / `agents()` / `terminals()` / `panes()` / `focused()` / `theme()` / `settings()` / `keys()` / `commands()` / `layouts()` / `machines()`——数据变自动重渲染，**不要轮询这些**。
- `run(命令, 参数)`：与键位/菜单同一套命令表。
- `openOverlay({mod, props, kind: "dialog"|"popup"|"window", width, height, at, frame, key, keep})` + `closeOverlay(id)`。
- `notify({title, body, level, icon, timeout, action})`、`playSound("done"|"blocked"|file)`。
- `exec(cmd, args, {cwd, on, timeout})`（用户真实环境；`on` 跑远程）、`hc(args)`、`readTextFile`/`writeTextFile`/`watchFolder`、`pickFolder`。
- `shared(key)` / `setShared(key, value)`（所有 mod 可见）、`nextCommand()`（接收键位命令）、`props()`。
- `setWindowTitle(title)`、`setBadge(text)`。

规则硬点：
- **绝不保存 `cx` 之后再用**——每个回调给自己的：`on_click((e, cx) => …)`、`cx.spawn(async (cx) => …)`、`cx.timer.every(ms, (cx) => …)`。
- 无顶层 `await`；慢操作走 `exec`/`fetch`；模块顶层别放副作用（热重载会共享模块代码）。
- 动画用原生 `.frames([...], 90)`、`svg(x).spin(900)`、`.loop(prop, {from, to, period})`、`.transition()`，不要 `cx.timer` + `cx.notify()` 刷帧。
- 虚拟列表容器要有确定高度（`flex_1()` 链上没有 `h(n)` 会画成 0 高）。
- `herdr-app check` 通过才算完成；`checking()` 为 true 时跳过辅助逻辑/写文件。
- 清理用可选 `async close(cx)`（≤250ms，退出时只保证 `exec` 和 `run("terminal.kill")` 发得出去）。

## 键位 `keys.json`

```json
{
  "global":   { "ctrl-shift-t": "tab.new", "ctrl-shift-o": "pane.split:right" },
  "terminal": { "ctrl-shift-c": "terminal.copy", "send:ctrl-left": "send:\u001bb" },
  "layout:terminals": { "ctrl-shift-pagedown": "space.next" },
  "mod:editor": { "ctrl-s": "editor.save" }
}
```

- 区块优先级：`mod:<名>` ＞ `terminal` ＞ `layout:<名>` ＞ `global`。
- 动作：命令名（`名:值` 填第一参数）、`send:<文本>`（往终端打字）、`<mod>.<命令>`、`terminal`（按键交给程序）。
- 参数对象写法：`{"command":"layout.resize","args":{"id":"sidebar","px":240}}`。
- 别抢程序依赖的键：`ctrl-c/d/l/r/w/a/e/u/k/z`、`ctrl-[`；Windows 的 `ctrl+alt` 是 AltGr。
- 升级后改过的 keys.json 不会被合并默认键，需要手动补。

## 主题

`themes/<name>.json`：`{ "background", "foreground", "selection", "cursor"?, "ansi": [16色], "colors"?: {...} }`，全 `#rrggbb`。`settings.json` 的 `theme` 可写 `{light, dark}` 跟随系统；`app.theme {name}` 切换。与布局同名的主题只在该布局的 Settings 里出现。

## settings.json 常用项

`zoom`（0.5–3）、`theme`、`appearance`、`colors`、`font`/`font_size`/`ui_font`/`font_weight`、`font_ligatures`、`terminal_line_height`/`terminal_cell_width`/`terminal_scroll_speed`、`sounds: {enabled, done, blocked}`、`worktrees: {directory}`、`copy_on_select`、`paste` 相关、`minimum_contrast`、`telemetry: false`（关匿名统计）。

## programs.json

```json
{ "programs": {
    "mytool": { "name": "My Tool", "icon": "my-icons/mytool.svg", "tint": true, "tile": "#334155" },
    "claude": { "run": ["my-claude"] } },
  "shells": ["xonsh"], "interpreters": ["pipx"] }
```

`run` 让它出现在 New 菜单（`knownAgents()` 可见）；用程序或函数，不用 alias。

## 分享

- 装社区件：先从 herdr.dev/layouts 或 herdr.dev/mods 的 `RECIPE.md` + 全部文件读一遍，告知用户是否联网/exec/读外部文件；复制到对应目录，**重名必须改名**（文件/文件夹、布局里引用的 mod 名、mod 内路径一起改）；mod 的 keys.json 合并进用户的并在覆盖已有键前询问。
- 发布自己的：公开 GitHub 仓库 + `RECIPE.md` + `preview.png` + topic `herdr-layout` / `herdr-mod`。
