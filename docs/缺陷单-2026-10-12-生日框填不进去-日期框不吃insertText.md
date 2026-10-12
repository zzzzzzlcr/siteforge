# 缺陷单（2026-10-12）：生日框填不进去 —— 原生日期框**对 `insertText` 静默丢弃**，而命令照样回「成功」

**一句话**：`cdp form --value …` 的普填那一路是「清空 → 逐字符 CDP `Input.insertText` → blur」，
而**原生 `<input type=date>` 对 insertText 静默丢弃**（只认 `.value` 赋值或按段落的键盘事件）
—— 打字那一下**一个事件都不发**、值恒空；命令又**不回读**，于是 stdout 照样是 `{"ok":true}`。
探索那一趟模型在这根框上试了 **7 种写法、29 步**，每次都"成功"、每次都空。

> ⚠️ 运营的反馈「生日那个组件好像是**需要点击**的，直接填写不行」——**点击不是解**：
> 真站实测点一下之后 DOM 里没有任何日历层（原生框的日历是浏览器自己的弹窗），
> 紧接着那条 `form` **照样空**，还会把框里已有的值**先清掉**。真正的原因是上面那句。

---

## 一、现场

| | |
|---|---|
| job | `job-9e2250b60c19`（8099 生产，10:40:56–10:43:14 CST，45 步 / 138 秒，**被人在第 46 步喊停**） |
| 站点 | `nitrouscompetitions.com/auth` 注册屏（点「Create account」之后那一屏）的「Date of birth」 |
| 那根框 | **原生** `<input type="date" required autocomplete="bday">`，无 id，React 控的（元素上挂着 `__reactProps$…`） |
| 账本里 | 第 21/29/31/33/37/41/44 步共 **7 次**填它，值分别是 `1990-05-14`×3、`05/14/1990`、`05141990`、`14051990`、`14/05/1990`；**回执全 ok=true，eval 读回全是 `dob: ""`** |
| 模型自己记的 | 「DOB didn't take (it's scrolled out of view). Scrolling it in, then filling.」——它把原因猜成了"没滚进视口/写法不对"，两种都不是 |

## 二、量到的证据（都在盘上，可复跑；222 真窗口）

**① 旧二进制、真站、同一根框**（`runtime-222/_verify_dob_fix.py`）：

```
旧（线上那份 df69a356）：cdp form input[type=date] --value 1990-05-14
    rc=0  stdout={"ok":true}     读回 value=""       reactPropsValue=""
```

**② watcher（给那根框装的事件记录，`_probe_dob_nitrous2.py`）** —— 这是根因那一条：

```
cdp form --value 1990-01-01 之后，框上收到的全部事件：
   [input,  t=314ms, ""]     ← 清空那一下（buildClearJS）
   [change, t=842ms, ""]     ← blur（buildBlurJS）
打字那 10 个字符：**一个事件都没有** ⇒ 值恒空
```

**③ 负控**：同一屏的**文本框**（Full name）用同一条 `cdp form` 填 —— **成功**（`Harriet Clarke`）。
⇒ 不是页面的事、不是整条路的事，**只在这根框的 type**。

**④ 「先点一下」不成立**：点完 DOM 里没有任何日历层（`[role=dialog]` / `class*=calendar` / `class*=picker]`
一共 0 个）；点之后框里的值**还在**，紧接着那条 `form` 的**清空那一步**把它抹掉、打字还是没进去。

**⑤ 正路（也是修法）**：原生 setter + input/change ⇒ 值进去；**React 自己的 props 也认**
（`__reactProps.value` 同步变成 `1990-01-01`），再逼一次重渲染（去填 Postcode）值**还在**。

## 三、根因

- 落点：`internal/form.go::FillText` 的普填路 —— `buildClearJS` → 逐字符 `c.insertText(string(ch))` → `buildBlurJS`。
- Chromium 的 `Input.insertText`（IME 提交那条路）**进不了日期框**：那几型控件的值是「年月日三段」的编辑模型，
  只吃**键盘事件**或**程序性赋值**。实测证据就是上面②的事件记录。
- 命令**不回读**（`cmd/form.go` 只在 `FillText` 返回 error 时失败）⇒ 空枪也是 `{"ok":true}`。
- 后果的传染面：**产物 py 与农场重放走的是同一道门**（`cdp form`）——凡目标站点用原生日期框的产物，
  这一格会一直是空的；`required` 当场挡住提交，看起来像"重放不过"。

## 四、连带撞见的第二个毛病（同一条路，独立）

账本里那根 `form:has(input[placeholder="A friend's code"]) input[type=date]` 里的**撇号**被
**原样**塞进 JS 单引号串 —— `internal/client.go` 有两处（`GetElementCenter`、`ScrollIntoView`）
漏了 `escapeJS`，别处都套了 ⇒

```
Error: scroll mouse wheel failed: JS exception: SyntaxError: missing ) after argument list
```

**这正是那单第 16、18 步的报错原文**（`_probe_dob_nitrous.py` 照原样复现过）。代价不止"那两步没做成"：
模型因此**弃掉那根好选择器**，改用 `body:nth-of-type(1) > … > label:nth-of-type(4) > input:nth-of-type(1)`
这种位置链 —— 后来 lint 点名拦「只有一根随时会漂的地址」，拦的正是那串。

## 五、已修（`tools/cdp`）

**落点一：`internal/form.go::FillText` 多一条岔路**（`fieldKindJS` 一次就问清目标是什么框）：

- 目标若是原生 `input[type=date|time|month|week|datetime-local]`（`typedValueInputs`）⇒ 走
  `fillTypedValue`：**先换算线格式**（`date` 要 `YYYY-MM-DD`；认 `YYYY-MM-DD` / `MM-DD-YYYY` / `MM-DD`，
  **斜杠与连写一律拒** —— `05/06/1990` 这种猜错就是静默填错，宁可不填）→ **算不出就在碰框之前失败**
  （一个字都不动）→ JS 里用 `HTMLInputElement.prototype` 上那个**原生 setter** 设值 + `input`/`change` + blur
  → **回读**，和想设的不一样就**非 0 退出**（浏览器对收不下的值是静默 sanitize 成空串）。
- 其余 target **一个字没变**，还是原来那条普填路（57 个生产脚本 + 68 个农场脚本照旧）。
- `isDatePickerJS` 改名 `fieldKindJS`（它现在答两件事：是不是 MUI 那型"假日期框"、以及这根框自己的 `type`）。

**落点二：`internal/client.go` 两处补 `escapeJS`**（`GetElementCenter`、`ScrollIntoView`）——
对没有引号/反斜杠的地址是恒等变换，零回归。

**这两刀都只动二进制 ⇒ 既有产物、农场 68 个脚本、ic 那版 py 一个字都不用重出。**

## 六、用例（先红后绿）

- 夹具 `internal/testdata/date_input.html`：无 id 的原生日期框 + 同页文本框（对照）+
  **React 值追踪器的照搬**（`inputValueTracking` 那几行：实例上 defineProperty 了 `value`，
  用 `el.value = x` 写的话 React 会当成"没变"、把那次 input 事件丢掉）。
- `cmd/form_date_input_e2e_test.go`：① 判决 —— 填 `1990-05-14` ⇒ rc=0、值真进去、**React 记的值也跟着变**；
  ② 对照 —— 同页文本框照旧能填；③ 反例 —— 填 `not-a-date` ⇒ **非 0 退出**、说清是哪个值、**一个字没动**。
  红的时候断言原文：`值没进日期框：dob= 想要 "1990-05-14"`（rc 是 0 —— 空枪那一半也钉住了）。
- 夹具 `internal/testdata/quoted_selector.html` + `cmd/selector_with_quote_e2e_test.go`：
  撇号在 `:has(...)` 里、与真站那条同形；scroll / click / form 三条路都要求成。
  红的时候 stderr 原文就是真站那句 `SyntaxError: missing ) after argument list`。
- `internal/form_test.go`：`typedWireValue` 的换算表（含"斜杠/连写必须拒"）+ 另四型原样过。

**套测**：`go test ./...` 全绿（cmd 340.959s、cmd/mcp、internal、internal/mcp）。

## 七、真站验收（222，新旧同屏，`runtime-222/_verify_dob_fix.py`）

| | 旧（df69a356） | 新（ab026def） |
|---|---|---|
| `form input[type=date] --value 1990-05-14` | rc=0 `{"ok":true}`，`value=""`、`reactProps=""` | rc=0，`value="1990-05-14"`、**`reactProps="1990-05-14"`** |
| 逼一次重渲染之后 | —— | 值**还在** |
| 真站那根带撇号的选择器 `form:has(input[placeholder="A friend's code"]) …` | rc=1 `SyntaxError: missing ) after argument list` | **rc=0**，值进去了 |
| 反例 `--value not-a-date` | rc=0（空枪） | **rc=1**：`date 框要的是 YYYY-MM-DD（也认 MM-DD-YYYY / MM-DD），"not-a-date" 认不出来…`，且框里原来那个值**一个字没动** |

## 八、上线（2026-10-12 18:07 CST，三份都换）

换的时候三边都 **0 趟在跑**：两个容器里没有 `cdp`/`form_fill` 进程；农场容器里只有 supervisord。

| 文件 | 谁在用 | 旧（备份） | 新 |
|---|---|---|---|
| `tools/cdp/cdp` | 产物 / 自测的 CLI（8099 + 8100 容器） | `df69a356` → `cdp.bak-20261012-preDateFill` | **`ab026def`** |
| `tools/cdp/cdp-mcp` | **agent 做动作走的那道门**（与 CLI 同一条 `FillText`） | `7dcd58dc` → `cdp-mcp.bak-20261012-preDateFill` | **`73ee4b02`** |
| `/opt/skills/auto-farm-skill/cdp` | **农场重放 py 用的那份** | `df69a356` → 同目录 `cdp.bak-20261012-preDateFill` | **`ab026def`** |

⚠️ 农场那份是**挂载**的（`/opt/skills/auto-farm-skill` 整目录 bind-mount，`forms/common.py` 的默认值）
⇒ 换文件即生效，不用重建镜像、不用重启。**回滚就一条命令**：把 `cdp.bak-20261012-preDateFill`
（以及另两份同名备份）`mv` 回去。

⚠️ 上线的那一份**就是真站验收过的那一份**（`/tmp/cdp_dob_main` 与线上 `cdp` 逐字节同指纹 `ab026def…`）。

## 九、还开着的

- **`job-9e2250b60c19` 本身还停在 8099**（第 46 步被人喊停、没交付 py）。修的是工具，
  这单要不要重跑、什么时候重跑，等运营/人定。
- 农场那 68 个脚本里只有 `forms/sites/healthwindow.py` 与日期沾边，而且它是**手写**的、
  自己带了一套 `s.value=…; dispatchEvent(change)` 的绕法（三个分开的 dd/mm/yy 框，不是 `type=date`）
  ⇒ 本次修复对它们**零影响**；受益的是**以后**凡是原生日期/时间框的产物。
- `time` / `month` / `week` / `datetime-local` 这四型：**不替人换算格式**，文本原样交给浏览器，
  收不下就报错（回读那一步管着）。真正的格式换算等碰上真站再加。
- 这四型之外的**任何** `<input>`（text/tel/email/number…）行为**一个字节没变** ——
  普填路没有加回读（会误伤带格式掩码的框），所以那边"动作已下发 ≠ 成功"这条老规矩照旧。

## 十、参考

- 现场与读数：`runtime-222/_look_auth_nitrous.py`（怎么切到带 DOB 的那一屏）、
  `_probe_dob_nitrous.py`（①②③）、`_probe_dob_nitrous2.py`（watcher 那段事件记录）、
  `_probe_dob_nitrous3.py`（两条对照）、`_verify_dob_fix.py`（新旧同屏验收）
- 账本与截图：`runtime/explore/job-9e2250b60c19/attempt-1.jsonl`（8099）、
  `runtime-222/_dob2.png` / `_dob3.png` / `_dob_fixed.png`
- 姊妹篇（同一天、同一个 job 的其它毛病不算）：`docs/缺陷单-2026-10-12-选了错国家-strict改写地址.md`、
  `docs/缺陷单-2026-10-12-选国家点不动-影子宿主.md`
