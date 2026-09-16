# Click 子命令设计文档

## 概述

新增 `click` 子命令，模拟人类点击操作。自动将元素滚入 viewport，获取元素中心坐标后施加高斯分布偏移，根据设备类型使用 touch 或 mouse 事件执行点击。

## 命令行接口

```
cdp click --selector "button.submit" [--frame-id "xxx"] [--track]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--selector` | 是 | CSS 选择器 |
| `--frame-id` | 否 | Frame ID，用于 iframe 内元素 |
| `--track` | 否 | 可视化滚动路径 |

输出：JSON 格式，包含点击坐标和执行状态。

## 执行流程

```
1. 连接 CDP，创建 client
2. 自动检测设备类型（touch/mouse），复用 IsTouchDevice()
3. 确定滚动目标（与 scroll 子命令逻辑一致）：
   - 无 frame-id：scrollSelector = selector
   - 有 frame-id：查找 iframe 在 frame tree 中的位置，
     scrollSelector = iframe:nth-child(idx)
4. 调用 ScrollToElement(scrollSelector, track)
   - 复用 scroll 子命令的完整滚动逻辑（最多 20 次迭代）
   - 将 iframe/元素滚入 viewport
5. 获取点击坐标（关键：处理 iframe 偏移）：
   - 无 frame-id：直接在主 frame 获取 selector 的 getBoundingClientRect()
     clickX = rect.centerX, clickY = rect.centerY
   - 有 frame-id：
     a. 在主 frame 获取 iframe 的 getBoundingClientRect() → iframeRect
     b. 在 iframe 中（通过 frameId）获取 selector 的 getBoundingClientRect() → elemRect
     c. clickX = iframeRect.x + elemRect.centerX
        clickY = iframeRect.y + elemRect.centerY
   - 同时获取元素宽高用于计算高斯偏移的 σ
6. 高斯分布偏移
   - σ = min(elementWidth, elementHeight) / 4
   - x' = clickX + N(0, σ)
   - y' = clickY + N(0, σ)
   - 约 80% 点击在中心 ±32% 范围，95% 在元素边界内
7. 执行点击
   - touch 设备：dispatch touchStart → touchEnd
   - mouse 设备：dispatch mousePressed → mouseReleased
8. 输出 JSON 结果（含最终点击坐标）
```

### 坐标计算说明

iframe 内元素的坐标是相对于 iframe 的，不是相对于页面 viewport 的。
因此当操作 iframe 内元素时，需要将 iframe 在页面中的位置 (iframeRect.x, iframeRect.y)
与元素在 iframe 中的中心坐标 (elemRect.centerX, elemRect.centerY) 相加，
得到页面级别的绝对坐标。

## 高斯偏移

使用 Box-Muller 变换生成正态分布随机数：

- σ = min(elementWidth, elementHeight) / 4
- 偏移量：dx = σ * sqrt(-2 * ln(u1)) * cos(2π * u2)，dy 同理用不同随机种子
- 约 80% 点击落在中心 ±1.28σ（±32% 元素尺寸）内
- 约 95% 点击落在 ±2σ（±50% 元素尺寸，即元素边界）内
- 约 5% 可能落在边缘外，模拟人类偶尔点偏

## 代码变更

### 新建 `cmd/click.go`

click 子命令实现，注册 selector/frame-id/track flags，调用 `client.ClickElement()`。

### 修改 `internal/client.go`

新增方法：

- `ScrollToElement(selector, frameID string, track bool) error` — 从 scroll 子命令提取的公共滚动方法，包含完整的滚动循环逻辑
- `ClickElement(selector, frameID string) error` — 完整点击流程：滚动→定位→高斯偏移→点击
- `GaussianOffset(sigma float64) (float64, float64)` — 使用 Box-Muller 变换生成高斯随机偏移 (dx, dy)
- `DispatchClickEvent(x, y float64, isTouch bool) error` — 根据设备类型分发 touch 或 mouse 点击事件

### 修改 `cmd/scroll.go`

重构为调用 `client.ScrollToElement()`，保持行为不变，消除与 click 的代码重复。

### 不修改的文件

`cmd/root.go`、`cmd/eval.go`、`cmd/snapshot.go`、`main.go`

## 点击事件序列

### Touch 设备

```
touchStart(x, y) → touchEnd(x, y)
```

### Mouse 设备

```
mousePressed(x, y, button=left, clickCount=1) → mouseReleased(x, y, button=left, clickCount=1)
```

## 错误处理

- 元素未找到：返回明确错误信息
- 滚动后元素仍不可见：返回错误
- 点击坐标计算失败：返回错误
