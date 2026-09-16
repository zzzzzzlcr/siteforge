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
| `D hash7` (`css-1x2y3z4`) | 7 位 base36 hash（emotion 那类形态，**未在真站核实**，R5） | ⚠️ **首选就是它、medium** —— RAND 漏网，见下 |
| `E hash8` (`css-1a2b3c4d`) | 8 位纯 hex hash | 被 RAND 滤掉，退化成结构路径 |
| `F stable class` | 稳定语义 class | `button.btn.btn-primary`、`medium` |
| `G deep path no id` | 4 段结构路径 | `low`（pathSel 上限 4 跳） |
| `H deep under stable id` | 4 段、表头是祖先 id | **`high`** —— 与 G 只差一个表头，见下 |
| `I plain button` | 裸 `<button>` | `body:nth-of-type(1) > button:nth-of-type(6)`、`medium` |
| `J random id` / `K random tid` | 随机 id / 随机 data-* | 被 RAND 滤掉（`id` / `data-*` 两路都过了 RAND） |
| `K random name` (`sid_9f8e7d6c5b4a`) | 随机 name | ⚠️ **首选就是它、`high`** —— `name` 那一路没走 RAND |

⚠️ 上表里带 ⚠️ 的两行是 Task 5 **报出来的真发现**（不是 fixture 写错了）：
`TestObserveSelectorRandomHashClassNotPreferred` 因此**当前是红的**，
原委、实测输出与修法建议见 `.superpowers/sdd/2026-09-16-tool-layer-observe/task-5-report.md`。
改 fixture 之前先读那份报告 —— 别把这两条擦掉，那正是这一轮要留住的信息。

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
