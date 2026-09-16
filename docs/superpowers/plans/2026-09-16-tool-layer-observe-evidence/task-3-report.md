# Task 3 报告：observe 真浏览器集成测试（light + shadow）

2026-09-16。提交 `ca8c771`（首轮）+ `b4f3928`（修复轮 1）。
**Status: DONE** —— 首轮审查已 Approved（审查方独立推演了 fixture 改动，判定为「前置条件补充、
不是挪球门」：加在 shadow **外面**的 landmark 救不了 `parentElement`-only 的实现，
它在 shadow root 边界就断，所以那个改动**在原理上不可能掩盖 bug**）；修复轮 1 的四条已落地并实测。

---

## 1. 写了什么

`tools/cdp/internal/observe_integration_test.go`（新建，141 行）：

- `serveFixtures(t)` —— `httptest.NewServer(http.FileServer(http.Dir("testdata")))`，`t.Cleanup` 关。
  **C30 遵守**：不碰 `localhost:8080` 那套外部 mock-server（既有 `form_integration_test.go` 的债没学）。
- `navigateAndObserve(t, url)` —— 复用迁入的 `shadowTestEndpoint()`；无浏览器 → `t.Skipf`。
  （**首轮**导航失败也走 `t.Skipf`；修复轮 1 已改成 `t.Fatalf`，见文末 ①。）
- 四条测试（**跨源那条按 C1 整条不写**，文件里不出现 `ObserveAll`；brief 里 `_ = strconv.Itoa`
  那行占位垃圾连同 `strconv` import 一并删掉）：

| 测试 | 断言什么 |
|---|---|
| `TestObserveLightDOM` | base.html：`shadow_roots==0`；`actions>=5`；两个同名 `Learn More` 的 `region` 集合 ≥2（header/footer）；`obstructions` 非空（cookie 横幅） |
| `TestObserveShadowPageTextIsNotEmpty` | 陷阱①：`shadow_roots>=2`（两层嵌套）；`page_text` ≥60 字；`fields>=3` 且每个 `shadow_depth==2` |
| `TestObserveShadowElementsNotFalselyOccluded` | 陷阱②：`actions` 里 `occluded_by != null` 的一个都不能有 |
| `TestObserveShadowRegionNotAllBody` | 陷阱③：`region` 不得只有 `body` 一个取值 |

---

## 2. 逐条实测结果（真耗时）

```
go test ./internal/ -run 'TestObserve(LightDOM|Shadow)' -v -count=1
--- PASS: TestObserveLightDOM (1.57s)
--- PASS: TestObserveShadowPageTextIsNotEmpty (1.55s)
--- PASS: TestObserveShadowElementsNotFalselyOccluded (1.55s)
--- PASS: TestObserveShadowRegionNotAllBody (1.55s)
PASS
ok  	cdp/internal	6.216s
```

连跑 3 遍（`-count=3`）12/12 全绿，耗时稳定 1.55–1.58s —— 每条都是 `httptest 起服务 → 真导航 →
1.5s 等渲染 → 真 `Client.Observe` 求值`，不是跳过的（SKIP 耗时恒为 0.00s，对照 Task 2 那六条
字符串断言就是 `0.00s`，一眼可辨）。

**全量**：`go test ./... -v -count=1` → **PASS 63 / FAIL 0 / SKIP 0**（exit 0；
`ok cdp/cmd 0.006s`、`ok cdp/internal 25.143s`；73 条 `=== RUN` = 63 顶层 + 10 子测试；
ginkgo 那条 `4 Passed | 0 Failed | 0 Pending | 0 Skipped`）。
**SKIP = 0 是逐条确认过的**：`grep -cE '^--- SKIP' /tmp/fulltest.log` = 0，
且日志里除 ginkgo 的 "0 Skipped" 字样外没有任何 skip 行 —— 没有「跳过装成通过」。

---

## 3. 唯一的失败：先红，后判定为**测试侧（fixture）问题**

第一次跑就是 3 绿 1 红：

```
=== RUN   TestObserveShadowRegionNotAllBody
    observe_integration_test.go:134: region 全部退化成 body —— parentElement 出不了 shadow 边界（陷阱 ③）
--- FAIL: TestObserveShadowRegionNotAllBody (1.55s)
FAIL
FAIL	cdp/internal	6.235s
```

**判定：不是 `observe.go` 的行为错，是这条断言在冻结的 fixture 上不可能成立。** 三层证据：

**证据 A —— 工具链 dump（临时探针，跑完已删）**：在 shadow.html 上把真 `observe` 的结果、
以及「正确实现」和「陷阱③实现」两版 region 并排打出来：

```
=== 真 observe 在 shadow.html 上的 actions ===
  tag=INPUT    text=""                     region=body   shadow_depth=2 sel=#fn
  tag=INPUT    text=""                     region=body   shadow_depth=2 sel=#ph
  tag=SELECT   text="ChooseGeorgiaCalifornia" region=body   shadow_depth=2 sel=#st
  tag=BUTTON   text="Tub to walk-in shower" region=body   shadow_depth=2 sel=#shower-opts > button:nth-of-type(1)
  tag=BUTTON   text="I need help deciding" region=body   shadow_depth=2 sel=#shower-opts > button:nth-of-type(2)
  tag=BUTTON   text="Get My Quote"         region=body   shadow_depth=2 sel=#submit
  shadow_roots=2 page_text=142 字

=== shadow.html 全页 landmark 元素数 = 0 ===
  "Get My Quote"  fixed=body  buggy=body  chain=[button#submit div#host2 div#host1 body html]
  （其余 5 条同样 fixed=body / buggy=body）
```

两条关键事实：
1. **合成树链是爬出去了的** —— `[button#submit div#host2 div#host1 body html]`，穿过了两层 shadow host。
   陷阱③的修法（`getRootNode().host`）**明明在用，而且生效**。
2. **两种实现都返回 body**，因为**这个页面根本没有 landmark**：`header/nav/footer/aside/main/
   [role=dialog]` + `.hero/.banner/.jumbotron` 全页计数 **= 0**（连 shadow 里面也没有）。
   没有 landmark 时，「爬出 shadow 到 body」和「爬到 shadow root 顶就断」是**同一个答案**。

也就是说：这条断言测的不是「region 会不会爬出 shadow」，而是「这个页面有没有 landmark」——
对**任何**实现都恒为红。**它对两种实现一样红 = 空转，不是闸门。**

**证据 B —— 对照合成页（同一次探针里跑）**，把 shadow host 包进 `<main>`：

```
=== 合成页：shadow host 被 <main> 包住 ===
  按钮穿透过 shadow 找得到: true
  正确实现 region=main  chain=button>div>main>body>html
  陷阱③实现 region=body  chain=button        ← parentElement 到 shadow root 顶就断
```

陷阱本身是**真的**（错误实现的链 `button` 就断了），只是**冻结的 fixture 缺那个触发条件**：
landmark 必须在 shadow **外面**（在 shadow 里面加 landmark 是没用的 —— 错误实现的
parentElement 遍历照样找得到它，仍然分辨不出来）。

**证据 C —— 变异**：把 `region` 退回 parentElement-only（见第 4 节 M-C），改 fixture 前
那条测试**照样红**（红对红）；改 fixture 后**只有**错误实现才红。同一断言、同一实现，
差别只在 fixture 有没有触发条件 —— 结论坐实。

### 处置：给 fixture 加一层 `<main>`（**加强**，不是放宽）

```diff
-<div id="host1"></div>
+<!-- Task 3：host 外面必须有 landmark，否则陷阱③分辨不出来 … -->
+<main><div id="host1"></div></main>
```

- **断言一个字没改**（还是 `len(regions)==1 && regions["body"] → Error`），brief 里的期望值原样保留。
- 改的是**被观测的页面**：让 fixture 具备触发条件。改完 `fixed=main / buggy=body`，断言才咬得住。
- 这是 `testdata/shadow.html` 相对 `docs/probes/2026-09-16-observe-r3/fixtures/shadow.html`
  的**唯一**差异（其余 5 个逐字节相同，md5 已核对）；差异在 fixture 内和测试文件里都写了注释。
- 为什么不是「改测试去迁就」：改测试是把断言改弱/挪到别的页面；这里是**让 fixture 具备测试
  本来就需要的条件**，断言反而更强了（改前它对任何实现都红，改后只对错误实现红）。
- 副作用核对：改后重跑 dump —— `shadow_roots=2`、`page_text=142 字`、6 条 action 的
  `shadow_depth=2` 全部与改前逐字相同，**只有 region 从 body 变 main**。

---

## 4. 变异实测：四条测试**每条都咬得住**（这是「闸门」claim 的证据）

`observe.go` 改一处 → 跑对应那条 → 看是否转红 → 还原。**还原后 md5 与改前一致**
（`2b7a138966854dc47bee54ced47c31f5`，脚本里断言过）。

| 变异 | 改动 | 期望 | 实测 |
|---|---|---|---|
| **M-A** 陷阱① | shadow root 不取子元素文本（模拟 `innerText` 拿到 `undefined`） | 陷阱①红 | ✅ 红 |
| **M-B** 陷阱② | 遮挡判定退回「元素在不在命中栈里」 | 陷阱②红 | ✅ 红 |
| **M-C** 陷阱③ | `region` 退回 `parentElement`-only | 陷阱③红 | ✅ 红 |
| **M-D** 语义 | `region` 恒返回 `body` | light 的同名按钮区分红 | ✅ 红 |

失败原文（证明红的原因**正是**要抓的那个病，不是别的）：

```
M-A  observe_integration_test.go:99: page_text 只有 10 字 —— shadow 里的正文没收到: "Shadow 表单页"
M-B  observe_integration_test.go:125: shadow 元素被误判为被遮挡（踩坑时实测 5/5 假阳性）:
     [←div#host1 ←div#host1 ChooseGeorgiaCalifornia←div#host1 Tub to walk-in shower←div#host1
      I need help deciding←div#host1 Get My Quote←div#host1]
M-C  observe_integration_test.go:139: region 全部退化成 body —— parentElement 出不了 shadow 边界（陷阱 ③）
```

两点值得一提：
- M-A 的 10 字**正是**探针记录的踩坑值（"Shadow 表单页" 10 个字符），逐字对上。
- M-B 实测是 **6/6** 假阳性（探针 README 写 5/5）：因为 `SEL` 把 `input/select/textarea`
  也算 action，shadow.html 上 action 共 6 条（3 字段 + 3 按钮）。遮挡签名清一色 `div#host1`
  —— 正是「`elementsFromPoint` 返回的是 shadow **host**」这个坑的指纹。
  **探针 README 的 5/5 是当时的页面/口径，本机实测 6/6**；首轮测试里的注释曾沿用 brief 的
  措辞「踩坑时实测 5/5」——**修复轮 1 ② 已按实测改成 6/6**（数值不影响断言：`len(bad) > 0`）。

---

## 5. 改了哪些文件

| 文件 | 变更 |
|---|---|
| `tools/cdp/internal/observe_integration_test.go` | **新建**（四条集成测试 + 两个 helper） |
| `tools/cdp/internal/testdata/shadow.html` | **修改**：host 外面包一层 `<main>`（+ 说明注释）。第 3 节详述，第 4 节 M-C 证明它必要 |

`observe.go`、其他 fixture、其他测试**一行未动**（变异脚本每轮都还原，md5 校验一致）。
临时探针 `internal/zz_probe_scratch_test.go` 已删，未提交。

---

## 6. 自查发现

1. **brief 的 `TestObserveCrossOriginFrameMerge` 里那句「保留 import 示例」是 `_ = strconv.Itoa`**
   —— 连同 `strconv` import 一起删了；文件现在只 import `net/http`、`net/http/httptest`、
   `testing`、`time`，`gofmt -l` 干净。
2. **`page_text` 实测 142 字，brief/探针记的是 141**（陷阱①注释里写「修好后是 141」，我实测 142）。
   差值 1 字，疑似计数口径（`join(' ')` 的那个空格 / 收尾空白）。**与断言无关**（阈值 60，
   两者都远超），我没有改 brief 写死的注释文字，只在此记录。另：这个 142 在加 `<main>` 前后**一致**。
3. **C30 核对**：新测试只用 `httptest`，没有任何 `localhost:8080` / 8892 依赖；`grep` 全包确认
   除本文件外没有别的测试读 `testdata/`。
4. **Chrome 目标没动**：跑完仍为 1 个 page 目标（`curl /json/list` 计数 = 1）—— 没关它，
   也没有把目标数跑成 0（那样集成测试会集体走 Skip，变成假绿）。
5. 跨源那档（outer.html/inner.html）我**没碰**；`outer.html` 里硬编码的
   `http://localhost:8892/inner.html` 对 Task 4 仍是活的坑（progress.md 第 471 行记过），
   与 C30 同向。
6. `go vet ./internal/` 通过。`gofmt -l internal/` 列出的 `form.go`/`form_test.go`/
   `snapshot_bdd_test.go` 是**既有**未格式化文件（我没碰、本次也没格式化，避免把无关 diff 搅进提交）。

---

## 7. 顾虑

1. **我动了 fixture**（虽然只是加一层 `<main>`，且有变异证据证明必要）。若控制方认为
   `testdata/*.html` 必须与探针副本逐字节一致，回退只需删掉那两行 —— 但**回退后陷阱③那条测试
   会永久红**（对任何实现都红，包括完全正确的实现），它就不再是闸门了。
   替代方案（我没选）：新增一个 `shadow_main.html` 专供陷阱③，或用测试内 JS 现包一层 `<main>`；
   都比「让同一批 fixture 真的能触发三条陷阱」更绕。
2. **探针 README 的陷阱③行没有实测数值**（陷阱①②都有：10/141、5/5），本次取证显示
   它的「症状」描述可能是**推断而非实测** —— 在冻结 fixture 上，「正确实现」给出的结果与
   「陷阱③实现」**逐字相同**。陷阱本身是真的（合成对照页已证），但那句
   「`region` 全部退化成 `body`」在原始 fixture 上**不是** parentElement 引起的。
   规格 §4.3 表格里那句描述建议按本报告订正，否则下一个人还会照它写出同一条恒红断言。
3. **`navigateAndObserve` 里 1.5s 的 `time.Sleep` 是 brief 写死的**，仍属固定等待而非条件等待
   （等 load 事件后还睡 1.5s）。本机连跑 3 遍 + 全量 1 遍均未抖，但慢机器上理论上有 flake 风险；
   真要根治得等 `document.readyState` + 元素出现，那是 Task 4+ 的事。
4. **四条测试都会导航当前这一个 page 目标**（测试间串行、无 `t.Parallel`，所以安全），
   但跑完它停在已关闭的 `httptest` URL 上；若有人后续依赖「跑测试前那个页面还开着」，会受影响。

---

# Task 3 修复轮 1（审查四条，提交 `b4f3928`）

主题：**让闸门没有暗门**。四条全部落地，每条都实测过，不是照着改。

## ① 导航失败不许当 skip ✅

`navigateAndObserve`：`NewClient` 失败仍 `t.Skipf`（环境缺失），**导航失败改 `t.Fatalf`** ——
url 是本测试**自己刚起**的 httptest server，连不上它是缺陷；而 skip 会把整个闸门悄悄变成
四条绿 skip。全文件现在**只有一处**允许 skip（`NewClient`），已在注释里标明。

**实测**（临时用例指向死端口，跑完即删）：

```
--- FAIL: TestZZNavigateFailureIsFatalNotSkip (0.03s)
    zz_fix_verify_test.go:7: 导航到自带 fixture server 失败（不是环境缺失，server 是本测试刚起的）:
        failed to navigate to http://127.0.0.1:65500/nope.html:
        navigate error: net::ERR_CONNECTION_REFUSED
```

FAIL 而非 SKIP，且消息能自证原因。**没加有界重试**：本轮累计 20+ 次导航 **0 次失败**
（含 `-count=3` 稳定性run、两轮全量、两次探针），没有抖动可吸收；且重试会多一条未被验证的
代码路径。若将来真出现抖动，加一次重试再 Fatalf。

## ② 注释里的数字改成实测 ✅（口径已查清）

原来照抄 brief 的「修好后是 141」「5/5」。**先查清再改**：

```
各 root 原文长度 perRootLens = [10 6 125]
求和（探针 page_text_len_raw，README 记 141）= 141
拼接+归一化后 JS .length（UTF-16 码元）= 142
生产 observe 返回的 PageText：len(runes) = 142 / len(bytes) = 148
两者是同一串吗: true
```

**差在哪**：探针的 `page_text_len_raw` 是 `perRoot.reduce((a,s)=>a+s.length,0)` —— 各 root
**原文**长度直接相加（`[10, 6, 125]` = 141），既没有 join 的分隔空格、也没有归一化；
测试量的是 `Observe` 返回的 `page_text`：`join(' ')` 后在 3 个 root 之间**多出 2 个分隔空格**、
各段内部空白又被折叠成单空格并 trim，**净 +1 → 142**。两个数都对，量的是不同的东西。
（此页全为 BMP 字符，故 JS 的 UTF-16 长度 142 与 Go 的 rune 数 142 一致；bytes 是 148。）

遮挡那条同类：探针 README 记 **5/5**，本机在 shadow.html 上实测 **6/6**
（该页 action 就是 6 条 = 3 字段 + 3 按钮，`SEL` 把 `input/select/textarea` 也算 action）。
断言是 `len(bad) > 0`，数量口径变化不影响判定。注释与 `t.Errorf` 文案都已改正。

## ③ 两条 shadow 测试补前置守卫 ✅

`requireShadowActions(t, m)`：要求 **动作 ≥5** 且 **≥5 个 `shadow_depth >= 2`**（后者证明
动作确实取自 shadow 里，而不是「页面变了、动作全在 light DOM」）。两条测试开头各调一次。

**实测守卫是活的**（把 `SEL` 改成匹配不到任何元素，`observe.go` 事后 md5 还原一致）：

```
observe_integration_test.go:92:  可动作元素太少: 0                                   ← light 自带守卫
observe_integration_test.go:105: 两个同名 Learn More 没被 region 区分开: map[]        ← light 自带守卫
--- FAIL: TestObserveLightDOM
--- PASS: TestObserveShadowPageTextIsNotEmpty                                          ← 它不依赖 actions，自带 shadow_roots/fields 守卫
observe_integration_test.go:146: shadow 页只取到 0 个可动作元素 —— 后面的断言会空转通过
--- FAIL: TestObserveShadowElementsNotFalselyOccluded
observe_integration_test.go:167: shadow 页只取到 0 个可动作元素 —— 后面的断言会空转通过
--- FAIL: TestObserveShadowRegionNotAllBody
```

**改前这两条会静默绿** —— 正是守卫要堵的那个洞。

## ④ testdata/README.md ✅（探针副本未动）

新增 `tools/cdp/internal/testdata/README.md`：写清哪个测试用哪个 fixture、
`shadow.html` 与探针副本的差异（host 外包一层 `<main>`）、**为什么必须**
（没有 landmark 时正确/错误实现都答 body，断言空转恒红；并附两版实现的合成树链对照表）、
以及 **「不要用探针目录覆盖 testdata」**（探针目录是历史记录，不许改）。
末尾指向本报告第 3 节。**探针副本 `docs/probes/2026-09-16-observe-r3/fixtures/shadow.html`
一个字节都没动**。

## 收尾实测

```
go test ./internal/ -run TestObserve -v -count=1
  → 9/9 PASS（四条集成测试 1.55–1.56s；Task 2 五条字符串断言 0.00s）
  ok  cdp/internal  6.223s

go test ./... -v -count=1
  → PASS 63 / FAIL 0 / SKIP 0（exit 0）
  ok  cdp/cmd 0.005s      ok  cdp/internal 24.897s
  SKIP 复核：`grep -inE '^\s*--- skip|SKIP:'` 在 73 条 RUN 的日志里**零命中**
```

`observe.go` 全程未改：每轮变异后 md5 还原一致（`2b7a138966854dc47bee54ced47c31f5`）。
临时文件 `zz_probe_len_test.go` / `zz_fix_verify_test.go` 均已删除，未提交。
`gofmt -l` 对新文件干净，`go vet` 通过。

## 本轮新增的顾虑

1. **`requireShadowActions` 的阈值 5 是贴着当前 fixture 定的**（该页 6 条 action）。
   将来若要给 shadow.html 加元素没问题，**减**元素会被守卫拦下 —— 那时要连着改守卫，
   别把守卫删掉。已在测试data/README 与测试注释里留下线索。
2. **重试没有加**（见 ①）。若 CI 上出现导航抖动，请加**一次有界重试**再 Fatalf —— 不要退回 skip。
3. 报告的 141/142 结论依赖「JS `.length` 与 Go rune 数在本页相等」这一前提；换成含
   代理对（emoji 等）的页面，两个口径会再差一次（UTF-16 码元 vs rune）。目前 fixture 无此字符。
