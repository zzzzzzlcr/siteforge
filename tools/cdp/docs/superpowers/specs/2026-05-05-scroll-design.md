# scroll 子命令设计

## 1. 命令接口

```bash
cdp scroll [--selector] <selector> [--frame-id <frame-id>] [--track]
```

- `selector`（必选）：CSS 选择器，在 main frame 或指定 frame 中查询元素
- `--selector` 是可选的 flag 名，支持两种调用方式：
  - `cdp scroll .my-class`
  - `cdp scroll --selector .my-class`
- `frame-id`（可选）：目标 frame ID，不提供则在 main frame 中查询
- `track`（可选）：开启轨迹可视化模式

## 2. 执行流程

```
用户调用 scroll [--selector] <selector> [--frame-id <frame-id>] [--track]
        │
        ▼
┌─────────────────────────────────────┐
│ 解析 selector、frame-id、track 参数   │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 获取 main frame 的 childFrames 列表  │
│ frame-id 提供 → 在 childFrames 中    │
│ 查找 frame.id == frame-id 的索引     │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 获取元素中心坐标 (centerX, centerY)   │
│ 执行 JS: document.querySelector     │
│ 返回 {x, y, centerX, centerY}      │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 判断设备类型: TouchDetectionJS      │
│ true → Touch 流程                  │
│ false → MouseWheel 流程            │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 执行 Touch/MouseWheel 滑动          │
│ track=true → 记录所有 points+颜色    │
│ 最多 20 次，直到元素可见面积 ≥ 80%    │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 执行 scrollIntoView                 │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ track=true → 在 main frame 绘制轨迹  │
└─────────────────────────────────────┘
```

## 3. Touch 滑动

### 上滑（元素在视口中心下方，centerY > viewport.height/2）

```
起点: startX = viewport.width/2 + randOffsetX, startY = viewport.height - randOffsetY
终点: stopX  = startX + randOffsetX,            stopY  = viewport.height/2 + randOffsetY
```

### 下滑（元素在视口中心上方，centerY < viewport.height/2）

```
起点: startX = viewport.width/2 + randOffsetX, startY = viewport.height/2 + randOffsetY
终点: stopX  = startX - randOffsetX,             stopY  = viewport.height - randOffsetY
```

### 随机偏移

- `randOffsetX`：模拟人类拇指水平位置不精确（±20-40px）
- `randOffsetY`：模拟人类拇指垂直位置不精确（基于拇指长度，拇指越长安移越大）

### 贝塞尔曲线

- 三次贝塞尔（4点：起点 P0、控制点 P1、控制点 P2、终点 P3）
- P0 = 起点
- P1 = P0 + (0, -(distance * 0.3)) + randomOffset
- P2 = P1 + (distance * 0.2, 0) + randomOffset
- P3 = 终点
- 离散采样 15 个点，分 3 次 `Input.dispatchTouchEvent` 发送

### Touch 事件分发

分 3 次发送 touch points：
1. `touchStart` + 1 个初始点
2. `touchMove` + 后续 points
3. `touchEnd` + 1 个终点

每次发送随机偏移不同（热力图随机）。

## 4. MouseWheel 滚动

- 判断元素是否在视野内：执行 JS 检查元素可见面积 ≥ 80%
- 元素不可见时，根据元素中心相对 viewport 中心的方向，计算滚动步长
- 每次 `Input.dispatchMouseScrollEvent` 滚动 `deltaX/Y` 带 `±rand() * 3` 随机偏移
- 最多滚动 20 次，直到元素可见面积 ≥ 80%

## 5. scrollIntoView

在 Touch/MouseWheel 滑动之后，执行 JS：

```javascript
document.querySelector('<selector>').scrollIntoView()
```

- 有 frame-id → 在对应 iframe 的 isolated world 中执行
- 无 frame-id → 在 main frame 中执行

## 6. 轨迹可视化（track 模式）

当 `--track` 参数存在时：

- 每次滑动的 touch points 数组全部保留
- 每次滑动随机生成一个颜色（HSL 格式）
- 最多滑动 20 次，直到元素可见面积 ≥ 80%
- 所有滑动轨迹在 main frame 上用 DOM 绘制：
  - 每个轨迹点绘制一条 SVG line
  - 最后一个点绘制一个圆点 + 发光效果
  - 500ms 后淡出，2.5s 后移除 DOM 元素

## 7. 文件变更

| 文件 | 变更 |
|------|------|
| `cmd/scroll.go`（新建） | `scroll` 子命令，解析 `--selector`、`--frame-id`、`--track` 参数 |
| `internal/client.go` | 新增 `ScrollTouch()` 和 `ScrollMouseWheel()` 方法 |
| `internal/js.go` | 新增 `GetElementCenterJS`（返回 `{x, y, centerX, centerY}`）、复用已有 `TouchDetectionJS`、`isElementVisible` |

## 8. 依赖

- `github.com/chromedp/cdproto/input`：Touch 和 MouseWheel 事件分发
- `github.com/chromedp/chromedp`：CDP WebSocket 连接
