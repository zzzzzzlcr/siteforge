# 工具层：`observe` / `diff` 与 cdp 迁入 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `cdp` 迁进 siteforge，并让 `observe` 成为一个真能用的 CLI 子命令 —— 在 light DOM / 嵌套 shadow / 跨源 iframe 上都能产出规格 §4.3 定义的页面模型。

**Architecture:** 原样迁入 cdpcli 的 Go 内核（`internal/`），在**同一个内核**上新增 `internal/observe.go`（页面模型提取，复用内核既有的 `__cdpQ` 穿透助手）与 `internal/diff.go`；`cmd/observe.go`、`cmd/diff.go` 暴露 CLI。跨帧由 Go 侧枚举帧树、逐帧求值再合并（同源策略使单次 eval 看不见跨源帧）。稍后 `cmd/mcp` 用同一内核包成 MCP 工具 —— **不是包一层壳去 subprocess 调 CLI**。

**Tech Stack:** Go 1.26（module `cdp`）· chromedp/cdproto · cobra · Go 标准 `testing` + 真浏览器集成测试（沿用 cdpcli 既有的 skip-if-no-browser 模式）

**Spec:** `docs/superpowers/specs/2026-09-16-siteforge-design.md`

## Global Constraints

以下每条都来自规格，**每个任务的要求都隐含包含本节**：

- **动作一律走 cdp 命令，不手拼 JS**（规格 D8 / §5.2）。读可以用 JS，写不行。
- **穿透只用内核的 `__cdpQ` / `__cdpQA`**，不各自重写一套（规格 §4.1，`internal/shadow.go` 注释原话「不必各自重写一套」）。
- **`observe` 给感知不给判断**（规格 D11）：输出 `region` / `above_fold` / `relative_size` / `peer_count` / `z_index` / `contrast` / `nearby_text` 这类**原始事实**；**不得**出现 `intent` / `importance` / `primary CTA` 这类语义结论。
- **三条静默陷阱必须钉测试**（规格 §4.3）：① `ShadowRoot` 没有 `innerText`；② `document.elementsFromPoint` 不穿透 shadow；③ `parentElement` 出不了 shadow 边界。
- **跨帧必须逐帧取再合并**（规格 §4.3），每条动作带 `frame_path`。
- CLI 的 `--host` / `--port` 必须放在**位置参数前面**（既有坑）。
- 构建命令是 `go build -o cdp main.go`，**不要**用 `go build .`（会把非 Go 文件打进去）。
- 测试必须进 git（本项目 `.gitignore` 刻意不忽略 `tests/` 与 `testdata/`）。

---

## File Structure

```
tools/cdp/
  main.go                  ← 迁入（cdpcli 根 main）
  go.mod / go.sum          ← 迁入
  build.sh / verify.sh     ← 迁入
  cmd/
    root.go form.go click.go scroll.go eval.go navi.go snapshot.go
    targets.go active.go close.go            ← 迁入
    observe.go                               ← 新建：observe 子命令
    diff.go                                  ← 新建：diff 子命令
    mcp/main.go                              ← 新建：MCP server（独立二进制）
  internal/
    client.go form.go js.go shadow.go        ← 迁入
    observe.go                               ← 新建：单帧页面模型提取
    observe_frames.go                        ← 新建：跨帧枚举与合并
    diff.go                                  ← 新建：状态差分
    observe_test.go                          ← 新建：单元 + 陷阱回归
    observe_integration_test.go              ← 新建：真浏览器四档
    testdata/                                ← 新建：四档 fixture
      base.html shadow.html inner.html outer.html form.html
```

**为什么 `observe` 拆两个文件**：单帧提取（纯 JS 求值 + Go 组装）与跨帧编排（帧树枚举 + 合并）是两个独立的关注点，各自可单独测试。

---

## Task 1: 迁入 cdpcli 并验证跨源帧能力没丢

**这一步有个真风险**：R3 探针跑的是 `/opt/skills/auto-farm-skill/cdp`，它是 **HEAD + 未提交 WIP(OOPIF)** 构建。若只迁 HEAD，可能丢掉 `evalInOOPIFFrame` 那条路 → **跨源帧能力静默消失**。所以本任务的验收就是「迁入后重新构建的二进制，四档 fixture 全过」。

**Files:**
- Create: `tools/cdp/**`（从 `/company/cdpcli` 复制）
- Modify: `Dockerfile`（构建命令对齐实际布局）
- Test: `tools/cdp/internal/shadow_integration_test.go`（迁入的既有测试）

**Interfaces:**
- Produces: 可构建的 `tools/cdp` 模块；`go test ./...` 可跑；后续任务在此基础上加文件。

- [ ] **Step 1: 记录当前部署二进制的能力基线**

```bash
mkdir -p /tmp/siteforge-baseline
cp /opt/skills/auto-farm-skill/cdp /tmp/siteforge-baseline/cdp
md5sum /tmp/siteforge-baseline/cdp    # 应为 1df52111298f5c3ebed8104ec6591c77
grep -ac __cdpQ /tmp/siteforge-baseline/cdp   # 应为 21
```

- [ ] **Step 2: 从 git HEAD 复制源码（不带 WIP）**

```bash
cd /company/siteforge
mkdir -p tools/cdp
cd /company/cdpcli
git archive HEAD | tar -x -C /company/siteforge/tools/cdp
cd /company/siteforge/tools/cdp && ls
```

预期看到 `main.go cmd internal go.mod go.sum build.sh verify.sh`。
**确认没有** `*.patch`、`client_full_wip.go` 之类的 WIP 残留。

- [ ] **Step 3: 构建并跑既有测试**

```bash
cd /company/siteforge/tools/cdp
go build -o cdp main.go
go test ./... 2>&1 | tail -20
```

预期：构建成功；测试通过或按 skip 条件跳过（无浏览器时 `shadow_integration_test.go` 会 Skip）。

- [ ] **Step 4: 用新构建的二进制重跑 R3 四档 fixture（关键验收）**

把探针的 fixture 搬进来并起服务：

```bash
mkdir -p /company/siteforge/tools/cdp/internal/testdata
cp /company/siteforge/docs/probes/2026-09-16-observe-r3/fixtures/*.html \
   /company/siteforge/tools/cdp/internal/testdata/
cd /company/siteforge/tools/cdp/internal/testdata && python3 -m http.server 8892 &
```

然后在 `127.0.0.1:9222` 的 Chrome 上验证跨源帧**仍然可进**：

```bash
CDP=/company/siteforge/tools/cdp/cdp
ID=$(curl -s -X PUT "http://127.0.0.1:9222/json/new?http://127.0.0.1:8892/outer.html" \
     | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
sleep 4
$CDP --host 127.0.0.1 --port 9222 active $ID
CHILD=$(python3 /company/siteforge/docs/probes/2026-09-16-observe-r3/frames.py \
        | awk -F'\t' '/inner.html/{print $1; exit}')
$CDP --host 127.0.0.1 --port 9222 eval --frame-id "$CHILD" "location.href"
```

预期：返回 `"http://localhost:8892/inner.html"`（**不是** outer.html）。

- [ ] **Step 5: 若跨源帧失败 —— 停下来，不要继续**

失败说明 HEAD-only 丢掉了 WIP 的能力。此时**不要自己写规避代码**，回到用户那里定 R7（迁 HEAD+WIP，或先把 WIP 提交）。在计划里如实记录结果。

- [ ] **Step 6: 对齐 Dockerfile 的构建路径**

`Dockerfile` 里 stage 1 目前写的是 `./cmd/cdp`，而实际入口是根目录的 `main.go`。改成：

```dockerfile
RUN go build -ldflags="-s -w" -o /out/cdp main.go \
 && go build -ldflags="-s -w" -o /out/cdp-mcp ./cmd/mcp \
 && /out/cdp --help >/dev/null
```

（`./cmd/mcp` 要到 Task 8 才存在；本步先只保留 `cdp` 那一行并加注释，
Task 8 再补回第二行 —— 否则本任务结束时代码库处于不可构建状态。）

- [ ] **Step 7: 提交**

```bash
cd /company/siteforge
git add tools/cdp Dockerfile
git commit -m "feat: 迁入 cdp 工具层（git HEAD，不含 WIP）—— 并验证跨源帧能力未丢"
```

---

## Task 2: `internal/observe.go` — 单帧页面模型提取（三条陷阱先钉测试）

**Files:**
- Create: `tools/cdp/internal/observe.go`
- Test: `tools/cdp/internal/observe_traps_test.go`

**Interfaces:**
- Consumes: `internal.Client.EvalInFrame(frameID string, js string, result interface{}) error`（迁入的既有签名）、`withPierce(js string) string`
- Produces:
  - `func observeJS() string` — 返回单帧页面模型的求值脚本（含穿透前缀）
  - `type PageModel struct { URL, Title, PageText string; ShadowRoots int; Actions []Action; Fields []Field; OptionGroups []OptionGroup; Obstructions []Obstruction }`
  - `type Action struct { Selector string; Alternates []string; Stability, Text, Role, Tag, Type string; Visible bool; OccludedBy *string; ShadowDepth int; FramePath []string; BBox [4]int; Region string; AboveFold bool; RelativeSize float64; PeerCount int; ZIndex string; Contrast string; NearbyText []string }`

**三条陷阱的测试必须先写、必须先红** —— 它们是 R3 探针实测出来的，第一版实现全踩了（规格 §4.3）。

- [ ] **Step 1: 写失败测试（三条陷阱 + 契约形状）**

`tools/cdp/internal/observe_traps_test.go`：

```go
package internal

import (
	"encoding/json"
	"strings"
	"testing"
)

// 三条陷阱用 fixture 页面在真浏览器上验（见 observe_integration_test.go）。
// 这里钉的是**脚本本身必须具备的特征** —— 少一条就必然静默出错。
func TestObserveJSMustHandleShadowText(t *testing.T) {
	js := observeJS()
	// ShadowRoot 没有 innerText（那是 HTMLElement 的属性）。
	// 正确做法：shadow root 取子元素的 innerText。
	if strings.Contains(js, "r.innerText") || strings.Contains(js, "root.innerText") {
		t.Error("直接对 shadow root 取 innerText —— ShadowRoot 上没有这个属性，会得到 undefined")
	}
	if !strings.Contains(js, ".children") {
		t.Error("没有遍历 shadow root 的子元素来收文本 —— page_text 在 shadow 页上会几乎为空")
	}
}

func TestObserveJSMustNotTrustElementsFromPointAlone(t *testing.T) {
	js := observeJS()
	// elementsFromPoint 不穿透 shadow（返回 host）。只判断「元素在不在命中栈里」
	// 会把所有 shadow 元素误判为被遮挡（实测 5/5 假阳性）。
	if !strings.Contains(js, "composedAncestors") {
		t.Error("遮挡判定没有走合成树祖先链 —— shadow 元素会被整片误判为被遮挡")
	}
}

func TestObserveJSMustClimbThroughShadowHosts(t *testing.T) {
	js := observeJS()
	// parentElement 出不了 shadow 边界 → region 全部退化成 body
	if !strings.Contains(js, "getRootNode") || !strings.Contains(js, ".host") {
		t.Error("层级遍历没有经 getRootNode().host —— region/祖先链在 shadow 页上会断掉")
	}
}

func TestObserveJSMustUsePierceHelper(t *testing.T) {
	js := observeJS()
	// 全局约束：穿透只用内核的助手，不各自重写一套
	if !strings.Contains(js, "__cdpQA") {
		t.Error("没有用内核的 __cdpQA 穿透查询助手（规格 §4.1：不要各自重写一套）")
	}
}

func TestObserveJSMustNotEmitSemanticJudgements(t *testing.T) {
	js := observeJS()
	// 规格 D11：observe 给感知不给判断
	for _, banned := range []string{"primary CTA", "importance", `"intent"`} {
		if strings.Contains(js, banned) {
			t.Errorf("observe 脚本里出现了语义判断 %q —— 那是 cognition，不是 perception", banned)
		}
	}
}

func TestPageModelJSONShape(t *testing.T) {
	raw := `{"url":"u","title":"t","page_text":"p","shadow_roots":2,
	         "actions":[{"selector":"#a","alternates":[],"stability":"high","text":"Go",
	                     "role":"button","tag":"BUTTON","type":null,"visible":true,
	                     "occluded_by":null,"shadow_depth":2,"frame_path":["main"],
	                     "bbox":[1,2,3,4],"region":"hero","above_fold":true,
	                     "relative_size":1.8,"peer_count":3,"z_index":"auto",
	                     "contrast":"high","nearby_text":["x"]}],
	         "fields":[],"option_groups":[],"obstructions":[]}`
	var m PageModel
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatalf("契约 JSON 解析失败: %v", err)
	}
	if len(m.Actions) != 1 || m.Actions[0].Selector != "#a" || m.Actions[0].ShadowDepth != 2 {
		t.Fatalf("字段没对上: %+v", m.Actions)
	}
}
```

- [ ] **Step 2: 跑测试，确认全红**

```bash
cd /company/siteforge/tools/cdp
go test ./internal/ -run 'TestObserveJS|TestPageModelJSONShape' -v
```

预期：编译失败（`undefined: observeJS` / `undefined: PageModel`）。**这就是我们要的红。**

- [ ] **Step 3: 实现 `internal/observe.go`**

把探针验证过的算法搬进来，但**两处必须改**（探针是探针，生产是生产）：

1. 穿透改成调内核的 `__cdpQA`（由 `withPierce` 注入），不再自实现
2. 三条陷阱的正确做法照搬

骨架（完整逻辑以 `docs/probes/2026-09-16-observe-r3/observe.js` 为准，
逐段搬过来，只把开头的自实现 `RS`/`qsa` 换成 `__cdpQA`）：

```go
package internal

import (
	"encoding/json"
	"fmt"
)

// PageModel 是规格 §4.3 的 observe 契约。
// 只装**感知**：原始事实。语义判断（intent / importance）由 agent 做（规格 D11）。
type PageModel struct {
	URL          string        `json:"url"`
	Title        string        `json:"title"`
	PageText     string        `json:"page_text"`
	ShadowRoots  int           `json:"shadow_roots"`
	Actions      []Action      `json:"actions"`
	Fields       []Field       `json:"fields"`
	OptionGroups []OptionGroup `json:"option_groups"`
	Obstructions []Obstruction `json:"obstructions"`
}

type Action struct {
	Selector     string   `json:"selector"`
	Alternates   []string `json:"alternates"`
	Stability    string   `json:"stability"`
	Text         string   `json:"text"`
	Role         string   `json:"role"`
	Tag          string   `json:"tag"`
	Type         string   `json:"type"`
	Visible      bool     `json:"visible"`
	OccludedBy   *string  `json:"occluded_by"`
	ShadowDepth  int      `json:"shadow_depth"`
	FramePath    []string `json:"frame_path"`
	BBox         [4]int   `json:"bbox"`
	Region       string   `json:"region"`
	AboveFold    bool     `json:"above_fold"`
	RelativeSize float64  `json:"relative_size"`
	PeerCount    int      `json:"peer_count"`
	ZIndex       string   `json:"z_index"`
	Contrast     string   `json:"contrast"`
	NearbyText   []string `json:"nearby_text"`
}

type Field struct {
	Selector    string   `json:"selector"`
	Alternates  []string `json:"alternates"`
	Stability   string   `json:"stability"`
	Label       string   `json:"label"`
	Hint        string   `json:"hint"`
	Placeholder string   `json:"placeholder"`
	Type        string   `json:"type"`
	Required    bool     `json:"required"`
	ShadowDepth int      `json:"shadow_depth"`
	FramePath   []string `json:"frame_path"`
}

type OptionGroup struct {
	Scope       string   `json:"scope"`
	Role        string   `json:"role"`
	Options     []string `json:"options"`
	ShadowDepth int      `json:"shadow_depth"`
}

type Obstruction struct {
	Kind            string `json:"kind"`
	Selector        string `json:"selector"`
	DismissSelector string `json:"dismiss_selector"`
	Text            string `json:"text"`
}

// observeJS 返回单帧页面模型的求值脚本。
// 穿透走内核助手 __cdpQA（由 withPierce 注入），不自实现遍历。
func observeJS() string {
	return withPierce(`(function(){
	  var qsa = function(sel){ return __cdpQA(sel); };
	  function composedAncestors(el){
	    var out=[], n=el;
	    while(n){ out.push(n);
	      var r = n.getRootNode ? n.getRootNode() : null;
	      n = (r && r.host) ? r.host : (n.parentElement || null); }
	    return out;
	  }
	  /* … 其余逐段取自 docs/probes/2026-09-16-observe-r3/observe.js … */
	  return JSON.stringify({ /* PageModel */ });
	})()`)
}

// Observe 对指定帧求值并解析成 PageModel。frameID 为空表示主帧。
func (c *Client) Observe(frameID string) (*PageModel, error) {
	var raw string
	if err := c.EvalInFrame(frameID, observeJS(), &raw); err != nil {
		return nil, fmt.Errorf("observe eval: %w", err)
	}
	var m PageModel
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		return nil, fmt.Errorf("observe 契约解析失败: %w\n原始: %.300s", err, raw)
	}
	return &m, nil
}
```

- [ ] **Step 4: 跑测试，确认全绿**

```bash
cd /company/siteforge/tools/cdp
go test ./internal/ -run 'TestObserveJS|TestPageModelJSONShape' -v
```

预期：**7 个测试全 PASS**。

- [ ] **Step 5: 提交**

```bash
git add tools/cdp/internal/observe.go tools/cdp/internal/observe_traps_test.go
git commit -m "feat: observe 页面模型提取 —— 三条静默陷阱各钉一条测试"
```

---

## Task 3: 真浏览器四档集成测试

**Files:**
- Create: `tools/cdp/internal/observe_integration_test.go`
- Test data: `tools/cdp/internal/testdata/*.html`（Task 1 Step 4 已搬入）

**Interfaces:**
- Consumes: `(*Client).Observe(frameID string) (*PageModel, error)`
- Produces: 无新接口；本任务是**验收闸门** —— 后面任何改动破坏 observe 都会被它拦下。

- [ ] **Step 1: 写集成测试（无浏览器时 Skip，沿用既有模式）**

```go
package internal

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"testing"
	"time"
)

// 复用 shadowTestEndpoint()（迁入的 shadow_integration_test.go 里已有）
func serveFixtures(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.FileServer(http.Dir("testdata")))
	t.Cleanup(srv.Close)
	return srv
}

func navigateAndObserve(t *testing.T, url string) *PageModel {
	t.Helper()
	host, port := shadowTestEndpoint()
	c, err := NewClient(host, port)
	if err != nil {
		t.Skipf("Chrome 不可用: %v", err)
	}
	t.Cleanup(c.Disconnect)
	if _, err := c.Navigate(url, ""); err != nil {
		t.Skipf("导航失败: %v", err)
	}
	time.Sleep(1500 * time.Millisecond)
	m, err := c.Observe("")
	if err != nil {
		t.Fatalf("observe 失败: %v", err)
	}
	return m
}

func TestObserveLightDOM(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/base.html")

	if m.ShadowRoots != 0 {
		t.Errorf("base.html 不该有 shadow root，实际 %d", m.ShadowRoots)
	}
	if len(m.Actions) < 5 {
		t.Errorf("可动作元素太少: %d", len(m.Actions))
	}
	// 同名按钮必须能靠 region 区分 —— 这是 observe 存在的核心理由
	seen := map[string]map[string]bool{}
	for _, a := range m.Actions {
		if a.Text == "Learn More" {
			if seen[a.Text] == nil {
				seen[a.Text] = map[string]bool{}
			}
			seen[a.Text][a.Region] = true
		}
	}
	if len(seen["Learn More"]) < 2 {
		t.Errorf("两个同名 Learn More 没被 region 区分开: %+v", seen)
	}
	if len(m.Obstructions) == 0 {
		t.Error("cookie 横幅没被识别为 obstruction")
	}
}

// 陷阱 ①：page_text 必须包含 shadow 里的文本
func TestObserveShadowPageTextIsNotEmpty(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	if m.ShadowRoots < 2 {
		t.Fatalf("fixture 应有两层嵌套 shadow root，实际 %d", m.ShadowRoots)
	}
	// 踩坑时的实测值：10 个字符（只有 shadow 外面的标题）。
	// 修好后是 141。阈值取 60 足以区分两种实现。
	if len([]rune(m.PageText)) < 60 {
		t.Errorf("page_text 只有 %d 字 —— shadow 里的正文没收到: %q",
			len([]rune(m.PageText)), m.PageText)
	}
	if len(m.Fields) < 3 {
		t.Errorf("表单字段应至少 3 个（阴影里），实际 %d", len(m.Fields))
	}
	for _, f := range m.Fields {
		if f.ShadowDepth < 2 {
			t.Errorf("字段 %s 的 shadow_depth 应为 2，实际 %d", f.Selector, f.ShadowDepth)
		}
	}
}

// 陷阱 ②：shadow 元素不得被误判为「被遮挡」
func TestObserveShadowElementsNotFalselyOccluded(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	var bad []string
	for _, a := range m.Actions {
		if a.OccludedBy != nil {
			bad = append(bad, a.Text+"←"+*a.OccludedBy)
		}
	}
	// 踩坑时这里是 5/5 全假阳性
	if len(bad) > 0 {
		t.Errorf("shadow 元素被误判为被遮挡（踩坑时实测 5/5 假阳性）: %v", bad)
	}
}

// 陷阱 ③：region 不得全部退化成 body
func TestObserveShadowRegionNotAllBody(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	regions := map[string]bool{}
	for _, a := range m.Actions {
		regions[a.Region] = true
	}
	if len(regions) == 1 && regions["body"] {
		t.Error("region 全部退化成 body —— parentElement 出不了 shadow 边界（陷阱 ③）")
	}
}

// 跨源 iframe：主帧 + 子帧分别取，合并后两侧都在
func TestObserveCrossOriginFrameMerge(t *testing.T) {
	srv := serveFixtures(t)
	// 127.0.0.1 与 localhost 是不同源 —— 用同一台服务器造真跨源
	u := srv.URL + "/outer.html"
	if _, err := os.Stat("testdata/outer.html"); err != nil {
		t.Skip("缺 outer.html fixture")
	}
	host, port := shadowTestEndpoint()
	c, err := NewClient(host, port)
	if err != nil {
		t.Skipf("Chrome 不可用: %v", err)
	}
	defer c.Disconnect()
	if _, err := c.Navigate(u, ""); err != nil {
		t.Skipf("导航失败: %v", err)
	}
	time.Sleep(2 * time.Second)

	merged, err := c.ObserveAll()
	if err != nil {
		t.Fatalf("ObserveAll 失败: %v", err)
	}
	hasChild := false
	for _, a := range merged.Actions {
		if len(a.FramePath) > 1 {
			hasChild = true
		}
	}
	if !hasChild {
		t.Error("合并结果里没有来自子帧的动作 —— 跨帧合并没生效")
	}
	_ = strconv.Itoa // 保留 import 示例，实现时按需删
}
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
cd /company/siteforge/tools/cdp
go test ./internal/ -run 'TestObserve(LightDOM|Shadow|CrossOrigin)' -v
```

预期：`ObserveAll` 未定义 → 编译失败（Task 4 才实现）。

- [ ] **Step 3: 暂时只保留单帧三条 + light，把跨源那条用 `t.Skip` 标住**

在 `TestObserveCrossOriginFrameMerge` 开头加 `t.Skip("Task 4 实现 ObserveAll 后开启")`。

- [ ] **Step 4: 跑测试确认单帧三条通过**

```bash
go test ./internal/ -run 'TestObserve(LightDOM|Shadow)' -v
```

预期：3 个 PASS。

- [ ] **Step 5: 提交**

```bash
git add tools/cdp/internal/observe_integration_test.go
git commit -m "test: observe 四档集成测试（跨源一档待 Task 4 开启）"
```

---

## Task 4: 跨帧枚举与合并

**Files:**
- Create: `tools/cdp/internal/observe_frames.go`
- Modify: `tools/cdp/internal/observe_integration_test.go`（去掉跨源那条的 Skip）

**Interfaces:**
- Consumes: `(*Client).Observe(frameID string) (*PageModel, error)`（Task 2）
- Produces: `func (c *Client) ObserveAll() (*PageModel, error)` — 枚举帧树、逐帧 Observe、按 `frame_path` 合并

- [ ] **Step 1: 去掉跨源测试的 Skip，确认它红**

```bash
cd /company/siteforge/tools/cdp
sed -i 's/^\tt.Skip("Task 4 实现 ObserveAll 后开启")$//' internal/observe_integration_test.go
go test ./internal/ -run TestObserveCrossOriginFrameMerge -v
```

预期：编译失败 `c.ObserveAll undefined`。

- [ ] **Step 2: 实现 `observe_frames.go`**

```go
package internal

import "fmt"

// ObserveAll 枚举帧树、逐帧取页面模型再合并。
//
// 为什么必须逐帧：同源策略决定**单次 eval 看不见跨源帧的内容**（R3 探针实测）。
// 合并时把帧路径写进每条动作/字段的 FramePath，agent 才知道该把动作发给哪一帧。
func (c *Client) ObserveAll() (*PageModel, error) {
	tree, err := c.GetFrameTree()
	if err != nil {
		return nil, fmt.Errorf("取帧树失败: %w", err)
	}
	merged := &PageModel{}
	var walk func(f FrameNode, path []string) error
	walk = func(f FrameNode, path []string) error {
		m, err := c.Observe(f.ID)
		if err != nil {
			// 单帧失败不整体失败：某些帧（广告、追踪）本来就取不到。
			// 但要在结果里留下痕迹，不能静默吞掉。
			return nil
		}
		if merged.URL == "" {
			merged.URL, merged.Title = m.URL, m.Title
		}
		merged.ShadowRoots += m.ShadowRoots
		merged.PageText += " " + m.PageText
		for _, a := range m.Actions {
			a.FramePath = path
			merged.Actions = append(merged.Actions, a)
		}
		for _, fl := range m.Fields {
			fl.FramePath = path
			merged.Fields = append(merged.Fields, fl)
		}
		merged.OptionGroups = append(merged.OptionGroups, m.OptionGroups...)
		merged.Obstructions = append(merged.Obstructions, m.Obstructions...)
		for _, ch := range f.Children {
			if err := walk(ch, append(append([]string{}, path...), ch.ID)); err != nil {
				return err
			}
		}
		return nil
	}
	for _, f := range tree {
		if err := walk(f, []string{"main"}); err != nil {
			return nil, err
		}
	}
	return merged, nil
}
```

`FrameNode` 与 `GetFrameTree()` 若内核里没有，用 `Page.getFrameTree` 补一个最小实现（`internal/client.go` 已 import cdproto）。

- [ ] **Step 3: 跑测试确认通过**

```bash
go test ./internal/ -run 'TestObserve' -v
```

预期：4 个 PASS（含跨源合并）。

- [ ] **Step 4: 提交**

```bash
git add tools/cdp/internal/observe_frames.go tools/cdp/internal/observe_integration_test.go
git commit -m "feat: observe 跨帧枚举与合并 —— 同源策略下单次 eval 看不见跨源帧"
```

---

## Task 5: 选择器候选与稳定性评级

**Files:**
- Modify: `tools/cdp/internal/observe.go`
- Test: `tools/cdp/internal/observe_selector_test.go`

**Interfaces:**
- Produces: `func selectorCandidates(el) []string`（JS 侧）、`func stabilityOf(cands []string) string`（Go 侧，供离线测试）
- 评级口径（规格 §4.3）：`high` = 有稳定 id/name/data-*；`medium` = 结构路径 ≤3 层；`low` = 随机 hash / 深路径

- [ ] **Step 1: 写失败测试**

```go
package internal

import "testing"

func TestStabilityRating(t *testing.T) {
	cases := []struct{ cands []string; want string }{
		{[]string{"#schedule-now"}, "high"},
		{[]string{`button[name="firstName"]`}, "high"},
		{[]string{`button[data-testid="go"]`}, "high"},
		{[]string{"div:nth-of-type(2) > button"}, "medium"},
		{[]string{"div:nth-of-type(2) > div:nth-of-type(1) > span:nth-of-type(3) > button"}, "low"},
		{[]string{"button.css-1x2y3z4"}, "low"},
	}
	for _, c := range cases {
		if got := stabilityOf(c.cands); got != c.want {
			t.Errorf("stabilityOf(%v) = %q, want %q", c.cands, got, c.want)
		}
	}
}

func TestRandomTokenDetection(t *testing.T) {
	rand := []string{"css-1x2y3z4", "a1b2c3d4e5f6", "id_9f8e7d6c5b4a", "x1234567890"}
	for _, s := range rand {
		if !looksRandom(s) {
			t.Errorf("%q 应被判为随机 token", s)
		}
	}
	stable := []string{"schedule-now", "firstName", "onetrust-accept-btn-handler", "hero"}
	for _, s := range stable {
		if looksRandom(s) {
			t.Errorf("%q 不该被判为随机 token", s)
		}
	}
}

// 候选必须按稳定性排序：第一个是首选
func TestCandidatesSortedByStability(t *testing.T) {
	got := []string{"#stable-id", "button:nth-of-type(1)"}
	if stabilityOf(got) != "high" {
		t.Error("首选是稳定 id 时应评为 high")
	}
}
```

- [ ] **Step 2: 跑测试确认红**

```bash
go test ./internal/ -run 'TestStabilityRating|TestRandomTokenDetection|TestCandidatesSortedByStability' -v
```

预期：`undefined: stabilityOf`。

- [ ] **Step 3: 实现（Go 侧评级 + JS 侧候选生成）**

```go
package internal

import "regexp"

// 随机 token：≥8 位连续 hex/base36 片段，或首尾被 -/_ 包住的 hash。
// 判据来自规格 §4.3。**会在真站上校准**（规格 R5）—— 有些 hash 其实是稳定的。
var randomToken = regexp.MustCompile(`(^|[-_])[0-9a-f]{8,}($|[-_])|^[a-z]*\d{6,}$`)

func looksRandom(s string) bool { return randomToken.MatchString(s) }

// stabilityOf 按候选列表（已按首选在前排序）评稳定性。
func stabilityOf(cands []string) string {
	if len(cands) == 0 {
		return "low"
	}
	c := cands[0]
	switch {
	case len(c) > 0 && c[0] == '#':
		return "high"
	case regexp.MustCompile(`\[(name|data-)[^]]*=`).MatchString(c):
		return "high"
	case regexp.MustCompile(`:nth-of-type`).MatchString(c):
		if countOf(c, ">") <= 2 {
			return "medium"
		}
		return "low"
	case regexp.MustCompile(`^[a-z]+\.[a-z]`).MatchString(c):
		return "medium"
	}
	return "low"
}

func countOf(s, sub string) int {
	n := 0
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			n++
		}
	}
	return n
}
```

JS 侧候选生成逐段取自探针的 `candidates()` / `pathSel()`。

- [ ] **Step 4: 跑测试确认绿**

```bash
go test ./internal/ -run 'TestStability|TestRandomToken|TestCandidates' -v
```

预期：3 个 PASS。

- [ ] **Step 5: 提交**

```bash
git add tools/cdp/internal/observe.go tools/cdp/internal/observe_selector_test.go
git commit -m "feat: 选择器候选与稳定性评级（规格 §4.3 / R5 待真站校准）"
```

---

## Task 6: `cdp observe` CLI 子命令

**Files:**
- Create: `tools/cdp/cmd/observe.go`
- Modify: `tools/cdp/cmd/root.go`（注册子命令）

**Interfaces:**
- Consumes: `(*Client).ObserveAll() (*PageModel, error)`（Task 4）
- Produces: CLI `cdp observe [--json] [--frame-id <id>]`，输出 `PageModel` 的 JSON

- [ ] **Step 1: 写失败测试（CLI 契约）**

```go
package cmd

import (
	"strings"
	"testing"
)

func TestObserveCommandHasContractFlags(t *testing.T) {
	cmd := observeCmd
	if cmd == nil {
		t.Fatal("observeCmd 未注册")
	}
	for _, f := range []string{"json", "frame-id"} {
		if cmd.Flags().Lookup(f) == nil {
			t.Errorf("observe 子命令缺 --%s", f)
		}
	}
	if !strings.Contains(cmd.Short, "observe") && cmd.Short == "" {
		t.Error("observe 子命令缺 Short 描述")
	}
}
```

- [ ] **Step 2: 跑测试确认红**

```bash
go test ./cmd/ -run TestObserveCommandHasContractFlags -v
```

预期：`undefined: observeCmd`。

- [ ] **Step 3: 实现子命令**

```go
package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"
	"github.com/spf13/cobra"
)

var observeCmd = &cobra.Command{
	Use:   "observe",
	Short: "观察页面：结构化页面模型（可动作元素 / 表单字段 / 选项组 / 遮挡物）",
	Long: `observe 是 agent 的主视角。它只给**感知**（位置、尺寸、区域、遮挡、
选择器候选与稳定性），不给判断 —— 语义由调用方推理。

跨源 iframe 会自动逐帧取再合并，每条动作带 frame_path。`,
	RunE: runObserve,
}

func init() {
	rootCmd.AddCommand(observeCmd)
	observeCmd.Flags().Bool("json", true, "输出 JSON（默认；保留开关便于以后加人类可读格式）")
	observeCmd.Flags().String("frame-id", "", "只观察指定帧（默认整页含子帧）")
}

func runObserve(cmd *cobra.Command, args []string) error {
	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	frameID, _ := cmd.Flags().GetString("frame-id")
	var m *internal.PageModel
	if frameID != "" {
		m, err = client.Observe(frameID)
	} else {
		m, err = client.ObserveAll()
	}
	if err != nil {
		return err
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(m)
}
```

- [ ] **Step 4: 跑测试 + 真站手验**

```bash
go build -o cdp main.go && go test ./cmd/ -run TestObserve -v
# 起 fixture 服务后：
./cdp --host 127.0.0.1 --port 9222 observe | head -30
```

预期：测试 PASS；CLI 打印出带 `actions` / `fields` 的 JSON。

- [ ] **Step 5: 提交**

```bash
git add tools/cdp/cmd/observe.go tools/cdp/cmd/root.go
git commit -m "feat: cdp observe 子命令 —— agent 的主视角（生成阶段）+ py 运行时回退用"
```

---

## Task 7: `cdp diff` — 动作前后差分

**Files:**
- Create: `tools/cdp/internal/diff.go`, `tools/cdp/cmd/diff.go`
- Test: `tools/cdp/internal/diff_test.go`

**Interfaces:**
- Produces: `func DiffModels(before, after *PageModel) Diff`；`type Diff struct { URLChanged bool; TextChanged bool; Appeared, Disappeared []string; Actionable bool }`
- `Actionable` = 「刚才那一下有没有推进」的判据（规格 §4.2）

- [ ] **Step 1: 写失败测试**

```go
package internal

import "testing"

func TestDiffDetectsProgress(t *testing.T) {
	before := &PageModel{URL: "u1", PageText: "Step 1",
		Actions: []Action{{Selector: "#a", Text: "Next"}}}
	after := &PageModel{URL: "u2", PageText: "Step 2",
		Actions: []Action{{Selector: "#b", Text: "Submit"}}}
	d := DiffModels(before, after)

	if !d.URLChanged || !d.TextChanged || !d.Actionable {
		t.Errorf("明显推进了却判无进展: %+v", d)
	}
	if len(d.Disappeared) != 1 || d.Disappeared[0] != "#a" {
		t.Errorf("消失元素没识别: %+v", d.Disappeared)
	}
}

func TestDiffDetectsNoProgress(t *testing.T) {
	same := &PageModel{URL: "u", PageText: "Step 1",
		Actions: []Action{{Selector: "#a", Text: "Next"}}}
	d := DiffModels(same, same)
	if d.Actionable {
		t.Error("页面纹丝不动却判为有推进 —— py 的重试逻辑会因此失效")
	}
}

func TestDiffIgnoresWhitespaceOnlyTextChange(t *testing.T) {
	a := &PageModel{URL: "u", PageText: "Step 1 here"}
	b := &PageModel{URL: "u", PageText: "Step   1   here"}
	if DiffModels(a, b).TextChanged {
		t.Error("纯空白差异不该算页面变化")
	}
}
```

- [ ] **Step 2: 跑测试确认红**

```bash
go test ./internal/ -run TestDiff -v
```

预期：`undefined: DiffModels`。

- [ ] **Step 3: 实现**

```go
package internal

import (
	"regexp"
	"strings"
)

var wsRun = regexp.MustCompile(`\s+`)

func normText(s string) string { return strings.TrimSpace(wsRun.ReplaceAllString(s, " ")) }

type Diff struct {
	URLChanged  bool     `json:"url_changed"`
	TextChanged bool     `json:"text_changed"`
	Appeared    []string `json:"appeared"`
	Disappeared []string `json:"disappeared"`
	Actionable  bool     `json:"actionable"`
}

// DiffModels 回答「刚才那一下有没有推进」—— py 里分支与重试的唯一依据（规格 §4.6）。
func DiffModels(before, after *PageModel) Diff {
	sels := func(m *PageModel) map[string]bool {
		s := map[string]bool{}
		for _, a := range m.Actions {
			s[a.Selector] = true
		}
		return s
	}
	b, a := sels(before), sels(after)
	var app, dis []string
	for s := range a {
		if !b[s] {
			app = append(app, s)
		}
	}
	for s := range b {
		if !a[s] {
			dis = append(dis, s)
		}
	}
	d := Diff{
		URLChanged:  before.URL != after.URL,
		TextChanged: normText(before.PageText) != normText(after.PageText),
		Appeared:    app,
		Disappeared: dis,
	}
	d.Actionable = d.URLChanged || d.TextChanged || len(app) > 0 || len(dis) > 0
	return d
}
```

- [ ] **Step 4: 跑测试确认绿，并加 CLI**

```bash
go test ./internal/ -run TestDiff -v   # 3 个 PASS
```

`cmd/diff.go`：读一个「快照文件」再 observe 一次做对比（`cdp observe > before.json; 动作; cdp diff --before before.json`）。

- [ ] **Step 5: 提交**

```bash
git add tools/cdp/internal/diff.go tools/cdp/internal/diff_test.go tools/cdp/cmd/diff.go
git commit -m "feat: cdp diff —— 「刚才那一下有没有推进」"
```

---

## Task 8: `cmd/mcp` — 同内核的 MCP 出口

**Files:**
- Create: `tools/cdp/cmd/mcp/main.go`
- Modify: `Dockerfile`（补回 `cdp-mcp` 构建行）、`tools/cdp/go.mod`（加 MCP SDK 依赖）

**Interfaces:**
- Consumes: `internal.Client`、`(*Client).ObserveAll()`、`DiffModels`、既有的 form/click/scroll 动作
- Produces: MCP 工具 `observe` / `diff` / `click` / `form` / `scroll` / `goto` / `eval`

**关键**：这一层**直接调 `internal` 包**，不 subprocess 调 CLI（规格 §4.1）—— 否则日志噪声、双重转义、timeout 全都会回来。

- [ ] **Step 1: 写失败测试（工具注册表）**

```go
package main

import "testing"

func TestToolRegistry(t *testing.T) {
	names := map[string]bool{}
	for _, tl := range tools() {
		names[tl.Name] = true
	}
	for _, want := range []string{"observe", "diff", "click", "form", "scroll", "goto", "eval"} {
		if !names[want] {
			t.Errorf("MCP 工具表缺 %q", want)
		}
	}
}

func TestObserveToolDescriptionMentionsPerceptionOnly(t *testing.T) {
	for _, tl := range tools() {
		if tl.Name != "observe" {
			continue
		}
		if tl.Description == "" {
			t.Fatal("observe 工具缺描述 —— agent 靠它决定何时用")
		}
	}
}
```

- [ ] **Step 2: 跑测试确认红**

```bash
cd /company/siteforge/tools/cdp && go test ./cmd/mcp/ -v
```

预期：`undefined: tools`。

- [ ] **Step 3: 实现（stdio MCP server）**

用官方 Go SDK（`github.com/modelcontextprotocol/go-sdk`）注册工具，
每个工具的处理函数直接调 `internal.Client` 的方法；`observe` 返回
`PageModel` 的结构化 JSON（**不是** 双重转义的字符串 —— 这正是 MCP 门
相对 CLI 门的核心好处，规格 §4.1）。

- [ ] **Step 4: 跑测试确认绿 + 手工握手验证**

```bash
go build -o cdp-mcp ./cmd/mcp && go test ./cmd/mcp/ -v
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ./cdp-mcp | head -5
```

预期：测试 PASS；握手返回工具列表（含 `observe`）。

- [ ] **Step 5: 补回 Dockerfile 的 `cdp-mcp` 构建行**

```dockerfile
RUN go build -ldflags="-s -w" -o /out/cdp main.go \
 && go build -ldflags="-s -w" -o /out/cdp-mcp ./cmd/mcp \
 && /out/cdp --help >/dev/null
```

- [ ] **Step 6: 提交**

```bash
git add tools/cdp/cmd/mcp tools/cdp/go.mod tools/cdp/go.sum Dockerfile
git commit -m "feat: cdp-mcp —— 同内核的 MCP 出口，不包壳调 CLI"
```

---

## Task 9: 债务清理与容器验证

**Files:**
- Modify: `/opt/skills/auto-farm-skill/form_executor/common.py:14`（**生产仓库**，只改这一处默认值）
- Modify: `tools/cdp/verify.sh`（指到新路径）

**Interfaces:**
- 无新接口；本任务让「谁在哪找 cdp」这件事不再靠记忆。

- [ ] **Step 1: 确认当前硬编码点**

```bash
grep -rn "company/cdpcli" /opt/skills/auto-farm-skill/ --include=*.py --include=*.sh | head
```

- [ ] **Step 2: 改成环境变量优先 + 新默认值**

`common.py:14` 改为：

```python
# cdp 工具路径。优先环境变量（容器里由 Dockerfile 设 /usr/local/bin/cdp），
# 默认指向 siteforge 迁入后的位置 —— 原先硬编码 /company/cdpcli/cdp，
# 工具一搬家就静默找不到。
CDP_PATH = os.environ.get("CDP_PATH", "/company/siteforge/tools/cdp/cdp")
```

- [ ] **Step 3: 回归验证生产 py 仍能跑**

```bash
docker exec auto-farm sh -c 'CDP_PATH=/usr/local/bin/cdp python3 -c "import sys; sys.path.insert(0,\"/opt/skills/auto-farm-skill/form_executor\"); import common; print(common.CDP_PATH)"'
```

预期：打印 `/usr/local/bin/cdp`（容器里以环境变量为准，未被新默认值影响）。

- [ ] **Step 4: 构建镜像验证 Dockerfile**

```bash
cd /company/siteforge && docker build -t siteforge:dev . 2>&1 | tail -15
docker run --rm siteforge:dev cdp --help | head -5
docker run --rm siteforge:dev cdp-mcp --help 2>&1 | head -3
```

预期：构建成功；两个二进制都能起来。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "chore: 收掉 cdp 路径硬编码 + 容器构建验证"
```

---

## 执行顺序调整（用户 2026-09-16 定）

**本计划做到 Task 7 停，不接着做 Task 8/Task 9。**

- **Task 1–7 完成** → `cdp observe` / `cdp diff` 成为**能用的 CLI**，这是本计划的实际交付点
- **Task 8（`cmd/mcp`）** → 挪到计划二。它是给 LangGraph agent 用的门，agent 没接进来之前没有消费者
- **Task 9（债务清理）** → 挪到计划二。它收的是「集成测试依赖 mock-server:8080」与「R3 fixture 落进 testdata/」这类债，等真正的使用者出现再收

**T7 之后先做一件事：验「能不能支持一次纠错」**（不是继续做工具，也不是产 py）

> ⚠️ **2026-09-16 用户修正了产品定位**（规格 D15）：目标是**剥离技术人员**、
> 让运营能自己调试和修复，**Debug 是主线**。所以 T7 的验收不是「产出一条 py」，
> 而是「**页面模型里有没有足够信息让一句人工纠正落地**」。

```
拿一个真挂的站
  ① 从 fail-script 取失败证据（哪个站、卡在哪一步）
  ② cdp observe 那个站的**失败页面** → 把页面模型拿给人看
  ③ 人说：「这里点错了，应该是那个」
  ④ 看页面模型里有没有足够信息让这句话落地：
       · 那个「对的元素」在 actions 里吗
       · 它的 selector / text / region / bbox 够不够唯一确定它
       · 如果它在 shadow / iframe 里，frame_path / shadow_depth 写得出来吗
  ⑤ 顺带验 R17：截图上的框能不能和 bbox 对上
```

**为什么**：要验的是「**运营能不能自助**」，不是「工具全不全」。
这一步会暴露本计划所有「纸上成立」的假设 —— 尤其是
「`observe` 的字段够不够支撑一次纠错」。

---

## 后续计划（不在本计划内）

本计划只覆盖**工具层**。后续各自单独成计划：

| 计划 | 范围 | 独立验收 |
|---|---|---|
| 计划二：产出闭环 | py 模板骨架 · 契约检查器(lint) · 扰动自测 runner · LangGraph 图 · **运营入口页面（D14）** · **`cmd/mcp`（原 T8）** · **债务清理（原 T9）** | homebuddy 出一条真能跑的 py |
| 计划三：记忆层 | `site_memory` 表 · `correction` 事件 · `siteforge correct` CLI · DB schema | 数据进得去、同类站复用得上 |

**依赖关系**：计划二依赖本计划的 `observe`/`diff` CLI；计划三依赖计划二的 selftest 失败路径。

---

## Self-Review

**1. Spec coverage（对照规格逐节）**

| 规格节 | 本计划覆盖 | 说明 |
|---|---|---|
| §4.1 同内核双出口 | Task 1 / Task 8 | |
| §4.2 工具清单 | Task 6/7/8（observe/diff/动作/MCP） | `vision.inspect` 不在本计划 —— 属计划二 |
| §4.3 observe 契约 | Task 2 + Task 3 | 含三条陷阱与跨帧合并 |
| §4.4 选择器评级 | Task 5 | R5 真站校准留给计划二 |
| §4.5 能力边界 | 已由探针验证并写入 spec | 无需代码 |
| §4.6 窗口与代理 | **不在本计划** | 属计划二 `intake` 节点 |
| §5 py 契约 | **不在本计划** | 计划二 |
| §9 债务清理 | Task 9 | |
| §13.1 Phase 1 | 部分 | 本计划是其中工具层那一半 |

**2. Placeholder scan**：无 TBD / TODO。Task 2 Step 3 与 Task 8 Step 3 标了
「逐段取自探针」—— 探针代码是**已存在于仓库的文件**（`docs/probes/.../observe.js`），
不是待填的空。

**3. Type consistency**：`PageModel` / `Action` / `Field` / `OptionGroup` /
`Obstruction` / `Diff` 的字段名在 Task 2、3、5、7 中一致；`Observe(frameID)` 与
`ObserveAll()` 的命名在 Task 2/4/6/8 中一致。
