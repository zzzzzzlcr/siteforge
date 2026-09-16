# internal/testdata —— observe 集成测试的 fixture

这 6 个 html 是 R3 探针（`docs/probes/2026-09-16-observe-r3/`）用过的那批难页面，
由 Task 2 搬进来，供 `observe_integration_test.go` 用 `httptest` 起服务观测。

| 文件 | 谁在用 | 档 |
|---|---|---|
| `base.html` | `TestObserveLightDOM` | light DOM：hero 主按钮 + 页脚同名按钮 + cookie 横幅 |
| `shadow.html` | 三条 shadow 测试（陷阱 ①②③） | 两层嵌套 open shadow root |
| `outer.html` / `outer_same.html` / `inner.html` | `TestObserveCrossOriginFrameMerge`（Task 4，已启用） | 跨源 / 同源 iframe |
| `form.html` | 表单片段（备用） | — |
| `selector.html` | `observe_selector_test.go`（Task 5 新增，不来自探针） | 选择器候选与稳定性评级 |
| `honeypot.html` | `cmd/honeypot_e2e_test.go`（R19b 新增，不来自探针） | 屏幕外陷阱（蜜罐）+ 正向对照 |
| `viewport.html` | `cmd/observe_e2e_test.go`（Defect 1 新增，不来自探针） | 撑出**滚动条**的页面（3000×3000） |
| `click_target.html` | `cmd/click_target_e2e_test.go` + `cmd/mcp_click_e2e_test.go`（2026-09-17 新增，不来自探针） | click 的目标解析：歧义选择器（点到了 Back）+ 两种禁用的目标 |

## `click_target.html`（2026-09-17 新增，**不是探针产物**，真站形态的复刻）

复刻的是 blinkist 漏斗上的两个实测缺陷（原委与原始证据：
`docs/probes/2026-09-17-observe-blinkist/`，规格 §4.1）。三组元素各司其职：

| 组 | 元素 | 复刻的是什么 | 谁在用 |
|---|---|---|---|
| **A** | 5 个 `<button class="choice">`：前四个只有 `aria-label`（Back / Disagree / Not sure / Agree）、**无文字**，第五个是 `Continue`、在**末位**、**disabled** | 模型给 Continue 的 selector 就命中这 5 个，而 `querySelector` 取的是文档序第一个 = **Back**。实测照这个点下去：坐标落在 Back 的 bbox 里，漏斗**倒退**一步 | `TestClickAmbiguousSelectorStillFirstMatchButNowSaysSo`（默认仍点 Back，但**说出来**）、`TestClickStrictRefusesAmbiguousSelector`（--strict 拒绝）、`TestMCPClickRefusesAmbiguousSelector` |
| **B** | `#unique-go`（唯一、可点） | **反向对照**：strict 不许变成「一律拒绝」 | 两条 `...StillClicksAUniqueEnabledTarget` |
| **C** | `#disabled-go`（`disabled` 属性）、`#aria-disabled-go`（`aria-disabled="true"`） | 两种禁用判据各一个 —— 只认一条的实现，另一条就是「点了没反应但报成功」 | `TestClickDisabledTargetSaysSoInsteadOfSilentlySucceeding`、`TestClickStrictRefusesDisabledTarget` 等 |

**Continue 刻意不给 `id`**：真站上模型给它的就是那个**歧义的** class 选择器。
给它一个 id 就等于把这条链修好了，测试也就测不到它了。
「唯一抓手」那一条（`TestClickDisabledContinueThroughItsOnlyUniqueHandle`）走的是
`observe` 自己给的 `alternates[0]`（positional 路径，实测唯一），与真站那晚
`blinkist.py` 打 `data-bk<rand>` 标记绕开的**是同一个坑**。

⚠️ **`__rec` 里那句 `aria-disabled` 检查不能删**：浏览器**不会**因为 `aria-disabled`
就不派发点击事件（它不是 IDL 属性）—— 「忽略点击」是**站点 JS 的约定**（MUI / Radix /
Bootstrap 的组件都在 `onClick` 里自己检查）。夹具不照这个来，「aria-disabled 的目标
点了没反应」这条事实就复现不出来。实测过：不加那一句，aria-disabled 的按钮照样记进
`__clicks`，而断言会以一条 Fatal 说话（不是静默变绿）。

`window.__clicks` 是「**实际**点到的是哪一个」的唯一证据（用事件目标记，不是闭包变量
—— 后者记的是「我想点的那个」）。测试用 `cdp eval 'JSON.stringify(window.__clicks)'` 读它。

## `viewport.html`（Defect 1 新增，**不是探针产物**）

页面上只有一个 3000×3000 的块和一个按钮 —— 它存在的**唯一**理由是让视口里出现
**滚动条**。

为什么非要有它：`observe` 报的 `viewport_css_px` 是 `window.innerWidth / innerHeight`
（**含**滚动条，与 `cmd/screenshot.go` 那条 `image_px = viewport_css_px × dpr` 同源）。
而 `document.documentElement` 的 `clientWidth / clientHeight` **不含**滚动条。页面不溢出
时这两个候选值**恒等**，用哪一个都测不出来 ——「报的是哪一个」这件事只有在滚动条真的
存在时才分得开。所以这个 3000×3000 的块是**前置条件**，不是装饰；
`TestObserveCommandViewportIsInnerWidthNotClientWidth` 里那条
`innerW <= clientW → Fatal` 就是它的守卫（夹具被改小、或浏览器换成 overlay 滚动条时，
那条断言会当场说话，而不是退化成恒绿）。

本机实测（Chrome 150.0.7871.124 headless，默认窗口）：inner 780×437 / client 765×422
（两条轴各 15px 滚动条）。

> ⚠️ 别把它合并进 `honeypot.html`（那张也是 4000×4000、也有滚动条）：两边的**用途**
> 不同，混在一起会让「蜜罐判据变了」把视口那条测试一起带红（反之亦然），
> 而那时报告里只会看到一条看不懂的红。

## `selector.html`（Task 5 新增，**不是探针产物**）

前 6 个是「难页面」探针的产物；`selector.html` 不一样 —— 它是一张**判据表**：
每个元素钉住 `candidates()` / `stability()` 的一条判据，用 `<button>文本</button>`
里那段**唯一文本**标注身份（测试按文本取元素 —— 按 selector 取会自我指涉，
而 selector 正是被测对象）。

元素与判据的对应（详见 `observe_selector_test.go` 顶部注释）：

| 元素文本 | 形态 | 实测结果 |
|---|---|---|
| `Stable Id` / `B stable name` / `C data-testid` | 稳定 id / name / data-* | 首选即它、`high` |
| `Full` | 五种标识俱全 | 候选顺序 id > name > data-* > class，且去重 |
| `L pure id` | 只有 id | 首选 `#pure-id`、`high`，**alternates 空** |
| `D hash7` (`css-1x2y3z4`) | 7 位 base36 hash（emotion 那类形态，**未在真站核实**，R5） | 被 RAND **形态③** 滤掉，退化成结构路径（修复轮 1 之前是「首选就是它、medium」） |
| `E hash8` (`css-1a2b3c4d`) | 8 位纯 hex hash | 被 RAND 滤掉，退化成结构路径 |
| `F stable class` | 稳定语义 class | `button.btn.btn-primary`、`medium` |
| `G deep path no id` | 4 段结构路径 | `low`（pathSel 上限 4 跳） |
| `H deep under stable id` | 4 段、表头是祖先 id | **`high`** —— 与 G 只差一个表头，见下 |
| `I plain button` | 裸 `<button>` | `body:nth-of-type(1) > button:nth-of-type(k)`、`medium` |
| `J random id` / `K random tid` | 随机 id / 随机 data-* | 被 RAND 滤掉（`id` / `data-*` 两路都过了 RAND） |
| `K random name` (`sid_9f8e7d6c5b4a`) | 随机 name | 被 RAND 滤掉（修复轮 1 之前**没走 RAND**、直接当首选且 `high`） |
| `M btn-primary` … `M text-2xl`（7 个） + `M name step2` | **正常类名/name**（反向边界） | 仍被采用（`button.<class>` / `input[name=]`）—— 放宽 RAND 时最容易被静默误伤的那一家 |
| `N hex10 form1-only` (`css-abcdef1234`) | 只翻转一次的 10 位 hex | 被 RAND **形态①** 滤掉。**它是形态①的专用守卫**：②要 ≥6 位数字、③要交替 ≥2 次，都够不着它 —— 删掉①这条就红（实测过：删①只红这一条） |
| `N random name 3` (`a1b2c3`) | 随机 name（形态③） | 被 RAND 滤掉，退化成结构路径 —— name 落点的**正向**边界 |
| `N subfield name` (`step2a`) | 人写的子字段名（`address1a`/`opt2b` 一家） | ⚠️ **被 ③ 一起抓走**、退化成结构路径 —— **已知且被接受的代价**（控制器裁定：一条规则、一个偏置，不给 name 开特例）。断言把这条代价钉成可观测事实 |

### 修复轮 1（RAND 放宽）与修复轮 2（把边界补成双向）

Task 5 首轮报出的真发现是 **RAND 够不着 `css-1x2y3z4` 这类 6~7 位 base36 hash**，
且 `name` 是四个落点里唯一没过 RAND 的。控制器裁定「修实现，不改测试」，修法是
给 RAND 加**形态③**（字母与数字来回交替 ≥2 次的片段）+ 给 `name` 补 `!RAND.test`。

⚠️ 但放宽 RAND 有**反向**风险：开始静默拒绝正常类名（`btn-primary` / `col-md-6` /
`step1` 这类），那会让 stability 全面变差且**不报任何错**。所以 fixture 里有上表
`M …` 那一行（7 个类名 + 1 个 name）专门守这一半 —— `TestObserveSelectorLegitTokensNotRejected`。
**改 RAND 时两条都要看**：一条管「该抓的抓到」，一条管「不该抓的别抓」。

修复轮 2 补齐了两处缺口（审查指出）：**`name` 落点原先只钉了单向** ——
而 name 正是这一轮改动的地方，③ 新危及的 `step2a`/`address1a` 一家被误抓时
套件不会说话；现在由 `TestObserveSelectorNameLandingBothWays` 双向钉住。
另给**形态①**补了专用用例（`css-abcdef1234`）：在此之前①独有的用例都被③接管了，
**删掉①套件照样全绿**（孤儿代码）。

**已知残留**（写在此处以免被当成已完备，真站校准 = R5）：
全字母 hash（`sc-bdVaJa`）、**≤4 字符的 hash 段**（`css-a1b2`，被③的 ≥5 位长度地板挡住）、
只翻转一次的非 hex hash（`css-abcdefg1`）。

RAND 的波及面**超出**这两个已测落点：`pathSel` 的祖先 id 检查、选项组 scope、
遮挡物/关闭按钮选择器**三处都没有断言**（本轮未做，已记录）。

原委、逐条实测与变异验证见
`.superpowers/sdd/2026-09-16-tool-layer-observe/task-5-report.md`（该目录 gitignore，
所以关键结论都留在本文件与 `observe_selector_test.go` 的注释里）。

## `honeypot.html`（R19b 新增，**不是探针产物**，真站复现）

复现的是首次真站跑（2026-09-16，`check.compareinsulation.io` 的漏斗页）里的蜜罐：

```html
<input name="company_url" type="text" style="position:absolute;left:-9999px;…">
```

真站那条是 `left:-9983px`。人看不见它；bot 填了就被站点标记成机器人 —— 而 `observe`
当时把它**同时**列进 `actions` 与 `fields`，两边都评 `stability: high`。fixture 里有
**两条**陷阱，两条轴各一（`off-document-left` / `off-document-top`）：只钉左侧的话，
「只判 `rect.left`」的实现照样能过。

**这张 fixture 真正的设计点是对照组，不是陷阱。** 页面被下面那个 `4000×4000` 的块撑开，
测试（`cmd/honeypot_e2e_test.go`）会先 `window.scrollTo(600, 600)` 再观测，于是：

| 元素 | 文档坐标 | 滚动后的视口坐标 | 该不该被当陷阱 |
|---|---|---|---|
| `input[name=company_url]` | 负（-9999） | 负 | **是** |
| `input[name=fax_number]` | 负（-9999） | 负 | **是** |
| `#reachable`（宽写死 120） | 正（≈8） | **负**（`left = 8-600 = -592`） | 否 |
| `input[name=email]` | 正 | **负** | 否 |

后两行就是判据的**分水岭**：判据若用视口坐标，它们连同一切「滚上去看不见」的正常内容
会被**静默**丢掉（实测变异：`actions` 直接变 `[]`）。所以「先滚动」是这条测试的
前置条件 —— 不滚的话两种实现给出同样的答案，对照就是白给的。测试里那两个元素各自
**自证**「此刻视口坐标下长得就像陷阱」（`requireViewportTrapLike`），不满足就当场 Fatal：
对照失效与测试通过不能长得一样。

⚠️ 别把 `#reachable` 的宽度改小到「滚 600 之后 `left+width > 0`」—— 那样对照会失效，
而它会以一条 Fatal 明说，不是静默变绿。

## ⚠️ `outer.html` 的 iframe 是硬编码端口，测试在**服务层**改写它

```html
<iframe id="ci" src="http://localhost:8892/inner.html" ...>
```

`8892` 是 R3 探针那台 `python3 -m http.server` 的端口，而 `httptest` 每次都是**随机端口**。
照搬这个 fixture 的话子帧会去连一个不存在的 `localhost:8892`，症状是
「**没有子帧**」—— 不报错，最难查（C3/C39，两位实现者独立踩过）。

所以 `TestObserveCrossOriginFrameMerge` 用的 `serveCrossOriginFixtures` 会拦下 `/outer.html`，
把 `8892` 换成这次真正在用的端口再发出去；换不上**当场报错**，不退化成上面那种假失败。
页面结构仍然一字不差来自本文件，改的只有 origin 里的端口。
跨源关系照旧：外层页走 `127.0.0.1`（httptest 绑的），子帧走 `localhost` —— 不同源也不同 site。

（`outer_same.html` 是相对路径的同源对照，没被这条测试用到：它验不到跨源。）

## 帧枚举：为什么 `ObserveAll` 用 `GetFrameTreeWithEvents`（原始协议证据）

**结论先说**：本机 Chrome 150.0.7871.124 headless（`127.0.0.1:9222`）实测，
page 目标的 **`Page.getFrameTree` 不报 OOPIF（跨源）子帧** —— 同源子帧报，跨源子帧
**一个都不报**。所以 `ObserveAll` 枚举走 `GetFrameTreeWithEvents`（帧树 ∪ DOM 穿透），
裸 `GetFrameTree` 会让它对**所有**跨源 iframe 视而不见。这段 JSON 是那条结论的原始凭据，
存在这里而不是工作区的报告里 —— 报告会被删，仓库不会。

### ① 跨源子帧：`Page.getFrameTree` 的响应里**根本没有 `childFrames` 这个键**

页面：`outer.html`（iframe 指向 `http://localhost:<port>/inner.html`），主帧 session，裸协议：

```json
{"frameTree":{"frame":{"id":"E5DDABB7C915EBF68A2AFBF0F4D7AA67","loaderId":"BF4923C1C0CDC9489ED68983CC084B44",
"url":"http://127.0.0.1:44759/outer_dyn.html","domainAndRegistry":"","securityOrigin":"http://127.0.0.1:44759",
"securityOriginDetails":{"isLocalhost":true},"mimeType":"text/html","adFrameStatus":{"adFrameType":"none"},
"secureContextType":"SecureLocalhost","crossOriginIsolatedContextType":"NotIsolated","gatedAPIFeatures":[]}}}
```

**不是 auto-attach 的问题**：在 page session 上先 `Target.setAutoAttach({autoAttach:true,
waitForDebuggerOnStart:false,flatten:true})`、等 2s 再查，响应**逐字节相同**，子帧数仍是 0。

**同源对照**（`outer_same.html`，相对路径 `src`）：同一个命令**报**子帧 ——
`childFrames` 里是 `{id: "FE79CEAF…", name: "ci", url: "…/inner.html"}`。
一句话：**只有跨源的那些消失，同源的一个不少**。

### ② 帧确实存在，只是不从这条路出来

- `Target.getTargets` 里有 `{type: "iframe", id: "F0060DA68E16997570DF113FD60866B1",
  url: "http://localhost:44139/inner.html"}`，而同一轮 `GetFrameTreeWithEvents`
  报的子帧 `frameID` **与这个 targetId 完全相同**（OOPIF：targetId == frameId）。
- 那个子帧 target **自己的** `Page.getFrameTree` 里带着父子关系：
  `{"frame":{"id":"F0060DA68E16997570DF113FD60866B1","parentId":"E5DDABB7C915EBF68A2AFBF0F4D7AA67", …}}`
  —— 所以不是 Chrome 不知道，是 page 目标这一路不带。
- 子帧的 eval 走的是 OOPIF 回退：page 目标上 `CreateIsolatedWorld(子帧)`
  报 `No frame for given id found (-32602)`，然后 `Target.attachToTarget(frameId)` 才成功。

### ③ 已知缺口：**跨源帧里面的**子帧，枚举看不见（当前会发诊断，但收不进来）

实测 `main(127.0.0.1) → OOPIF(localhost) → 同源子帧`（两层）：

| 来源 | 结果 |
|---|---|
| `GetFrameTreeWithEvents` | 2 帧（主帧 + OOPIF）—— **看不见 OOPIF 里面的那一层** |
| OOPIF target 自己的 `Page.getFrameTree` | **有**：`childFrames:[{id:"62F0B452…", parentId:"BCC65730…", name:"ci", url:"…/inner.html"}]` |

原因：OOPIF 的 `<iframe>` 元素在**父**文档里，父文档的 DOM 穿透看得见它；但**跨进程
没有 `contentDocument`**，所以穿不进 OOPIF 内部，它的子孙帧就整个消失。
要真收进来得**逐 OOPIF target 取树**（`Target.getTargets` → attach → `Page.getFrameTree`，
机制已实测可行），Task 4 没做 —— 它现在由 `ObserveAll` 的逐帧对账守卫兜成一条
`diagnostics`（`kind:"frame-blind"`），**不再静默**，但内容确实拿不到。

## ⚠️ `shadow.html` 与探针副本**不同**（有意为之，别"修回去"）

```diff
-<div id="host1"></div>
+<main><div id="host1"></div></main>
```

（外面还加了一段说明注释。其余 5 个文件与 `docs/probes/2026-09-16-observe-r3/fixtures/`
逐字节相同，`md5sum` 已核对。）

**为什么必须加**：陷阱③那条断言（`TestObserveShadowRegionNotAllBody`，`region` 不得全
退化成 `body`）需要页面里**有 landmark**，而且 landmark 必须在 shadow **外面**。
2026-09-16 实测：没有 landmark 时，`region` 的逻辑（沿合成树找 `header/nav/footer/
aside/main/hero`）对**两种**实现给同一个答案 ——

| 实现 | `#submit` 的合成树链 | 结果 |
|---|---|---|
| 正确（走 `getRootNode().host`） | `button#submit → div#host2 → div#host1 → body → html` | `body` |
| 陷阱③（只走 `parentElement`，到 shadow root 顶就断） | `button#submit` | `body` |

两边都 `body` → 断言恒红、**对任何实现都红**，也就测不出陷阱③了。加 `<main>` 后
（链变成 `… → div#host1 → main → body → html`）两者才分道扬镳：正确实现 `main`、
错误实现 `body`。这是**加强**断言，不是放宽 —— 断言一个字没改，改的是被观测的页面。

注意：landmark 加在 shadow **里面**是没用的 —— 错误实现的 `parentElement` 遍历照样
找得到它，仍然分辨不出来。必须在 shadow 外面（真实站点就是这个形态：Salesforce
Lightning 那种把表单塞进 shadow 的页，外面有 header/nav/main）。

## 🚫 不要用探针目录覆盖这里的 fixture

```bash
# 别干这个：
cp docs/probes/2026-09-16-observe-r3/fixtures/*.html tools/cdp/internal/testdata/
```

`docs/probes/.../fixtures/` 是**历史记录**（探针当时真跑过的东西，README 里的数字都基于它），
**不许改**；`testdata/` 是**活的测试输入**。两者对 `shadow.html` 的差异是有意的。
覆盖回去的后果是陷阱③退回「永久红且空转」，而下一个人看到一条怎么修都红的断言，
第一反应多半是去削弱它 —— 那是本项目反复踩过的坑。

原委与实测数据：`.superpowers/sdd/2026-09-16-tool-layer-observe/task-3-report.md`（第 3 节）。
