# 循环型「打标记 → 量落点」在真站上成立吗 —— 2026-10-16

这一趟是**只读探针**（不点、不填、不导航以外的写操作），给
`docs/superpowers/specs/2026-10-16-弹窗与遮挡-能力进骨架-design.md` §3.3 那句
「⚠️ 真浏览器上仍要验一次（计划 Task 1）」收口，也是计划 Task 3 选**走法 A（标记后量落点）
/ 走法 B（只做钩子表）**的判据。

**真站**：`compareinsulation`（循环型，池子里有产物 `forms-222/sites/compareinsulation.py`）。
**机器**：Bit `192.168.1.222`（8100 那台）。**窗口**：视口 **377×757**，跑完已 `bit.sh close`。

⚠️ **URL 的来路**（不是猜的，也不是 brief 里写的那一格）：brief 让从 `PROVENANCE['entry_url']`
读，但**那份产物的 `entry_url` 是空串** —— 循环型的产物**故意留空**（`docs/交接-2026-09-24-步进循环型.md:214`：
「页面由平台按状态匹配开好，循环型读到什么就做什么」）。所以两页都从**我们自己的记录**里取：

| 页 | URL | 来路 |
|---|---|---|
| 问卷页（arrive） | `https://check.compareinsulation.io/` | 产物 `PROVENANCE['arrive_url']`（= 账本里「到没到问卷」的判据） |
| 落地页（§1.2-③ 出事那页） | `https://compareinsulation.io/article-1-c` | `runtime-222/explore/job-37d8e6bfa080/attempt-1.jsonl` 第 1 步 `observe.url`（`pre_steps` 走的就是这页） |

---

## 一、现场：覆盖是真的，而且**就是 §1.2-③ 那条真事那个形状**

问卷页**第一次加载**时，探针（`cover_probe.js`，brief Step 2 原文）逐字回：

```json
{"url":"https://check.compareinsulation.io/","frames":0,"covered":[
 {"tag":"button","txt":"Get Started","covered_by":"div#cookiescript_header."},
 {"tag":"a","txt":"Privacy Policy","covered_by":"span."}]}
```

连跑 3 次**逐字相同**。把落点那一摞摊开（`/tmp/stack_dump.js`，3 次逐字相同）：

```json
{"n":1,"url":"https://check.compareinsulation.io/","rows":[
 {"txt":"Get Started","rect":[40,559,282,52],"center":[181,585],"stack_len":17,
  "stack":["div#cookiescript_header.","div#cookiescript_injected.","button.inline-flex",
           "div.flex-1","div.items-center","div.flex","div.space-y-4","div.container"],
  "self_at":2}]}
```

⇒ 那颗 282×52 的 `<button>Get Started</button>` **自己在栈里排第 3**（`self_at:2`），
它上面结结实实压着两层同意浮层。几何对得上（`#cookiescript_injected` `position:fixed`、
`rect [0,542,362,215]`、`z 999997`；`#cookiescript_header` `rect [15,557,319,38]`；
按钮 `rect [40,559,282,52]` ⇒ 中心 `(181,585)` 正落在 header 里）。

落地页同理（`div#si-cookie-text` = 那条「By clicking "Accept All Cookies", you ag…」）：

```json
{"url":"https://compareinsulation.io/article-1-c","frames":0,"covered":[
 {"tag":"a","txt":"","covered_by":"div#si-cookie-text."}]}
```

⇒ **标记的对象是真实存在的、被真实盖着的**，不是合成页造出来的。

### ⚠️ 覆盖现场是**间歇**的（这一条比读数本身更要紧）

同一个窗口、同一条 URL，**来回趟一趟落地页之后，同一条探针返空**：

```json
{"url":"https://check.compareinsulation.io/","frames":0,"covered":[]}
```

再 `navi` 一次（第 3 趟加载）+ 等 8 秒，仍然是空。同一时刻的页内状态：

```json
{"has_injected":false,"localStorage":["_gcl_ls=…","eng_mt=…"],
 "cookie":"_gcl_au=…; _ga=…; _clck=…; dicbo_id=…; CookieScriptConsent={\"googleconsentmap\":{\"ad_sto…"}
```

⇒ `#cookiescript_injected` **整个不在 DOM 里**了，`CookieScriptConsent` 这条 cookie 在场。
**为什么没量清楚**（是落地页那趟把同意记下了、还是本站的 implied-consent 计时器、还是别的），
**不编**。要记的就是这一句：**「这一屏有没有覆盖」是随状态变的** ——
`covered:[]` **不等于**这一站不会盖人；反过来，第一次那三份读数也不是每次开窗都能复现的。

---

## 二、四件事的逐条读数

### ① 真页面上有没有被盖着的可点元素 —— **有**

| 页 | 被盖着的可点元素 | 盖着它的是谁 |
|---|---|---|
| 问卷页（首载） | `<button>Get Started</button>` `[40,559,282,52]` | `div#cookiescript_header`（字面「This website uses cookies」） |
| 问卷页（首载） | `<a>Privacy Policy</a>` `[205,635,79,16]` | 一个 `<span>`（字面「By clicking "Accept All Cookies", …」） |
| 落地页 | 一个 322×180 的空 `<a>` | `div#si-cookie-text` |

⇒ **走法 A 有真实靶子**。两页的覆盖都是**同意浮层**，与 spec §1.2-③
（「自测两轮都卡在『Accept All Cookies』弹层」）同族。

### ② 跨域子帧里答什么 —— **量不到（这一站没有子帧）**

| 页 | `window.frames.length` | `document.querySelectorAll('iframe,frame').length` |
|---|---|---|
| 问卷页（多次采样） | **0** | **0** |
| 落地页 | **0** | **0** |

```json
{"url":"https://check.compareinsulation.io/","frames_len":0,"iframe_els":0,"srcs":[]}
```

⇒ 这一问**不是「够不着」，是「没有」** —— `compareinsulation` 两页都不往外挂子帧，
所以 spec §3.3 括号里那句「**尤其跨域子帧**」在真站上**一格证据都没拿到**。
**如实记：未验证。**（`window.frames` 连影子里的子帧也数得到，所以 0 是结论性的。）

### ③ 影子宿主里有没有落点 —— **没有**

| 页 | 可点元素总数 | 在 light DOM | 在 shadowRoot | shadow 宿主 | 里面可点 |
|---|---|---|---|---|---|
| 问卷页 | 18 | 18 | **0** | 1 个：`next-route-announcer`（**open**） | 0 |
| 落地页 | 149 | 149 | **0** | **0 个** | — |

⇒ **真站上没有「影子里的落点」这种靶子**，「穿 / 不穿影子」两条路在这一站**量不出差别**。
能说的只有：**这条探针确实会穿** —— `deepAll` 走 `root.querySelectorAll('*')` 找 `shadowRoot`
往深里递归，而且它当场就摸到了那个 Next.js 的 `next-route-announcer` 宿主。
**闭 shadow（`mode:'closed'`）一次都没出现。**
⇒ Task 3 Step 3-2「穿 / 不穿」只能靠**合成页**定（两套骨架同一把尺子那条要求，真站这边给不了）。

### ④ 开销：一次探针多少秒 —— **与循环型自己那次 read 同量级**

端到端量的是 `cdp eval`（进程 + websocket + eval），就是产物走的那条路
（`forms-222/common.py:212` `CDPHelper.eval()` = `subprocess.run([CDP_PATH,"eval",script])`）：

| 跑什么 | 页 | 三次/五次实测（秒） |
|---|---|---|
| `cover_probe.js`（brief 那条） | 落地页 | 0.043 / 0.063 / 0.048 / 0.052 / 0.059 |
| `cover_probe.js` | 问卷页 | 0.097 / 0.054 / 0.069 |
| `cover_detail_probe.js`（更重：走全树） | 落地页 | 0.044 / 0.035 / 0.052 |
| **对照**：循环型自己的 `READ_PAGE_JS` | 落地页 | 0.045 / 0.051 / 0.046 |

**页内**真正的计算量（`performance.now()` 自夹）：**0.4 ~ 1.0 毫秒** —— 全部时间都在
传输/进程启动上。⇒ 挂在**每次动手前**成本可接受；「能不能搭它那次 read 的车」是**设计选择**，
不是成本问题（spec §5.4 说「量了再定」，这里就是那个量）。

对照口径提醒：blavity 那次记的是 **0.15~0.6 秒/步**（`docs/probes/2026-10-10-blavity-ad-popup/`），
比这里大 3~10 倍。这次是**宿主侧 CLI 直量**，那边是产物内部调；两边别互相引用。

---

## 三、结论

> **走法 A（先打标记、再量落点）在真站上成立** —— ① 有真实靶子、落点量得出「谁盖着」、
> ④ 成本可忽略；③ 影子那一格真站给不了靶子，② 跨域子帧这一站压根没有子帧。

⇒ **Task 3 做落点判据**；但下面两格必须**记成未验证**、不许写成验过：

| 格 | 状态 | 谁能补 |
|---|---|---|
| 覆盖现场存在、量得出 who | ✅ 真站验到（含一条真事同族的 `#cookiescript*` 浮层） | — |
| `elementFromPoint` 在**跨域子帧**里够不够得着 | ⚠️ **未验证**（这一站 `frames=0`） | 合成页 / 换一站（spec §3.3 那句「尤其跨域子帧」**今天没有真站证据**） |
| 影子里的落点 | ⚠️ **未验证**（这一站 shadow 里 0 个可点） | 合成页（闭 shadow 更是一次都没见过） |
| （新冒出的一格）覆盖是**间歇**的 | ⚠️ 第一次加载量得到、来回一趟之后量不到 | Task 3 别把「探针返空」当「没有覆盖」 |

---

## 四、复现命令

```bash
# ⚠️ 一律 8100（Bit 192.168.1.222）；8099 运营在用，别碰
BIT_ID=$(grep -m1 '^BIT_ID=' /company/siteforge/siteforge.env | cut -d= -f2- | tr -d '"'"'"' \r')
/opt/skills/auto-farm-skill/bit.sh open 192.168.1.222 "$BIT_ID"
# 问卷页（首载那三份读数就是在这一步之后 4~6 秒拿到的）
/opt/skills/auto-farm-skill/cdp navi "https://check.compareinsulation.io/" --host 192.168.1.222 --port 61129
/opt/skills/auto-farm-skill/cdp eval --file cover_probe.js --host 192.168.1.222 --port 61129
# 更细的一份（覆盖件几何 / shadow 宿主 / 子帧 / 页内耗时）
/opt/skills/auto-farm-skill/cdp eval --file cover_detail_probe.js --host 192.168.1.222 --port 61129
# 落地页（§1.2-③ 出事那页）
/opt/skills/auto-farm-skill/cdp navi "https://compareinsulation.io/article-1-c" --host 192.168.1.222 --port 61129
/opt/skills/auto-farm-skill/cdp eval --file cover_probe.js --host 192.168.1.222 --port 61129
# 跑完必须关窗
/opt/skills/auto-farm-skill/bit.sh close 192.168.1.222 "$BIT_ID"
```

⚠️ **裸 `return` 直接喂 `cdp eval --file` = SyntaxError ⇒ 返回恒为空**（blavity 那次假通过就是它）。
本目录两份探针**都已经是 `(function(){…})()` 包好的**，可以直接喂。改之前先想这条。

---

## 五、缺口（这一趟**没**验的，别当验过）

1. **「打标记」那一步没在真站上跑。** 这一趟是**只读**探针（硬约束：不许点 / 不许填 /
   不许导航以外的写操作），`setAttribute('data-sf-pick','1')` 是一次 DOM 写，所以**没做**。
   验到的是**量落点那一半**（元素 → 中心点 → `elementsFromPoint` → 谁盖着）；
   没验的是「打标记 → 按 `[data-sf-pick="1"]` 找回来」这个往返。
   这一段是浏览器原生行为、与站点无关，但**它确实没在真站上跑过**。
2. **跨域子帧 / 影子里的落点：真站零靶子**（见 ② ③）。spec §3.3 那句「尤其跨域子帧」
   今天**没有真站证据**，只有合成页能给。
3. **覆盖现场是间歇的，机制没量清楚**（见第一节）。第三次加载起 `#cookiescript_injected`
   整个不在 DOM 里，只有 `CookieScriptConsent` 在场 —— 因果**没查**。
4. **没做「点掉它」**（本来就不该做：只读任务）。

---

## 六、文件

| 文件 | 是什么 |
|---|---|
| `cover_probe.js` | brief Step 2 那条探针的**逐字原文**，且已包成 `(function(){…})()`，可直接喂 `cdp eval --file` |
| `cover_detail_probe.js` | 这一趟**另加**的只读细探针：覆盖件几何/position/z、可点元素在 light 还是 shadow、shadow 宿主与 mode、`iframe`/`window.frames`、页内耗时（`probe_ms`）。同目录，一并留底 |
| `README.md` | 本文件 |

排查时用到的临时脚本（**未留底**，在 `/tmp`）：`stack_dump.js`（摊落点那一摞）、
`cs_state.js`（同意浮层在不在 + cookie）、`frames.js`（子帧）、`read_page.js`
（从 `forms-222/sites/compareinsulation.py` 里 `ast.literal_eval` 抠出来的循环型自己的
`READ_PAGE_JS`，做开销对照用）。

---

## 七、Task 8（真站验收）用到的脚本 —— 2026-10-16/17

上面一~六节是 **Task 1 的只读探针**（量「覆盖在不在、谁盖着」）。这一节是**同一目录**里
Task 8 加的那些（都是**产物本体在真站上**跑，不是探针复刻）：
读数与结论见 `docs/superpowers/plans/2026-10-16-弹窗与遮挡-能力进骨架-实测.md`。

| 文件 | 是什么 |
|---|---|
| `rerender_loop.py` | 把**循环型产物**用当前骨架重渲（`tools/rerender_site.py` 只认固定步骤表那一族）。带两道核对：业务字面量逐字节守恒 + 逐行「加了哪些」 |
| `verify_popup.py` | 跑**产物本体**的弹层入口：`fixed` = `_clear_obstructions()`、`loop` = `clear_popups()`、`cost` = 量代价（read / 探针 / 合一趟；页内 `performance.now()` + 端到端两把尺子） |
| `ad_creative_watch.py` | **只读**盯「广告 creative」那一行挑中了谁（几何 / 定位 / `display`/`visibility` / 祖先 / 落点）。⚠️ 它的 `vis()` 照抄钩子表那条（**只看矩形**）—— 自己加「真的看得见」的闸会得出 0 候选 |
| `consent_facts.py` | **只读**一眼两问：同意层在不在（CookieScript 那族几何）+ 第 4 行此刻挑中谁 |
| `hook_by_hook.py` | **只读**把 `_POPUP_HOOKS` **拆开一条一条问**（整段答话看不出是哪条哑了），外加 `_CONSENT_BOX_JS` 交叉参照 |
| `bytes_and_rerender.py` | 两族骨架的字节数与重渲耗时（Review Focus #7）+「机器码两族逐字节同一份」的核对 |

⚠️ 两条踩坑（改这些脚本之前先读）：
1. **裸 `return` 会被外层空壳吞掉**（`cdp eval --file` 那条路的老账）：`timed_eval` 的第一版
   自己又包了一层 IIFE ⇒ 探针恒返空；`read` 那一格量出 `9`（= `String(undefined)`）。
   正解：**写成函数体**（`var …; return …`）交给 `_ev` 去包那一层。
2. 钩子表那一段在产物里是**算出来的**（`_POPUP_PROBE_JS = _popup_probe_js()`），不是字面量 ——
   `ast.literal_eval` 抠不到，要 `exec(_POPUP_MACHINERY_SRC)` 才拿得到。
