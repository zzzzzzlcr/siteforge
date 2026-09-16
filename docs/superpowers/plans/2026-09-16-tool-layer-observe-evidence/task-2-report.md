# Task 2 报告：`internal/observe.go` —— 单帧页面模型提取

**Status: DONE_WITH_CONCERNS**
**提交:** `63aa818` feat: observe 页面模型提取 —— 三条静默陷阱各钉一条测试
**测试:** 6 PASS / 0 FAIL / 0 SKIP（brief Step 4 命令）；全包单元扫 29 PASS / 0 FAIL / 0 SKIP

---

## 1. 实现了什么

`tools/cdp/internal/observe.go`（314 行）：

- `PageModel` / `Action` / `Field` / `OptionGroup` / `Obstruction` 五个契约结构，
  json tag 与规格 §4.3 逐字段对齐（键集已用脚本核对，见 §4 F3）。
- `observeJS() string` —— 单帧页面模型求值脚本，**自己套 `withPierce`**（C11）。
  把 R3 探针 `docs/probes/2026-09-16-observe-r3/observe.js` 的算法逐段搬入，
  **只按 C10 改两处**：
  - `var RS = []` + 递归 walk → `var RS = __cdpRoots(document);`
  - `function qsa(sel){...}` 自实现全 root 合并查询 → `var qsa = function(sel){ return __cdpQA(sel); };`
  - `composedAncestors` **保留自实现**（内核只做穿透查询，合成树遍历不属于那一类）。
- `(*Client).Observe(frameID string) (*PageModel, error)` —— 按 brief 骨架，
  走 C32 已验证的取值路径（`var raw string` → `json.Unmarshal`）。

返回的 JSON 键集**恰好**是 PageModel 的 8 个 tag。

---

## 2. TDD 证据

### RED —— brief Step 2

```bash
cd /company/siteforge/tools/cdp
go test ./internal/ -run 'TestObserveJS|TestPageModelJSONShape' -v
```

```
# cdp/internal [cdp/internal.test]
internal/observe_traps_test.go:12:8: undefined: observeJS
internal/observe_traps_test.go:24:8: undefined: observeJS
internal/observe_traps_test.go:33:8: undefined: observeJS
internal/observe_traps_test.go:41:8: undefined: observeJS
internal/observe_traps_test.go:49:8: undefined: observeJS
internal/observe_traps_test.go:67:8: undefined: PageModel
FAIL	cdp/internal [build failed]
FAIL
```

为什么这个失败是预期的：brief 就要求「编译失败（`undefined: observeJS` / `undefined: PageModel`）。
**这就是我们要的红」—— 输出逐字相符（6 行，5 行 `observeJS` + 1 行 `PageModel`）。
测试文件是 brief 原文逐字复制，未改一个字符。

> ⚠️ 一类红说明不了的事：编译失败的红**不能证明这些断言在实现写错时会红**
> （C33 已承认字符串断言可被注释糊过去）。下面 §4 F4 的变异测试补上了这一条。

### GREEN —— brief Step 4（对已提交状态重跑）

```bash
go test ./internal/ -run 'TestObserveJS|TestPageModelJSONShape' -v
```

```
--- PASS: TestObserveJSMustHandleShadowText (0.00s)
--- PASS: TestObserveJSMustNotTrustElementsFromPointAlone (0.00s)
--- PASS: TestObserveJSMustClimbThroughShadowHosts (0.00s)
--- PASS: TestObserveJSMustUsePierceHelper (0.00s)
--- PASS: TestObserveJSMustNotEmitSemanticJudgements (0.00s)
--- PASS: TestPageModelJSONShape (0.00s)
PASS
ok  	cdp/internal	0.007s
```

**6 个，不是 brief 写的 7 个** —— brief Step 4 的「预期：7 个测试全 PASS」是计数笔误，
brief 给出的测试代码里就是 5 个 `TestObserveJS*` + 1 个 `TestPageModelJSONShape`。
（`-run 'TestObserveJS|TestPageModelJSONShape'` 也只可能匹配到 6 个。）
我按「代码逐字照抄、不改测试」处理，未自行补第 7 个。

### 附带：全包单元扫（确认本任务是纯增量、无回归）

```bash
go test ./internal/ -run 'TestSelectorBuildersPierceShadowRoots|...|TestObserveJS|TestPageModelJSONShape' -v
# 29 PASS / 0 FAIL / 0 SKIP
go build ./...   # BUILD OK
go vet ./internal/   # 无输出（exit 0）
gofmt -l internal/observe.go internal/observe_traps_test.go   # 无输出（干净）
```

---

## 3. 改了哪些文件

| 文件 | 动作 |
|---|---|
| `tools/cdp/internal/observe.go` | 新增（314 行，md5 `2b7a138966854dc47bee54ced47c31f5`） |
| `tools/cdp/internal/observe_traps_test.go` | 新增（74 行，brief 逐字） |

提交只含这两个文件（`git show --stat` 确认 2 files changed, 388 insertions）。
**未提交任何探针文件、未改 `internal/shadow.go`、未改任何既有文件。**

提交时仓库没有 git identity（`Author identity unknown`），已按既有提交的作者
设**仓库本地** `user.name=lcr` / `user.email=lcr@3tkj.cn`（仅本仓库，未动 `--global`）。

---

## 4. 自查发现（读自己的 diff + 机器核对）

### F1 —— 逐段搬移是无损的（机器 diff，不是我肉眼看）

把生成出的脚本导出（临时测试 + `node --check`，临时文件已删），与探针 `observe.js`
按「剥注释 + 去空白」逐行 diff：

- **helpers 块（`txt`/`vis`/`shadowDepth`/`composedAncestors`/`occludedBy`/`pathSel`/
  `candidates`/`stability`/`region`/`lum`/`contrastBand`/`nearbyText`）：差异 0 行**
- 尾部块（SEL → page_text）：差异 1 处，就是下面 F2

### F2 —— 唯一一处超出 C10 的改动（主动披露）

`page_text` 逐 root 收集处的循环变量 `r` → `rt`：

```js
var perRoot = RS.map(function (rt) {
  if (rt.body) { try { return rt.body.innerText || ''; } catch (e) { return ''; } }
  for (var i = 0; i < rt.children.length; i++) { var c = rt.children[i]; ... }
```

语义零变化（`rt.children` ≡ `r.children`）。改名的两个理由：① 该块旁边 `r` 已被
当 rect 用（`getBoundingClientRect`），同名易误读；② 防止后来者在此块写
`r.innerText` 时**恰好命中**陷阱①的字符串断言（见 F4 的 M1b）。若控制方要求逐字
一致，回退是一行的事。

### F3 —— 返回值键集 = 契约键集（机器核对）

```
probe returned keys : ok, url, title, page_text, page_text_len_raw, shadow_roots, counts, actions, fields, option_groups, obstructions
mine  returned keys : url, title, page_text, shadow_roots, actions, fields, option_groups, obstructions
PageModel json tags : url, title, page_text, shadow_roots, actions, fields, option_groups, obstructions
MATCH: True
```

**决策点（披露）**：探针的 `ok` / `counts` / `page_text_len_raw` 三个诊断键**没有搬**。
理由：PageModel 是 brief 声明的契约，骨架也写 `JSON.stringify({ /* PageModel */ })`；
这三个键不在任何 Go 结构里，`json.Unmarshal` 会静默丢弃，等于死输出（且 C11 已确认
这条脚本根本不能用 `cdp eval` 看）。若控制方要保留（例如 Task 3 想核对「600 字截断
之前有多长」），加回是 2 行。Task 3/4 的 brief 只用 PageModel 字段，不受影响。

### F4 —— 变异测试：断言非空转，但陷阱②③ **有盲区**（重要）

对已提交文件做 4 次变异，每次跑测试后 `git checkout --` 还原（`git status` 已确认干净）：

| 变异 | 结果 | 说明 |
|---|---|---|
| **M1** 逐 root 收文本改成 `return rt.innerText \|\| ''`（陷阱①复活，变量 `rt`） | ✅ **FAIL（抓到了）** | 该块被删后 `.children` 全脚本消失 → 第二条断言咬住 |
| **M1b** 同上但写 `r.innerText`（探针第一版的字面写法） | ✅ **FAIL（抓到了）** | 第一条断言直接咬住 |
| **M2** 遮挡判定退回「元素在不在命中栈里」（陷阱②复活） | ❌ **PASS（漏了）** | `composedAncestors` 仍因 `region` 在用而存在于脚本里 → 断言满足 |
| **M3** `region` 退回 parentElement-only 遍历（陷阱③复活） | ❌ **PASS（漏了）** | `getRootNode` / `.host` 仍因 `shadowDepth` + `composedAncestors` 而在 → 断言满足 |

**结论**：陷阱①的断言是真闸门；**陷阱②③的字符串断言只证明「脚本里有这个助手」，
不证明「遮挡判定/region 真的用了它」**。陷阱②③的真正闸门是 Task 3 的
`TestObserveShadowElementsNotFalselyOccluded` / `TestObserveShadowRegionNotAllBody`。

按 C33「已被接受，不加强」我**没有自行加强测试**（那是改 brief 的要求）。但变异
证据把盲区**精确定位到调用点**了，控制方若想在此收口，最小改法是把断言从
「脚本含 `composedAncestors`」改成「`occludedBy` 函数体内含 `composedAncestors`」。

### F5 —— 反糊弄证据（我没用注释糊 C33 那四条）

把导出脚本里**所有注释剥掉**后重新逐条检查：6 条断言仍全过，且被要求的构造都落在
真实代码行上：

```
trap1a: 无 r.innerText/root.innerText   PASS
trap1b: 含 .children                    PASS   （代码行 229, 230 —— perRoot 那个循环）
trap2:  含 composedAncestors            PASS   （代码行 57 定义 + 73 occludedBy + 120 region）
trap3:  含 getRootNode + .host          PASS   （.host 在 51 shadowDepth、62 composedAncestors）
C10:    含 __cdpQA                      PASS
D11:    无语义判断                      PASS
ALL PASS WITH ALL COMMENTS STRIPPED: True
```

### F6 —— 语法校验（字符串断言抓不到的那类错）

`node --check /tmp/observe_dump.js` → **SYNTAX OK**。导出的脚本里
`__cdpRoots` **定义恰好 1 次**（无重复注入），唯一一次 `function walk` 在
`pierceJS` 内部（内核自己的助手），**没有第二份自实现穿透** —— C10 达标。

### F7 —— C11 遵守情况

全程**没有用过 `cdp eval` 碰过这个脚本**；所有验证都走 Go（`internal` 包测试）。
`observeJS()` 自己套了 `withPierce`，且 doc 注释里写明了「EvalInFrame 不注入，
所以本脚本不能用 `cdp eval` 调试」，免得下一个人重踩。

### F8 —— **`internal/testdata/` 不存在**（给 Task 3 的预警，非本任务范围）

Task 3 brief 写着「Test data: `tools/cdp/internal/testdata/*.html`（Task 1 Step 4 已搬入）」，
实际**没有这个目录**，fixture 只存在于 `docs/probes/2026-09-16-observe-r3/fixtures/`
（`base.html` / `shadow.html` / `outer.html`），且 `git log --all -- 'tools/cdp/internal/testdata/*'`
为空 —— **从未提交过**。后果：Task 3 的 `serveFixtures` 会服务一个空目录，
`TestObserveLightDOM` 会在 `len(m.Actions) < 5` 上**失败**（不是 skip，因为 404 页
导航本身是成功的）。派 Task 3 前需要先把三个 fixture 搬进去。

---

## 5. 顾虑

1. **（高）陷阱②③的单元断言有盲区**（F4 的 M2/M3 实证）。本任务的"绿"在②③上**不构成
   实现正确的证据**，Task 3 的行为测试才是闸门。派 Task 3 时应写明：**若 Task 2 绿而
   Task 3 红，说明陷阱没真实现**（progress.md 已记这条诊断信号，我这里是它的实证）。
2. **（中）`contrastBand` 的背景色回退走 `parentElement`** —— 在 shadow 里会提前断链，
   于是 shadow 元素常常拿到 `contrast: ""`（JS 侧 null）。它是**空值而非错档**，
   不属于「静默出错」，且不在 C10 的两处之内，故**保留探针原样未改**。若要 shadow 站
   有 contrast 信号，需另开任务（改动点：背景回退也走 `composedAncestors`）。
3. **（中）容器内查询不穿嵌套 shadow**：选项组用 `g.querySelectorAll(...)`、遮挡物内
   找按钮用 `el.querySelectorAll('button,a')`、字段用 `el.closest('label')` —— 都是
   **元素级**查询，不下钻嵌套 shadow root。同为探针原算法、C10 未列，故未改。
   影响面：组/容器**自身内部**再嵌 shadow 时会漏收（是**少收**，不是错收）。
4. **（低）跨帧信息**：`OptionGroup` / `Obstruction` 没有帧路径字段，Task 4 的 `ObserveAll`
   合并时也不给它们打 `FramePath`（Task 4 brief 原文如此）—— 将来「这个选项组属于哪个
   帧」会丢。不是本任务的接口，仅记录。
5. **（低）环境相关**：本机 Chrome 在 `127.0.0.1:9222` 活着，包内 integration 测试**没有
   build tag**（靠 `NewClient` 失败才 skip）。我按裁定**没跑 integration**。附带实测：
   `go test ./internal/ -run TestSnapshotBDD` 现在是 **PASS**（Task 1 报告 §R6 当时记录为
   FAIL）—— 说明那条既有失败是环境相关的，未深究。
6. **（低）`OccludedBy *string` 的 `visible: true` 恒真**：`actions` 只从 `vis(el)` 里筛出，
   所以 `visible` 字段恒为 `true`，信息量为零（探针原样）。保留未改（契约字段要有），
   但 agent 别把它当信号用。这条值得进 skill 的能力边界说明。

---

# Task 2 修复轮 1（收口陷阱②③断言 + 补搬 fixture）

**Status: DONE**
**提交:** `4046623` test: 收口陷阱②③断言（落到函数体内）+ 补搬 R3 四档 fixture
**测试:** brief Step 4 六条 **6 PASS / 0 FAIL / 0 SKIP**；`go test ./... -v` **69 PASS / 0 FAIL / 0 SKIP**

> **实现一个字没动**：`internal/observe.go` 的 md5 与本轮前**完全一致**
> （`2b7a138966854dc47bee54ced47c31f5`，`git diff 63aa818 HEAD -- internal/observe.go` 为空）。
> 收口只改了测试与 fixture —— 这本身就是「实现早就是对的、只是断言空转」的旁证。

## ① 收口陷阱②③（裁定要求的验收：收口后它们**能**失败）

改法（照我上一轮报告给的最小改法）：断言从「整个脚本含 X」落到**函数体内**。
新增测试侧助手 `jsFuncBody(t, js, name)`：按 `function name(` 定位，花括号配平
（跳过引号内字符）取出正文。

再加了一层（**主动扩了一点，披露**）：`jsFuncBody` 返回的正文**先剥掉注释**
（`stripJSComments`）。理由：不剥的话，函数体内塞一行 `// composedAncestors`
就满足断言 —— 而「断言能被一段注释满足」正是这轮要根治的病根。不剥注释时我实测
它就是绿的（下表 M6）。若控制方认为这层不该加，去掉 `stripJSComments` 一个调用即可。

三条断言现在分别是：
- 陷阱①：不变（禁令 + `.children`）
- 陷阱②：`jsFuncBody(js,"occludedBy")` 内含 `composedAncestors`
- 陷阱③：`jsFuncBody(js,"composedAncestors")` 内含 `getRootNode` **且** `.host`
  ＋ `jsFuncBody(js,"region")` 内含 `composedAncestors`
  （两段都要：只查 region 的调用点的话，把助手掏空成 `return [el]` 仍是绿的）

### 变异验证：收口**前** vs 收口**后**（全部实测，无推定）

同一套变异，只改 `observe.go`（实现不当动），观察三条断言。
「收口前」列用的是 `63aa818` 版测试文件，「收口后」用 HEAD 版（`4046623`）。

| 变异（只改 observe.go） | 收口前 | 收口后 | 收口后失败行 |
|---|---|---|---|
| **M1** 逐 root 收文本 → `rt.innerText`（陷阱①） | ✅ 抓住 | ✅ 抓住 | `:19`（`.children` 消失） |
| **M1b** 同上、变量名 `r`（探针第一版字面写法） | ✅ 抓住 | ✅ 抓住 | `:16` ＋ `:19` |
| **M2** `occludedBy` 退回「元素在不在命中栈里」（陷阱②） | ❌ **漏** | ✅ **抓住** | `:114` |
| **M3** `region` 退回 `parentElement`-only（陷阱③） | ❌ **漏** | ✅ **抓住** | `:129` |
| **M4a** 助手正文整个换成 `return [el]`（`getRootNode`/`.host` 文本删除） | ❌ **漏** | ✅ **抓住** | `:126` |
| **M6** 注释糊法：体内塞 `// composedAncestors(el);` | ❌ **漏** | ✅ **抓住** | `:114` |
| **M5\*** 调用但丢弃结果（`composedAncestors(el); var anc = [el];`） | ❌ 漏 | ❌ **仍漏** | — |
| **M4b** 助手改成死代码（`n = null` 短路，文本仍在） | ❌ 漏 | ❌ **仍漏** | — |

**汇总：收口前抓住 2 / 漏 6；收口后抓住 6 / 漏 2（M5\*/M4b 是同一类）。**
裁定要求的「把那两处分别退回错误写法 → 测试必须变红」**已满足**，逐字输出：

```
--- FAIL: TestObserveJSMustNotTrustElementsFromPointAlone (0.00s)
    observe_traps_test.go:114: occludedBy 没有走合成树祖先链 —— shadow 元素会被整片误判为被遮挡
--- FAIL: TestObserveJSMustClimbThroughShadowHosts (0.00s)
    observe_traps_test.go:129: region 没有走合成树祖先链 —— 它的层级遍历会退化成 body
```

未变异时三条断言仍全绿（正例已复跑：`ok cdp/internal`），且 `observe.go`
md5 全程恒定 `2b7a138966854dc47bee54ced47c31f5` —— 变异与还原都发生在实现之外。

### 残余盲区（诚实说明，不粉饰）

M5\*（调用了但丢弃返回值）和 M4b（死代码）**字符串断言原理上盖不住** ——
它看的是文本，不是数据流。要把这类也盖住只有两条路：跑 JS（Task 3 的行为测试
`TestObserveShadowElementsNotFalselyOccluded` / `TestObserveShadowRegionNotAllBody`
正是干这个），或把脚本重构成可静态分析的结构（不值得）。
所以本轮的结论是**把「空转」收成「只剩原理性上限」**，不是「断言完备」。
Task 3 仍是真闸门；但至少现在，「陷阱②③没真实现」不会再以「单元测试全绿」的
面貌出现了（M2/M3 会当场变红）。

## ② 补搬 R3 四档 fixture（Task 1 遗漏）

```
tools/cdp/internal/testdata/  =  base.html  form.html  inner.html  outer.html  outer_same.html  shadow.html
```

来源 `docs/probes/2026-09-16-observe-r3/fixtures/`，**6 个全搬**（不只是 Task 3
brief 点名的 base/shadow/outer —— `outer.html` 的 iframe 指向 `inner.html`，
`form.html`/`outer_same.html` 是同源对照档，缺一个就少一档）。
搬前已确认 `testdata` 没有被任何 `.gitignore` 命中（`git add` 输出 `A` 确认入库）。

**新发现（给 Task 3 / Task 4 的预警）**：`outer.html` 里的 iframe 是**硬编码绝对 URL**：

```html
<iframe id="ci" src="http://localhost:8892/inner.html" ...>
```

`8892` 是探针那台 `python3 -m http.server` 的端口。Task 3 用的是
`httptest.NewServer`（**随机端口**），所以照搬这个 fixture 时，跨源帧会去连一个
不存在的 `localhost:8892` → 子帧加载失败 → Task 4 的
`TestObserveCrossOriginFrameMerge` 找不到 `len(FramePath) > 1` 的动作而**红**。
两条出路（属 Task 3/4 的取舍，我没动 fixture）：把 `8892` 换成运行时端口
（测试里改写 fixture 或改走 `outer_same.html` 的相对路径），或者起服务时占住 8892。
`outer_same.html` 用的是相对 `src="inner.html"`，天然适配 httptest，是现成的同源对照。

## 本轮跑了什么

```bash
cd /company/siteforge/tools/cdp
go test ./internal/ -run 'TestObserveJS|TestPageModelJSONShape' -v   # 6 PASS / 0 FAIL / 0 SKIP
go test ./... -v -count=1                                            # 69 PASS / 0 FAIL / 0 SKIP
gofmt -l internal/observe_traps_test.go                              # 无输出（干净）
```

**SKIP = 0 需要解释吗** —— 不需要，因为本机 Chrome 在 `127.0.0.1:9222` 活着，
包内 integration 测试全都**真跑了**（`cdp/internal` 包耗时 14.8s 就是证据，
不是 0.0Xs 的空跑）。这也是本轮唯一一次跑了 integration（裁定要求的 `go test ./...`）。
（`-count=1` 是刻意的：不加的话第二次跑会命中 go 的测试缓存，打印 `(cached)` ——
虽然缓存命中本身也证明「文件与上次 69 绿时逐字节相同」，但报告里要的是真跑数。）

---

# Task 2 修复轮 2（两条空转断言收口：C10 唯一闸门 + 陷阱①禁令）

**Status: DONE**
**提交:** `2fea194` test: 修复轮 2 —— 两条空转断言收口（C10 唯一闸门 + 陷阱①禁令改名字无关）
**测试:** `go test ./internal/ -run TestObserveJS -v -count=1` → **5 PASS / 0 FAIL / 0 SKIP**；
`go test ./... -v -count=1` → **69 PASS / 0 FAIL / 0 SKIP**（`cdp/internal` 14.8s，integration 真跑）
**实现未动**：只有 `observe_traps_test.go` 一个文件（+75 −9）。

## ① C10 的唯一闸门：`TestObserveJSMustUsePierceHelper` 原本恒真

裁定说的机制我复核了，**成立**：`observeJS()` = `pierceJS` 前导段 + 正文，
而前导段本身就定义了 `__cdpQA`（`var __cdpQA = function(sel){…}`），
正文也调它 —— 于是 `strings.Contains(js, "__cdpQA")` 无论如何都真。
新写法（按裁定，但**没照抄**审查方给的 `"function __cdpRoots"`
——那是错的，源码实际是 `var __cdpRoots = function(root) {`）：

```go
if n := strings.Count(js, "__cdpRoots = function"); n != 1 { … }   // 注入证据
body := observeBody(t)                                             // 剥掉前导段
strings.Contains(body, "__cdpRoots(document)")                     // 正文证据
strings.Contains(body, "__cdpQA(")                                 // 正文证据
!strings.Contains(body, ".shadowRoot")                             // 无自实现 walk
```

`observeBody` 的切分点取自 `pierceJS` **常量本身**（内核定义注入段的地方），
不靠猜文本 —— 比正则在返回值里找边界稳。

## ② 陷阱①禁令：不再依赖变量名

原禁令 `r.innerText` / `root.innerText` 在变量改名 `rt` 之后确实**再也打不着**
（`rt.` 不含 `r.`；我上轮 M1b 的「抓住」是因为变异时故意用回了旧名字 —— 裁定说得对）。
改成**从代码里读出变量名**再禁：

- `rootsVars(t, body)`：正则读 `([A-Za-z_$]\w*)\s*=\s*__cdpRoots\(document\)` 拿到 roots 列表变量（`RS`），
  再读 `<RS>\.map\(\s*function\s*\(\s*(\w+)\s*\)` 拿到每个 root 的变量名（`rt`）。
- 禁令：`(^|[^A-Za-z0-9_$])<name>\s*(\[[^\]]*\])?\s*\.innerText`，对**两个名字**各跑一次
  （带上界符避免 `XRS` 误命中；带下标形式是为了盖 `RS[1].innerText`）。
- 正例不误伤：正确代码是 `rt.body.innerText`（receiver 是 `rt.body`，不是裸 `rt`）→ 不匹配。

## ③ 自查发现：我自己的新断言第一版也是**注释可满足**的（变异 M11 抓的）

把 `__cdpRoots(document)` 改成 `__cdpRoots(document.documentElement)` 后，
`TestObserveJSMustUsePierceHelper` **仍然绿** —— 因为正文里那行说明性注释
`// ── 穿透：内核助手。__cdpRoots(document) = [document, ...] ──` 把它满足了。
正是这轮要根治的病根，出现在我刚写的新断言上。
修法：`observeBody` 也过 `stripJSComments`（上轮为 `jsFuncBody` 写的那个）。
**修完 M11 转红**（`:197`）。反向也验了：注释里提 `.shadowRoot` 而代码没自实现 walk → **不假红**。

## 变异矩阵（9 条 + 2 条反向，全部实测，无推定）

| 变异（改实现或内核，测试不动） | 结果 | 咬住它的断言 |
|---|---|---|
| **M7** `withPierce` 变空操作（注入没了） | ✅ 红 | `:193` count = 0（C11 证据） |
| **M8** 前导段注入两份 | ✅ 红 | `:193` count = 2 ＋ `:203` `.shadowRoot` |
| **M9** 正文加回自实现 walk（`.shadowRoot`） | ✅ 红 | `:203` 正文含 `.shadowRoot`（C10） |
| **M10** 正文退回裸 `document.querySelectorAll` | ✅ 红 | `:200` 正文无 `__cdpQA(` |
| **M11** 正文不再取 document 的 root 列表 | ✅ 红 | `:197` 正文无 `__cdpRoots(document)`（修③前**漏**） |
| **M12** 取 `rt.innerText`（同一名字） | ✅ 红 | `:26` 禁令（＋`:17` `.children`） |
| **M13** 回调参数改名 `root` + `root.innerText` | ✅ 红 | `:26` 禁令 |
| **M14** 回调参数改名 `q` + `q.innerText`（禁令**从未写过**这个名字） | ✅ 红 | `:26` 禁令 → **名字无关性证据** |
| **M15** 下标形式 `RS[1].innerText` | ✅ 红 | `:26` 禁令（listVar 分支） |
| **反向 M16** 代码不用 `__cdpQA(`，但留一行注释说「用 `__cdpQA(` 穿透」 | ✅ 红 | `:200` —— 注释糊不动 |
| **反向 M17** 注释里提 `.shadowRoot`，代码没有自实现 walk | ✅ 绿（**不误伤**） | — |

正例（未变异）全程绿。

## 本轮没做的（裁定已记 ledger，备终审）

整脚本作用域断言与内核文本耦合 · `contrastBand` 仍走 `parentElement`（shadow 里得空值）·
JS 键名与 struct tag 无绑定 · `page_text` 截断无信号 · `jsFuncBody` 不跳注释。

> 最后一条我核对了一下，把范围说准：`jsFuncBody` **返回的正文是剥了注释的**，
> 但它是**先配平花括号、后剥注释** —— 配平那一步不跳注释也不跳正则字面量。
> **实测**（2026-09-16）：在 `region` 正文的注释里塞一个 `{` → 配平错位 → 正文被
> **延长**（吞掉后面的代码），两条断言**仍然绿**。
> 也就是说这条局限的失效方式是**静默变弱**（断言退回成近似「整脚本」），**不是报错** ——
> 我原本想写「会 Fatalf 报错、是响的」，实测把这个说法推翻了，按实测写。
> 形态上它和本轮那两条病同族（不报错、只是悄悄弱），值不值得修请控制方定；
> 要修的话把配平扫描也改成先剥注释再数括号即可。

## 一条顺手观察（非本轮范围）

`gofmt -l internal/` 会点出 `form.go` / `form_test.go` / `snapshot_bdd_test.go` 三个文件 ——
**既有的**（`git status` 对它们干净，不是本轮引入）。`observe*.go` 干净。
