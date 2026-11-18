# Atticus `try.atticus.com/ssd/benefits`：线上失败日志（2026-10-24）

只读。**没动 8099、没动 FMR、没改脚本**。证据全部来自 FMR 的 `fail_diag`（按站点键直接查，
不经过 config 解析）与 `formLogRank`。

## 怎么拉的

```bash
python3 docs/probes/2026-10-24-atticus-benefits/pull_logs.py site   # 按站点列 diag
python3 docs/probes/2026-10-24-atticus-benefits/pull_logs.py show <task_id>
python3 docs/probes/2026-10-24-atticus-benefits/pull_logs.py rank [YYYY-MM-DD]
```

- `GET /api/quest/formLog?site=…` 现在**回 404 `config not found`** —— 因为这个站名下的
  config（**id 162**）是**停用**状态，而该读口只认启用的。日志本身没丢，
  是「按 config 找失败」这条路断了。
- 能用的两个口：
  - `GET /api/quest/failDiag?site=<键>&fdate=&tdate=` —— 按站点键直查，**不需要 config**；
  - `GET /api/quest/formLogRank?date=&min_streak=1&days=7&settle=0` —— 每天的失败站点榜，
    停用的配置也标得出来（`config_status: 停用`）。

## 先说结论

**不是「站点整个改版」，而是循环脚本走进了自己不会走的分支**：

`Who are you seeking disability benefits for?` 这一题被随机挑成
**「Family member or friend」/「Patient or client」** 之后，联系信息那一屏
（`How can we reach you?`）会多出一个**必填的关系下拉**
（`data-testid="relationship"` / `patient-client-professional-role`）。
循环读得到它、但 `fill()` 填不上（`cdp.form` 填 select 后回读不到值），
于是每一轮都只做两件事 —— 「relationship 没填上 ⇒ 下一轮再看」+ 点一次 Next。
Next 一直是禁用态 ⇒ **连点 8 次同一屏 ⇒ 如实报失败**。

探路那版（`2026-10-21-atticus-stop/try.draft.py`）走的是 **Myself** 分支，
那一屏只有 `:r1:`–`:r4:` 四个输入格，**从来没有 relationship 这一格** ——
所以「我们验过的那条路」是通的，「随机走进去的这条」从没被验过。

## 今天（10-24）的两趟，逐行

### task 26807496（at 2026-10-24T04:45:46+08:00，exit=unknown）

```
step 10 [] Who are you seeking disability benefits for? Mys ->  Family member or friend
...
step 38: 填了 :r2: / :r3: / :r5:
step 39: 填了 :r6:
step 39: relationship 没填上 ⇒ 下一轮再看
step 40: relationship 没填上 ⇒ 下一轮再看
step 40: 这一屏可见的东西还没有 ⇒ 再看一眼再决定
step 41: relationship 没填上 ⇒ 下一轮再看
step 41: Next（How can we reach you?）
...（40→48 每轮都是这两句）
step 49: relationship 没填上 ⇒ 下一轮再看
step 49: 连着 8 次点 Next、页面还是那一屏 —— 如实报失败
```

### task 26807011（at 2026-10-24T04:21:00+08:00，exit=unknown）

```
step 11 [] Who are you seeking disability benefits for? Mys ->  Patient or client
step 14 [] Do you already get Social Security retirement pa ->  No, I'm not getting retirement
step 42 [] How can we reach you? ->  I'm a care professional
step 43: patient-client-professional-role 没填上 ⇒ 下一轮再看
step 43: 这一屏可见的东西还没有 ⇒ 再看一眼再决定
...（43→51 每轮都是这两句）
step 51: patient-client-professional-role 没填上 ⇒ 下一轮再看
step 51: Next（How can we reach you?）
step 52: 连着 8 次点 Next、页面还是那一屏 —— 如实报失败
```

两趟前面的题都答得很顺（10 多屏、每屏 1–2 步），**只死在这一格**。

## 另外两趟（今天之外，已处理 / 另一回事）

- **26786002（10-23 08:01）与 26780603（10-22 19:17）**：已经走完全程到
  「We'll reach out in a few minutes about your case.」成功屏，却在之后空转 60 步被判失败
  —— 就是 10-23 16:47 推的 `43fa4bcb`（成功判据补 `post-conversion` 前缀 + 三种正文）修掉的那条。
- **26789962（10-23 12:55 UTC / 20:55 CST）**：zip 填完后停在
  「Hi there! I'm Jackie. Together, we'll se…」这一屏，连点 8 次 Next 页面不动。
  与 relationship 无关（还没走到联系信息屏），暂记为**另一条**（intro 屏点击不生效）。

## 后端状态（顺手量到的）

| 项 | 值 |
| --- | --- |
| `formLogRank` 里这个站的 config | **162 / 停用**（至少从 2026-10-14 起就是停用） |
| `GET /api/quest/formConfig?site=…` | 404 `config not found` |
| `GET /api/quest/formScript?site=…&type=debug` | 200，`status: 0`，sha256 归一行尾后 = `43fa4bcb…`（= 本地 `forms-222/sites/try.loop.py`） |
| 正常读（只认启用） | 404 `script not found` |

⇒ 推送的那一版**在库里**，但这一行**没有启用**；`formConfig` 那条路查不到它，
所以面板上按这个站「查失败」会得到 404（是「量不到」，不是「没有失败」）。

近 11 天每天这个站的失败条数（`min_streak=1` 的榜，只数归到这个 config 的）：

```
10-14  4     10-18  0     10-22  1
10-15  0     10-19  0     10-23  -（当天两趟没归到这份 config）
10-16  1     10-20  0     10-24  2
10-17  0     10-21  0
```

## 下一步（未做，等点头）

1. **8100 上复现关系屏**：打开 `try.atticus.com/ssd/benefits`，在
   「Who are you seeking…」挑 `Family member or friend`，到 `How can we reach you?`
   用 `cdp` 读 `[data-testid="relationship"]` 的真实 DOM —— 是原生 `<select>`、
   还是自定义 listbox（`div[role=combobox]`）。这决定修法：
   - 原生 `<select>`：`cdp.form` 填了却没留住 ⇒ 补「展开 + 点选项」那条路；
   - 自定义 listbox：填值这条路根本不通，得**点开再点那一项**。
2. 另一条更省事的选择：**让循环优先走 `Myself` 分支**（把已验过的那条路作为默认，
   其余选项排后面）。代价是问卷多样性下降，但这条路的成功率是验过的。
3. `26789962` 那条 intro 屏点击不生效，单独再查（先看是不是 hydration 时序）。

## 现场

- 原始日志：FMR `fail_diag` id 756 / 755（今天），751 / 743 / 738（之前）。
- 每次复跑：`pull_logs.py`（本目录，只读）。

## 2026-10-24 11:35 截图推翻了一半结论：**值也填错位了**

`fail_img` 落盘在本目录（`fail-26807496.png` / `fail-26807011.png` / `fail-26789962.png`）。

### `26807496`（Family member or friend 那支）—— 联系信息屏

```
First name : Carter                        ← 这是 last_name 的值
Last name  : larryhandley65@yahoc…         ← 这是 email 的值
Email      : 10001        （红字：Please enter a valid e-mail address）
Phone      : 51           （红字：The phone number you entered is too short）
Relationship to applicant : 空（必填）
```

对日志：那一趟填的是 `:r2:` / `:r3:` / `:r5:` / `:r6:`，而账本里 `:r1:` 是 First、
`:r2:` 是 Last、`:r3:` 是 Email、`:r4:` 是 Phone —— **这一屏的 React 自动 id 整排挪了一位**
（账本里从没有过 `:r5:` `:r6:`），而产物按 id 认格子 ⇒ 每一格都拿到了**前一格**的值。

### `26807011`（care professional 那支）—— 同一屏、另一个形状

```
First name: James   Last name: Carter      ← 这两格对
What is your role?  ◉ I'm a care professional
Relationship to applicant : 空（红字 Required）
```

这一支的 id 没挪，只是多出一个必填的关系下拉，循环填不进去。

### 生产上还有一条同形状的（今天之外）

`26789962`（10-23 20:55）：zip 填完停在「Hi there! I'm Jackie…」那屏，
连点 8 次 Next 页面不动 —— 见下面 12:47 的复现。

## 11:55 用户裁定：**只走本人申请那一支**

于是 `Myself` / `I'm the applicant` 这两条分支是唯一验过的路（那一屏只有 `:r1:`–`:r4:`）。

改动（模板 + 产物两处，先红后绿）：

- `agent/template_loop.py`：`render_loop(..., pick_hints=())` → 产物多一格
  `PICK_HINTS`；`pick_option()` **先看提示**（按文字精确比、大小写不敏感），
  池子里没有才照旧掷骰子（提示是**优先**不是**只认**）。
- `agent/fix.py`：`PICK_HINTS` 进了 `LOOP_KNOB_NAMES` 与 `loop_spec_from_source` 的回读表
  （修站不能把它丢掉）。
- `agent/graph.py::_loop_source`：`pick_hints=list(spec.get("pick_hints") or [])` 透传。
- 产物 `forms-222/sites/try.loop.py`：`PICK_HINTS = ['Myself', "I'm the applicant"]`，
  新 sha（LF 归一）`109ab71f8a0965f2…`、75197 字节。**没推 FMR。**

测试：`test_loop_template.py` + `test_fix.py` **90 passed**、`test_graph.py` **162 passed**
（新用例：`test_a_pick_hint_wins_over_the_dice`、`test_a_product_without_hints_…`、
`test_a_loop_draft_keeps_its_pick_hints_when_read_back`、负控一条）。

## 12:47 真站复验被**另一条**挡住：zip 屏的 Next 一直是禁用态

8100 上连跑三趟（`runtime/selftest/manual-atticus-loop-20261024-*`）：

| 趟 | 结果 |
| --- | --- |
| 034221 | `landing/default` 的 zip 屏：填了 `10001`、Next 禁用、点 8 次不动 |
| 034446 | 过了 landing，卡在 **`survey/claim/zip`**：同样 `#zipcode=10001`、Next 禁用 |

现场（第二趟窗口）：

```json
{"url":"…/survey/claim/zip","btn":[{"t":"Next","d":true}],
 "inputs":[{"id":"zipcode","ph":"ZIP code","v":"10001"}]}
```

关键量证（同一个窗口里手工做的）：

- `cdp form '#zipcode' --value 90210` 回 `{"ok":true}`，**值没变**（还是 10001）；
- 用 JS 走原生 setter 写值 + 补齐 `keydown/keypress/input/keyup/change/blur`
  ⇒ **`Next` 立刻从禁用变可用**（`d:false`）、值变成 90210。

⇒ 页面本身没坏，是**填值那一下没进到 React 的状态**（hydration 前后那一类）；
而循环的回读只认 **DOM 的 value**（值是留住的），于是它以为这一格填好了，
**再也不会补发一次事件** —— 与 10-22 那条「值被页面弄丢」不是同一个形状
（那条是值没了，这条是值在、状态没进）。生产 `26789962` 就是这个形状。

**没修**（用户这一轮只裁到「只走本人申请」；这是另一条根因）。

## 13:05 zip 那条的根因（量到了）

在 8100 的窗口里把「填值 → Next 禁用」拆开量，结论是**站点的 zip 查询请求被中止了**：

```
performance.getEntriesByType("resource")
  → https://api.atticus.com/geo_locator/benefitsByZip?zip=10001   responseStatus: 0   (2507ms)
```

同一次窗口里手工补一刀（`cdp form` 用**同一个值**重打一遍）：

```
before : {"value":"10001","disabled":true}
form   : {"ok":true}
after  : {"value":"20002","disabled":false}     ← 3 秒后 Next 变可用
resources → benefitsByZip?zip=20002  200 (1246ms)
```

⇒ **不是选择器、不是没填进去**：value 是留住的（回读也说填好了），但站点那一次查询
**没拿到回话**（`responseStatus: 0`，多半填值发生在 hydration 前后被中止），
app 就一直把 Next 禁着；而循环只认 DOM 的 value ⇒ 再也不会碰这一格 ⇒ 干点 8 次收摊。
线上 `26789962`（10-23 20:55）就是这个形状。

## 13:10 修法：Next 是禁用态 + 格子有值 ⇒ **把值重打一遍**

模板与产物各三处（先红后绿）：

- `READ_PAGE_JS` 多交一格 `nextDisabled`（`nextVisible && !nextEnabled`；
  `nextEnabled` = 命中「下一步」的那些元素里**至少有一个没 disabled**）；
- `_nudge(page)`：Next 禁用 + 输入格都有值时，把每一格**用同一个值重打一遍**
  （`fill()` → `cdp form`），一屏（题面 + 地址）只打一次，打不动就照旧点 Next；
- ③ 那一支：`if page.get("nextDisabled") and self._nudge(page): continue`。

测试：`test_a_disabled_next_with_filled_fields_gets_the_value_retyped`（先红后绿），
并断言 `DEFAULT_READ_JS` 里有 `nextDisabled`。
`test_loop_template.py + test_fix.py + test_graph.py` **253 passed**。

## 13:20 真站复验（8100）

`runtime/selftest/manual-atticus-loop-20261024-042811`：**rc=0**，76 步，
最后落在 `survey/rejection/ssi-initial`（「We're sorry.」= 用户裁定的第二种成功）。

- `step 14 … Who are you seeking disability benefits for? … -> Myself` —— **PICK_HINTS 真生效**；
- 这一趟 zip 屏**没**触发中止（一次过），所以 `_nudge` 没被真站踩到
  —— 它由单测钉住，真站上属于「下次再遇到就自动补一刀」。

## 13:25 推 FMR

```
LOCAL  77728 bytes  sha256 d85ede81d98db37639b114650e43eb7a50f63a86ce2e19cc69167aa60ef9ce89
WRITE  ok / status 200 / success（后端回的新指纹 d85ede81d98db376）
AFTER  sha256 d85ede81d98db376…  MATCH_LOCAL True  MATCH_BACKEND_REPORT True
AFTER  status: 0（**停用**）—— 正常读仍 404 `script not found`
BACKUP docs/probes/2026-10-22-atticus-loop-limit/fmr_before_4cd8562eeda5.py
```

⚠️ 推完这一行仍是 `status: 0`、config 162 仍是停用 —— **启用是另一个动作**
（`POST /api/quest/formConfig/status`），这一轮没有做。

## 2026-10-24 14:45 失败（task 26826628）与 zip 修复

用户报「依旧是失败，14:45:13」，并给了失败截图：**ZIP 第一屏**，`#zipcode=10001`，Next 灰着。

### 结论：不是认不出，也不是等太短

- 顺序确实变了：ZIP 现在是第一屏（`survey/claim/zip` / `claim/zip-first-state`），
  但脚本**认得出** `#zipcode`——日志 step 1 就是「填了 zipcode」，截图里值也是 10001。
- 真站量证（同一屏，`probe_zip_timing.py`）：
  - `cdp form '#zipcode' --value 10001` → **45 秒** Next 一直禁用；
  - 原生 setter + `keydown/keypress/input/keyup/change/blur` 写 `90210` → **1–2 秒** Next 变可用；
  - `api.atticus.com/geo_locator/benefitsByZip?zip=90210` 约 **1.7 秒**返回。
- ⇒ `cdp form` 只改 DOM 的 value，没进 React state；14:45 日志里的「重打 zipcode」用的还是
  `cdp form`，所以重打也没用。**等待不是主因**（等 45 秒也没用）。

### 修复（本地 `forms-222/sites/try.loop.py`，sha `c847e829…`）

- `_nudge` 从 `cdp form` 重打改成 `_set_value_events`：原生 setter + 全套事件，
  穿透 shadow DOM；事件发完最多等 5 秒让 Next 变可用。
- 初始填充路径不动（`_nudge` 只在「Next 禁用 + 格子有值」时触发）。

### 验证

- `probe_zip_timing.py`：上面的 45s vs 1–2s 量证；`survey/claim/zip` 直接可开。
- `probe_nudge_method.py`：直接调 loop 的 `_set_value_events`，`nextDisabled` 从 True 变 False。
- `run_loop_zip.py`：用修好的 loop 从 `survey/claim/zip` 真跑一趟 →
  `survey/rejection/ssi-initial`，**80 秒，RUN_DONE ok=True**。
  ⚠️ 这趟正常填充后 Next 已可用，`_nudge` 分支没被日志踩到；它由上面的同屏量证钉住。
- 线上推前（`e5aaf969`，78913 字节，CRLF）与本地补丁前（`d85ede81`，77728 字节，LF）
  **归一化 CRLF 后逐字一致**，diff 只有这一刀。

### 推 FMR（`push_fmr_zip_fix.py --apply`）

```
BEFORE  try.atticus.com/ssd/benefits  sha256 e5aaf96927866314…  78913 bytes
WRITE   status 200 / success / config_id 162 / created:false
LOCAL   forms-222/sites/try.loop.py   sha256 c847e8294e09f9e2…  81417 bytes
READBACK                               c847e8294e09f9e2…  逐字节一致
AFTER   config 162 sites 1  [{site: try.atticus.com/ssd/benefits, config_id: 162, status: 1}]
底稿    fmr_before_e5aaf969.py
```

站点行本来就是 status 1；这次只改脚本，没动 `formConfig/status`。

## 2026-10-24 17:xx Age-first 兼容性（顺序换回来也能跑）

`probe_age_first.py` 开 `landing/default`（这趟正好是 Age 变体）实测：

```
INPUTS [["\u0004:Rcrlbd7rufkq:", ""]]
FILL_ORDER ['age', 'age-2', 'postcode', 'first_name', 'last_name', 'email', 'phone']
FIELD '\u0004:Rcrlbd7rufkq:' -> value_for = '52'      # 年龄，不是 ZIP
```

ZIP-first 的 token `\u0004zipcode` 直接命中 `postcode`（`#zipcode`）→ `value_for` 给 10001。
⇒ 循环**不靠固定顺序**：每轮读当前屏，`FILLS` 里 age/postcode 都在，谁空填谁；
`FILL_ORDER` 只在 token 认不出时兜底（第一格就是 age）；`_nudge` 按「Next 禁用 + 格子有值」
触发，与是哪一格无关。

唯一脆弱点：若某格 `id` 变成不可读的 React 自动 id、且不在 `FILL_ORDER` 对应位置，
位置兜底可能填错。当前两个变体都不触发（ZIP 有稳定 `id=zipcode`，Age-first 的账本顺序
第一格是 age）。
