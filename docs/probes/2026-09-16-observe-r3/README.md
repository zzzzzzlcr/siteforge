# R3 能力探针：`observe` 契约在难页面上的覆盖率

2026-09-16。**探针产物，不是生产代码** —— 用来回答规格 §12 R3：
「`observe` 在 shadow 站 / 跨源 iframe 上到底能不能取齐」。

## 结论：可行（四档全过）

| 档 | 页面 | 结果 |
|---|---|---|
| 1 | light DOM（含 hero 主按钮 + 页脚同名按钮 + cookie 横幅） | ✅ actions 6 / fields 1 / obstructions 1；**同名按钮靠 `region` 区分成功** |
| 2 | 两层嵌套 open shadow root | ✅ fields 3 / option_groups 1 / shadow_depth 2 |
| 3 | 跨源 iframe（`127.0.0.1` vs `localhost`） | ✅ 需**逐帧 eval 再合并**（同源策略） |
| 4 | 跨源 iframe **套**两层 shadow（最难） | ✅ actions 6 / fields 3 / groups 1 / 被遮挡 0 |

## 挖出的三个坑（都会**静默**产出错误页面模型）

1. **`ShadowRoot` 没有 `innerText`**（是 `HTMLElement` 的属性）→ 逐 root 收文本时
   拿到 `undefined`，shadow 页 `page_text` 只剩 **10 字符**（修好后 141）
2. **`document.elementsFromPoint` 不穿透 shadow**（返回 host）→ 判遮挡时
   **所有 shadow 元素全被误判为被遮挡**（实测 5/5 假阳性）
3. **`parentElement` 出不了 shadow 边界** → `region` 全部退化成 `body`

## 怎么重跑

```bash
cd fixtures && python3 -m http.server 8892 &      # 需同时可从 127.0.0.1 和 localhost 访问
python3 run.py .                                   # 需要 127.0.0.1:9222 上有 Chrome
python3 frames.py                                  # 列帧树（跨源那档要靠它拿子帧 id）
```

`observe.js` 是自包含的 IIFE，`cdp eval --file observe.js [--frame-id <id>]` 即可。

⚠️ 生产版**不应**沿用这里的自实现穿透 —— 应走 Go 内核的 `internal` 包
（同 `__cdpQ` 那套），理由见规格 §4.1 与「不要各自重写一套」。
