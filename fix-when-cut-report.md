# 修复报告：判据从哪剪（600 字窗口）+ 哪一步算承重

**状态**：DONE_WITH_CONCERNS —— 五处改动都做了、都有用例；真站跑了三趟。
**「出来了、而且过了」的 py 今天仍然没有**（差在哪见 §5，不粉饰）。
**commit**：**没有** —— 全在工作区里（`git status` 见 §7）。
**一行测试结论**：`tests/test_browser_agent.py` → **216 passed / 1 skipped**；
`tests/` 全量见 §6。

---

## 0. 一条根因 —— 今天所有事都长在它上面

`cdp observe` 的正文是**每一帧各切 600 字**再拼起来的
（`tools/cdp/internal/observe.go` 的 `pageText.slice(0, 600)`，**没有截断标记**）。

而生成侧**所有**判据工作都只看得见它：

| 用它的地方 | 干什么 |
|---|---|
| `_snippet` | 剪判据 |
| `when_holds` | 验判据（正例与反面都走它） |
| `_unique_snippet_for` 的 ③ | 重挑时的正例 |

**产物那一侧看的是整页** —— `template.page_signature()` 的 docstring 原话：
「**不裁长度** —— 成功文案可能在第 600 字之后」。

⇒ **两把尺子。** 页头 + 导航把那 600 字占满时（实测 kotzheating 首页那一眼，402 条里
`nav 272 / main 98 / header 20`，**`main` 的第一条排在第 299 条**），剪出来的判据
必然是全站公用文案 ⇒ 撞车 ⇒ IR 拦下整趟。

**全部 118 趟真账本里，`draft_failed` 26 趟；其中 21 趟是「判据分不开页」，
21 趟里有 10 趟判据剪的就是壳文案。**

按形状归（**按趟**，不是按行）：

| 形状 | 趟数 | 站点 | 治了吗 |
|---|---|---|---|
| ① 无障碍跳转链接（`Skip to main content`） | **7** | shorecrestwpb ×5、kotzheating ×2 | 今天这两刀 |
| ② 页面标题（`Auto Quiz - PolicyQuail`） | 2 | policyquail | 今天这两刀 |
| ③ 同一页被判成好几个状态（判据是 JS 源码） | **9** | getfixapro | ✅ 2026-09-30 已治，之后没复发 |
| ④ 页脚 / 公告条 | 1 | zekear | 今天这两刀 |
| ⑤ 其它 | 2 | go（判据是空串）、surepayroll | 没查 |

---

## 1. 拆掉 `_unique_snippet_for` 的 `node_texts_truncated` 开关

**改了什么**：那个函数开头有一句 `if ... or model.get("node_texts_truncated"): return ""`，
整段删掉。

**凭什么**：那一格（`node_texts_truncated`）的**全部意思**是「这份**清单**不是这一页的全部」。
而重挑要的三件事没有一件靠「清单全不全」——

- ① 候选是**单个元素自己的话**：它就是逐条 `_snippet(节点)` 取的，天然满足；
- ② 「别的页上也有吗」比的是**别的页**，跟这一页的清单长短无关；
- ③ 「自己这一页上有吗」当场另比一遍。

那半句是**从 `_snippet_in_one_node` 抄过来的**，而那边防的是另一件事（「这段判据
**不在任何单个元素里**」这个**结论**）。**把两件事当一件事了。**

**真站证据**：kotzheating 首页有 400 个叶子元素 ⇒ 这个开关必然置位 ⇒
`_repair_ambiguous_whens` **一次都没跑过**。全仓 118 趟账本里 `repicked=False` ——
不是它没用，是它**从来没被允许跑**。

---

## 2. 正例那把尺子换成 `_one_page_text`

**改了什么**：`_unique_snippet_for` 的 ③、以及 `_repair_ambiguous_whens` 的正例复验，
从「`page_text` 那 600 字」换成 `_one_page_text`（**正文 + 逐元素正文**，与产物一侧同宽）。
两处**必须同宽** —— 否则「挑的时候说成立、换的时候又说不成立」，那条路只会把整趟白白砍死。

**凭什么**：见 §0。产物那侧不裁，生成侧只看 600 字 = 拿一把比产物**更窄**的尺子验正例。

**⚠️ 只动正例。** 反面（`_gates_that_hold_elsewhere` 里 `when_holds` 比**别的页**）
一个字没改 —— 放宽反面 = 少拦坏判据，那是**另一刀**，没做（见 §7）。
核过 `when_holds` 的六个调用点：另外五个（`:790` 分状态、`:3784` 重放核验、
`:5064`/`:5078` 并页）**全都跑在重挑之前**，作用面是干净的。

**顺带更正**：`when_holds` 的 docstring 写着「判据与产物里的 `_applies` **逐条对齐**」——
那句话说的是**算法**（怎么与、怎么或），**不是取材宽度**。已加一句说明。

---

## 3. 判据改从「这一页自己的话」里剪（`_own_word_nodes`）

**改了什么**：新增按区域筛的办法，`_when_for` 在**剪到的那条落在公用区域上**时改道，
从这一页**自有区域**的第一个元素自己开头剪。

**为什么不用引插件**（用户原话：「有没有啥能提取关键字的插件不用一次性扔给AI的」）：
`observe` **早就**把「标签@区域」逐条带回来了（`node_elements`），
而那是**在浏览器里当场算的纯函数**（`observe.go` 的 `region()`：landmark 标签优先、
class 派生兜底）—— 确定性、零依赖、零模型、零额外调用。

**规则**（`_CHROME_REGIONS` = `nav/header/footer/aside/dialog`）：

> **`body` 要不要丢，看这一页有没有 `main`。** `body` 那一格是**混的** ——
> 没有 landmark 祖先的元素都落到它（跳过链接、cookie 小条都是 `a@body`/`div@body`），
> 而整页没有 `<main>` 的站点，正文本身也在 `body` 里。
> 有 `main` ⇒ `body` 那些就是壳，一起丢；没有 ⇒ 留着（丢正文比留着壳坏得多）。

**真站证据**（`job-c4c0f4ee0ad7`，账本上五条原话）：

> 「kotzheating」这个状态：这一段判据原来剪在**公用那一层**上
> （`'Press Alt+1 for screen-reader mode, Alt+0 to'`，页头/导航那类全站每页都有的）
> ——已经改从这一页**自己**的话里剪：`'Kotz Heating, Cooling and Plumbing'`

**⚠️ 只在「剪到的是壳」时改道** —— 没撞的门判据一个字节都不许变。
改道完顺手也躲开了「跨元素拼接」（取的是单个元素的开头，不是拼的）。

---

## 4. 挑候选时躲开「在自己这页的公用区域里也出现」的（`_also_in_chrome`）

**这是真站当场逮到的**（同一趟 `job-c4c0f4ee0ad7`）：`/contact/` 那道门定成了
`'Contact Us'` —— 它**确实**是从 `h1@main` 剪的（合法），可**同一个字符串也是导航项的标签**：
那一页的逐元素正文里它出现在**两个 `a@nav`** 上。⇒ 这道门在任何带「Contact Us」
导航的页面上都成立，而**没人喊**（反面那把尺子只看得到别的页那 600 字，导航不在里面）。

**补的是局部一问**：这段字在**自己这页**的公用区域里也有吗。免费、当场问得出来。

**不花新提交的验收**（拿那扇开着的窗取的现场模型）：

```
修前： /contact/ 判据 = 'Contact Us'                                    ← a@nav 上的导航标签
修后： /contact/ 判据 = 'When you need help with your heating, cooling' ← p@main 的整句
```

---

## 5. `auxiliary` —— 探路自己就作成过也做败过的**无名**目标，不承重

**现场**（`job-22e8c86afe10` 的账本，逐字）：

```
step 30  click 「没写名字的元素」  ok=True    ← 浮窗那个 ✕，当时挡住了 Submit，顺手关掉
step 45  click 「没写名字的元素」  ok=False   ← 同一趟里**又碰到它、没找着**
```

产物忠实地把 step 30 抄成一步 ⇒ 自测时浮窗没出来 ⇒ 那一步没做成 ⇒
`selftest` 判「**承重步**失败」⇒ **整趟否决**（而产物自己一路跑到成功页、退出码 0）。

**那个元素是谁**（现场在真页面上认的）：

```
#agency_bot_option_2_options
  class     "s-b-container openOptions"      ← 开/收是个状态 —— 这就是"有时候有有时候没有"
  position  fixed      z-index 999999
  里面的文字 "Facebook Messenger / Review Us / Contact Us / Schedule Appointment / Web Chat"
  那个按钮   <button onclick="botWidget.hideOptions()"><img alt="close-icons" ...></button>
             btn_text=""  btn_aria=null       ← 没有任何能叫出它的文字
```

**用户原话**：「浮窗本来就是类似弹窗有时候有有时候没有，不能把它认为不做就继续不下去」。

**规则**（两条缺一不可）：

> **目标没名字**（没有 text / label / placeholder / aria-label）**而且**
> 同一趟探路里**同一目标作成过也做败过** ⇒ 那一步不承重。

**为什么第二条是"量出来的"不是猜的**：它说的是「**这一次探路里它自己就不稳**」。
另一条路（认「这一下点的是不是遮挡物」）**现场走不通** —— `observe` 的 `obstructions`
在那一页上报的是**空**（`[]`），没有可靠来源。

**为什么第一条不能省**：有名字的（`Submit` / `Schedule Online`）是漏斗的一部分，
它失败**必须**照样否决 —— 否则「产物其实没走通」会被判成「过了」。

**落地四处**：

| 文件 | 干什么 |
|---|---|
| `agent/browser_agent.py` | `_not_load_bearing_clicks` 标出那些目标；`states()` 打 `auxiliary: True` |
| `agent/template.py` | 产物把它写进 trace 那一行 |
| `agent/selftest.py` | `_verdict` 分档时认它 —— **不否决那一遍，但照样点名**（与 `scroll`/`wait` 同一档） |
| 测试 | 生成侧正题 + **负控（有名字的不许标）** + 判据侧 |

**验收（全用今天的真数据，没花新提交）**：
把新逻辑跑在 `job-22e8c86afe10` 那 58 步上 ⇒ **正好标中那一步，而且只标了它一步**
（另一处同名按钮从没失败过 ⇒ 不标）。渲染冒烟：STATES 第 680 行带着它，
骨架第 3076 行把它写进 trace。

**⚠️ 它治不了**：浮窗**只出现一次、之后再没碰过**的那种 —— 没有"做败过"的记录，标不出来。
那种得靠 `obstructions` 那条通道，而现场实测它报空（观测层的事）。

---

## 6. 测试与回归

| | |
|---|---|
| `tests/test_browser_agent.py` | **216 passed / 1 skipped** |
| `tests/test_budget_calibration.py` | 9 passed |
| `tests/` 全量 | 见文末（跑完补） |

**新用例 6 条**：重挑在截断页上要跑 / 重挑能从 600 字之外挑 / 判据从自有区域剪 /
**页头本来就是自己的话时一个字节不变（负控）** / **没有 `main` 时 `body` 不丢（负控）** /
**挑候选不许挑自己导航里也有的** / 无名不稳定目标不承重 + **有名字的仍承重（负控）** /
判据侧读 `auxiliary`。

**真站三趟**：

| job | 结果 |
|---|---|
| `job-c8d966155ec6` / `job-53e8caeece5e`（8099，改前） | `draft_failed` ×2 —— **一份 py 都没有** |
| `job-22e8c86afe10`（8100，改后） | **出了 py**（251KB）；自测挂（见 §7） |
| `job-c4c0f4ee0ad7`（8100，第三刀后） | 判据从壳改成了自有区域的整句；停在 `draft` 闸 |

---

## 7. 我没做的 / 为什么

### 7.1 反面那把尺子（用户说的「另一刀」）—— 没动

`when_holds` 比**别的页**时仍然只看那 600 字 ⇒ **拦不住真分不开的**。
今天那趟的产物就是账单：`contact` 那条门钉的 `'Accessibility Screen-Reader Guide, Feedback,'`
**首页也有**（逐元素正文里数过），而 `gate_collisions` 是空的。

放宽它会**多拒一些趟**（诚实但难看），动的是「什么算判据成立」 ⇒ 得人点头。

### 7.2 门的语言加第三格（用户提的「表单填没填」）—— 没做

用户原话：「但是到了联系页有表单填的区别啊。没填的话就没有成功条件啊。」

说得对：那两页**确实分得开**，只是「门」这门语言现在只会说两件事
（`url_contains` / `text_contains`），**不会说「而且表单还空着」**。
而那份数据**已经记着了** —— 2026-10-08 起账本每个输入框都记一格 `empty`。
**数据有，门的语言接不上。** 这是动**产物契约**的一刀（`when` 形状 + `_applies` + skeleton sha）。

### 7.3 「出来的 py 能用」—— **今天没做到**

`job-22e8c86afe10` 那份 py 自测挂在**第 14 步**（今天 §5 那个浮窗），
**但 A 救不了它** —— 我把 trace 重看了一遍：

```
第 14 步  click 点浮窗的 ✕   ok=false   ← A 之后不否决了
第 17 步  form  填「Last」    ok=false   ← ★ 有名字、是正经一步，照样否决
```

**第 17 步是往「Last」输入框填字，没找到框。** 所以那趟自测**仍然不会过**。

⇒ **下一步就是查它**：填不进去是为什么（页面变了？上一步失败导致的？）。
那才是今天真站上唯一挡住"交付"的东西。

→ **查完了，答案在 §7.4**：它不是「填不进去」，是**表单那时已经交掉了**。

### 7.4 第 17 步查完了 —— 不是「填不进去」，是**表单已经交出去了**（2026-10-09 晚，真站复现钉死）

**先看那一行本身**（自测 trace 第 17 步，逐字）：`ok=false`、`selector_used=""`、**`recovery=null`**、
`success_in_page=true`，同时 `progress=true`、页面被滚回顶部、`page_sig` 前 600 字没变而**尾部从
表单内联脚本变成了页脚**。

`recovery=null` 是关键：`_relocate` 只要有**一个**候选过了 `_usable` 就会写 `resolve_why` ⇒
它一个都没匹配上 ⇒ `#gform_12` 连 `#input_12_1_6` **整个不在页面上**（observe 的 `fields` 只收
`vis()` 的：`display:none` / `visibility:hidden` / 0×0 全被滤掉）。三个声明选择器也全失败，
`progress=true` 说明页面确实变了。

**谁交的**：账本里那条「关浮窗」的步骤（`target.text=None`，就是 §5 那个浮窗 ✕）。
重放时浮窗不在 ⇒ 声明选择器失效 ⇒ `_relocate` **只凭 `role`**（把握 0.50）把它认成了
**`#gform_submit_button_12`** 并点下去 —— 表单当场交出去。真站复现逐字（`_repro/repro.trace.jsonl`）：

```
step 14  scroll  把「Submit」滚进了视口
step 15  click   点了「没写名字的元素」（换了第 2 个找法才找到）  sel=#gform_submit_button_12  level=1
                 【恢复】声明里的地址全失效，按意图重找到了它（命中：role，把握 0.50）
         → 成功：判据成立了（页面上那串字 / 网址那一截）
跑完那一刻的页面：url=…/contact/?gf_protect_submission=1   form=false   confirmation=true
                  「Thanks for contacting us! We will get in touch with you shortly.」
```

**结论**：那份 py **能交付**（用站点收的邮箱跑，提交真的成了、确认文案真渲染出来、退出码 0）。
「第 17 步找不到 Last」**是成功之后的余波**——表单已经变成确认文案，框当然没了。
⚠️ 反过来说：成功是**那一下重找点中 Submit** 送出去的（账本里那条「点 Submit」根本没轮到）——
这条既要修（无名 target 不该落到提交控件上），也别当成「本次侥幸」。

**顺带量到的两件事**：

| 事 | 量到的 |
|---|---|
| 邮箱 | `john.smith@example.com` **站点不收**（`The email address entered is invalid.`）。失败态 = 表单还在 + 红字 + **没有确认文案**；而且**产物判据在那状态不会亮**（盯了 64 秒、连点两下 Submit 也试过）。⇒ 喂这种地址的任务**永远过不去**。哪来的：产物 `FILLS['email']['source']` 取的是任务资料的小写 `email`；而探索那趟自己的答案日志里，小写 `email` 填的是 `john.smith@exa…`、大写 `Email` 填的是 `john.smith.kotz@…`（站点收的那个）。那份 `form.json`（`/tmp/siteforge-form-fvmyfngu/form.json`）已经没了，**再生产时留意这一格** |
| 潜在假成功（另案） | `cdp observe` 的 `page_text` 会把**子帧的脚本文本**读进正文：0×0 的帧不渲染 ⇒ `body.innerText` 退化成 `textContent`（`observe.go:1272` 的 `perRoot`），`observe_frames.go:287` 又把各帧拼起来。今天实测合并后的 `page_text` 里带着 `grecaptcha.render(…)` / `botWidget.bot` / `TTDCM`。**这页上漏出来的串里没有成功文案**（量过），所以今天的成功不是它误报；但 `_succeeded()` 读的 `page_signature()` 会把这个 `page_text` 追加进去（`forms/sites/*.py` 的 `page_signature`）—— 换成脚本里带成功文案的站，就会见面就真 |

**今天动了判定那一格**（`agent/selftest.py:_verdict`）：失败那步的 trace 行里
`success_in_page is True` ⇒ 与辅助步**同档**（这一遍仍算过，但**照样点名**，并说清
「失败那一刻页面上成功判据已经成立」）。用例：
`tests/test_selftest.py::test_a_step_that_failed_after_the_page_already_showed_success_does_not_take_the_run_down`
（含两条负控：成功**之前**的失败、显式 `false` 的，照旧否决）。
⇒ 有了它 + §5 的 `auxiliary` 标记 + 站点收的邮箱，`job-22e8c86afe10` 那种趟才会**既出来、又过**。

---

## 8. 两个踩过的坑（工具层面，免得下次重踩）

1. **长跑的进程别从 `ctx_execute` 的沙箱里起。** 我有一趟回归是从沙箱里 `Popen` 的，
   它继承了沙箱的 `TMPDIR`（`/tmp/.ctx-mode-XXXX/`），而那目录在调用结束时**被清掉** ⇒
   测试全爆 `E`。用 Bash 工具起（`nohup … &`）就没事。
2. **`test_budget_calibration.py::test_the_calibration_note_says_it_is_still_unmeasured`
   是一条「防旧话」的守卫** —— 它 grep `browser_agent.py` 里有没有「那是另一件事」这五个字
   （那原是一句已经过时的注的签名）。**写注释时撞词会被它逮到**，而它逮得对 —— 改措辞，别改它。

---

## 9. 工作区

`git status`：**二十来个文件未提交**（今天的五处 + 之前几天的）。
提交前先看 §7 —— 有几件事是「已知没做完」，别让 commit message 读起来像做完了。
