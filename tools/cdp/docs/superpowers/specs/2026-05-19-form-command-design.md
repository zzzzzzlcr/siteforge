# form 子命令设计

## 概述

新增 `form` 子命令，通过 CDP 协议模拟人类手势填写表单控件，支持文本输入、checkbox/radio 切换、select 选项选择。与 click/scroll 保持一致的「人类手势」设计风格。

## CLI 接口

```
cdp form [selector] --value '文字'          # 文本类输入
cdp form [selector] --check true|false      # checkbox/radio 勾选/取消
cdp form [selector] --select 'option值'     # select 选择选项
```

### Flags

| Flag | 类型 | 说明 |
|------|------|------|
| `--selector` | string | CSS 选择器，也可作为位置参数 |
| `--value` | string | 文本输入内容 |
| `--check` | string | checkbox/radio 目标状态，值为 "true" 或 "false" |
| `--select` | string | select 选项匹配值（按 value 或 textContent） |
| `--frame-id` | string | 目标 frame（空则主 frame） |
| `--track` | bool | 可视化操作轨迹 |

- `--value`、`--check`、`--select` 互斥，必须且只能指定一个
- `selector` 复用 `resolveSelector`，支持位置参数或 flag

## 行为流程

### 文本类输入（`--value`）

```
1. 确定滚动目标（若有 frame-id，先用 ResolveIframeSelector 获取 iframe selector）
2. ScrollToElement(scrollSelector, track) — 人类手势滚动
3. ScrollIntoView(selector, frameID) — 精确滚动到位
4. WaitForPositionStable(scrollSelector, "") — 等位置稳定
5. element.focus() — 聚焦
6. 全选清除（Ctrl/Cmd+A → Backspace）— 清除已有内容
7. 逐字符输入（每字符：keydown → 更新 value → input → keyup，间隔 20-60ms 随机）
8. element.blur() — 失焦，触发 change 事件
```

### checkbox/radio（`--check`）

```
1. 滚动到元素可见区域
2. 等待位置稳定
3. 检查当前 checked 状态
4. 若当前状态 != 目标值：DispatchMouseClick 点击元素中心（带 Gaussian 偏移）
5. 触发 click → input → change 事件链
```

### select（`--select`）

```
1. 滚动到元素可见区域
2. 等待位置稳定
3. DispatchMouseClick 点击 select 打开下拉
4. 按 option 的 value 属性匹配，匹配不到则按 textContent 匹配
5. DispatchMouseClick 点击匹配的 option
6. 触发 change 事件
```

## 实现

### 新文件

- `cmd/form.go` — 命令定义、参数解析、主流程编排
- `cmd/form.go` 内注册到 rootCmd

### internal 层新增方法

- `Client.FillText(selector, text string, frameID string, track bool) error`
- `Client.CheckElement(selector string, checked bool, frameID string) error`
- `Client.SelectOption(selector, option string, frameID string) error`

### 复用现有方法

- `resolveSelector` — selector 参数解析
- `ScrollToElement` / `ScrollIntoView` / `WaitForPositionStable` — 滚动定位
- `GetElementCenter` — 获取元素坐标
- `DispatchMouseClick` — 鼠标点击（checkbox/radio/select）
- `EvalInFrame` — 执行 JS（聚焦、全选、输入、状态检查）
- `RenderTrack` — 轨迹可视化

## 错误处理

- selector 找不到元素 → 返回错误 "element not found"
- `--value`/`--check`/`--select` 都没指定或同时指定多个 → 返回参数错误
- select 找不到匹配 option → 返回错误 "option not found: <值>"

## 测试

```bash
# 文本输入
go run . form --selector '#username' --value 'hello'

# checkbox 勾选/取消
go run . form --selector '#agree' --check true
go run . form --selector '#agree' --check false

# select 选择
go run . form --selector '#country' --select 'CN'

# iframe + track
go run . form --selector '#field' --value 'test' --frame-id <id> --track
```
