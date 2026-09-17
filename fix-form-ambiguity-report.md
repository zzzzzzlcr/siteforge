# 修复报告：第 17 步的现场 + `cdp form` 的歧义闸（对称性缺口）

**状态**：DONE_WITH_CONCERNS —— 现场量清了、三件都修了、钉子都扎了；
**但自测仍未过**（成功文案没出现、产物没落盘），差在哪见 §5，不粉饰
**commit**：见文末（只本地 commit，没 push）
**一行测试结论**：`pytest tests/ -q` → **394 passed / 12 skipped**；`go test ./...`（tools/cdp）
→ **全绿**（internal 66.5s / cmd 46.0s / mcp 0.01s）。新增钉子 10 条（Go 7 + Python 3），旧断言一条没放宽。

## 1. 第 17 步：**量出来的**（不是推断）

在真站上跑到那一刻抓的现场（`/tmp/gwacc4/zipwatch.jsonl`，探针每 2 秒看一次输入框的现状）。
**那个框自己长这样**（observe 原样 + DOM 原样）：

```
observe.selector     input.MuiInputBase-input.MuiInputBase-inputAdornedStart
observe.label        ""                        ← 空
observe.hint         "textField-173838"        ← 不透明的 MUI id
observe.placeholder  "e.g. 06801"              ← **只是个例子，没有 zip/postal 这个词**
observe.type         "tel"                     ← 弹数字键盘用（**歧义源**）
observe.nearby_text  null                      ← 一个词都没有
DOM: id='textField-173838' name='' type='tel' placeholder='e.g. 06801' label=''
```

四问四答：

| 问 | 答（证据） |
|---|---|
| ① ZIP 框里到底是什么值 | 探索那一趟模型自己写的是 **`90210`**（邮编）；**复跑时产物写进去的是 `form.json` 的 `phone`**（`3055550142` 那一族）—— 用户截图里那串 `07936567874` 是同一个来源 |
| ② 落对了框还是别的框 | **落对了框**（值就在 `textField-173838` 里）—— 所以**线索 2（备用选择器落到别的字段上）在这一格不是原因**；错的是**值的语义**（把邮编框当成手机号 → 从运营资料里取了手机号） |
| ③ 页面报不报错 | 那一刻 `aria-invalid=false`、没有红字；红字是**提交之后**才出现的（`Please enter a valid US zipcode.`，用户截图那一张） |
| ④ Continue 是被拦住还是没点到 | `elementFromPoint` 在它中心点上站着的是 **`button`**（就是它自己）—— **不是被盖住**，是**校验拦住的** |

**结论**：第 17 步不是「点错框」，是**语义判错**：那个框身上**一个语义信号都没有**
（label 空、hint 是 id、placeholder 是例子、nearby_text 空），只剩 `type=tel` 这一条 ——
而 tel 是**键盘提示**，邮编框也用它。按它判成手机号 → 运营资料里的手机号被打进邮编框 ✓
（就是用户 2026-09-17 在窗口里看到的那条）。

## 2. 顺着现场修的三件

### 2.1 `type=tel` 且语义全落空时，用**探索那一趟自己写的值**当证据

账本里躺着现成证据：探索时模型往这个框里写过 `90210` ✓（邮编形状）。
`agent/browser_agent.py` 新增 `_kind_from_recorded_value`：**只在歧义时**（判成 phone 而
`type=tel`）且记下来的值是 **5 位数字**（美国邮编形状）时改判 `postcode` ——
手机形状的值（10~11 位、带括号/横线）不会被误读 ✓（反例进了测试）。
名字（= source）与随机值都跟着这个判断走，于是**运营资料里的 `zip`/`postcode` 对得上了**。

### 2.2 `nearby_text`：**字段这条路上原先一个词都没有**（工具侧的缺口，已补）

`tools/cdp/internal/observe.go`：动作那条一直有 `nearby_text: nearbyText(el)`，
**字段那条没有** ✗ —— 这正是「语义三档的第二档」在字段上永远不触发的原因。
现在字段也填它（同一份 `nearbyText` 实现，不另写一套）。
⚠️ **但实测这一格仍然是空的**：那个 MUI 输入框的兄弟节点都是空壳，
`nearbyText` 拿不到词 → 所以 2.1 那条证据法才是这一格的实际出路（如实记下）。

### 2.3 `cdp form` 的歧义闸（对称性缺口，控制器点名的那条）

`click` 早就有严格门（命中多个 → 拒绝），`form` 一直缺 —— 同一个洞，后果更隐蔽：
**值照样填进去了、回执照样 ok，只是填进了另一个框**。

- 新增 `internal.StrictPick` + CLI 的 `--strict` / `--expect-label`（**默认关**：
  CLI 是 57 个生产脚本的接口，它们的行为一个字节都不能动）。
- 判据三句话，都能说成人话（写进了代码注释）：
  1. 只命中一个 → 照常（这一闸什么都不改）；
  2. 命中多个 → **拿字段自己的身份认它**（`--expect-label` 对着 id / name / aria-label /
     placeholder / 它的 `<label>` 文本 / type 比）；认出**恰好一个** → 用**唯一化**的选择器
     （id，或一路 `tag:nth-of-type(k)`）去填 —— **不是拿原来那条再赌一次**；
  3. 认不出、或认出不止一个 → **大声失败**（非 0 退出、一个框都不填）。
- 产物侧：`CDPHelper.form(...)` 加了**加法式**的 `strict=False, expect_label=""` 两个参数
  （默认值保持老行为），产物填值时一律带上 `--strict` + 这一步的字段名（`FILLS[name].label`）。

## 3. 钉子（10 条）

Go（`tools/cdp`）：
- `TestPickUnique_*` 6 条（纯函数：一个不动 / 零个交回原路 / 多个没身份→拒 /
  身份恰好认出一个 / 身份对不上→拒 / 身份认出不止一个→拒）
- `TestStrictFormPickLandsInTheRightBox`（真浏览器：两框共用 class → 值进**对的那个**、
  另一个**一个字符都没有**；认不出的两种形态都**拒绝且不填**；只命中一个时原样照做）
- **变异验证**：把「没身份就挑第一个」改回去 → 对应用例立刻变红 ✓

Python：
- `test_form_steps_carry_the_strict_gate_and_the_field_identity`（产物带 `--strict` + 字段身份）
- `test_a_tel_field_with_no_words_is_decided_by_what_the_explore_typed_there`（2.1 那条 + 两个反例）
- 替身 common 的 `form` 收下新参数并**单独记**（不动既有断言的元组形状）

## 4. 真站复跑（这一轮，全部修法都在）

窗口新开、探路换了干净窗口（B）、自测跑在干净会话（R-F1）。job `job-817e14417d61`。

**探路**：走到了成功文案（探索那一趟模型自己往 ZIP 框里写的是 `90210` ✓ —— §1 的证据就是
从这儿来的）。

**自测 baseline 的 trace（12 步，一步没失败）**：

```
 1 goto    打开了 /auto-warranty/
 2 click   点了「Accept Cookies」        ← 干净会话里它真的在
 3 click   点了「Get Free Quote」
 4 click   点了「Sedan」
 5 click   点了「Get Matched」（换第 3 个找法）
 6-11      三个组合框 + 选项（2020 / Acura / MDX）
12 click   点了「没写名字的元素」（帧内，换第 2 个找法）；页面没有变化
—— 之后的状态**全部静默跳过**（`when` 不成立）→ 跑完也没见成功文案
```

**差在哪（如实说）**：第 12 步之后的状态没被应用（`when` 判据不成立 → 整组跳过），
于是既没走到后面的题、也没见到成功文案 → `delivered=false`、`forms/sites/` 里没有产物。
**这一格我这一轮没有钉死**：它是「`when` 不成立就静默跳过」那条老链的又一次现身，
与第 17 步（这轮量清并修了的那条）不是同一件事。**不报「走深 N 步」当成功** ——
成功只有一条：成功文案真的出现。

## 5. 顾虑

1. **`when` 不成立就整组静默跳过**仍然是最大的坑（这一轮的 12 步之后就是它）：
   跳过不出声、不计失败、也不进 trace —— 读报告的人只看到「跑完了没成功」。
   建议下一轮专门做它（要么让跳过**出声**（trace 里留一行 + 计数），要么把判据放宽到
   「这一页像不像」的其余证据上）。
2. **`type=tel` 的歧义是站点常态**（邮编框用它弹数字键盘）：这轮靠「探索那一趟自己写的值」
   当证据（5 位数字 → postcode ✓）。若某个站第一次探路就没填对，这条证据也就没了 ——
   那时**没有别的信号**（label 空、nearby_text 空、placeholder 只是例子）。
3. `nearby_text` 在字段上补上了（工具侧），但**这一格实测仍然是空的**（MUI 的兄弟节点是空壳）
   —— 补的是「本该有」的那一份，不是「这一格就有救」。
4. `cdp form --strict` **默认关**（57 个生产脚本的行为不动）；产物一律带上。
   若别的调用方（MCP 门）也想严格，得再给它加参数（这轮没动 MCP）。
