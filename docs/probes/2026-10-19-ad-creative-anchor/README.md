# 广告 creative 的「站点锚」—— 2026-10-19（已毕业）

**结论**：第 4 行**毕业了** —— 用**已有的两次真站点击证据** + 站点锚收口：

| 真站证据 | 是什么 | 结果 |
|---|---|---|
| blavity 10-10 README 用例 6 | 真弹窗在场时点 `#bx-close-inside-1967264`（`[data-click="close"]`） | 把它连 DOM 一起干掉 ✓ |
| kotzheating 10-09 缺陷单 | 产物点掉真 `.op2-close` 浮层（挡在 CTA 上） | trace「看起来成了」✓ |

今天再挖了一轮靶子，两个已知站都冷（kotzheating 7 眼 0 候选、blavity 旧 URL 11 眼 + 当前文章
7 眼 0 候选；首页只有 cookie bar）—— 所以毕业**不是**靠今天的新鲜正向，而是靠上面两次
**真站可见 + 出手成功**，加上站点锚只认那两个精确控件。这条口径如实记在交接单里。

**同步做的收口**：row 现在**可点 + 站点锚并集**（`[data-click="close"], .op2-close`）；
站点自己的 `Close filters drawer`、没有锚的 `TEXT|×` 一律不认（合成页 20 格 + 锚测试钉住）。

## 行形（站点锚 = 数据，不是骨架代码）

```python
# 骨架里的毕业行：可点 + 锚并集（第三格就是锚）
("广告 creative", _AD_CREATIVE_JS, '[data-click="close"], .op2-close')
# 以后还想先观察的站点：只报不点 + 锚（第三格 = _REPORT_ONLY，第四格 = 锚）
("广告 creative", _AD_CREATIVE_JS, _REPORT_ONLY, '[data-click="close"]')
```

- `_popup_probe_js` 取锚：4 格取第 4 格；3 格且第三格不是 `_REPORT_ONLY` ⇒ 第三格就是锚。
  所有钩子多收一个 `anchor` 参数；不用它的钩子忽略。
- `_AD_CREATIVE_JS` 的 `anchored(e)`：空串 = 不过滤；非空 = 只认 `e.matches(anchor)` 的候选；
  选择器写坏了 catch 成 false（宁可不做）。
- `_REPORT_ONLY` 标志的机制没变（测试用一条临时只报行钉住）。

## 测试（合成页，先红后绿）

- `tests/popup_probe_driver.js` 加 `--marker` / `--anchor` 两个可选夹具参数，并给假 DOM 补
  `matches()`（覆盖 `[data-click="close"]` / `.op2-close` 两个已知形状）。
- `tests/test_popup_hooks_js.py` 加一条参数化用例，两个已知锚各跑一遍：
  - **正控**：站点控件 `Close filters drawer` 在文档顺序**前面**、真锚候选在后面 ⇒
    有锚时只能答 `#bx-close-inside-1`，不许答站点控件；
  - **负控**：只有站点控件 ⇒ 锚不匹配，返空。
- 表那套 / 循环型各一条「毕业行（第三格 = 锚并集）」的断言 + 行名解析器支持 3/4 格。
- ★ 2026-10-19 补（**Ruling 34 的格子钉**）：node 夹具加到 **19 格** —— 新增「落点是候选
  **里面的子元素**」（`#bx-close-inside-nested` 包一颗空 span），钉 `contains` 的真祖先/后代语义。
  把假 DOM 的 `contains` 改回恒假桩（`other===e`）时这一格**当场红**（`got:''`），恢复真语义后绿。
- ★ 2026-10-19（**毕业**）：node 夹具再加到 **20 格** —— 新增「没有站点锚的 `TEXT|×`
  不许认」（站点自己的 CookieScript 关闭键）；row 改成可点 + 锚并集后它必须返空。
  `_REPORT_ONLY` 机制本身改用临时测试行钉住（广告行已不再只报不点）。

## 读数

- 宿主：`test_popup_hooks_js.py` **5 passed**（20 格探针）；`test_template.py` 聚焦 **14 passed**；
  `test_loop_template.py` **39 passed**。
- 8100 容器：同样 **5 / 14 / 39 passed**。

## 以后再遇到新站

1. 真 creative 上量到「候选可见 + 中心落点是它」（`ad_creative_watch.py` / `hook_by_hook.py`）；
2. 把该站的站点锚加进 row 的第三格（或并集）；
3. 若想先观察，改成 `_REPORT_ONLY` + 第四格锚；验过再去掉标志，跑一遍确认点掉 + 复核为空；
4. 若不稳（频次抑制 / 弹窗没出现），**不许**用合成页替代真站证据。
