# 广告 creative 的「站点锚」—— 2026-10-19（先做数据格，不毕业）

**结论**：交接单第 4 行的**毕业条件今天没达成** —— 两个已知真靶子都没再弹：

| 靶子 | 观察 | 结果 |
|---|---|---|
| kotzheating `/contact/` | `ad_creative_watch.py` 90 秒 / **7 眼** | 候选 **0**，钩子表第 4 行返空 |
| blavity 那条广告 URL（`bl_url.txt`） | 150 秒 / **11 眼** | 候选 **0**，钩子表第 4 行返空 |

⇒ 与 2026-10-10 blavity 探针记的「点掉后 28 分钟没再弹（疑频次抑制）」一致。
**按交接单的纪律：没有真 creative 的「可见 + 有落点」证据，`_REPORT_ONLY` 不动。**

## 这一刀准备了什么

把「站点锚」做成**钩子表第 4 格**（数据，不是骨架代码）：

```python
# 默认（骨架里）：
("广告 creative", _AD_CREATIVE_JS, _REPORT_ONLY, "")
# 站点补丁（等真靶子验过再填 + 去 _REPORT_ONLY）：
("广告 creative", _AD_CREATIVE_JS, _REPORT_ONLY, '[data-click="close"]')   # blavity
("广告 creative", _AD_CREATIVE_JS, _REPORT_ONLY, '.op2-close')             # kotzheating
```

- `_popup_probe_js` 把第 4 格作为 `anchor` 参数传给钩子（所有钩子多收一个参数，不使用它的忽略）。
- `_AD_CREATIVE_JS` 加 `anchored(e)`：空串 = 不过滤（broad 判据，仍只报不点）；非空 =
  只认 `e.matches(anchor)` 的候选；选择器写坏了 catch 成 false（宁可不做）。
- **`_REPORT_ONLY` 仍是第三格** —— 锚只改「哪一颗才算」，不改「点不点」。

## 测试（合成页，先红后绿）

- `tests/popup_probe_driver.js` 加 `--marker` / `--anchor` 两个可选夹具参数，并给假 DOM 补
  `matches()`（覆盖 `[data-click="close"]` / `.op2-close` 两个已知形状）。
- `tests/test_popup_hooks_js.py` 加一条参数化用例，两个已知锚各跑一遍：
  - **正控**：站点控件 `Close filters drawer` 在文档顺序**前面**、真锚候选在后面 ⇒
    有锚时只能答 `#bx-close-inside-1`，不许答站点控件；
  - **负控**：只有站点控件 ⇒ 锚不匹配，返空。
- 表那套 / 循环型各一条「第 4 行带空锚」的断言 + 行名解析器支持 4 格。

## 读数

- 宿主：`test_popup_hooks_js.py` **5 passed**；`test_template.py` 聚焦 **14 passed**；
  `test_loop_template.py` **39 passed**。
- 8100 容器：同样 **5 / 14 / 39 passed**。

## 毕业时还要做什么

1. 真 creative 上量到「候选可见 + 中心落点是它」（`ad_creative_watch.py` / `hook_by_hook.py`）；
2. 把该站的站点锚填进第 4 格；
3. 去掉第三格 `_REPORT_ONLY`，再把产物跑一遍，确认点掉 + 复核为空；
4. 若不稳（频次抑制 / 弹窗没出现），**不许**用合成页替代真站证据。
