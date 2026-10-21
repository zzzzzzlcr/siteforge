# lendio：探路到了成功页，却自动重探 3 趟（`job-be76b7ecd92c`）

- 站点：`https://lp.lendio.com/sba-loans`（`site=lp`）
- 判据：`Congratulations!Your business is eligible for several financing options.`
- 结果：`explore_unfinished`，3 趟探路（82 / 42 / 57 步），**不写 py**
- 证据来源：8099 的 `runtime/checkpoints.sqlite`（`thread_id=job-be76b7ecd92c`，最后一版
  checkpoint 的 `channel_values`）与 `journey.steps`

## 现场

最后一趟第 43 步点「See Your Results」之后，页面**确实**出现了成功文案：

- `observe`（第 44/45/48/55 步）的 `node_texts[64]` = `Congratulations!`（`h3@body`），
  `node_texts[65]` = `Your business is eligible for several financing options.`（`h5@body`）；
- 模型 `final_answer` 写着「✅ 成功走到目标状态」；模型自己的 notes 里也点破了：
  「`body.innerText` 读不到……文字藏在 shadow root 里」；
- 收尾照图 `vision_hit=True`。

而图上的结算 `_explore_reached_success` = **False** ⇒ 按 `EXPLORE_ATTEMPTS=3` 自动重探
（每一趟都是一次**真提交**）。

## 三个根因（都在代码里）

1. **探路的「整页正文」不穿 shadow DOM** —— `agent/browser_agent.py` 的 `_FULL_TEXT_JS`
   只读 `document.body.innerText`。这一站的问卷/成功页整棵在 open shadow root 里：
   - `page_text_full`（2908 字）是落地页文案，连「How much money…」都没有；
   - `node_texts` 有（observe 是逐 root 收的）；
   - 产物那份 `template._PAGE_TEXT_JS` 早就改成逐 root 收了（2026-09-23/28 修过同一个坑），
     探路这份没跟上。
2. **判据与页面排版差一个空白** —— 页面把一句话拆在 `h3` + `h5` 上（视觉上是换行），
   正文拼出来中间会多一个空白；人给的判据没有那个空格 ⇒ 逐字子串**永远**对不上
   （即使 (1) 修好也一样）。
3. **重探闸只算一次** —— `agent/graph.py:_explore` 的 `wont_help` 只在第 1 趟之后算一次，
   第 2/3 趟就算 `vision_hit=True` 也拦不住下一趟。

## 修法

- `_FULL_TEXT_JS` → 逐 root 收（与 `template._PAGE_TEXT_JS` 同口径：跳 style/script、
  `innerText`、压空白、20000 字上限）。
- `browser_agent.criterion_in` / 骨架里的 `_criterion_in`：判据匹配**空白可有可无**
  （先逐字比，不中再比去掉所有空白的那份）；`_success_hit`、`_success_line_in`、
  `graph._explore_reached_success`、`replayable_prefix`（R3）、产物 `_succeeded`
  全都换到这一把尺子上。
- `_explore` 的 `wont_help` **每趟重算**。

## 真站验证（8100，`job-cca93e907a19`）

同一站点、同一判据、同一目标，重启 8100 后重跑一趟：

| | 修复前（8099 `job-be76b7ecd92c`） | 修复后（8100 `job-cca93e907a19`） |
|---|---|---|
| 趟数 | 3（82 / 42 / 57 步） | **1**（83 步） |
| `explore_reached_success` | false | **true** |
| `journey.stop_reason` | `ended` | **`reached_success`** |
| 谁认的 | —— | **代码读到的正文**（`vision_hit=false`） |

账本：`explore_spent={"steps":83,"rounds":0,"attempts":1}`；
`explore_say`：「探路走完了：页面上见到了人给的那句**成功文案**（见到就收摊…）」。
验证停在 draft 闸后由人「停」掉（账本留在 checkpoint，可 reopen），窗口已关。

## 钉子（先红后绿）

- `tests/test_browser_agent.py::test_the_whole_page_read_pierces_open_shadow_roots`
- `tests/test_browser_agent.py::test_it_stops_when_the_page_puts_the_success_sentence_in_two_blocks`
- `tests/test_browser_agent.py::test_the_settlement_recognizes_the_success_sentence_split_across_two_blocks`
- `tests/test_browser_agent.py::test_r3_recognizes_the_success_sentence_split_across_two_blocks`
- `tests/test_browser_agent.py::test_the_two_criterion_matchers_are_one_yardstick`
- `tests/test_template.py::test_the_product_sees_the_success_words_a_page_split_into_two_blocks`
- `tests/test_graph.py::test_a_later_pass_that_saw_the_line_in_the_screenshot_stops_further_retries`
