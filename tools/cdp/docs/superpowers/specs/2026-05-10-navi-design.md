# navi 子命令设计

## 概述

添加 `navi` 子命令，通过 CDP 协议在当前页面中导航到指定 URL，等待页面加载完成后返回 frame 信息。

## 命令用法

```bash
cdp navi <url>
```

- `url`：必填位置参数，目标 URL

## Flags

- `--frame-id`（可选）：指定在哪个 frame 中导航，默认为主 frame

## 实现方案

在 Client 上添加 `Navigate` 方法，保持与 click/scroll/eval 一致的架构模式。

### 新增文件

**`cmd/navi.go`** — 命令定义和执行逻辑：
- 定义 `naviCmd` cobra 命令，`Args: cobra.ExactArgs(1)`
- `runNavi`：解析 URL → 创建 Client → 调用 `client.Navigate(url)` → JSON 输出

### 修改文件

**`internal/client.go`** — 添加 `Navigate(url string)` 方法：
- 使用 `chromedp.Navigate(url)` 导航到目标 URL
- 使用 `page.LoadEventFired` 等待页面完全加载
- 调用现有 `GetFrameTree()` 获取 frame 信息并返回

## 输出

与 snapshot 一致的 JSON frame 树结构：

```json
{
  "frameId": "main",
  "title": "Page Title",
  "url": "https://example.com",
  "readyState": "complete",
  "children": [...]
}
```

## 不包含

- referer 参数
- 新 tab 打开
- 等待策略选项（默认等待 load 事件）
