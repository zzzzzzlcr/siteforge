# eval 子命令设计

## 概述

给 `cdp` CLI 添加 `eval` 子命令，可在指定 frame 或主帧上执行任意 JavaScript 代码。

## 界面设计

### 用法

```bash
# 直接传入 JS 代码
./cdp eval --frame-id <frame-id> "document.title"

# 从文件读取 JS 代码
./cdp eval --frame-id <frame-id> --file script.js

# 在主帧执行（省略 frame-id）
./cdp eval "document.title"
```

### Flags

| Flag | 必选 | 默认值 | 说明 |
|------|------|--------|------|
| `--frame-id` | 否 | 空（主帧） | 目标 frame 的 ID |
| `--file` | 否 | 空 | 从文件读取 JS 代码，为空则从命令行参数获取 |

### 退出码

- `0`: 执行成功
- `1`: 执行失败（JS 异常或其他错误）

### 输出

- 正常：JS 返回值直接输出到 stdout
- 错误：错误信息输出到 stderr，不影响 stdout

## 实现

### 文件结构

```
cmd/eval.go    # eval 子命令定义
```

### 依赖

- `internal/client.go` 中的 `EvalInFrame` 方法（已存在）

### 逻辑

1. 解析 `--frame-id` 和 `--file` flags
2. 获取 JS 代码源（`--file` > 命令行参数）
3. 调用 `client.EvalInFrame(frameID, js, &result)`
4. 将 result JSON 序列化后输出到 stdout
5. 错误时输出到 stderr 并退出码 1

## 设计决策

- **两种输入源**: 命令行参数（简单场景）和文件（复杂脚本）
- **直接输出原始值**: 便于管道和脚本处理
- **错误到 stderr**: 符合 Unix 惯例，不污染 stdout
