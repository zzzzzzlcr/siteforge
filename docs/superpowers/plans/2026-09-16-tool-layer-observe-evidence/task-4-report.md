# Task 4 报告：跨帧枚举与合并（`ObserveAll`）

提交 `d64ab93`（`feat: observe 跨帧枚举与合并 —— 同源策略下单次 eval 看不见跨源帧`）

| 文件 | 变更 |
|---|---|
| `tools/cdp/internal/observe_frames.go` | **新建**：`ObserveAll` + `observeInto` + `mergeFrameModel` + 两个小 helper |
| `tools/cdp/internal/observe_integration_test.go` | **修改**：+263 行 —— 跨源那条（C1 从 Task 3 移来）+ 三个 helper；文件头那段「本文件不得出现 ObserveAll」改成指向文末 |
| `tools/cdp/internal/testdata/README.md` | **修改**：表格里「未启用」→ 已启用，并补一节说明端口改写在服务层（C3/C39 的坑，防止下一个人再踩） |

`observe.go` / `client.go` / 其它 fixture **一行未动**。临时探针 `zz_probe_scratch_test.go`、
`zz_probe2_scratch_test.go` 取证完已删，**未提交**（`git status` 干净）。

---

## 1. ⚠️ 最重要的发现：`page.getFrameTree` **不报跨源子帧** —— 所以枚举不能用它

这是本任务最有价值的产出，也是**唯一一处偏离 brief 代码草稿**的地方。

brief Step 2 的草稿写的是 `tree, err := c.GetFrameTree()`（C9 只纠正了类型，没质疑
数据源）。照着写出来的 `ObserveAll` **能编译、能跑、能返回 nil error**，但对**所有**
跨源 iframe 视而不见 —— 而「看得见跨源 iframe」正是这个函数存在的全部理由。
换句话说：它会以「一条都不报」的形式静默失效，正是 C3/C39 描述的那种假失败。

### 实测（三个探针，本机 Chrome 150.0.7871.124 headless，`127.0.0.1:9222`）

**A. 同源对照（`outer_same.html`，相对路径 `src`）—— 裸 `GetFrameTree()` 报子帧**

```
frameID=E5DDABB7C915EBF68A2AFBF0F4D7AA67 name="" url=http://127.0.0.1:36797/outer_same.html
  frameID=FE79CEAF701086A9C4C8DAACF6A689E4 name="ci" url=http://127.0.0.1:36797/inner.html
```

**B. 跨源（`outer.html`，`src` = `localhost:<port>`）—— 裸 `GetFrameTree()` 一个都不报**

```
B1. page.GetFrameTree        → 子帧数 0
    frameID=E5DDABB7C915EBF68A2AFBF0F4D7AA67 url=http://127.0.0.1:36797/outer_dyn.html
B2. GetFrameTreeWithEvents   → 子帧数 1
    frameID=E5DDABB7C915EBF68A2AFBF0F4D7AA67 url=http://127.0.0.1:36797/outer_dyn.html
      frameID=F0D453FED9F597EE5784D3BC8C9107CD url=http://localhost:36797/inner.html
```

裸协议层的原始 JSON（绕开 Go 结构体，证明不是解析问题）—— **整个响应里根本没有
`childFrames` 这个键**：

```json
{"frameTree":{"frame":{"id":"E5DDABB7C915EBF68A2AFBF0F4D7AA67","loaderId":"BF4923C1C0CDC9489ED68983CC084B44",
"url":"http://127.0.0.1:44759/outer_dyn.html","domainAndRegistry":"","securityOrigin":"http://127.0.0.1:44759",
"securityOriginDetails":{"isLocalhost":true},"mimeType":"text/html","adFrameStatus":{"adFrameType":"none"},
"secureContextType":"SecureLocalhost","crossOriginIsolatedContextType":"NotIsolated","gatedAPIFeatures":[]}}}
```

**不是 auto-attach 造成的**：在 page session 上 `Target.setAutoAttach({autoAttach:true,
flatten:true})` 后等 2s 再查 —— 两次响应**逐字节相同**，子帧数仍是 0（探针 2 ④）。

**子帧自己是知道爹是谁的**（同一轮，子帧 target 自己的 session）：

```json
{"frameTree":{"frame":{"id":"F0060DA68E16997570DF113FD60866B1",
"parentId":"E5DDABB7C915EBF68A2AFBF0F4D7AA67", ... "url":"http://localhost:44139/inner.html" ...}}}
```

所以不是「Chrome 不知道」，是**page 目标的 `Page.getFrameTree` 这一路不带 OOPIF 子帧**。

### 结论与取舍

`ObserveAll` 改用 **`GetFrameTreeWithEvents`**（HEAD 里已有的东西，`client.go:238`）——
它是「`page.getFrameTree`（同源子帧全、层级准）」∪「`DOM.getDocument(pierce=true)`
（穿透带出 OOPIF 的 contentDocument，Task 1 刚修好 `Name` 的 `collectFramesFromDOM`）」，
**两种来源都要**：只用前者漏跨源，只用后者漏同源（同源帧没有独立 target，
`Target.getTargets` 那条路也看不见它）。这不是偏好，是这个环境下唯一的并集。

C9 照办：**没有新建 `FrameNode`**，直接递归 `*page.FrameTree`（`.Frame.ID/.URL/.Name`
+ `.ChildFrames`）。

### 跨源子帧的 eval 走的是 OOPIF 回退路（顺带取证）

`Observe(childFrameID)` 能成功，靠的是 `EvalInFrame` 的第二条路。证据（探针 2 ②）：

```
② GetFrameOrCreateContext(子帧) -> err = failed to create isolated world for frame
   F0060DA68E16997570DF113FD60866B1: No frame for given id found (-32602)
```

page 目标上给跨源子帧建 isolated world **必然失败**，于是落到 `evalInOOPIFFrame`；
它能成是因为 **OOPIF 的 targetId 就等于 frameId**（探针 2 ①，同一轮实测）：

```
① target type=page    id=E5DDABB7C915EBF68A2AFBF0F4D7AA67 url=http://127.0.0.1:44139/outer.html
① target type=iframe  id=F0060DA68E16997570DF113FD60866B1 url=http://localhost:44139/inner.html
   子帧 frameID      = F0060DA68E16997570DF113FD60866B1   ← 同一个
```

**可靠性边界（说清楚，别当成普适结论）**：① 我只在本机这一版 Chrome（150 headless）上
验了；② 但 `GetFrameTreeWithEvents` 是并集，**不管**哪一路看得见它都收得进来，
所以换版本/换站点的风险面比裸 `GetFrameTree` 小得多。

---

## 2. `ObserveAll` 怎么合并的

```go
func (c *Client) ObserveAll() (*PageModel, error)   // observe_frames.go
    tree, err := c.GetFrameTreeWithEvents(frameEnumerationWait)
    c.observeInto(merged, tree, []string{mainFramePath}, true)
```

**帧路径怎么定**（`observeInto`，先序 DFS）：

| 帧 | `FramePath` | 约定 |
|---|---|---|
| 主帧（帧树根） | `["main"]` | 常量 `mainFramePath`；`Observe` 对主帧传 `""` |
| 第 n 层子帧 | `["main", <frameId>, …]` | 逐层追加**真实 frameId** |

- 每条 `Action` / `Field` 都**盖**上自己那一帧的路径（值拷贝，改的是副本）；
  子帧里那些动作/字段因而带着 `["main", <childId>]`，agent 才知道动作发给哪一帧。
- 子路径用 `append(append([]string{}, path...), id)` **复制**再拼 ——
  直接 append 到共享底层数组会让兄弟帧互相串路径。
- `URL` / `Title` 取**第一个取到的帧**（先序第一个就是主帧）：agent 要的是「这个 tab 现在停在哪」。
- `PageText` 逐帧拼（中间补一个空格，不留前导空格）；`ShadowRoots` 逐帧累加
  （所以合并后的值是**全页**的 shadow root 数，不是某一帧的）。
- `OptionGroups` / `Obstructions` 原样并 —— 见顾虑 2（契约里没有 FramePath，跨帧归属丢失）。

**单帧失败怎么处理**（与 brief 草稿**不同**，这是有意的）：

| | brief 草稿 | 本实现 | 为什么 |
|---|---|---|---|
| 主帧失败 | `return nil`（吞掉，整体返回空模型 + `nil` error） | **整体报错** `主帧 observe 失败: %w` | 空模型 + nil error 正是本项目反复栽的**假成功**（执行器把 deferred 的 form 步骤算成过，实测 13/13）。主帧取不到 = 这份观测整个没有意义，必须说 |
| 子帧失败 | `return nil`（吞掉，**没有任何痕迹**）—— ⚠️ 草稿的注释写着「要在结果里留下痕迹，不能静默吞掉」，**代码却没做** | 不整体失败，但往 `merged.Obstructions` 追加 `{kind:"frame-error", selector:<frameId>, text:<err 前 160 字符>}` | 注释说的才是需求。「少了一帧」和「那一帧本来就空」在结果里长得一模一样，agent 分不出来 |

「子帧失败不整体失败」这条**保留**了草稿的决定（广告/追踪帧本来就取不到），
所以行为上不冲突，只是多了痕迹。用 `Obstructions` 是因为它是 `PageModel` 里唯一
能承载「环境异常」的字段，且**不用改 Task 2 定的契约**（`Selector` 放帧 ID，
agent 顺着能查是哪一帧）。`text` 按 rune 截断（`firstRunes`），不切碎 UTF-8。

**枚举前的等待**：`frameEnumerationWait = 500ms`（`GetFrameTreeWithEvents` 的参数），
给**运行时注入**的 iframe 一点时间落到 DOM 上。取值是权衡：越小越跟手，越大越不容易漏帧。

---

## 3. 跨源那条测试怎么处理端口的

**选了哪条出路**：C3/C39 给的「动态生成 outer 页」—— 但落在**服务层**，不是
「测试里另写一个 outer 页」：

```go
// serveCrossOriginFixtures：拦下 /outer.html，把 fixture 里的
// http://localhost:8892 换成本次 httptest 真正在用的端口，再发出去
_, port, _ := net.SplitHostPort(r.Host)          // 端口从请求自己的 Host 取，不抓 srv.URL
out := strings.ReplaceAll(string(raw), hardcodedFixtureOrigin, "http://localhost:"+port)
io.WriteString(w, out)                            // 其余路径照旧走 http.FileServer(testdata)
```

**为什么这么选**（而不是「测试里拼一个 outer 页」或「改用 `outer_same.html`」）：

1. **页面结构仍然一字不差来自 fixture** —— 唯一的合成物是 origin 里的端口号。
   测试里拼 HTML 的话，fixture 就不再是被测页面，`outer.html` 会退化成没人读的装饰。
2. **改写当场自证**：换不上就 `t.Errorf` + 500，直接停在这条测试里，
   **不退化成**「子帧连不上 → 没有子帧」那条最难查的路径。fixture 将来改了端口，
   会得到一句明确的错误，而不是一个看不出所以然的红。
3. `outer_same.html` 那条出路**不能选** —— 它是同源对照，验不到跨源，
   而这条测试的全部意义就是跨源。
4. 端口从 `r.Host` 取（而不是闭包抓 `srv.URL`）—— 免掉「server 已开始监听、变量还没赋值」
   那个理论竞态，代码也更短。

**跨源关系保持不变**：外层页走 `srv.URL`（httptest 绑 `127.0.0.1`），子帧走 `localhost`
—— 主机名不同 ⇒ 既不同源也不同 site ⇒ 真 OOPIF（实测见第 1 节）。

**不盲睡**：`waitForChildFrame` 轮询到子帧真的出现在帧树里才开始断言（超时 10s，
失败时把整棵帧树打出来）。这是**前置条件**：子帧没起来的话后面每条断言都会以
「合并里没有子帧的东西」红，那就没法区分「子帧没起来」和「合并没生效」。
轮询**必须**用 `GetFrameTreeWithEvents` —— 用裸 `GetFrameTree` 轮询在跨源子帧上
**必然超时**（第 1 节的实测）。

**这条测试的证据链是三段的**（只留第三段就能被同源误配骗绿）：

1. **反证**：主帧单帧 `Observe("")` 里**没有** `#fn` / `#submit`（同源策略的后果）
2. **自证跨源**：`document.getElementById('ci').contentDocument === null` 必须成立
   （同源的话拿得到 Document）—— 断言「这条测试测的确实是跨源」
3. **合并**：`ObserveAll()` 后 `#fn` / `#submit` **在**结果里，且 `frame_path == ["main", <childId>]`

再加：子帧字段 `shadow_depth >= 2`（`inner.html` 的表单在两层 shadow 里 ⇒ 逐帧那一趟
连穿透一起在子帧里生效了）、`page_text` 同时含主帧和子帧正文（并集不是覆盖）、
`ShadowRoots >= 2`、**正常路径上不得出现 `frame-error` 痕迹**（有的话说明有帧静默失败了）。

---

## 4. 实测结果（真实耗时）

### `go test ./internal/ -run 'TestObserve' -v -count=1` → **PASS=10 / SKIP=0 / FAIL=0**（包 6.99s）

```
--- PASS: TestObserveLightDOM (1.56s)
--- PASS: TestObserveShadowPageTextIsNotEmpty (1.55s)
--- PASS: TestObserveShadowElementsNotFalselyOccluded (1.55s)
--- PASS: TestObserveShadowRegionNotAllBody (1.55s)
--- PASS: TestObserveCrossOriginFrameMerge (0.77s)      ← 本任务新增
--- PASS: TestObserveJSMustHandleShadowText (0.00s)
--- PASS: TestObserveJSMustNotTrustElementsFromPointAlone (0.00s)
--- PASS: TestObserveJSMustClimbThroughShadowHosts (0.00s)
--- PASS: TestObserveJSMustUsePierceHelper (0.00s)
--- PASS: TestObserveJSMustNotEmitSemanticJudgements (0.00s)
ok  	cdp/internal	6.987s
```

跨源那条的日志：

```
子帧 frameID=2B3387F553594D3567A1826B3EADF50D url=http://localhost:42827/inner.html
主帧单帧观测：fields=0 actions=0 text="跨源 iframe 外层"（outer.html 只有 h1 + iframe，本就该是空的）
合并后：fields=3 actions=6 shadow_roots=2 page_text=137字
```

**抖动检查**：`-count=3` 连跑 3 遍 → 0.80s / 0.79s / 0.77s，3/3 PASS，
合并结果逐项一致（fields=3 actions=6 shadow_roots=2 page_text=137字）。
第一次跑（未优化前）也是 0.79s。

### `go test ./... -v -count=1` → **PASS=64 / SKIP=0 / FAIL=0**（EXIT=0）

```
ok  	cdp/cmd	0.005s
ok  	cdp/internal	26.500s
```

**SKIP=0 不需要解释**（Chrome 在 `127.0.0.1:9222` 活着，1 个 page 目标；跑完仍是 1 个，
没关它，也没把目标数跑成 0）。

**复跑（提交 `d64ab93` 上、工作区干净时再量一遍）**：`TestObserve*` → 10 PASS / 0 SKIP /
0 FAIL（包 **7.006s**，跨源那条 **0.78s**）；`go test ./...` → 64 PASS / 0 SKIP / 0 FAIL
（`cdp/cmd` 0.006s、`cdp/internal` **26.035s**）。与上表同一量级，逐秒的差是正常的用例抖动。

**别把 64 和 Task 2 报告里的 69 对不上当成丢了测试** —— 我按同一命令量了基线：
把测试文件换回 HEAD 版本跑（`git show HEAD:...` 临时覆盖，跑完还原）→ **63 PASS**；
带我的改动 → **64 PASS**。**差集恰好就是新增的 `TestObserveCrossOriginFrameMerge`**，
一条没少（`/tmp/head_names.txt` vs `/tmp/now_names.txt` 的 diff 只有新增那一行）。
Task 2 的 69 与本次口径不同（大概是它把某些输出行也计进去了），**不是回归**。

`go vet ./...` 干净；`gofmt -l` 对我这两个文件无输出
（`form.go` / `form_test.go` / `snapshot_bdd_test.go` 的既有未格式化状态我没碰）。

---

## 5. 变异测试：证明断言真的有牙

**没有用例失败**（这一节的失败是**故意植入**的变异，用来证明断言不是空转 ——
本项目最怕的正是「跳过和通过长得一样」「对任何实现都绿」）。

**变异 1 —— 把 `ObserveAll` 的枚举换回草稿写的裸 `GetFrameTree()`**：

```
--- FAIL: TestObserveCrossOriginFrameMerge (0.27s)
    observe_integration_test.go:383: 合并结果里没有子帧的字段 #fn —— 跨帧合并没生效。fields=0
```

红得**精确**：`waitForChildFrame` 前置条件仍通过（它用并集），所以失败被准确归因到
「合并没生效」，而不是含混的「没有子帧」。这条同时证明第 1 节那个改动是**承重的**，
不是装饰。

**变异 2 —— `mergeFrameModel` 里不给 Actions 盖 `frame_path`**：

```
--- FAIL: TestObserveCrossOriginFrameMerge (0.78s)
    observe_integration_test.go:398: #submit 的 frame_path = [main]，
        应为 [main 817E075806B333429E8B1BE55A033EEB]
```

两处变异跑完都**已还原**（`diff -q` 校验过），最终提交的代码是干净版本。

---

## 6. 自查发现

1. **`GetFrameTreeWithEvents` 内部会静默吞掉 DOM 查询错误**（`client.go:259`
   `return ft, nil`）。真发生的话，`ObserveAll` 会**静默**退化成「只有同源子帧」——
   正是第 1 节那个坑的另一种形态，而且这次没有任何信号。我没有改 `client.go`
   （不在本任务文件清单里，且它被 `ResolveIframeSelector` 等共用）。见顾虑 1。
2. **`frame_path` 的 `"main"` 是给人读的标记，不是能回传的 frameID**：本包对主帧的约定
   一律是**空串**（`EvalInFrame` / `ClickElement` / `Observe`）。agent 若把 `"main"`
   原样塞回 `frameID` 参数会失败。已写在 `mainFramePath` 的注释里（brief 写死了这个值，
   我照办）。见顾虑 2。
3. 子帧正文里的 `PageText` 是**逐帧拼接**的，总量没有上限（单帧 600 字 × N 帧）。
   `page_text` 的 600 字上限是**单帧**口径，合并后不再成立。
4. 测试用的 `#fn` / `#submit` / `"Get your free quote"` 都绑死在 `inner.html` 上。
   这是有意的（断言要咬住真内容，不能只看数量），但 fixture 一改这几条就得跟着改。
5. 三个 helper（`hardcodedFixtureOrigin` / `waitForChildFrame` / `describeFrameTree` /
   `findAction` / `findField`）都放在集成测试文件里、包级可见；`internal` 包测试文件
   较多，将来重名会编译不过（`go vet` 目前干净）。
6. 跑测试会把那个 page 目标导航到本测试的 httptest URL 并在跑完后停在已关闭的 server 上
   （Task 3 报告里已记录同一现象）。**目标数仍是 1**，没关它。

---

## 7. 顾虑

1. **`ObserveAll` 的跨源可见性依赖 `DOM.getDocument(pierce=true)` 那一刻成功。**
   若那次 DOM 查询失败（`GetFrameTreeWithEvents` 内部吞掉错误、静默降级），
   `ObserveAll` 会**安静地**只剩同源子帧 —— 没有错误、没有痕迹，正好是本项目最忌的
   假成功形态。根治要动 `client.go`（让 `GetFrameTreeWithEvents` 把 DOM 错误暴露出来，
   或让 `ObserveAll` 自己走一遍 pierce 并检查），**不在本任务文件清单里，我没动**。
   建议给 Task 5/6 或后续留一条：`ObserveAll` 至少在「帧树里没有任何子帧」时不做任何
   静默假设 —— 但注意页面真的没有 iframe 时那也是正常结果，无法靠这个区分，所以
   正解还是让枚举层把错误说清楚。
2. **`OptionGroup` / `Obstruction` 没有 `FramePath` 字段**，跨帧合并时它们的**归属是丢失的**
   （一个来自子帧的 cookie 横幅，和主帧的混在同一个数组里，agent 分不出来；
   更实际的是 `obstructions[].selector` 到底该在主帧还是子帧执行，无从判断）。
   这是 Task 2 定的 `PageModel` 契约限制，改契约的代价（Task 6/7 消费者 + JSON 形状测试）
   超出本任务。代价若错：agent 在跨源遮罩上可能把动作发错帧。
3. **`"main"` 这个标记与「主帧 = 空串」的工具约定不一致**（第 6 节 2）。brief 写死了
   `[]string{"main"}`，我照办并加了注释；如果 Task 6 的 CLI 直接把 frame_path 透给
   agent、而 agent 又把它当 frameID 用，就会踩。建议在 CLI 层把 `"main"` 翻成 `""`，
   或统一成主帧也用真实 frameId（那会改动 brief 指定的值，我没自作主张）。
4. **第 1 节的结论只在本机这一版 Chrome 上取过证**（150.0.7871.124 headless）。
   若 `page.getFrameTree` 在别的版本上报 OOPIF 子帧，`GetFrameTreeWithEvents` 仍然正确
   （并集，多出来的会被 `collectExistingFrameIDs` 去重）—— 这个方向的错不会出问题；
   反过来（某版本连 DOM 穿透也不给 OOPIF）则见顾虑 1。
5. 本任务的验收只覆盖**一级**跨源子帧。**嵌套**（跨源里的跨源、跨源里的同源）
   只靠 `collectFramesFromDOM` 的递归保证 + `frame_path` 逐层拼接，
   **没有测试咬住**。如果 Task 5/6 要处理嵌套 iframe（Stripe 内嵌收银台那类），
   建议补一条两层嵌套的跨源 fixture。

---

## 附：怎么复现第 1 节的结论（约 30 行）

临时探针（**跑完删掉，别提交**）：`httptest` 起 fixture 服务 + 服务层换端口（同第 3 节），
导航到 `127.0.0.1:<port>/outer.html`，然后

```go
c.GetFrameTree()                    // 期望：ChildFrames 空（跨源被漏掉）
c.GetFrameTreeWithEvents(500*time.Millisecond)  // 期望：1 个子帧
c.Observe(子帧ID)                    // 期望：url=localhost、fields=3、shadow_roots=2
```

再配一次 `Target.getTargets`（browser WS）确认 `type=iframe` 的 `targetId` 与子帧
`frameID` 相同，以及 `GetFrameOrCreateContext(子帧)` 报 `No frame for given id found`。

---

# Task 4 修复轮 1（审查四条 + 顺带四条，提交 `9219e93`）

审查结论：Needs fixes。① 自证链有一环是废的（最重要）、②③ 合成一个修法（诊断通道）、
④ 并集没去重保护，外加 #5–#8 四条小项。下面逐条写**怎么修的、怎么验的**。

---

## ① 自证链里那一环确实是废的（审查说得对，我复核了）

审查的判据我**在代码里核实过**：`shadow.go` 的 `__cdpRoots` 只做
`querySelectorAll('*')` + `.shadowRoot`，**从不碰 `contentDocument`**；而
`querySelectorAll` 本身也不跨帧。所以主帧的单帧观测**看不见任何 iframe 的内容，
同源也一样** —— 原来那条「主帧看不见 #fn」只演示了「为什么要有 ObserveAll」，
**判不了源是否相同**。删掉。

**新判据 1（主判据）**：从**子帧自己**取 `location.href`，断言 host ≠ 主帧 host：

```go
if err := c.EvalInFrame(childID, "location.href", &childHref); err != nil { t.Fatalf(...) }
if !strings.HasSuffix(childU.Path, "/inner.html") { t.Fatalf("子帧没加载起来或加载失败") }
if mainU.Host == childU.Host { t.Fatalf("子帧与主帧**同源**（host 都是 %q）…", mainU.Host) }
```

一条同时担保三件事：**跨源**（host 不同）、**子帧文档真的 commit 了**（取得到 href
且就是 `inner.html`）、**跨源子帧的 eval 确实能跑**（走 OOPIF 回退）。后两条顺带堵掉
了 `waitForChildFrame` 那个「帧存在 ≠ 文档就绪」的假失败口子。

原来的 `contentDocument === null` 那条**降为判据 2**（不再删）：判据 1 先证明了文档
已 commit，所以它不再有「同源且尚未 commit 时也是 null」那个没验的口子，
转而证明另一半 —— 父帧 JS 确实够不到子帧，**必须**逐帧 eval。

### 本轮验收：同源变异（把 fixture 的源改成同源）

变异方式：`serveCrossOriginFixtures` 里 `want := "http://localhost:" + port`
→ `"http://127.0.0.1:" + port`（子帧与主帧同源）。这测的是**夹具的源**，
不是实现 —— 正是审查指出我上一轮没测到的那一维。

**M-A：同源夹具 + 守卫原样 → 红（必须）**

```
=== RUN   TestObserveCrossOriginFrameMerge
    observe_integration_test.go:349: 子帧 frameID=53A537D1F50E897E78489D512FDFDFC3 url=http://127.0.0.1:34339/inner.html
    observe_integration_test.go:375: 子帧与主帧**同源**（host 都是 "127.0.0.1:34339"）—— 这条测试测不到跨源合并，先查 fixture 的 iframe src 是不是被改成了同源地址
--- FAIL: TestObserveCrossOriginFrameMerge (0.26s)
FAIL	cdp/internal	0.270s
```

**M-B：同源夹具 + 两条守卫降级为 `t.Logf` → 绿（证明判据 3 单独判不了源）**

```
    observe_integration_test.go:375: 子帧与主帧**同源**（host 都是 "127.0.0.1:45289"）…
    observe_integration_test.go:389: 主帧能碰到子帧文档（contentDocument 探测 = "same-origin"）…
    observe_integration_test.go:467: 合并后：fields=3 actions=6 shadow_roots=2 page_text=137字 diagnostics=0
--- PASS: TestObserveCrossOriginFrameMerge (0.77s)
```

**M-B 绿 = 审查的诊断被实测坐实**：夹具同源、守卫拿掉之后，判据 3（合并断言）
**原样通过**（fields=3 actions=6 shadow_roots=2，与跨源时逐项相同）——
`ObserveAll` 对同源子帧一样工作，所以合并断言**不是**跨源判据。
两次变异跑完都已还原（`diff -q` 校验 byte-identical）。

---

## ②③ 诊断通道：`diagnostics` 与 `obstructions` 彻底分开

`PageModel` 新增（**契约新增**，规格 §4.3 由控制方同步）：

```go
Diagnostics []Diagnostic `json:"diagnostics"`     // {Kind, Detail, FramePath}
```

- **`obstructions` 恢复成只装页面遮挡物**（cookie 横幅那类，带 selector /
  dismiss_selector 语义）。`ObserveAll` 不再往里面塞任何帧相关的东西 ——
  帧的问题全部走 `diagnostics`。
- kind 常量**导出**（跨 JSON 边界到 CLI / agent）：
  `DiagKindFrameError = "frame-error"`、`DiagKindFrameBlind = "frame-blind"`。
- `frame-error`（子帧观测失败）：`Detail` 放错误（按 rune 截 160），`FramePath` 指出是哪一帧。
- `frame-blind`（枚举可能退化了）：见下。

### ③ 的退化守卫 —— 我把它从「只在主帧」改成**逐帧**对账（有实测理由）

审查给的取数是「主帧上 `__cdpQA('iframe').length`」。我照做之后，顺手拿它去量
**两级嵌套**的场景，发现**盲区是逐父帧成立的**（见下面「新发现」），只在主帧对账
正好漏掉那一种。所以最终实现是**每一帧各自对账**：

```go
// observeInto 里，这一帧 observe 成功之后：
c.checkFrameCoverage(merged, frameID, ft, path)     // 它的 DOM 里几个 iframe 元素 vs 枚举到几个子帧
```

代价是**每帧多一次 eval**（读操作，`withPierce` 要自己套）。取数可靠的理由：
iframe **元素**在它自己那一帧的文档里，**与子帧是什么源无关**，所以这条线索
**不依赖子帧可达性**，是独立的一路。

实测三种场景下守卫的表现（探针，夹具在测试里动态生成）：

| 场景 | 帧树 | 对账结果 |
|---|---|---|
| 扁平跨源（本轮交付的那条） | main + OOPIF 子帧 | **不喊**（1 个 iframe 元素 = 1 个子帧）✅ 无误报 |
| 两级嵌套（同源） | main + 子帧 + 孙帧 | **不喊**（每层都 1:1）✅ |
| 两级嵌套（**跨源里再嵌一层**） | main + OOPIF（**孙帧不见了**） | **喊**：`kind=frame-blind`，`path=[main 098A1CF…]` ✅ |

守卫本身也要被验证「会响」，否则它就是一段没人见过它响的代码：测试里**判据 4**
把帧树掐成「只剩主帧」（这正是 DOM 穿透失败时 `GetFrameTreeWithEvents` 返回的形态）
→ 断言必须出现 `frame-blind`。

---

## ④ 并集去重：`seen` 集已加 —— 但实测**没复现出重复**（如实说明）

`observeInto` 现在带 `seen map[string]bool`，同一个 frameID 只观测一次。

**但我没能在本机复现出审查描述的重复**：专门做了两级嵌套夹具（同源嵌套、跨源嵌套各一），
`GetFrameTreeWithEvents` 的帧树**节点数 = 唯一 ID 数**，两种都是 0 个重复 ID：

```
/nest_same.html  ：帧树节点数=3 唯一 ID=3 重复 ID 数=0 最大深度=2
/nest_cross.html ：帧树节点数=2 唯一 ID=2 重复 ID 数=0 最大深度=1
```

所以这道防线在本轮是**防御性**的（代码注释里也是这么写的），不是已经复现的缺陷。
我没有因为「没复现」就不加它 —— 观测这一层不设防本身就不该，而且将来若加第三路
帧来源（比如逐 OOPIF target 取树），这个洞会立刻变成真的。

（顺带，嵌套夹具还额外验到了路径算术：`/nest_same.html` 上三帧的 frame_path 是
`main/5A2D…/EE8D…` —— 逐层追加，一层不差。）

---

## 顺带四条

- **#5** `frameEnumerationWait` 的注释改准了：`GetFrameTreeWithEvents` 是**先取基线、
  再睡、再穿透 DOM**，所以这半时间给的是**穿透那一趟**（等运行时注入的 iframe 落到
  DOM 上）；基线那半在等待**之前**就取完了，晚出现的同源子帧只能靠 DOM 穿透补。
- **#6** `ObserveAll` 对主帧改传 `""`（不传真实 frameID）。注释写明理由：
  传真实 frameID 会走 `CreateIsolatedWorld`，在页面里常驻一个 isolated world，
  并往 `c.frameCtxs` 塞一条**导航后不会失效**的缓存项（同一个 frameID 跨导航复用
  → 指向上一次的文档）。
- **#7** 代码注释里「这不是取舍，是唯一解」改成「**用现有助手**的唯一解」，
  并写清 `Target.getTargets` 也能直接枚举 OOPIF（`targetId == frameId`，实测过），
  只是要另起 browser WS 会话、而且同源子帧不在那份列表里，**照样得并集**。
- **#8** 支撑核心结论的原始协议 JSON（含三次交叉验证）从 `.superpowers/`（workspace
  会被删）**挪进 `testdata/README.md`** 的「帧枚举」一节；代码/测试里的指向也一并改成
  指那份 README。

---

## 本轮实测（提交 `9219e93`，工作区干净）

### `go test ./internal/ -run 'TestObserve' -v -count=1` → **PASS=10 / SKIP=0 / FAIL=0**（7.026s）

```
--- PASS: TestObserveLightDOM (1.56s)
--- PASS: TestObserveShadowPageTextIsNotEmpty (1.55s)
--- PASS: TestObserveShadowElementsNotFalselyOccluded (1.55s)
--- PASS: TestObserveShadowRegionNotAllBody (1.59s)
--- PASS: TestObserveCrossOriginFrameMerge (0.78s)      ← 本轮改的就是它
--- PASS: TestObserveJSMustHandleShadowText (0.00s)
--- PASS: TestObserveJSMustNotTrustElementsFromPointAlone (0.00s)
--- PASS: TestObserveJSMustClimbThroughShadowHosts (0.00s)
--- PASS: TestObserveJSMustUsePierceHelper (0.00s)
--- PASS: TestObserveJSMustNotEmitSemanticJudgements (0.00s)
ok  	cdp/internal	7.026s
```

日志（新判据）：

```
子帧 frameID=C2BA7BFB7FEBEB4065024CC65A2AAFC7 url=http://localhost:43947/inner.html
主帧 http://127.0.0.1:43947/outer.html ／ 子帧 http://localhost:43947/inner.html —— host 不同（真跨源，且子帧文档已 commit）
合并后：fields=3 actions=6 shadow_roots=2 page_text=137字 diagnostics=0
```

### `go test ./... -v -count=1` → **PASS=64 / SKIP=0 / FAIL=0**（`cdp/cmd` 0.005s、`cdp/internal` 26.108s）

抖动：跨源那条 `-count=3` 连跑 3 遍全过（包 2.345s）。Chrome 跑完仍是 **1 个 page 目标**。
`go vet ./...` 干净，`gofmt -l` 对本次动过的四个文件无输出。

---

## 🔴 本轮**新发现**：跨源帧**里面**的子帧，枚举看不见（本轮未修）

写 ③ 的守卫时顺手量了两级嵌套，撞到这条 —— 它比守卫本身更重要：

```
main(127.0.0.1/nest_cross.html)
  └── OOPIF(localhost/outer_same.html)   ← GetFrameTreeWithEvents 报到这里为止
        └── inner.html                    ← **整个消失**（同源，在 OOPIF 里面）
```

- `GetFrameTreeWithEvents` 报 **2 帧**（探针实测，见 `testdata/README.md`）。
- 那条孙帧**确实存在**，而且 OOPIF target 自己的帧树里有它：
  `{"childFrames":[{"frame":{"id":"62F0B452…","parentId":"BCC65730…","name":"ci",
  "url":"http://localhost:44217/inner.html"}}]}`（探针 3 原始输出）。
- 根因：OOPIF 的 `<iframe>` 元素在**父**文档里，父文档的 DOM 穿透看得见它；
  但**跨进程没有 `contentDocument`**，穿不进 OOPIF 内部，子孙帧就整个消失。
  `client.go` 里 `collectFramesFromDOM` 那条「Recurses into ContentDocument to find
  nested iframes (e.g. Stripe's embedded checkout inside a parent Stripe iframe)」
  的**注释意图**，在这一版 Chrome 上实际达不到。
- 后果：`ObserveAll` 拿到的模型**少了那一段真实内容**（探针里 fields=0 actions=0），
  而它**不发任何声音** —— 正是本项目最忌的静默假成功。
- **本轮的缓解**：逐帧对账守卫把它变成一条 `frame-blind` 诊断
  （`path=[main <OOPIF id>]`，detail 写明「1 个 iframe 元素 / 0 个子帧」）。
  **不再静默，但内容确实拿不到** —— agent 也没有别的工具能下去（孙帧的 frameID
  只能从 OOPIF target 的帧树里取）。

**为什么本轮没修**：收进来要新增一条枚举路径（`Target.getTargets` → 对每个
`type=iframe` 的 target attach → 它自己的 `Page.getFrameTree` → 并进树 → 递归），
涉及会话/目标生命周期与新的失败模式，是**新能力**而不是本轮四条的修法；
审查给的是修法清单，我不擅自扩。**机制已经实测可行**（上面那条 JSON 就是它取出来的），
建议**单开一轮**做，落地时要带：逐 OOPIF target 取树的递归 + 两级跨源夹具 + 一条
「孙帧的字段也在合并结果里」的断言。当前 `frame-blind` 诊断就是它落地前的诚实出口。

---

## 顾虑（修复轮 1 更新）

1. 🔴 **嵌套跨源帧的内容收不进来**（上一节）：本轮由诊断兜成「不静默」，
   但内容拿不到 —— 真要看得见，得逐 OOPIF target 取树。**这是当前最大的已知缺口**，
   现实里的形态正是 Stripe 内嵌收银台那类「跨源里再嵌一层」。
2. ⚠️ **`GetFrameTreeWithEvents` 会静默吞掉 DOM 查询错误**（`client.go:259 return ft, nil`）：
   真发生时枚举会退化成只剩同源帧。逐帧守卫能逮住**大部分**（iframe 元素数对不上），
   但逮不住「页面确实没有 iframe」与「有 iframe 但穿透全挂」之外的情形 —— 例如
   OOPIF 的子孙帧（第 1 条），主帧那一层是对得上的，只有**逐帧**对账才逮得到。
   根因仍在 `client.go`，不在本任务文件清单里。
3. `OptionGroup` / `Obstruction` 没有 `FramePath`（Task 2 契约）：跨帧归属仍会丢。
   本轮把「观测者的问题」分出去之后，**剩下**的跨帧归属问题只影响这两类页面元素。
4. `"main"` 这个标记与「主帧 = 空串」的工具约定不一致（brief 写死的值，我照办并注释）。
   CLI（Task 6）落地时若把 frame_path 透给 agent，需要把 `"main"` 翻成 `""`。
5. 诊断通道现在**只在测试里验了「不喊」和「会喊」两种**（判据 3 附加 + 判据 4）；
   `frame-error`（子帧 observe 失败）那条路径**没有确定性的测法**
   （让子帧 eval 必然失败且帧仍在树里，构造起来不稳），目前只有一个守卫它的断言：
   正常路径上 `diagnostics` 必须为空。要真验它得设计一个专门的坏帧夹具。
6. 本轮**没动**验收范围之外的任何东西：`observe.go` 只加了 `Diagnostics` 字段与
   `Diagnostic` 类型（契约新增，控制方同步规格），`client.go` 一行未动。
