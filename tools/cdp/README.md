# cdp CLI

Chrome DevTools Protocol CLI 工具，用于与 Chrome 浏览器交互，支持页面快照、执行 JavaScript、模拟人类手势操作和页面管理。

## 安装

```bash
go build -o cdp main.go
```

## 前置条件

Chrome 需启用远程调试：

```bash
chrome --remote-debugging-port=9222
```

## 使用方法

### 全局选项

```bash
--host string   Chrome 地址 (默认 "127.0.0.1"，支持 CDP_HOST 环境变量)
--port int      Chrome 端口 (默认 9222，支持 CDP_PORT 环境变量)
```

### snapshot - 获取页面快照

获取当前活跃页面所有 frame 的 DOM 树结构（JSON 格式），包含 frameId、title、URL、DOM 层级。

```bash
cdp snapshot
cdp snapshot --host 192.168.1.100 --port 9222
```

### eval - 执行 JavaScript

在指定 frame 中执行 JavaScript 表达式。

```bash
cdp eval '"hello"'
cdp eval --frame-id <frameId> 'document.title'
cdp eval --file script.js
```

Flags:
- `--frame-id` - 目标 frame（空则为主 frame）
- `--file` - 从文件读取 JS

### click - 模拟人类点击

模拟人类手势点击指定元素（CSS 选择器定位）。

```bash
cdp click '#btn'
cdp click --selector '#btn'
cdp click '#btn' --frame-id <frameId>
cdp click '#btn' --track
```

Flags:
- `[selector]` - CSS 选择器（位置参数，或 `--selector`）
- `--frame-id` - 目标 frame
- `--track` - 可视化操作轨迹

### scroll - 模拟人类滚动

模拟人类手势滚动到指定元素。

```bash
cdp scroll '#content'
cdp scroll --selector '#content'
cdp scroll '#section' --frame-id <frameId>
cdp scroll '#content' --track
```

Flags:
- `[selector]` - CSS 选择器（位置参数，或 `--selector`）
- `--frame-id` - 目标 frame
- `--track` - 可视化操作轨迹

### navi - 页面导航

导航到指定 URL，等待页面加载完成后返回 frame 树。

```bash
cdp navi https://example.com
cdp navi https://example.com --frame-id <frameId>
```

Flags:
- `[url]` - 目标 URL（必填位置参数）
- `--frame-id` - 目标 frame（默认主 frame）

### form - 表单填充

模拟人类手势填写表单元素，支持文本输入、复选框切换和下拉选择。

```bash
cdp form '#input' --value 'hello'
cdp form '#checkbox' --check true
cdp form '#checkbox' --check false
cdp form '#select' --select 'option-value'
cdp form '#input' --value 'hello' --track
```

Flags:
- `[selector]` - CSS 选择器（位置参数，或 `--selector`）
- `--value` - 文本输入值
- `--check` - 复选框状态（"true" / "false"）
- `--select` - 下拉选项值或文本
- `--frame-id` - 目标 frame
- `--track` - 可视化操作轨迹

三种操作模式互斥，每次只能指定 `--value`、`--check`、`--select` 其中之一。

### targets - 列出页面

列出所有打开的页面，标记当前活跃页面。

```bash
cdp targets
```

输出每个页面的 id、title、url 和 active 状态。

### active - 激活页面

切换到指定页面，后续命令均操作该页面。

```bash
cdp active <target-id>
cdp active --target <target-id>
```

Flags:
- `[target]` - 目标页面 ID（位置参数，或 `--target`）

### close - 关闭页面

通过 target ID 关闭指定页面，支持批量关闭和关闭所有非活跃页面。不能关闭当前活跃页面。

```bash
cdp close <target-id>
cdp close <target-id1> <target-id2>
cdp close --all
```

Flags:
- `[target-id...]` - 要关闭的页面 ID（可指定多个）
- `--all` - 关闭所有非活跃页面

关闭后输出剩余页面列表（JSON 格式）。

## 依赖

- Go 1.26.2
- [chromedp](https://github.com/chromedp/chromedp) - Chrome DevTools Protocol 库
- [cobra](https://github.com/spf13/cobra) - CLI 框架
