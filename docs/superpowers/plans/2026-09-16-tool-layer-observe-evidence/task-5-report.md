# Task 5 报告：选择器候选与稳定性评级的行为测试

**日期**：2026-09-16　**分支**：master　**状态**：DONE_WITH_CONCERNS（1 条真发现，测试有意留红）
　　　　　　→ **修复轮 1 已落地**（控制器裁定「修实现，不改测试」）：见文末第 7 节
　　　　　　→ **修复轮 2 已落地**（审查四条：name 补成双向 + 陈旧数字/注释 + 形态①孤儿 + 残留披露）：见第 8 节，现 **0 FAIL / 0 SKIP**

> **brief 的主体已作废**，按重定义执行：不实现 Go 侧 `stabilityOf()` / `looksRandom()`
> （那两个函数不存在、也不需要 —— 算法已由 Task 2 作为 **JS** 整体移植进
> `observe.go` 的 `candidates()`(`:160`) / `stability()`(`:174`)，与 R3 探针
> 0 行差异）。本任务把 brief 的**验收标准**落到**真浏览器里 `PageModel` 的输出**上。

---

## 1. 交付物

| 文件 | 说明 |
|---|---|
| `tools/cdp/internal/testdata/selector.html` | **新增 fixture**（不来自探针，是一张判据表） |
| `tools/cdp/internal/observe_selector_test.go` | **新增**：6 条测试，5 绿 1 红 |
| `tools/cdp/internal/testdata/README.md` | 补 `selector.html` 一节（元素↔判据对照表 + 两个 ⚠️） |
| `tools/cdp/internal/observe.go` | **未改**（变异验证期间改过 5 次，每次已 `git checkout` 还原，`git diff` 为空） |

`shadow.html` / `base.html` / 探针副本**一个字没动**（README 里那条「别覆盖」的警告照办）。

fixture 用 `httptest` 自带服务（C30）：**没有新增对 `localhost:8080` 的依赖**。

### fixture 的设计

每个元素用 `<button>` 里一段**唯一文本**标注身份，测试按文本取元素 ——
按 `selector` 取会自我指涉（selector 正是被测对象）。input 没有 textContent，
改用 `placeholder` 找。取不到元素时 `actionByText` **当场 Fatal**，不让断言空转。

| 元素文本 | 形态 | 覆盖的判据 |
|---|---|---|
| `Stable Id`（`#schedule-now` + `data-testid`） | 稳定 id | 判据 1 + alternates 顺序 |
| `B stable name`（`name` 无 id） | 稳定 name | 判据 1 |
| `C data-testid`（无 id/name） | 稳定 data-* | 判据 1 |
| `Full`（id + name + data-testid + class） | 五种标识俱全 | 判据 4（顺序 + 去重） |
| `L pure id` | 只有 id | 判据 1 + 「alternates 空洞」留档 |
| `D hash7` `css-1x2y3z4` | 7 位 base36 hash（brief 的字面例子） | 判据 2 ⚠️ |
| `E hash8` `css-1a2b3c4d` | 8 位纯 hex hash | 判据 2 / RAND 形态① |
| `F stable class` | 稳定语义 class | medium 档 |
| `G deep path no id` | 4 段结构路径 | 判据 3 |
| `H deep under stable id` | 4 段、表头是祖先 id | 判据 3 的**例外**（特征化） |
| `I plain button` | 裸 `<button>` | 判据 5 |
| `J random id` `a1b2c3d4e5f6` | 随机 id | RAND 形态①（id 落点） |
| `K random tid` `x1234567890` | 随机 data-testid | RAND 形态②（data-* 落点） |
| `K random name` `sid_9f8e7d6c5b4a` | 随机 name | RAND（**name 落点：没有**）⚠️ |

**「不该出现 X」类断言都自证有效**：`requireTokenCarriedBy` 从 fixture 里读出
该元素那一行，断言 token **真的挂在它身上** —— 否则 token 打错一个字符，断言就
退化成「候选里没有这个不存在的串」，恒真、永远绿。

---

## 2. 逐条实测结果

**命令**：`go test ./internal/ -run 'TestObserve' -v -count=1`

```
PASS=15   SKIP=0   FAIL=1        （退出码 1，包耗时 35.3s 的 ./... 全集见下）
```

**命令**：`go test ./... -v -count=1`

```
PASS=79   SKIP=0   FAIL=1
ok    cdp/cmd        0.005s
FAIL  cdp/internal   35.336s     ← 唯一的 FAIL 是设计留红的那条
```

唯一失败项：`--- FAIL: TestObserveSelectorRandomHashClassNotPreferred (1.56s)`
（第 3 节）；**SKIP = 0**，没有靠跳过混过去的绿。

新增的 6 条（每条各自导航一次，`navigateAndObserve` 里含固定 1.5s 等待 ——
**耗时几乎全是那个 sleep，真正的 observe eval ≈ 50~70ms**）：

| 测试 | 耗时 | 结果 |
|---|---|---|
| `TestObserveSelectorStableIdentifiersPreferred` | 1.57s | PASS |
| `TestObserveSelectorCandidatesOrderedAndDeduped` | 1.57s | PASS |
| `TestObserveSelectorDeepPathRatedLow` | 1.55s | PASS |
| `TestObserveSelectorPlainButtonRating` | 1.55s | PASS |
| `TestObserveSelectorRandomTokensNotPreferred` | 1.55s | PASS |
| `TestObserveSelectorRandomHashClassNotPreferred` | 1.56s | **FAIL**（有意） |

### 2.1 真浏览器里的原始输出（`selector.html` 整页，14 个可动作元素）

```
action text="Stable Id"              selector=#schedule-now                        stability=high   alternates=[button[data-testid="schedule"]]
action text=""                       selector=#full                                stability=high   alternates=[input[name="full-name"] input[data-testid="full-tid"] input.form-control]
action text=""                       selector=input[name="email-addr"]             stability=high   alternates=[body:nth-of-type(1) > input:nth-of-type(2)]
action text="C data-testid"          selector=button[data-testid="go-btn"]         stability=high   alternates=[body:nth-of-type(1) > button:nth-of-type(2)]
action text="D hash7"                selector=button.css-1x2y3z4                   stability=medium alternates=[body:nth-of-type(1) > button:nth-of-type(3)]   ← ⚠️
action text="E hash8"                selector=body:nth-of-type(1) > button:nth-of-type(4)  stability=medium alternates=[]
action text="F stable class"         selector=button.btn.btn-primary               stability=medium alternates=[body:nth-of-type(1) > button:nth-of-type(5)]
action text="G deep path no id"      selector=section:nth-of-type(1) > article:nth-of-type(1) > div:nth-of-type(1) > button:nth-of-type(1)
                                                                                   stability=low    alternates=[]
action text="H deep under stable id" selector=#deep-wrap > div:nth-of-type(1) > div:nth-of-type(1) > button:nth-of-type(1)
                                                                                   stability=high   alternates=[]                              ← ⚠️
action text="I plain button"         selector=body:nth-of-type(1) > button:nth-of-type(6)  stability=medium alternates=
action text="L pure id"              selector=#pure-id                             stability=high   alternates=[]                              ← 留档
action text="J random id"            selector=body:nth-of-type(1) > button:nth-of-type(8)  stability=medium alternates=[]
action text="K random tid"           selector=body:nth-of-type(1) > button:nth-of-type(9)  stability=medium alternates=[]
action text=""                       selector=input[name="sid_9f8e7d6c5b4a"]       stability=high   alternates=[body:nth-of-type(1) > input:nth-of-type(3)]  ← ⚠️
```

### 2.2 判据逐条对账

| 判据（brief 验收标准） | 实测 | 结论 |
|---|---|---|
| 1. 稳定 id/name/data-* → `high`，且**首选就是它** | `#schedule-now` / `input[name="email-addr"]` / `button[data-testid="go-btn"]` 三条全是首选 + `high` | ✅ 通过 |
| 2. 随机 hash class → 首选**不得**是它 | `css-1a2b3c4d`（8 位 hex）被滤掉 ✅；`css-1x2y3z4`（7 位 base36）**成了首选** ❌ | ❌ **1 条真发现** |
| 3. `nth-of-type` >3 层 → `low` | 纯结构 4 段 → `low` ✅；**以祖先 id 开头**的 4 段 → `high` ⚠️ | ⚠️ 判据有例外，见 4.2 |
| 4. alternates 有序、不冗余 | `[name, data-testid, class]` 顺序正确；`pathSel` 重复吐的 `#full` 被去重滤掉 | ✅ 通过 |
| 5. 裸 `<button>` 的评级是否合理 | `body:nth-of-type(1) > button:nth-of-type(6)` → `medium` | ⚠️ 见 4.4 |
| RAND 的模式（形态①/②）也要测到 | 形态①（`a1b2c3d4e5f6` id）、形态②（`x1234567890` data-*）都被滤掉 ✅ | ✅ 通过 |

---

## 3. 真发现：随机 hash class 成了**首选选择器**（测试留红）

`TestObserveSelectorRandomHashClassNotPreferred` 原样输出（提交后的版本实测，
行号对着提交里的 `observe_selector_test.go`）：

```
observe_selector_test.go:389: 7 位 base36 class（emotion / MUI 的实际形态）：首选选择器里带着随机 token "css-1x2y3z4" —— selector="button.css-1x2y3z4" stability="medium"。emotion 的 `css-` + murmur2→toString(36)，通常 6~7 位；形态①要 hex ≥8 位、形态②要整串无 `-`，两道都够不着。判据要求首选不得是随机 token，且 high 必须「不含随机 hash」

observe_selector_test.go:398: name 里带随机 token 时首选仍是它（"input[name=\"sid_9f8e7d6c5b4a\"]"，stability="high"）—— candidates() 对 id/data-*/class 都过了 RAND，`if (el.name)` 这一行没有，是漏网点。该 token = `_` 夹住的 12 位 hex（RAND 形态①，RAND 自己是认得的）

--- FAIL: TestObserveSelectorRandomHashClassNotPreferred (1.56s)
```

### 3.1 漏网点 A：RAND 够不着 7 位 base36 hash（**主要发现**）

`observe.go:147`：

```js
var RAND = /(^|[-_])[0-9a-f]{8,}($|[-_])|^[a-z]*\d{6,}$/i;
```

对 `css-1x2y3z4`：形态①要求 `-` 之后是 **≥8 位连续 hex**，`1x2y3z4` 只有 7 位、
且 `x/y/z` 连 `/i` 也不是 hex；形态②要求整串无 `-`，`css-` 前缀又挡一道。**两道都够不着。**

后果链条（每步都是实测，不是推断）：

1. `candidates()` 的 class 过滤 `!RAND.test(c)` 判定「这不是随机 class」→ **保留**
2. 该 class 排到候选链首位（元素没有 id / name / data-*）
3. `stability()` 走 `/^[a-z]+\.[a-z]/` → 答 **`medium`**
4. agent 拿到的首选是 `button.css-1x2y3z4`，`medium` 与 `button.btn.btn-primary` 同档

规格 D3 写着「**选择器是 py 准不准的头号因素**」—— 这个 hash 会被抄进 py 脚本，
站点下次发版（hash 重算）就断，而 `medium` 会让 agent 以为它有中等可信度。
判据里这条本该是：`low = 依赖随机 class hash`。

**这条漏网不是边角**：brief 自己举的例子就是 `css-1x2y3z4`，而 emotion / MUI v5
（`css-` 前缀 + murmur2→`toString(36)`）正是**这类站最常见的 CSS-in-JS 形态**。
（「emotion 的 hash 是 6~7 位」这句来自我的既有知识，**本任务没有在真站上核实** ——
核实这件事本身就是规格 R5「会在真站上校准」。可核实的是它的一半：brief 把
`css-1x2y3z4` 当作随机 hash 的字面例子，而实现不认它。）

**建议**（按控制器要求：只报，不改）：

- 现在这条正则是「**宽进严出**」的：漏判的代价（hash 进首选，py 脚本断）远大于误判
  的代价（少一条 class 兜底候选）。所以该往**多滤**的方向校准。
- 一个低误伤的方向：把「被 `-`/`_` 分隔、长度 ≥5、**字母与数字相邻混排**的片段」
  判为 hash。验算：`css-1x2y3z4`→`1x2y3z4`(7, 混排)✅；`css-1a2b3c4d`✅；
  `MuiButton-root`（无数字）✗；`text-2xl`（混排片段只有 3 位）✗；`col-md-8`、`p-4` ✗ ——
  即 Bootstrap/Tailwind 的常规类名都不会被误伤。
- 代价必须说清：这是启发式，**要真站校准**（R5），不能当成「改完就对了」。

### 3.2 漏网点 B：`name` 那一行没过 RAND

`candidates()` 四个落点里，`id`（`:162`）、`data-*`（`:166`）、`class`（`:169`）都写了
`!RAND.test(...)`，唯独 `name`（`:163`）：

```js
if (el.name) out.push(el.tagName.toLowerCase() + '[name="' + el.name + '"]');   // 没有 RAND
```

`name="sid_9f8e7d6c5b4a"` 的 token 是 `_` 夹住的 12 位 hex —— **RAND 形态①本来就认得它**，
只是没人问它。于是首选成了 `input[name="sid_9f8e7d6c5b4a"]` 且 `stability=high`
（判据明写 high 须「不含随机 hash」）。

严重性低于 A：`name` 在真站上通常是语义化的（`firstName` / `zip`），随机 name 少见。
但它是**同一类错误**（少一道过滤），补法是**一行**，且能让四个落点对称。

### 3.3 为什么测试留红（而不是改小期望）

控制器裁定：*「若是 `candidates()`/`stability()` 的行为不对 —— 那是真发现，
报 `DONE_WITH_CONCERNS` 并原样贴输出，**不要改测试期望去迁就**」*。

所以这条测试断言的是**判据**（随机 token 不得作首选、不得评 high），红的不是测试。
另有一条**有意保留的后果**：如果有人把 `selector.html` 里的 `D hash7` / `K random name`
两个元素擦掉，测试会以 `actionByText` 的 Fatal 说话 —— 想抹掉这个发现得先绕过一道门。
`testdata/README.md` 里也标了 ⚠️ 并指向本报告。

**留红不是永久状态**。两条出路（都需要留痕，不该由写测试的人悄悄做）：

1. **修实现**：加宽 RAND + 给 `name` 补过滤 → 这条测试自然转绿；
2. **改判据**：由规格侧明确裁定「7 位 base36 不算随机」→ 那时改的是**判据**，
   要连带改 brief / 规格 §4.3 和本条测试的注释，并说明为什么。

---

## 4. 读 `candidates()` / `stability()` 时发现的问题（含对判据本身的怀疑）

### 4.1 alternates 有**结构性空洞**：元素自己带稳定 id 时，结构路径永远不出现

实测：

| 元素 | selector | alternates |
|---|---|---|
| `L pure id`（只有 id） | `#pure-id` | **`[]`** |
| `Stable Id`（id + data-testid） | `#schedule-now` | `[button[data-testid="schedule"]]` |
| `Full`（id + name + data-* + class） | `#full` | `[name, data-testid, class]` ← **没有结构路径** |
| `C data-testid`（无 id） | `[data-testid=]` | `[结构路径]` |

原因：`pathSel()` 第一跳就撞上元素**自己**的 id（`if (n.id && !RAND.test(n.id)) { parts.unshift('#'+n.id); break; }`），
吐回同一个 `#id`；`candidates()` 末尾的去重又把它滤掉。

后果：**最需要兜底的元素反而没有兜底** —— 一个只有 `#id` 的按钮，`alternates` 是空的；
而 id 恰恰是最可能消失的东西（未登录态、A/B 变体、站点改版）。
`alternates` 这个字段的存在意义就是「首选断了换一条」，在这里它退化成空。

**这条我没有写成红断言**：它是不是缺陷取决于设计意图（「稳定 id 本来就不需要兜底」
也是一种说得通的立场），我按控制器的路子**报出来**，请裁定。
测试里只在 `L pure id` 上留了一条 `t.Logf` 把空 alternates 记下来，
**没有钉死「必须为空」** —— 免得将来有人补上结构兜底时撞上一条测试拦路。

### 4.2 `stability()` 的 `high` 分支被 `/^#/` 放宽成「选择器以 # 开头」

实测对照（两张表只差**表头**，尾巴一模一样）：

| 元素 | selector | 实测 |
|---|---|---|
| `G deep path no id` | `section:nth-of-type(1) > article:nth-of-type(1) > div:nth-of-type(1) > button:nth-of-type(1)` | **`low`** |
| `H deep under stable id` | `#deep-wrap > div:nth-of-type(1) > div:nth-of-type(1) > button:nth-of-type(1)` | **`high`** |

`stability()` 的第一条分支 `if (/^#/.test(c) || …) return 'high'` 会先命中，后面的
`:nth-of-type` 深度判据**再也不会执行**。于是：

- 同形状、同脆度（3 跳 `nth-of-type`，插一个 div 就断）的两条路径，评级差**两档**；
- `high` 的实际含义从「有稳定的 id」漂成「选择器里有 `#`」—— 而真实站点上
  `#root` / `#app` 这类容器包住整个 SPA，于是**大量深元素**会以
  `#root > div:nth-of-type(1) > … > button:nth-of-type(k)` 的形态拿到 `high`。
  agent 分不出它和 `#schedule-now` 的差别。

规格 §4.3 的两种读法**都不支持** `high`：按「结构路径 ≤3 层」读，H 是 4 段；
按「nth-of-type 层数」读，H 有 3 层。`high` 只在第三种读法（「选择器里有 #」）下成立。

**这条我也没有写成红断言** —— 判据文字确实两可（「有稳定的 id」的主语可以是**元素**、
也可以是**选择器**），把它判成缺陷等于替作者定意图。测试里做的是**特征化断言**
（钉住 `#deep-wrap > ` 前缀 + 3 个 `>` + 选择器里 `#` 恰好一次 + 元素自身无 name/data-*），
并 `t.Logf` 把 H=`high`／G=`low` 这组对照写进输出。若将来改了判据，日志会明确提示复核。

**建议方向**（供裁定）：`high` 只给**不带后代组合器**的标识选择器（`#id`、`[name=]`、
`[data-*=]`），`#id > … > x:nth-of-type(n)` 交给深度判据 —— 但那会把
`#card > button:nth-of-type(1)` 从 high 降到 medium，是否可接受请规格侧定。

### 4.3 `medium` 是个**杂物档**，agent 无法区分「稳的 class」和「纯位置路径」

实测同档 `medium` 的两类：

- `button.btn.btn-primary`（语义 class，跨版本大概率还在）
- `body:nth-of-type(1) > button:nth-of-type(6)`（**纯位置**，页面上多一个按钮就错位）

判据文字把 `medium` 定义成「结构路径 ≤3 层」，而实现额外把 class 选择器也塞进这一档
（`/^[a-z]+\.[a-z]/`）。规格没给 class 选择器定档，实现的选择不算错，但**信息量被抹平**：
agent 拿到 `medium` 无从判断手里这条是「稳的 class」还是「数到第 6 个 button」。
建议将来把「位置路径」单独降级（例如纯 `nth-of-type` 且首段是 `body`/`html` 的位置路径
给 `low`），这同样属于 R5 校准的范畴。

### 4.4 裸 `<button>`（判据 5）：`medium` —— 判据上说得通，实践上偏乐观

**实测值**：`body:nth-of-type(1) > button:nth-of-type(6)`，`stability=medium`，`alternates=[]`。

**这个值合不合理？** 拆开看：

- **判据层面站得住**：规格写「medium = 结构路径 ≤3 层」，这条是 2 段，机械上对得上；
  断言它「不得评 high」是过硬的（裸 button 既没有稳定 id/name/data-*，
  也没有「文本全局唯一」这条豁免 —— 页面上一堆同名按钮是常态）。
- **实践层面偏乐观**：它 0 个稳定锚点，靠的是「body 下第 6 个 button」这个**位置**。
  真站上按钮增删（cookie 横幅按钮、埋点按钮、A/B 变体）都会让它错位，
  而它和上面 4.3 那种真正稳的 class 选择器**同档**。

所以我的判断是：**判据没错，但它把 `medium` 的语义用满了**，而 agent 拿到 `medium`
时缺一条「这条是靠位置还是靠标识」的信息。修法同 4.3（位置路径降级），不必改判据文字。

---

## 5. 变异验证

方法：直接改坏 `observe.go` 的 JS（真浏览器里立即生效，无需重编译），
跑 `go test ./internal/ -run 'TestObserveSelector' -count=1`，记录哪些测试变红，
然后 `git -C /company/siteforge checkout -- tools/cdp/internal/observe.go` 还原
（每次还原后 `git diff` 均为空 —— 交付的 `observe.go` 与提交前逐字节相同）。

| 变异 | 改了什么 | 变红的测试 | 是否逮住 |
|---|---|---|---|
| **M1** | `stability()`：`if (/^#/.test(c) \|\| …)` 去掉 `/^#/` 分支 | `TestObserveSelectorStableIdentifiersPreferred` | ✅ |
| **M2** | `stability()`：`depth <= 2` → `depth <= 99`（深路径不再降级） | `TestObserveSelectorDeepPathRatedLow` | ✅ |
| **M3** | `RAND` 整个失效：`/(?!)/`（永不匹配） | `TestObserveSelectorRandomTokensNotPreferred`（**三条用例全红**） | ✅ |
| **M4** | `candidates()`：去掉末尾的去重 `.filter(...)` | `TestObserveSelectorCandidatesOrderedAndDeduped` | ✅ |
| **M5** | `pathSel()`：跳数上限 `hops < 4` → `hops < 3` | `TestObserveSelectorDeepPathRatedLow` | ✅ |

**稳定性评级的两次变异（M1 / M2）是控制器点名要求的那一项** —— 结果：改坏 `high` 分支
或改坏深度阈值，测试**各自立刻变红**，说明 `stability()` 现在真的被断言守住了。

M3 的原始输出（顺带证明这几条断言确实咬在 RAND 上，而不是恒真）：

```
8 位 hex class（形态①）：首选选择器里带着随机 token "css-1a2b3c4d" ——
  selector="button.css-1a2b3c4d" stability="medium"
12 位 hex id（形态①）：首选选择器里带着随机 token "a1b2c3d4e5f6" ——
  selector="#a1b2c3d4e5f6" stability="high"
10 位数字 data-testid（形态②）：首选选择器里带着随机 token "x1234567890" ——
  selector="button[data-testid=\"x1234567890\"]" stability="high"
```

（注：上面 `#a1b2c3d4e5f6` 那行说明**没有 RAND 时随机 id 会直接当上首选** ——
即 RAND 现在承担的正是「不让随机 token 变成选择器」这件事。）

M4 的原始输出：

```
候选链里 "#full" 重复出现（第 4 条）—— 去重没生效
```

M5 的原始输出：

```
G 的 selector = "article:nth-of-type(1) > div:nth-of-type(1) > button:nth-of-type(1)"，
应为 "section:nth-of-type(1) > article:nth-of-type(1) > div:nth-of-type(1) > button:nth-of-type(1)"（pathSel 上限 4 跳）
```

**没有被变异照到的部分**（诚实记账）：`region()` / `occludedBy()` / `contrastBand()` /
`nearbyText()` 不在本任务范围内（前者已有 Task 3 的陷阱测试）；
`option_groups` 与 `obstructions` 的 `candidates()` 调用点（`:269` / `:285`）也没测 ——
它们复用同一个 `candidates()`，所以变异 M3~M5 会连带影响，但没有专门断言。

---

## 6. 顾虑

1. **一条测试是红的**（3.1）。这是有意的：判据说随机 token 不得当首选，实现做不到。
   我按控制器的规矩没有改小期望。**它需要一次裁定**（修实现 / 改判据二选一），
   在那之前 `go test ./...` 会一直 FAIL=1。若团队暂时不能接受长红的套件，
   请把这条裁定明确写进 brief 或 README，再决定怎么处理 —— 但**别直接删测试**。
2. **RAND 的校准（R5）我没有做**，只测出了「7 位 base36 漏网」这一个可复现的缺口。
   真站上还有多少 hash 形态漏网、`MuiButton-root` 这种「hash + 稳定前缀」混排 class
   该怎么算，都需要拿真站样本回归 —— 那是 R5 的活。
   本任务**不引入网络依赖**，所以没做这件事。
3. **`alternates` 结构性空洞**（4.1）与 **`high` 被 `/^#/` 放宽**（4.2）我只报了、没改断言。
   两条都需要设计裁定；4.1 我特意**没有**钉死「alternates 必须为空」，
   免得将来补兜底时被测试拦路。
4. **`TestObserveSelector*` 每条各导航一次**（6 次 × 1.5s ≈ 9s）。
   这是为了失败能定位到单条判据；若将来嫌慢，可以合并成一条测试 + `t.Run` 子测试共用一个
   `PageModel`，但要保留「每条判据独立可见」这一点。
5. fixture 里 `D hash7` / `K random name` 两个元素**不是装饰**：它们是上面那条真发现的
   载体（`actionByText` 找不到就 Fatal）。`testdata/README.md` 已标 ⚠️ 并指向本报告。
6. 本任务的测试**不依赖网络**；Chrome 不可用时仍走 `navigateAndObserve` 的那一处
   `t.Skipf`（环境缺失），但**实测 SKIP=0**，没有靠跳过混绿。

---

# 7. Task 5 修复轮 1：修实现（控制器裁定）

**裁定**：*「修实现，不改测试。那条 FAIL 就是本轮的验收。」*
两处都是同一个判据的漏网 —— ① RAND 够不着 `css-1x2y3z4` 这类 6~7 位 base36 hash；
② `candidates()` 的 `name` 落点没过 RAND。**硬要求**：双向钉边界 ——
不光测「该抓的抓到」，还要测「不该抓的别抓」，且偏置（宁可过度检测）必须被断言钉住。

## 7.1 改了什么（`observe.go`，两处）

### ① RAND 加**形态③**（`observe.go` 的 `var RAND` 一行）

```js
// 改前
var RAND = /(^|[-_])[0-9a-f]{8,}($|[-_])|^[a-z]*\d{6,}$/i;
// 改后（形态①②保留，②从「整串」放宽到「片段」，新增③）
var RAND = /(^|[-_])[0-9a-f]{8,}($|[-_])|(^|[-_])[a-z]*\d{6,}($|[-_])|(^|[-_])(?=[a-z0-9]{5,}($|[-_]))[a-z0-9]*([a-z][0-9]+[a-z]|[0-9][a-z]+[0-9])[a-z0-9]*($|[-_])/i;
```

三形态（任一命中即算随机）：

| 形态 | 判据 | 例 |
|---|---|---|
| ① | 被 `-`/`_` 或串首尾夹住的 **≥8 位 hex** 片段 | `a1b2c3d4e5f6` ／ `id_9f8e7d6c5b4a` |
| ② | 同上边界内「可选字母前缀 + **≥6 位数字**」 | `x1234567890` ／ `css-x1234567890` |
| ③ | 同上边界内、**≥5 位**、字母与数字**来回交替 ≥2 次** | `css-1x2y3z4` ／ `1x2y3z4` |

形态①②**逐字保留**（②只是把锚点从 `^…$` 放宽到片段边界，仍是超集）—— 所以首轮
已经绿的三条（`css-1a2b3c4d` / `a1b2c3d4e5f6` / `x1234567890`）不可能因这次改动回退。

**③ 的「交替 ≥2 次」是这次改动的核心决策**，理由：正常类名的数字只有三种落法，
**都不产生「字母→数字→字母」的来回** ——

| 落法 | 例子 | ③ 会不会误伤 |
|---|---|---|
| 无数字 | `btn-primary` / `hero-banner` / `form-control` | 不会（没有数字） |
| 数字自成一段 | `col-md-6` / `icon-24` / `p-4` | 不会（段内没有字母+数字混排） |
| 数字在词尾 | `step1` / `section1` / `text-2xl` | 不会（没有「数字后有字母」的窗口） |

**为什么不干脆用「混排 + 长度 ≥5」**（更简单）：那会把 `step1` / `section1` /
`panel2` 这一家全部误伤 —— 而它们恰好是多步表单里最常见的命名（本项目自动化的
正是注册/报价漏斗）。取舍（⚠️ 下面的比例是**算出来的概率模型，不是实测**）：对随机 7 位 base36 串，③ 能认出的比例 ≈ **77%**
（全字母的 10% 与只翻转一次的 13% 认不出），而「混排+长度」虽也是 100% 认 `css-1x2y3z4`
却要赔上 `step1` 一家。**这是我自己算的概率模型，不是实测数据**，写在这里是为了让
取舍可被复核，不是当结论用。

### ② `name` 落点补 RAND（`candidates()`）

```js
if (el.name && !RAND.test(el.name)) out.push(...);   // 补 !RAND.test，与 id/data-*/class 对齐
```

## 7.2 反向边界的 fixture 与断言（**协调者点名的那一半**）

`testdata/selector.html` 新增 7 个「只带一个 class」的按钮 + 1 个 input：

```
M btn-primary   M hero-banner   M col-md-6   M icon-24
M step1         M section1      M text-2xl   （+ name="step2" 的 input）
```

断言在 `TestObserveSelectorLegitTokensNotRejected`：**selector 必须恰好是
`button.<class>`**（被误判的话 class 会被丢掉、selector 变成结构路径
`body:nth-of-type(1) > button:nth-of-type(k)` —— 两种形态差得很远，不会看不出来），
外加 `stability != low`；name 那路断言 `input[name="step2"]` 仍在。
「只带一个 class」是刻意的：候选里 `cls.slice(0,2)` 只取前两个，一个类名对应一条断言最好判。

同时把 `TestObserveSelectorRandomHashClassNotPreferred` 里 name 那条**加强**了：
原来只断言「selector 不含那个 token」，现在**同时断言退化成的是结构路径**
（`body:nth-of-type(1) > input:nth-of-type(...)`）—— 因为「滤掉随机 name」的正确结果
是退回结构路径，不是「变成别的随机串」也不是「元素整个丢掉」。空断言变成了正向断言。

## 7.3 修复后的实测

```
go test ./internal/ -run 'TestObserve' -v -count=1   → PASS=17  SKIP=0  FAIL=0
go test ./... -v -count=1                            → PASS=81  SKIP=0  FAIL=0
```

（`TestObserve` 子集首轮是 16 条 = 15 PASS + 1 FAIL，本轮加反向边界那条 → 17 条全绿；
`./...` 首轮 79 PASS + 1 FAIL → 本轮 81 PASS，差额正是「那条 FAIL 转绿」+「新增一条」。）

新增的 7 条（含反向边界那条）：

| 测试 | 耗时 | 结果 |
|---|---|---|
| `TestObserveSelectorStableIdentifiersPreferred` | 1.57s | PASS |
| `TestObserveSelectorCandidatesOrderedAndDeduped` | 1.56s | PASS |
| `TestObserveSelectorDeepPathRatedLow` | 1.57s | PASS |
| `TestObserveSelectorPlainButtonRating` | 1.55s | PASS |
| `TestObserveSelectorRandomTokensNotPreferred` | 1.56s | PASS |
| `TestObserveSelectorRandomHashClassNotPreferred` | 1.55s | **PASS**（首轮的 FAIL 转绿 —— 这就是本轮的验收） |
| `TestObserveSelectorLegitTokensNotRejected` | 1.56s | PASS（新增，反向边界） |

关键两行的实测输出（`-v` 的日志）：

```
K random name：selector="body:nth-of-type(1) > input:nth-of-type(4)" stability="medium"（随机 name 被 RAND 滤掉 → 退回结构路径）
反向边界：7 个正常类名 + 1 个正常 name 全部仍被采用（selector=button.<class> / input[name=] ）
D hash7 的 selector 变为 body:nth-of-type(1) > button:nth-of-type(k)（不再是 button.css-1x2y3z4）
```

⚠️ 放宽 RAND 触及**每一个 fixture**（base/shadow/form/inner/outer 都在用同一份脚本），
所以 `./...` 全集是必跑项、不是可选项 —— 实测 81/0/0，没有连带回归。

## 7.4 变异验证（本轮要求：RAND 改回原样 → 新测试必须变红）

| 变异 | 改了什么 | 结果 |
|---|---|---|
| **M6**（控制器点名） | RAND **整条改回原样**（撤掉形态③） | `TestObserveSelectorRandomHashClassNotPreferred` **红**，输出与首轮一字不差：`selector="button.css-1x2y3z4" stability="medium"` ✅ |
| **M7** | 只把 `name` 那一路的 `!RAND.test` 撤掉（形态③保留） | 同一条测试**红**，两条 name 断言都开火（token 仍在 + 没退化成结构路径）—— 证明两处漏网**各自**被守 ✅ |
| **M8** | RAND 改成 `/./`（什么都算随机 = 过度检测那一边） | `TestObserveSelectorLegitTokensNotRejected` **红**：7 个正常类名 + 1 个 name 全部报出「被判成了随机 token」，且错误信息里带着退化后的结构路径 ✅ |

M8 是**反向边界那条测试自己的变异验证** —— 没有它，「不该抓的别抓」就只是一句声明。

⚠️ 形态③ 的表达式里有个 `(?=…)` 长度地板与两个 capture group，属于「聪明正则」。
它的可读性靠的是 RAND 上方那 20 行注释 + 这一节的取舍表；**行为**由 M6/M8 两个方向的
变异钉住。若将来要做真站校准（R5），建议连这条正则一起重写并用真站样本跑本文件的两组断言。

**仍认不出的（留给 R5，别当成已完备）**：全字母 hash（styled-components 的
`sc-bdVaJa`，没有数字可认）与只翻转一次的 hash（`css-abcdefg1`）。前者要认就得引
前缀表（`css-` / `sc-` / `jss`），那是站点知识、应由真站样本得出，本轮刻意没做。

## 7.5 本轮**没做**的（控制器已记进 ledger）

`alternates` 对「元素自身有稳定 id」的元素恒空（第 4.1 节）与 `stability()` 的 `high`
被 `/^#/` 放宽（第 4.2 节）—— 本轮不动。两者仍由特征化断言把现状钉住。

---

# 8. Task 5 修复轮 2：把边界补成双向（审查四条）

**审查结论**：修复轮 1 Approved，但有一处**正好打在那条硬要求上**的缺口 ——
① 我对 `class` 钉了双向，对 `name` **只钉了单向**，而 `name` 恰恰是这轮改动的落点。
另有四条 Minor，同一个主题：**测试/fixture 里陈旧的数字与描述着修复前行为的注释**。

## 8.1 ① name 落点补成双向（重要）

原缺口：③ 在 `name` 上新危及的是 **L→D→L 家族**（`step2a` / `address1a` / `opt2b`
这类「步骤+序号+子项」的人写惯例）—— 被误抓时退化到位置路径，而**套件里没有任何
东西会说话**（正向只钉了「随机 name 必须丢」）。

fixture 加两条：

| 元素 | 期望 | 实测 |
|---|---|---|
| `N random name 3`（`name="a1b2c3"`，③ 形态） | **必须丢**，且退化成结构路径 | `body:nth-of-type(1) > input:nth-of-type(5)`、`medium` ✅ |
| `N subfield name`（`name="step2a"`，人写的子字段名） | **接受被丢**，但必须**确实退化成结构路径** | `body:nth-of-type(1) > input:nth-of-type(6)`、`medium` ✅ |

两者合起来一条 `TestObserveSelectorNameLandingBothWays`（两个方向 + 两条退化断言）。

**取舍写进了代码旁**（审查要求：别只留在报告或 ledger 里）—— `observe.go` 的 `name`
那一行上方现在写着：③ 对 class 是「捡便宜」（class 由框架生成、hash 常见），对 name 是
「收益小、代价稍大」（name 人写、hash 罕见，而 `step2a` 一家正好落进 ③ 的形态）；
**不给 name 开特例**的理由是「一条规则、一个偏置」比按落点分叉更好维护 ——
分叉意味着同一个 token 在不同落点有不同命运，那正是本仓反复踩的「两份判据」；
且退化的代价**轻微**（仍能选中元素，只是不再抗结构变化）。控制器裁定：接受。
那条测试里也写了交叉引用：**若将来有人收窄 ③ 让 `step2a` 不再被丢，这条会红，
错误信息会提醒他同步改掉那处注释**，别让注释和测试各说一套。

## 8.2 ② 四条 Minor（陈旧数字与描述着修复前行为的注释）

| # | 问题 | 处理 |
|---|---|---|
| 1 | 测试里写「11 按钮 + 3 input = 14（实测 14）」，但 fixture 早就不止 | 改成**实测值**：19 按钮 + 6 input = **25**（数的是 `-v` 日志里每次导航 dump 的 action 行数，25×8 条测试 = 200，与日志逐条对上；不是顺着审查给的数字抄的） |
| 2 | `selector.html` 两处注释描述的是**修复前**的行为（引旧的两形态 RAND；写「`name` 这一路**没有** RAND」） | 都改了：第 7 节表头改列**三形态**并注明四个落点全过 RAND；7c 改成「**修复轮 1 之前**没过 RAND」；2a（`D hash7`）从「实测没被 RAND 滤掉」改成「修复轮 1 之前没被滤掉、之后退化成结构路径」—— **fixture 现在与它自己的 README 一致了** |
| 3 | **形态① 成了孤儿**（①独有的用例都被③接管，删掉①套件仍绿） | 加 `N hex10 form1-only`（`class="css-abcdef1234"`：`abcdef` 全字母 + `1234` 全数字，**只翻转一次**，②要 ≥6 位数字、③要交替 ≥2 次，都够不着）→ 由 `TestObserveSelectorRandomTokensNotPreferred` 里的专用用例守着 |
| 4 | 未披露的残留：③ 有 ≥5 位长度地板 → **≤4 字符的 hash 段**（`css-a1b2`）漏网 | 补进 `observe.go` 的「已知遗漏 b)」+ README 的残留清单 |

## 8.3 修复轮 2 的实测

```
go test ./internal/ -run 'TestObserve' -v -count=1   → PASS=18  SKIP=0  FAIL=0
go test ./... -v -count=1                            → PASS=82  SKIP=0  FAIL=0
```

（首轮 15+1FAIL → 修复轮 1 的 17 全绿 → 本轮加 name 双向那条 = **18**；
`./...` 79+1FAIL → 81 → **82**。）

## 8.4 变异验证（本轮：撤掉 name 的过滤 → 新加的那条必须红）

| 变异 | 改了什么 | 结果 |
|---|---|---|
| **M9**（本轮点名） | 撤掉 `name` 那一路的 `!RAND.test` | `TestObserveSelectorNameLandingBothWays` **红**：方向一两条断言都开火（`a1b2c3` 仍在首选 + 没退化成结构路径），方向二两条也都开火（`step2a` 没被 ③ 抓走 + 没退化）—— 一条抓随机、一条抓退化，正合审查的要求；另一个含随机 name 的测试也同时红 ✅ |
| **M10**（Minor 3 的验证） | 从 RAND 里**删掉形态①** | 只红**一条**用例：`N hex10 form1-only`（`css-abcdef1234`）。`E hash8` / `J random id` / `K random tid` 全绿（它们已被 ②/③ 接管）—— 正好证明「①不再是孤儿」且**隔离是精确的**：① 的存在与否现在只由那一条用例说话 ✅ |

⚠️ **过程事故（一并记账）**：M10 第一次是用 `sed` 改的，模式尾部多了一个 `\|`
（BRE 里那等于**空分支**）→ 匹配空串 → 整份 `observe.go` 每一行都被插入了
`var RAND = /`。当时文件被写坏；因为改前先 `cp` 了备份，`cp` 还原后
`diff -q` 确认**逐字节相同**、`go vet` 通过，交付物未受影响（M10 随后改用编辑器精确替换重做）。
教训：**这份文件里的 RAND 是含 `|` 的正则，不要用 sed 去改它** —— 用结构化编辑。

## 8.5 本轮**没做**的（审查已记 ledger）

- **M8 是个粗糙的变异**：`RAND=/./` 会连另一条测试一起弄红，它证明了反向测试**会响**、
  没证明它**必要**。更有信息量的是被明确否决的设计「混排 + 长度 ≥5」——
  那个只有反向测试能抓。本轮未跑，记着。
- **RAND 的波及面超出那两个已测落点**：`pathSel` 的祖先 id 检查、选项组 scope、
  遮挡物/关闭按钮选择器 —— 三处都无断言。已写进 README 的残留清单。
- 4.1/4.2 两条设计观察（`alternates` 空洞、`high` 被 `/^#/` 放宽）仍不动，由特征化断言钉住。
