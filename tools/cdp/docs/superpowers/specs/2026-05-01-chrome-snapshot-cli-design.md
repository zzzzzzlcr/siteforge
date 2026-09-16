# Chrome Snapshot CLI 设计文档

## 概述

使用 Go + chromedp 库实现的 CLI 工具，通过 Chrome DevTools Protocol (CDP) 连接到 Chrome，获取当前活动 target 的所有 frame 信息，并在每个 frame 的隔离世界中执行 JavaScript 代码，最终收集所有结果以 JSON 数组输出。

## 命令结构

```
cdp [global flags]
  --host string   Chrome 主机地址 (default "127.0.0.1")
  --port int      Chrome 端口 (default 9222)

snapshot [子命令]
  无额外参数，读取全局 --host/--port
```

## 执行流程

1. **连接 Chrome**：使用 `chromedp.NewClient` 连接到 `http://<host>:<port>`
2. **获取 FrameTree**：调用 `page.GetFrameTree` 获取当前页面所有 frame 信息
3. **创建隔离世界**：遍历 frameIds，对每个 frame 调用 `page.CreateIsolatedWorld` 创建隔离 JavaScript 世界
4. **执行脚本**：在隔离世界中执行内嵌的 `snapshot.js`
5. **收集结果**：收集所有 JSON 结果，合并为 JSON 数组输出到 stdout

## 文件结构

```
cmd/
├── root.go      # 根命令，定义全局 --host/--port
└── snapshot.go  # snapshot 子命令实现
internal/
├── client.go    # CDP client 封装
└── snapshot.go  # 内嵌 snapshot.js 字符串
main.go          # 程序入口
go.mod
```

## 依赖

- `github.com/chromedp/chromedp`
- `github.com/spf13/cobra`

## 输出格式

```json
[
  {"frameId": "frame1", "data": {...}},
  {"frameId": "frame2", "data": {...}}
]
```

## 错误处理

- 连接失败：输出错误到 stderr，退出码 1
- frame 执行失败：跳过该 frame，继续处理其他 frame
- 最终输出：成功的结果正常输出，失败的 frame 在数组中标记错误信息