# Atticus：SAME_NEXT_LIMIT 3 → 8 后重跑循环型

2026-10-22 09:52–09:58（CST）。只在 **8100（siteforge-222，Bit 192.168.1.222）** 上跑。
改动只有一处：`forms-222/sites/try.loop.py` 的 `SAME_NEXT_LIMIT = 3` → `8`
（原文件备份在本目录 `try.loop.before.py`）。**没有推 FMR、没有动 8099。**

## 结论先说

调大常量**没有害处，也没有把最后一屏打开**：两趟分别卡在另外两个缺口上。

| 趟 | 窗口 | 走到哪 | 停因 |
| --- | --- | --- | --- |
| 1（`fresh-run.*`） | `fresh_open()` 干净窗口（清 cookie/缓存） | 一直停在 landing/default（ZIP 屏） | ZIP 填值**没落到页面上**（value 为 `""`），Next 禁用；连点 8 次 Next 收摊 |
| 2（`repro-warm.trace.jsonl`） | 同一个窗口、预热后 | **`claim/age-zip-first-seeking-benefits-for`**（「Who are you seeking…」） | 选项是 `<label><input type="checkbox"></label>`，读取器把这种 label 全跳过 ⇒ 连着 60 步「没有可动作的东西」 |

第 2 趟是**昨天 20:14 那趟没到过的那一屏**（昨天停在 `Disability benefits provide monthly checks` 的
空转上）。也就是说：常量调大之后循环型能往下走，但落地页的填值和这一屏的选项识别各有一个真缺口。

## 趟 1：填值没落上去（`form` 仍报 ok）

`fresh-run.log` / `fresh-run.trace.jsonl`：

- 01:53:46 到 `landing/default`（ZIP 变体，页面下方还有 FAQ 手风琴）
- 01:53:48 `step 1: 填了 zipcode`（trace `ok=true, progress=false`）
- 跑完用 cdp 读同一页：`document.querySelector('#zipcode').value === ""`、
  `Next` 的 `disabled === true`
- 01:53:49 `step 2` 把 FAQ 项「What is Social Security Disability?」当成一题点了（假选项）
- 01:53:54–01:54:08 连点 8 次 Next（按钮一直是禁用态）→ 收摊

手工对照：同一个窗口里用 `cdp form '[id="zipcode"]' --value 10001` 再等 ~3 秒，
value 留住、Next 变可用。⇒ **不是选择器错**，是「填了但没留住」这一类：
`_filled_ok()` 只看 `cdp form` 的回话，**不回读 value**（已知缺口，昨天 README 第 4 条）。
干净窗口（冷缓存）更容易在 hydration 完成前把值写进去、随后被 React 覆盖掉。

## 趟 2：label 包着 checkbox 的选项被跳过

`repro-warm.trace.jsonl`（同一窗口，先导航到入口，再起产物）：

- 4–6 秒：到 ZIP 屏；`#zipcode` 填 `10001` 留住，Next 在 ~7.5 秒变可用
- 8–9 秒：点 Next → ZIP 屏之后的页 → Age 屏（`fill :r0:`）
- 10.5 秒：next 之后 URL 变成
  `.../survey/claim/age-zip-first-seeking-benefits-for`
- 之后从 step 10 到 step 69：`没有可动作的东西` ×60 → `IDLE_LIMIT` 收摊

那一屏的选项（cdp 实测）：

```html
<label class="css-11t1xnh"><strong data-mask-text="true">Myself</strong>
  <div class="css-13rl97k"><input type="checkbox"></div></label>
```

读取器里那条规则（2026-09-26 加的）：

```js
if(e.tagName==='LABEL'&&(e.htmlFor||e.querySelector('input,select,textarea')))return;
```

它本来是为了不把「📬 Email — where your blueprint is sent」这种**输入框自己的 label**
当成选项点掉；但这里 label 包着的是**选项本身的 checkbox**，于是三个选项全被丢掉。

手工复核：`label.click()` 之后 `input.checked === true`，并且页面上出现可用的 `Next`。
⇒ 这一屏不是「没有可点的东西」，是读取器的 label 过滤**收得太宽**。

## 另外发现（暂未处理）

- landing 页的 FAQ 手风琴按钮（`What is Atticus?` / `How does this work?` …）被当成**选项**，
  第 2 步会先点掉一个假选项；不影响最终推进，但会浪费一步、并污染 `answered_sig`。
- 趟 1 的 ZIP 填值问题在趟 2 没复现（暖缓存）⇒ 是时序/缓存相关的，不是稳定必现。

## 下一步（按证据排）

1. **`form` 之后回读 value**，没留住就重填（顺带等 Next 从禁用变可用）—— 趟 1 的真因。
2. **label 过滤收窄**：只有 label 指向/包着**文本类**输入（`input:not([type=checkbox]):not([type=radio])`、
   `select`、`textarea`）才跳过；包着 checkbox/radio 的 label 是选项，收。
3. （可选）FAQ 手风琴不当选项。
4. 若 1、2 修好，再把 `SAME_NEXT_LIMIT=8` 的价值在「monthly checks」那屏上验一遍
   （那屏要 2–3 秒才换页，昨天的 3 次闸正是卡在这里）。

## 现场

- 窗口留在 `claim/age-zip-first-seeking-benefits-for`（Bit 窗口会自动到点，不用手工关）。
- 证据：`fresh-run.log`、`fresh-run.trace.jsonl`、`repro-warm.trace.jsonl`、`rerun_loop.py`。
- 产物改动在 `forms-222/sites/try.loop.py:222`（bind mount 进容器，`/opt/siteforge/forms/sites/try.loop.py` 已生效）。

## 2026-10-22 10:07 修完两个缺口后的干净窗口重跑（`fixed-run.*`）

Fixes（模板与产物同三处）：

1. `fill()` 之后**回读 value**（`_fill_and_check` / `_read_back`；0.4s 后读，没留住重填，最多 3 次）；
2. 页面读出来**这一格又空了** ⇒ 抹掉 `_filled` 里「填过」的记号，用同一个值再填一次；
3. label 过滤收窄：只有 label 指向/包着**文本类**输入才跳过，包 checkbox/radio 的是选项。

结果：**两个缺口都验过了**，而且比之前都走得远：

- step 1–2 连填两次 `:Rcrlbd7rufkq:`（第一次没留住、回读抓到了，第二次留住）→ Age 屏过了；
- step 10 `Who are you seeking disability benefits for?` → 点了 **`Family member or friend`**
  （label 包 checkbox 的选项，收进来了）；
- 之后 `No, never`（applied SSDI）→ **`Yes`（Did you ever hire a lawyer…）** ⇒ 落到
  `survey/rejection/legal-representation`，正文：

  > We're sorry. We can only help people who haven't had a representative for their claim before.

- 这一屏没有题/Next（只有 `Atticus Logo`、`Close quiz`），循环如实空转 60 步收摊（81 步）。

**新卡点不是读取层，是「答什么」**：修站那一版（`2026-10-21-atticus-stop/try.draft.py`）
在 `legal_representation` 点的是 **`No`**（探路那趟是这么过的），而循环型按 `PICK='random'`
随机挑到了 `Yes` ⇒ 被站方判不合资格。循环型现在**没有**「按账本答案挑」这一层。

下一步候选（未做）：

- 把探路账本里每道题**真选过的答案**带进循环型（例如 `PROVENANCE.answers` /
  `PICK_HINTS`：题面/选项能对上就优先，不然再随机）——这是让循环型能走完整条合格分支的关键；
- 顺带把 FAQ 手风琴项（`Is my information secure?` / `Why do we need to know?`）从选项里滤掉，
  它们不是题，点了只是展开/收起。

证据：`fixed-run.log`、`fixed-run.trace.jsonl`；模板改动在 `agent/template_loop.py`，
测试 `tests/test_loop_template.py`（新增 3 条，47 passed）。

## 2026-10-22 10:47 用户裁定：不合资格也是成功

用户原话：「其实这种不合资格的也算成功也就是有两种成功一种是随机回答刚好有资格，
一种就是现在没资格。」

产物 `SUCCESS_URL_MARKERS` 补上拒绝分支：

```python
SUCCESS_URL_MARKERS = [
    ".../survey/qual/post-conversion-ah",   # ① 随机答到合格分支
    ".../survey/rejection/",                # ② 答到不合资格（拿到明确结论 = 跑完了）
]
```

干净窗口重跑（`success-run.*`）：**rc=0**，step 19 `SUCCESS(url): .../survey/rejection/legal-representation`，
总共 21 步收尾。两种成功都成立。

注意：这一条目前只落在**产物**上（`forms-222/` 被 gitignore）；生成器
（`agent/graph.py::_loop_source`）的 `success_urls` 仍只来自 intake —— 重新生成这一站时
要把拒绝网址一起给，否则会退回「只有合格分支算成功」。

## 2026-10-22 10:51 success_urls 支持多条（载荷 → 图 → 产物）

用户同意把上面那条打通，改动四处（**同一个解析器**，不再各写一份）：

- `agent/service.py`：`RunRequest.success_urls` 收 `str | list[str]`；
  `_success_urls_text()` 归一成**一行一条**的字符串（列表也收、逐行 strip、去空）。
- `agent/browser_agent.py::wanted_urls`：按换行拆（单条 URL 不受影响）。
- `agent/template_loop.py::render_loop`：改用 `wanted_urls`（与固定步骤表同一份解析）。
- `agent/console.html`：`#runSuccessUrl` 从 `<input type=text>` 改成 `<textarea rows=2>`，
  提示「多条：一行一条」。
- 测试：`test_success_urls_can_carry_more_than_one_marker`、
  `test_two_success_urls_can_travel_in_one_field`、`test_success_urls_takes_a_list_or_several_lines`。

于是重新生成 Atticus 时，面板那一格写成两行：

```
/survey/qual/post-conversion-ah
/survey/rejection/
```

产物就会同时认两种成功。

## 2026-10-22 13:46 合格分支实测（`qual-run.*`）

干净窗口一趟：**rc=0**，52 步，最后停在

```
https://www.atticus.com/disability-benefits/n/get-help/survey/qual/post-conversion-ah
标题头：We’ll reach out tomorrow about your case.
```

路径：step 42/44 填 `:r1:`–`:r4:`（联系方式四格）→ step 43 答「I'm the applicant」→
step 46 点 **Submit** → step 48 `SUCCESS(url)`。这是一次**真提交**（产物本来的行为）。
⇒ 两种成功（合格 + 拒绝）现在都在真站上各验过一遍。

## 2026-10-22 13:52 推 FMR（`push_fmr.py`）

用户确认「拒绝分支也算成功」后推送。key 是页面 URL：

```
try.atticus.com/ssd/benefits
```

- 旧值（10-13 · 27 状态表 · status 0）：sha `8d507d3668a7…`，备份 `fmr_before_8d507d3668a7.py`
- 新值：sha `32146f9107d17497…`，72813 字节
- 写入回执 `ok / status 200 / success`；**回读 sha 与本地逐字节一致**；`SAME_NEXT_LIMIT = 8`
  和 `survey/rejection/` 都在
- ⚠️ 推完 `status` 仍是 **0**（`config_status: null`）—— 与 10-16 的口径一致：
  **已存在的停用行不会因为上传就启用**。要上线得在面板手工启用。

## 2026-10-22 13:58 按用户口径再推一次（电话取资料 + 年龄随机）

用户澄清：**电话肯定要取资料**（「别取资料」只是个别特殊脚本的处理）；邮箱等资料都有；
**年龄要随机**。改动：

- 产物 `try.loop.py`：`_is_phone` 那一支先查 `form_data`（资料），账本/池子只当兜底；
  `EXAMPLES["age"] = EXAMPLES["age-2"] = random.randint(50, 64)`（每趟一个值，两格共用；
  资料真给了 `age` 仍以资料为准）。
- 模板 `agent/template_loop.py`：**默认改成资料优先**；特殊脚本用 `prefix` 或
  `from_profile: false` 点名「现编」；年龄格默认 `50–64` 随机（资料给了就听资料的）。
- 测试：`test_loop_template.py` **49 passed**（手机那条改成「默认取资料 / 点名才现编」，
  新增年龄随机一条）。

再推 FMR：新 sha `6b5bd19130c91c2b…`，73135 字节；回读 sha 一致（这次没有 CRLF 差异）。
**关键变化：正常读（`allow_disabled=False`）能读到了，`form_config_sites` 里这一行
`status: 1`（已启用）** —— 与第一次推完 `status: 0` 不同。第一次的 `enable` 可能是晚一步生效，
每次推完都要用正常读 + sites 列表复核。

⚠️ 生产 `--form-file` 的键要叫 `first_name` / `last_name` / `email` / `phone`（FILLS 的键）；
对不上时它会退回 `EXAMPLES`（James / Carter / james.carter.58@example.com / 2125550147）。
⚠️ 后端读回会把 LF 换成 CRLF（1112 行 ⇒ 多 1112 字节，sha 变）—— 比对要按行尾归一。



## 2026-10-23 08:00 真单假失败（`26786002`）：合格屏有第二个网址

用户报「8 点多那趟到成功页了还判失败」。FMR `failDiag` 实读（`site=try.atticus.com/ssd/benefits`）：

- `task_id=26786002`，`at=2026-10-23T08:01:30+08:00`，机器 `192.168.1.142`，`exit=unknown`
- `fail_img` = `.../screenshot/fail/2026/10/23/6adaa3db1a45e.png` —— **图上就是成功页**
  （「Your chances of qualifying: HIGH / We’ll reach out in a few minutes about your case.」）
- 日志：step 44 填完联系方式 → step 45 起正文已是成功页，之后 **60 步「没有可动作的东西」** →
  `连 60 步没有可动作的东西（答过 15 题）—— 如实报失败`。
  **没有第二个提交**（提交发生在 step 43 的 Next，之后只是空转）。

### 真因：合格屏的网址随「客服在不在上班」变

从站方静态资源读到的路由（`static.atticus.com/ssdi-screener/production/_next/static/chunks/layout-42e8ba4012677484.js`）：

```js
"contact/info": w.contactInfo([
  w.to("qual/post-conversion-ah", e => { let {isBusinessOpen:t}=e; return !t; }),
  w.catchall("qual/post-conversion")
], …)
```

同一份 postConversion 组件（`chunks/6946.7fef09e3438c42b3.js`）按 `isBusinessOpen` 写标题：

```js
"We’ll reach out ".concat(e?"in a few minutes":t?"tomorrow":"Monday"," about your case.")
```

⇒ 上班时间落 `.../survey/qual/post-conversion`（**没有 `-ah`**）、标题是「in a few minutes」；
下班才落 `-ah`、标题才是「tomorrow」。那趟 08:00 CST = 美东 20:00（上班），
而产物里两处判据只写了 `-ah` 与「tomorrow」⇒ **两处全落空** ⇒ 空转到 IDLE_LIMIT。

对照：2026-10-22 13:46 那趟 qual-run（CST 13:46 = 美东 01:46，下班）才落在 `-ah`。
所以不是站方变了，是**我们只验过下班那一种**。

### 修法（产物 `forms-222/sites/try.loop.py`）

```python
SUCCESS_URL_MARKERS = [
    "https://www.atticus.com/disability-benefits/n/get-help/survey/qual/post-conversion",  # 前缀：-ah 与不带都收
    "https://www.atticus.com/disability-benefits/n/get-help/survey/rejection/",
]
SUCCESS_TEXTS = [
    "We’ll reach out in a few minutes about your case.",   # 上班
    "We’ll reach out tomorrow about your case.",           # 下班/明天开
    "We’ll reach out Monday about your case.",             # 下班且明天不开
]
```

**不**收成 `/survey/qual/`：同一张路由表里还有 `qual/post-alj` 这种**联系信息之前**的过渡屏，
按整个 `qual/` 收会把「还没提交」判成成功。

### 验证

- 旧值先红：`post-conversion`（不带 -ah）→ `False`、`in a few minutes` → `False`（08:44 量）
- 新值：两种网址 + 三种正文全 `True`；`py_compile` 通过
- 真页面：在 8100 打开 `.../survey/qual/post-conversion` 让产物自己 `read()` →
  `url` 与 `head` 都命中 `success(...)=True`（08:46 量；当时美东下班，所以正文是 tomorrow，
  网址判据是这一修的主路）
- 改前 08:41 那趟完整重跑落在 `survey/rejection/legal-representation`，仍 `SUCCESS(url)`、rc 0
  ⇒ 拒绝分支没被这次改动碰掉

### 推 FMR（16:47，用户点头后）

`push_fmr.py --apply`：旧值 sha `6b5bd19130c9…`（备份 `fmr_before_6b5bd19130c9.py`）→
新值 sha `43fa4bcbe1094042…`、74200 字节；写入回执 `ok / 200 / success`，
**回读 sha 与本地逐字节一致**；`form_config_sites` 里这一行仍是 `config_id 162 / status 1`。

### 注意

- 这一修**只在产物里**；重新生成这一站时，`success_urls` 那格要写
  `/survey/qual/post-conversion`（前缀，别写回 `-ah`）+ `/survey/rejection/`，否则会退回去。
- 08:43 之后 8100 上留着一个 Bit 窗口（`ws://192.168.1.222:61129/…`），停在
  `.../survey/qual/post-conversion`，用完可关。
