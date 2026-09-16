# Spec: form --select 支持自定义下拉框（React-Select）

## 背景

`cdp form --select` 目前只支持原生 `<select>` 元素。React-Select 等组件库使用
自定义 DOM 结构（隐藏 input + 可点击 control + 动态 menu），无法用现有逻辑
处理。

## 目标

`cdp form '#wrapper' --select 'OptionText'` 同时支持原生 `<select>` 和自定义下拉框，
自动检测类型并走对应流程。

## 设计

### 检测逻辑（`SelectOption` 入口）

```
selector 命中的元素是 <select> 标签？
  YES → 走现有 native 流程（click select → buildSelectOptionJS）
  NO  → 走 custom 流程（见下）
```

检测方式：`EvalInFrame` 执行 `document.querySelector(selector).tagName === 'SELECT'`。

### Custom 流程

传入 `#country-wrapper`，`--select 'Canada'`：

1. **找 control**：在 wrapper 内按优先级搜索可点击的触发元素
   - a) `[onclick*="toggle"]`、`[onclick*="menu"]`、`[onclick*="open"]`
   - b) wrapper 自身（fallback：直接 click wrapper）
   - 返回 control 的 CSS selector 供后续使用

2. **Click control**：`DispatchMouseClick` 点 control 中心，等待菜单打开

3. **找 option + click**：在 wrapper 内搜索包含目标文本的可见元素
   - 搜索范围：wrapper 内所有可见子元素（排除 control 自身和 `display:none`）
   - 匹配：`textContent.trim() === 'Canada'` 或 `textContent.includes('Canada')`
   - 找到后 `DispatchMouseClick` 点击 option 中心

4. **不做 close**：页面自己的 onclick handler（如 `selectCountry()`）
   会关闭菜单并设值，工具不额外操作。

### 错误处理

| 场景 | 行为 |
|------|------|
| control 未找到 | 返回 error："no clickable control found in wrapper" |
| option 未找到 | 返回 error："option 'X' not found in custom dropdown" |
| option 不可见（0×0）| 跳过，继续搜索下一个匹配的 option |

### 涉及文件

| 文件 | 改动 |
|------|------|
| `internal/form.go` | `SelectOption` 加分支：tagName===SELECT 走 native，否则走 custom |
| `internal/form.go` | 新增 `findControlJS` / `pickCustomOptionJS` 生成 JS helper |
| `internal/form_test.go` | 新增 JS 生成函数的单元测试 |
| `internal/form_integration_test.go` | 新增 react-select 页面集成测试 |

### 测试策略

**单元测试**（不需要 Chrome）：
- `buildSelectOptionJS` 保持不变
- 新增 `buildFindControlJS` 输出包含 `[onclick*="toggle"]` 回退逻辑
- 新增 `buildPickOptionJS` 输出包含 textContent 匹配 + click

**集成测试**（需要 Chrome + localhost:8080/react-select）：
- `TestSelectOption_CustomDropdown_Single`：选 Canada，验证 `#country-input` 值为 Canada
- `TestSelectOption_CustomDropdown_NotFound`：选不存在的选项，验证返回 error
- `TestSelectOption_NativeSelect_StillWorks`：对原生 `<select>` 仍然正常

### 不涉及

- 不支持 blur/Escape 关闭菜单（页面自行处理）
- 不支持多选（multi-select）的额外逻辑
- 不引入新的 CLI flag
