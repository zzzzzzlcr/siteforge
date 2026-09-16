# SDD ledger — plan: docs/superpowers/plans/2026-09-16-tool-layer-observe.md

Spec: docs/superpowers/specs/2026-09-16-siteforge-design.md (读作权威)

## Setup

Ruling: 在 /company/siteforge 的 master 上原地实施，不建 worktree — 本会话被
  配置为原地工作（harness 明确要求 skip worktree），且该仓库是新建的本地仓库、
  无 remote、目前只有文档。代价若错：若用户其实想要分支隔离，需要 rebase。
Ruling: 宿主 go 在 /usr/local/go/bin 不在 PATH — 每次 dispatch 注入
  export PATH=/usr/local/go/bin:$PATH。代价若错：无（纯环境操作）。

## 预检冲突扫描

### 任务对（共享文件或接口）

| 任务对 | 一方产出 → 另一方消费 | 发现 |
|---|---|---|
| T1 → T2 | withPierce / EvalInFrame → observe.go 用 | ✅ 匹配 |
| T2 → T3 | Observe() → 集成测试用 | ✅ 匹配 |
| T2 → T4 | Observe() → ObserveAll 用 | ✅ 匹配 |
| T3 → T4 | **T3 的测试引用 ObserveAll（T4 才有）** | ❌ **C1** |
| T4 → T6 | ObserveAll() → cmd/observe.go 用 | ✅ 匹配 |
| T2 → T7 | PageModel/Action → diff.go 用 | ✅ 匹配 |
| T1,T4,T7 → T8 | internal + ObserveAll + DiffModels | ✅ 匹配 |
| T1 → T9 | verify.sh | ✅ 匹配 |
| T5 → T2 | T5 原地修改 T2 的 observe.go | ✅ 顺序依赖，非冲突 |

### 各任务自洽性

| 任务 | 自洽？ | 发现 |
|---|---|---|
| T1 | ✅ | Step 6 已明说 Dockerfile 过渡态（C7） |
| T2 | ✅ | 陷阱测试是字符串断言，偏弱 —— 真验收在 T3 集成测试，接受 |
| T3 | ❌ | **C1** + **C4** |
| T4 | ⚠️ | fixture 端口问题 **C3** |
| T5 | ✅ | |
| T6 | ✅ | |
| T7 | ❌ | **C2** |
| T8 | ✅ | 依赖网络 **C8** |
| T9 | ❌ | **C5** |

### 裁定

Ruling C1: Task 3 的 TestObserveCrossOriginFrameMerge 移到 Task 4。
  计划让 T3 「用 t.Skip 标住」跨源那条 —— **但 Skip 救不了编译错误**：
  引用未定义的 c.ObserveAll() 会让整个 internal 包编不过，T3 想跑的三条
  单帧测试也一起跑不了。跨源测试本来就是 T4 交付物的验收，归 T4 更合理。
  代价若错：T3 的测试文件少一条，T4 补上即可。

Ruling C2: Task 7 的 cmd/diff.go 按 Task 6 同形补全（flags 测试 + 完整代码），
  代码随 dispatch 下发。计划里那一步只写了「加 CLI」没有代码没有测试，
  违反计划的 No Placeholders 自查。代价若错：多写十几行胶水代码。

Ruling C3: Task 4 的跨源测试**动态生成** outer 页（端口取自 httptest 的 srv.URL）。
  计划里的 fixtures/outer.html 硬编码 src="http://localhost:8892/inner.html"，
  而 httptest.NewServer 用随机端口 → iframe 必然加载不到，测试会以
  「没有子帧」的形式假失败（而不是报错），最难查。代价若错：测试写法稍繁。

Ruling C5: Task 9 只做 Step 1/4/5（查硬编码点、容器构建验证）。
  **Step 2/3 改生产仓库 common.py 被 parked** —— 那是本工作区之外的副作用，
  且改的是正在跑生产订单的那份代码，属四个停止条件之一。
  等用户明确批准再动。代价若错：债务清理晚一轮，无功能影响。

Ruling C7: Task 1..7 期间 Dockerfile 不含 cdp-mcp 构建行（计划已明说）——
  接受过渡态，T8 补回。代价若错：无，Dockerfile 在此期间本就不该能构建全。

Ruling C8: Task 8 若 proxy.golang.org 拉不到 MCP SDK，换 goproxy.cn。
  代价若错：无。

## 进度


## 任务执行

Task 1: dispatched (BASE 3bd4329, model sonnet) — 迁入 cdpcli + 验证跨源帧

### 派发期间的补充核查（HEAD 依赖验证）

Ruling C9: Task 4 的计划文本写错了帧树类型。计划里用的是自造的 `FrameNode`
  （`f.ID` / `f.Children`），而 HEAD 里 `GetFrameTree()` 返回的是 cdproto 的
  `*page.FrameTree`（字段是 `.Frame`（`*page.Frame`，含 `.ID`/`.URL`）与
  `.ChildFrames []*page.FrameTree`）。**不要新建 FrameNode 类型** —— 直接遍历
  cdproto 的结构，少一层无谓的转换。Task 4 派发时带上这条。
  代价若错：多写一个冗余类型定义。

已核实的依赖（HEAD 均在）：
  internal/client.go:116  func (c *Client) GetFrameTree() (*page.FrameTree, error)
  internal/client.go:132  func (c *Client) GetFrameTreeWithEvents(wait time.Duration)
  internal/shadow_integration_test.go:22  func shadowTestEndpoint() (string, int)
  internal/shadow.go:55   func withPierce(js string) string
  internal/shadow.go:48   var __cdpQA

Ruling C8 (settled): proxy.golang.org **不可达**（实测 dial tcp i/o timeout）。
  goproxy.cn 可用 —— MCP SDK 已实测拉取并构建通过。
  **每个 Go dispatch 都要带**：export PATH=/usr/local/go/bin:$PATH
  以及 GOPROXY=https://goproxy.cn,direct（拉新依赖时必需）。
  cdpcli 既有依赖已在 /home/dev/go/pkg/mod 缓存里，Task 1 无需网络。
  Dockerfile 里我原本就写了 ENV GOPROXY=https://goproxy.cn,direct —— 已对。
  代价若错：无。

Ruling C10 (Task 2 的换装口径): 探针 docs/probes/2026-09-16-observe-r3/observe.js
  自带一套自实现的穿透（顶部 `var RS=[]` 递归 walk + `function qsa(sel)`）。
  迁进 internal/observe.go 时**两处都要换成内核助手**（pierceJS 暴露的正是
  这两个，一一对应，不是重写）：
      qsa(sel)  →  __cdpQA(sel)                 （选择器查询，全 root 合并）
      RS        →  __cdpRoots(document)          （root 列表，page_text 逐 root 收集要用）
  **不要再自己 walk 一遍 shadow root** —— 那正是 shadow.go 注释警告的
  「不必各自重写一套」，也是 Global Constraints 里那条。
  陷阱测试只断言了 `__cdpQA` 出现，抓不到重复的 roots walk，所以这条靠 dispatch 交代。
  代价若错：多一份重复遍历，行为不变但留了坏样板。

  另：`composedAncestors`（三条陷阱里的合成树祖先链）探针里是自带的，
  内核没有 —— 这个**保留自实现**，它不属于「穿透查询」那一类。

Ruling C11 (Task 2 的调试姿势): HEAD 里 `EvalInFrame`（internal/client.go:262）
  **不注入穿透** —— 它直接 `runtime.Evaluate(js)`，原样下发。withPierce 只出现在
  client.go 的 485/527/549 三处动作助手（GetElementCenter / IsElementVisible /
  ScrollIntoView）和 form.go 的 8 个选择器入口。

  两个后果，都要交代给 Task 2 的实现者：
  1. `observeJS()` **必须自己套 withPierce**（计划的骨架里已经这么写了 ✓）。
     重复注入是幂等的（shadow.go 注释：「重复求值只是重新定义一次」），无害。
  2. **不能用 `cdp eval` 调试 observe 的 JS** —— 那条路没有注入，`__cdpQA`
     会是 `ReferenceError: __cdpQA is not defined`。要调试只能走
     `internal.Client.Observe()`（或写个临时 Go 测试）。
     不知道这条会浪费很多时间去怀疑「助手名写错了」。

  这条也正是根因 R2（cdp eval 不注入）在 Go 层的同一个形状：
  注入了的只有那几个动作助手，通用求值入口一直没有。
  代价若错：无（纯交代）。

Task 1: BLOCKED（commit 无；工作区保留 `?? tools/`）
  受控差分（我复核过）:
    基线 HEAD+WIP  evalInOOPIFFrame=2  __cdpQ=21  md5 1df52111298f  → 跨源子帧 ✅
    新建 HEAD-only evalInOOPIFFrame=0  __cdpQ=21  md5 b35c382f033c  → 跨源子帧 ❌
                                    报 failed to create isolated world ... No frame for given id found
    边界精确：主帧✅ 同源 iframe✅ 跨源 OOPIF❌；go test ./... 全绿（无测试覆盖此缺口）
  → C-预判被实测证实：丢的正是 WIP 的 evalInOOPIFFrame，shadow 能力（eddee5f）在 git 里没丢

  附带发现（待裁）:
  - WIP 有 4 个测试文件未进 cdpcli git（batch/click/client_integration_test.go、client_test.go）+ .mcp.json
  - gobwas/ws 标 // indirect，而 WIP 的 cdpSend 直接用 —— WIP 未跑 go mod tidy
  - git archive 带进 tools/cdp/.claude/settings.json（只含 enabledPlugins，cdpcli 的开发环境配置）

Ruling C12: tools/cdp/.claude/settings.json 保留但从 cdpcli 的语境里剥出来 —— 内容只是
  开了 gopls-lsp 等 4 个插件，对 Go 工作有用；但它属于 cdpcli 的开发配置，
  siteforge 应当**自己决定**要不要。实施时在 siteforge 根放一份自己的 .claude/，
  而不是让它随代码树漂进来。代价若错：无。
Ruling C13: gobwas/ws 的 // indirect 在迁移落地后要跑 go mod tidy 修正。
  代价若错：无。

★ 停下等用户定 R7（这是四个停止条件之一：工作区之外的副作用）★

Ruling R7 (用户裁决，方案 B): 先把 WIP 落到 cdpcli 的一个分支再迁。
  执行：/company/cdpcli 新建分支 `wip/oopif-iframe-ax`，commit `780fa90`。
  提交内容 = 7 个已跟踪改动文件 + 4 个此前游离在 git 外的测试文件。
  **未提交 .mcp.json** —— 那是开发者本地的 chrome-devtools-mcp 客户端配置，
  进仓库会把「用哪个 MCP server」强加给所有人。
  master 未动（仍 eddee5f）、未推送（用户定的规矩）。
  commit message 里如实写了「这是在制品、非完成态」+ 已知未完成项
  （gobwas/ws 未 tidy、未跑全量测试）—— 不粉饰。
  代价若错：那份 WIP 的作者若本不想让它进 git，需要 revert 这个分支（本地，无成本）。

Task 1: re-dispatched（resume 同一实现者）—— 来源换成 wip/oopif-iframe-ax@780fa90，
  并带上 C12（删 tools/cdp/.claude/settings.json）与 C13（go mod tidy）。

## 用户提问：新系统产出的 py 现有系统能直接用吗（2026-09-16 逐字核对）

结论：**能**。六项契约与生产脚本逐字一致 —— sys.path 插入方式 / from common import /
四个 CLI 参数 / sys.exit(0 if f.run() else 1) / tid 推导(task_id or correlation_id.split('_')[0]) /
report_url 调用形式与签名。成功判据 ad-task.py:1533 只看 returncode==0，超时 600s。

Ruling C14: 计划 Task 9 指向了错的文件。站点 py 脚本 import 的是 **forms/common.py**，
  不是 form_executor/common.py：
    forms/common.py:14          CDP_PATH = "/opt/skills/auto-farm-skill/cdp"（硬编码、无 env）
    form_executor/common.py:14  CDP_PATH = os.environ.get("CDP_PATH", "/company/cdpcli/cdp")
  **不阻塞产物运行** —— 产物跑在容器里，而该硬编码路径在容器里存在（md5 1df52111298f）。
  但 Task 9 若要「收掉 cdp 路径硬编码」，真正的目标文件是 forms/common.py。
  代价若错：Task 9 改了个对产物无关的文件，硬编码依旧。

Ruling C15: **计划二必须补一步「加路由表」** —— ad-task.py:260+ 那张
  `(["域名"], "forms/sites/<name>.py", "<slug>")` 表，不加的话产物永远调不到。
  siteforge 规格与计划一/二都没覆盖这一步。代价若错：产出的 py 在容器里躺着没人调。

Ruling C16: 上线仍是人工（docker cp 到 /opt/skills/auto-farm-skill/forms/sites/）——
  与规格 §11「不做自动上线」一致，非缺口，但要在计划二的验收清单里写明。

Ruling C16 (修订 — 用户纠正): 上线**不是**长期人工 docker cp。目标是闭环
  「产物 → 后台配置 → 系统读取」，用户明确说「这是后面要做的闭环」。
  我原来记窄了。

  现状核实（2026-09-16）：
  - py 站点脚本**没有任何后台下发通道** —— ad-task.py:2597
    `site_script = _match_site_route(current_url, log)` 是**本地 63 行路由表**
  - 只有 **JSON config** 有通道：ad-task.py:1337
    `http://192.168.1.51:6060/api/quest/formConfig?site=<key>`
  - `fmr.3tkj.cn` 的路由全是任务与上报（task_apply / formMessage / formLog /
    screenshot / task/switch / siteLimit），**不下发脚本**
  - ⚠️ 已知分叉（记忆 2026-09-12 标记为硬阻塞，本次未复验）：
    **ad-task 读本地 6060，运营后台写 fmr.3tkj.cn** —— 两边不通

  对 siteforge 的含义（影响计划二，不是现在做）：
  1. 产物需要一个**注册/身份**概念，不只是一个文件路径 —— 域名 → 脚本标识
     → 版本，否则后台无从配、worker 无从读
  2. 「注册」要和文件一起产出，而不是事后再补一步（C15 的路由表就是这个缺口的
     最小形态：现在是本地表，将来是后台一条记录）
  3. JSON config 那条路是这个闭环的**现成先例，也是现成的坑**（6060/fmr 分叉）
     —— siteforge 设计注册机制时应当先解掉那个分叉，否则新通道会重复它
  4. 与规格 §11「不做自动上线」的关系：§11 说的是**第一版**不做，长期目标是闭环。
     计划二的验收清单应写明：第一版人工，注册信息按将来能迁到后台的形态产出。
  代价若错：计划二把「注册」当补丁而非一等公民，将来接后台要返工。

Task 1: DONE_WITH_CONCERNS → commit e09e298（67 文件）
  ✅ Step 4 关键验收通过：新二进制跨源子帧 eval → "http://localhost:8892/inner.html" EXIT=0
  ✅ R3 四档全过（含跨源子帧套两层 shadow：6 动作/3 字段/1 选项组）
  ✅ C7/C12/C13 落实；go mod tidy 后 gobwas/ws 转直接依赖、go.sum 未变
  ⚠️ go test ./... 非绿：TestSnapshotBDD 一条 spec

Ruling C17: **现在就修那条红 spec（Task 1b），不记为欠债。**
  理由：siteforge 立项的理由就是消灭脏基线（规格 §1.2）。第一天带红测试进去，
  后面每个任务的「全绿」信号都被污染，终审还要额外 triage。诊断已完成
  （该 spec 用 NewClient 取活动页、自己不导航 → 整包跑时被别的测试留下的页面
  污染），修法明确：让它自带导航到已知 fixture 页，自成隔离。
  实现者的排除工作已足够支撑这个判断（单独跑过/整包失败、与来源分支逐字节同、
  真实页面上 CLI 实跑嵌套帧正确）。
  代价若错：小绕路；但它换来的是后续 8 个任务可信的「全绿」。

Ruling C18: 实现者指出我的指令第 3 步期望值写错了（`grep -ac evalInOOPIFFrame`
  应为 1，实测 3 —— 调用点+注释+定义各一行）。**它没改代码去迁就错误期望，是对的。**
  检查意图（OOPIF 能力确实迁进来了）已满足。记此以正视听。
  代价若错：无。

Ruling C19: Task 8 要恢复的 Dockerfile 位置是 **4 处**，不是 2 处 ——
  stage 1 的 cdp-mcp 构建行、stage 2 的 `COPY --from=tools /out/cdp-mcp`、
  以及 `chmod +x /usr/local/bin/cdp-mcp` 与相关注释。
  实现者指出「COPY 源缺失会让 build 无条件失败」，故 stage 2 也必须连带注释 ——
  这是对 C7 的正确扩展。Task 8 派发时必须带上这 4 处的完整清单（在报告里）。
  代价若错：Task 8 漏恢复一处，镜像构建失败（会被立刻发现，非静默）。

Task 1b: NEEDS_CONTEXT（工作树零改动，等授权）
  ★ 实现者**推翻了上一轮自己的诊断**：不是「测试隔离缺陷」，是**代码 bug**。
    真根因：internal/client.go:322-328 `collectFramesFromDOM` 构造帧只填 ID/URL，
    **没填 Name**（旁边同一个 nodeGetAttr 正在取 src）。inner 帧若在开头
    GetFrameTree() 之后才建好（机器繁忙）→ 走 DOM 并入路径 → Name 空。
    单独跑绿/整包跑红 = **竞态**，与隔离无关。
  影子验证（/tmp，仓库零改动）：只加那一行 + **原始测试一行不改** → `ok 4.228s`。
  计数：当前仓库 PASS 52 / SKIP 0 / FAIL 1 —— SKIP 确实为 0，**不是假绿**。
  它还把上一轮基于错误诊断加的测试编辑**回滚了**。

Ruling C21: **授权修那一行。** 三条理由：① 影子验证证明测试一直是对的
  （只改代码、测试不动就绿）；② `Name` 空会让任何「按名字认帧」的调用方失效，
  Task 2 `observe` / Task 4 `ObserveAll` 正好踩；③ 它是偶发竞态，不修就留个
  随机红的套件。代价若错：改了采纳代码的一行，但已被影子验证覆盖。
Ruling C22: **不在 cdpcli 里修同一个 bug**（不扩大范围），但要**记一笔待回灌上游**
  —— 该 bug 在 `wip/oopif-iframe-ax` 分支上同样存在。实现者已在报告里写了。
  代价若错：上游带着一个已知 bug 长期存在（已记录，不会丢）。
Ruling C23: Task 1b 的验收是 **53/0/0**（PASS/SKIP/FAIL），**SKIP 必须为 0** ——
  cdpcli 集成测试在无浏览器时走 t.Skipf，跳过和通过长得一样。
  这条是我给的验收标准，不是实现者提的。

Task 1b: DONE → commit 3e6ded5（一行：Name: nodeGetAttr(node.Attributes,"name")）
  验收一 TestSnapshotBDD → Ran 4 of 4 — SUCCESS!  ok 4.203s
  验收二 go test ./... → PASS 53 / SKIP 0 / FAIL 0（与影子预算逐字相符：修前 52/0/1）
  额外：BDD 套件连跑 5 次全绿（4.198–4.214s）→ 确认竞态修掉，不再「有时候绿」
  它刻意留下一个 tab 不关 —— 关掉会让页面目标数为 0，集成测试走 Skip → 假绿。
  这个判断是对的，记一笔。
  它发现我的 1c5f8e9 docs 提交出现在历史里（并行提交），确认非它所为、e09e298 仍是祖先 ✓

用户问：「失败的脚本也让这边来修就行？」—— 答：是，且前置比规格写的成熟。
  实测：GET https://fmr.3tkj.cn/api/quest/formStep|formLog → **401（接口活着、要鉴权）不是 404**
  取证链已就绪：ad-task.py:631/952 一直在上报；fail-script/src/diagnosis/datasource.py
  已有带 X-Api-Token 的客户端；farmer-handoff.md 记 ① formLog 列表接口**已建**，
  farmer 侧还做了「失败>10次/日→停用+钉钉告警」。
Ruling C24: 改规格 §11 —— 原写「不做 farmer 的 failures 接口本身（是前置依赖，另立项）」
  **是过时结论**（接口 2026-09-10 已建）。「修」入口**不要重复建取证层**，接 fail-script 现成的。
  代价若错：若那接口实际不可用（401 需 token，token 未验），修入口要补鉴权 —— 届时再议。
Ruling C25: 「修」的 `explore` 应以**旧 py 的行走路线为起点**（拿旧路线在当前页面跑一遍，
  看哪一步开始对不上），不是白纸探索。这是新站没有的优势。已写进 §6.1。
  代价若错：无。

用户问：「后续能不能也把 json 修复的加进去，麻烦话就后续再完善」
Ruling C26: 加，放 Phase 2（第三个入口）。核实：产线 py 63 + **json-configs 69**，
  而 JSON 修复分三层，**第一层已存在** —— form_executor/auto_fixer.py 约 10 条
  确定性规则（_fix_field_types/_fix_field_placeholder/_fix_button_eval/_remove_form_id/
  _fix_success/_fix_loop_until/_fix_missing_wait…），0 LLM。
  siteforge 要补的是**第 2 层（选择器失效）**，而它**不需要 agent 写代码** ——
  只要 observe/diff 找新选择器 + 改 JSON 一个字段 + 跑一遍验证。
  比产 py 便宜：产物是数据（schema 校验即可，不用 lint）、改动局部（人审容易）、
  旧 JSON 在手、auto_fixer 已做掉机械那半。
  升级判据复用 §1.1 已有边界：需要 JSON 表达不了的原语（分支/跨轮状态/换策略）→ 升 py。
  代价若错：Phase 2 的两个入口共用 observe/diff，若 JSON 那路实际需要额外能力，
  在 Phase 2 开工时再评估。已写进 §6.1 + §13.1。

Ruling C27（含一处**我自己的更正**）：JSON 修复在 siteforge 侧，但要与 auto_fixer 划清关系。
  更正：我上一条说 auto_fixer 是「执行期」—— **错了**。核实 form_executor/json_pipeline.py:286：
  它在 `json.loads(content)`（LLM 刚生成完）之后、Step 2 浏览器验证之前调用，
  是**产出期的生成物清洗器**，不是运行时修复器。
  故两者是**串行**：产出 JSON → auto_fixer（纯规则）→ siteforge 补的那层 → 验证 → 人审 → 上线。
  siteforge **不要重写 auto_fixer 的规则**，只补「规则判不了、必须看着页面才知道」的那层。
  另一条闭环：人审通过的修法反复出现后**沉淀成 auto_fixer 的新规则** —— 确定性规则是
  人工纠正的终点（不要钱、不会自信地错，呼应 R0/§6.5）。已写进 §6.1。
  代价若错：无。

## Task 1 审查（reviewer: sonnet，审 3bd4329..3e6ded5）

结论：**Needs fixes**。Spec ❌ 两条（Important）+ 5 Minor + 3 ⚠️。
审查方独立重建二进制指纹（__cdpQ=21 / OOPIF eval=5，与部署基线一致）确认 OOPIF 能力真在树里。

⚠️ 三条我（控制方）自己解掉了：
  1. `diff -r` 与 wip/oopif-iframe-ax 导出 → **只差 go.mod（C13）与 client.go（Name 行）**，
     其余逐字节一致 → 迁移忠实 ✓
  2. `git show --stat 3e6ded5` → 只动 internal/client.go（3+/2-）✓
  3. **我自己重跑 Step 4 验收**：跨源子帧 eval location.href → "http://localhost:8892/inner.html" ✓
     （不只采信报告）

Ruling C28（**我自己的记账错误，更正**）：我先前写「验收二 go test ./... → PASS 53 / SKIP 0 / FAIL 0」
  并据此判定「SKIP 为 0，不是假绿」。**那个判断只对了一半。**
  审查方抓到：internal/close_integration_test.go 与 targets_integration_test.go 都以
  `//go:build integration` 开头 → `go test ./...` **根本不编译它们**（排除 4 条：
  TestCloseTarget / TestCloseNonExistentTarget / TestListPageTargets / TestListPageTargetsWithActive）。
  算术是铁的：57 个顶层 Test（cmd 13 + internal 44）− 4 = **53**，正好等于报告的 PASS 数。
  **教训：build tag 排除 ≠ skip —— `go test` 的 SKIP 计数看不见不编译的测试。**
  代价若错：后续任务继续把「53 绿」当成全量覆盖的信号。
Ruling C29: 修复轮 1 三项 —— ① 更正报告 §R8.5 的不实陈述 + 跑 `-tags integration` 量出真实覆盖；
  ② 补注释 Dockerfile:66-67 与 :76（C7 的另两处，恢复清单从 4 处变 **6 处**）；
  ③ gofmt 修 `client.go:325-328` 的缩进回归（`3e6ded5` 引入，非继承）。
  代价若错：无（三项都是小改）。

Minor（记此备终审 triage，不进修复轮）：
  m2 测试套件与 localhost:8080 dev server 耦合 —— 10 条测试丢弃 Navigate 错误，
     在没那个 server 的机器上 FAIL 而非 skip；SKIP:0 还依赖 Chrome 至少有一个开着的页面
  m3 Brief Step 4 的 tools/cdp/internal/testdata/ 最终没落进仓库（fixture 仍在 docs/probes/ 下）
  m4 tools/cdp/docs/ 带进 28 个文件（~6.5k 行），3 个含原作者机器路径（/home/ansible/…）
  m5 tools/cdp/.dockerignore 惰性（构建上下文是仓库根）

Ruling C30（控制方主动核查，非审查方提出）：cdpcli 集成测试的外部依赖已查清。
  `internal/` 里 10 条测试导航到 `http://localhost:8080/{carwarranty,mui-datepicker,react-select}`，
  服务方是 **/company/mock-server（另一个仓库）**，cdpcli 自己没有 fixture server。
  这些测试**丢弃 Navigate 的错误**，所以在没跑 mock-server 的机器上会 **FAIL 而非 skip**
  → **`go test ./...` 的「全绿」不可从 git 复现** —— 对一个以「消灭脏基线」立项的项目，
  这是必须显式化的依赖，不能靠"这台机器上恰好有"。

  处置（不扩大 Task 1 范围）：
  - **Task 3/4 的新测试必须自带 fixture** —— 计划里 Task 3 已用
    `httptest.NewServer(http.FileServer(http.Dir("testdata")))` ✓ 干净，保持。
    Task 3 派发时要写明：**不得新增对 localhost:8080 的依赖**。
  - **Task 9（债务清理）加两项**：
    ① 把「集成测试需要 /company/mock-server 跑在 :8080」写进 `tools/cdp/README.md`
       （或让它 skip 而非 FAIL —— 二选一，派发时定）
    ② 把 R3 的四档 fixture 从 `docs/probes/…/fixtures/` 落进 `tools/cdp/internal/testdata/`
       （reviewer Minor #3；Task 4 的跨源回归测试要用它）
  代价若错：Task 9 多两项小活；不做的代价是每个任务的「全绿」都只在本机成立。

Task 1 修复轮 1: DONE → commit 9247aea（2 files, 7+/4−）
  ① §R8.5 更正 + 补跑 -tags integration：1 个 page target → 43/1/0（唯一 skip 是
     TestCloseTarget 自己声明的前置「需 ≥2 page target」）；开第二个 tab 后 → **44/0/0**，
     4 条全真跑全过
  ② Dockerfile 两处已加 # Task 8: 标记；§R4 清单定点化：**4 个引用点 / 6 处要动**，
     其中只有 2 处是真恢复代码，另 4 处是删标记
  ③ gofmt 已修，`gofmt -d internal/client.go` 现为空
  最终树上重跑 Step 4：跨源子帧 → "http://localhost:8892/inner.html" EXIT=0 ✓

Ruling C31（**实现者第二次纠正我**）：我说「gofmt -l 那 6 个文件都是缺尾换行」—— **错**。
  实测 cmd/snapshot.go、internal/form.go、internal/form_test.go、internal/snapshot_bdd_test.go
  **有**尾换行（后者 6 个 hunk 是 import 顺序/单行 if 拆行），只有 cmd/eval.go、cmd/root.go 真缺。
  所以我引的「snapshot_bdd_test.go 仍缺尾换行 → 反证测试没动」**不成立**；
  测试确实一行没动，是靠**逐字节同一性**证明的。结论对、证据错，两条分开记。
  代价若错：无（已更正）。

Ruling C32（控制方主动核查，为 Task 2 去风险）：Task 2 的 `Observe()` 取值路径已验通。
  `EvalInFrame`（internal/client.go:440）末尾 `json.Unmarshal(remoteObj.Value, result)` ——
  JS 返回字符串时 `remoteObj.Value` 是**带引号的 JSON 编码串**，解进 Go `string` 会自动去引号，
  所以 `var raw string; EvalInFrame(...); json.Unmarshal([]byte(raw), &m)` **成立** ✓
  顺带解释了今天反复见到的双重转义：`cmd/eval.go` 解进 `json.RawMessage`（原样字节、保留引号），
  所以 CLI 输出是带引号的字符串，要解两次。
  另看清跨源帧机制：chromedp 路径先试，**任何错误**都回退 `evalInOOPIFFrame`（裸 websocket）。
  Task 2 派发时带上这条，省得实现者怀疑取值方式。
  代价若错：无。

Ruling C33（控制方自查计划弱点，提前定调）：计划 Task 2 的三条「陷阱测试」是**字符串断言**
  （`observeJS()` 里必须/不得出现某些子串），因此**可被一段注释糊过去** ——
  写个 stub 加一行 `// 我们用 __cdpQA，不自己 walk，用 .children 收文本，composedAncestors 处理祖先链`
  就能让四条全绿。

  裁定：**接受这个弱点，不加强 Task 2 的测试**。理由：
  - Task 2 是纯单元层，没有浏览器；行为验证在 Task 3（真浏览器四档）
  - **Task 3 的三条正是按陷阱写的**：TestObserveShadowPageTextIsNotEmpty /
    TestObserveShadowElementsNotFalselyOccluded / TestObserveShadowRegionNotAllBody ——
    那才是真闸门
  - 若 Task 2 的字符串测试绿而 Task 3 的行为测试红，说明陷阱没真实现 ——
    **这个组合本身就是诊断信号**，派 Task 3 时要写明这一点
  代价若错：Task 2 通过但实际没做到，被 Task 3 拦下（可接受，不会漏到下游）。

Task 1 修复轮 1 复审: **all findings addressed，新增破坏 None**
  ① 报告已更正（W1 节）：明确撤回不实陈述、写出算术（57−4=53）、两次 -tags integration 结果
     一致（43/1/0 → 44/0/0）、且**正确区分**「显式声明的前置 skip」与「build tag 排除」两种失败模式
  ② Dockerfile 两处标记已加，风格与既有一致；无遗漏的活 cdp-mcp 引用
  ③ gofmt 纯缩进（4+/4−）、无逻辑变化、Name 行保留

Task 1: complete (commits 3bd4329..9247aea, review clean)
  代价若错（Task 1 相关裁定的总账）：C1 若判断错、C5 若该改而没改、C17 若不该修而修了 ——
  均已在各条 ruling 里记明；终审会看到全部 34 条。

Task 2: DONE_WITH_CONCERNS → commit 63aa818（2 文件 388 行）
  6 PASS / 0 FAIL / 0 SKIP；RED 是预期的编译失败（undefined: observeJS/PageModel）✓
  ★ 实现者做了**变异测试**（超出要求）：4 次变异中，陷阱①两种错写法都被抓住；
    但把「遮挡判定退回命中栈」「region 退回 parentElement-only」后**测试仍全绿** ——
    因为 composedAncestors/getRootNode/.host 因别处使用仍留在脚本里，字符串断言就满足了。

Ruling C34（**修订 C33**）：C33 说「接受陷阱测试可被糊过去这个弱点」—— 那是在**没有证据**时说的。
  现在有变异实证：**四条里有两条是空转的**。修订为：**收口这两条**。
  实现者给了最小改法：把断言从「整个脚本含 composedAncestors」改为
  **「`occludedBy` 函数体内含它」**（同理 region 那条断在 region 函数体内）。
  这把它从「可能空转」变成「真的在测」。
  代价若错：测试措辞更脆（依赖函数名），但换来的是它真能失败。
  这正合 R0 的缓解方向：**让错误有办法被发现** —— 变异测试就是最好的自查工具。

Ruling C35: 修复轮同时把 **R3 四档 fixture 搬进 `tools/cdp/internal/testdata/`**。
  理由：Task 3 的 brief 写着「Task 1 Step 4 已搬入」，**那是错的** —— fixture 只在
  `docs/probes/2026-09-16-observe-r3/fixtures/`，`git log --all` 里没有。
  不搬的话 Task 3 **会 FAIL 而不是 skip**（它的 httptest 以 testdata 为根）。
  一条 `cp` + 提交即可。这也兑现了 C30 与 reviewer Minor#3。

已知限制（记此备 skill 能力边界与终审，不进本轮修复）：
  L1 容器内查询不穿嵌套 shadow：选项组内的 querySelectorAll、`closest('label')`、
     遮挡物内找按钮 —— 都是「少收」不是静默出错。R3 探针在嵌套 shadow 页上仍拿到
     option_groups=1，说明常见情况够用；更深嵌套会漏。
  L2 `contrastBand` 的背景回退走 `parentElement`，shadow 里常得空值 —— 契约里这个字段会常为空。
  L3 `actions[].visible` 恒为 true（只从可见集筛出），信息量为零 —— 要么去掉，要么在
     skill 能力边界里写明「observe 只返回可见元素」。

Ruling C36（用户 2026-09-16 定）：**运营入口归 siteforge 自己**（D14），不长在 /sites 也不长在 farmer。
  理由：/sites 在 auto-llm-script 容器里，而 §8.2 已标它「后续单独决定是否退役」——
  往待退役的东西上加功能 = 往错的基线上改；farmer 是正式后台，改动面大。
  siteforge 骨架已预留：Dockerfile 装 fastapi/uvicorn，entrypoint.sh 起 uvicorn agent.service:app。
  **取数层不重写** —— /sites 的 triage 本就是 import fail-script 的
  （diagnosis.datasource.HttpSource + diagnosis.triage），siteforge 照样 import。
  §6.1 与 §13.1 已同步：Phase 1 = 最小页面 + 两个 CLI 作为底层（不再是「不做 UI」）。
  代价若错：Phase 1 多一个页面的工作量；但换来运营真能用，且不碰待退役的容器。

Ruling C37（控制方建议，待用户定）：计划一 T3–T7 完成后 **`observe`/`diff` 即可用的 CLI**，
  T8（MCP）要等 agent 接入才需要、T9 是收债。建议 **T7 后停下验最短路径**
  （拿一个真挂的站手工串一遍 observe→产 py→跑），而不是把 T8/T9 做完再说。
  理由：用户验的是「运营能不能用」，不是「工具全不全」。
  未获批准前按原计划继续。

Ruling C37（**用户已批准**）：计划一做到 **T7 停**，T8/T9 挪到计划二。
  T1–T7 完成 → observe/diff 是可用的 CLI（本计划的实际交付点）。
  T8(MCP) 挪计划二（agent 没接进来之前没有消费者）；
  T9(债务清理) 挪计划二（收的是测试依赖 mock-server、fixture 落 testdata 这类债，等真使用者出现）。
  T7 之后先**验最短路径**：拿一个真挂的站 → ①fail-script 取证据 ②cdp observe 看模型对不对
  ③据 observe 产 py ④跑 ⑤给人看（模型 + py + 结果）。
  目的：验「运营能不能用」，不是「工具全不全」；并暴露本计划所有纸上成立的假设。
  已写进计划文档的「执行顺序调整」节。
  代价若错：T8/T9 延后，agent 接入前无法用 MCP（但那时也不需要）。

Task 2 修复轮 1: DONE → commit 4046623（7 files；实现 observe.go 一字未动，md5 仍 2b7a13…）
  ★ 验收达成：收口后错误写法**会变红**。变异对照（收口前 2 抓住/6 漏 → 收口后 6 抓住/2 漏）：
    M2 occludedBy 退回命中栈 → 抓住(:114) | M3 region 退回 parentElement-only → 抓住(:129)
    M4a 助手正文换 return [el] → 抓住(:126) | M6 注释糊法 → 抓住(:114)
    M1/M1b 陷阱①两种写法 → 一直抓住 | M5*/M4b → **仍漏**（原理性上限，字符串断言看文本不看数据流）
  测试：brief 六条 6/0/0；go test ./... -v -count=1 → **69 PASS / 0 FAIL / 0 SKIP**
  它额外加了 stripJSComments（brief 之外，已披露）—— 不加的话体内塞注释就满足断言，那正是病根。**保留**。

Ruling C38: 接受它加的 `stripJSComments`。C33/C34 的病根就是「字符串断言可被注释糊过去」，
  M6 实测证明不剥注释则修复本身仍可被糊 —— 保留是自洽的。
  代价若错：断言多依赖一层剥离逻辑（已由 M6 变异覆盖）。

Ruling C39（**它独立重新发现了 C3**）：fixture `outer.html` 的 iframe 是硬编码绝对 URL
  `http://localhost:8892/inner.html`，而 Task 3 用 httptest（随机端口）→ 子帧连不上 →
  Task 4 的跨源测试会红。**这正是我预检时裁定的 C3**（"Task 4 跨源测试动态生成 outer 页，
  端口取自 srv.URL"）。它的出路建议（运行时改写 src / 改用 outer_same.html 相对路径）
  与 C3 同向。Task 3/4 派发时必须带上这条。

它自查出的过程失误（复测时相对路径覆盖了测试文件、还原静默失败 → 一轮数据是旧文件跑的）
已从 HEAD 还原并**全部重测**，工作树与 HEAD 逐字节一致 —— 如实披露，记一笔。

## Task 2 审查（reviewer: sonnet，审 9247aea..ec235c6）

结论：**Approved** + 1 Important（plan-mandated）+ 5 Minor + 5 ⚠️。
审查方逐条独立验证：C10/C11/C32/C34/C35 都真落地；变异表的行号与提交文件逐字相符；
与探针逐行对比确认移植忠实（只差两处 C10 替换、披露的 r→rt 重命名、返回键集、注释）。

⚠️ 五条我自己解掉三条（另两条非问题）：
  1. `__cdpRoots` = `var out=[root]` → **document 是元素 0** ✓（`RS.length - 1` 数 shadow root 正确、
     `rt.body` 分支成立）。读 internal/shadow.go 确认。
  2. `__cdpQAIn` = `var out=[]; out.push(...); return out` → **纯数组** ✓（`.filter/.map/.slice/.forEach` 可用）。
  3. **前导段含 `__cdpQA`：True；含 `.children`：False** → **独立证实了 Important #1 的机制**：
     `strings.Contains(js,"__cdpQA")` 因前导段恒真；`.children` 那条才是真在测。
  4/5. `ok`/`counts`/`page_text_len_raw` 非契约键、brief 的 PageModel 没有 → 丢得合规；
     traps 测试里指向 observe_integration_test.go 的引用是 Task 3 的事。

Ruling C40: **Important #1 要修**（不能推给 Task 3）。审查方的洞察是对的：
  **C10 是结构性约束，行为测试盖不住** —— 就算有人重新加回自实现的 walk，集成测试照样过。
  所以那条断言是 C10 的**唯一**闸门，而它空转。这与 C34 是同一类病，不修不自洽。
  修法（比审查方给的更精确，因为它的 `"function __cdpRoots"` 与源码文本不符 ——
  实际是 `var __cdpRoots = function(root) {`）：
    · C11/注入证据：`strings.Count(js, "__cdpRoots = function") == 1`
      （删掉 withPierce → 0 → 红；双重注入 → 2 → 红）
    · C10/正文证据：断言**正文里**含 `__cdpRoots(document)` 与 `__cdpQA(`，
      且正文**不含** `.shadowRoot`（自实现 walk 的特征）
    · 实现者须先用 mutation 验证这三条真能变红，再落地
  代价若错：断言与内核文本耦合更紧（内核改实现则测试要跟着改）——
  但换来 C10 第一次有真闸门。
Ruling C41: Important #2（陷阱①的 `r.innerText` 禁令因 r→rt 重命名而失效）同修。
  同一类病（空转断言），而且就在同一文件。让禁令不依赖变量名。
  代价若错：无。
Minor 3–7 记此备终审（整脚本作用域断言与内核文本耦合 / contrastBand 仍走 parentElement /
  JS 键名与 struct tag 无绑定 / page_text 截断无信号 / jsFuncBody 不跳注释）。

Task 2 修复轮 2: DONE → commit 2fea194（单文件 +75/−9；observe.go 未动，md5 仍 2b7a13…）
  ① C10 唯一闸门收口：`strings.Count(js,"__cdpRoots = function")==1`（0=没注入/2=重复注入）
     + 正文（`observeBody` 按 pierceJS 常量切分，不猜文本）含 `__cdpRoots(document)`/`__cdpQA(`、不含 `.shadowRoot`
  ② 陷阱①禁令改成名字无关：`rootsVars` 从代码读出变量名再禁那两个名字（含下标）上的 `.innerText`
  变异矩阵：**9 正例全红 + 2 反向对照**（M14 改回调名为 q 仍抓住 → 名字无关性证据；
     M17 注释提及 .shadowRoot 不误伤 → 防误杀）。测试 5/0/0 与 69/0/0。
  ★ 它在新写的断言上**又抓到一次同一个病**：`__cdpRoots(document)` 第一版**能被注释满足**
    （正文说明性注释含该串）→ M11 改掉代码后仍绿 → 修法：`observeBody` 也过 stripJSComments。
  ★ 它还**推翻了自己想写的一句话**：原想写「jsFuncBody 配平失败会 Fatalf、是响的」，
    实测是**静默变弱**（往 region 注释塞 `{` → 正文被悄悄延长、断言仍绿）。按实测改，没按想当然写。

Ruling C42: 接受它把 `observeBody` 也过 `stripJSComments` —— 与 C38 同因（不剥注释则断言可被注释糊）。
  另：它自查出的「配平失败 → 静默变弱」记进 Minor 清单（终审 triage），本轮不修。

Task 2 修复轮 2 复审: **all findings addressed**，零阻塞
  审查方把 pierceJS 与 withPierce(...) 的真实字符串从源码抠出，逐字重跑 shipped 断言逻辑
  （同 regex / 同 stripJSComments / 同 TrimPrefix）在内存里变异验证，未采信报告。
  · 删 withPierce → inject-count=0 RED ✓（C11 首次真有闸门）
  · rootsVars 推空集 → **t.Fatalf**（堵死「修复本身又空转」这个我最担心的风险）✓
  · observeBody 切分不可能静默塌成整脚本：若 withPierce 改包裹，TrimPrefix no-op，
    而前导段含 .shadowRoot → 立刻红（响的）✓
  · 它自报的 bug 在提交版真修了且 load-bearing：M11 无剥注释绿 / 有剥注释红 ✓
  · 名字无关性：改名 root/q 都 RED；`__cdpRoots(document)[1].innerText` 这类第三方标识符逃逸
    —— 比原缺陷窄得多，且不恢复原失败模式
  新增 Minor（**不进修复轮**，终审 triage）：observe_traps_test.go:192 的计数用**未剥注释**的 js ——
    正文里一句提到前导段定义的注释会让计数变 2、**正确代码变红**；与两行后 :131 刻意剥注释不一致。
    响亮（非空转），一行可修。

Task 2: complete (commits 9247aea..2fea194, review clean)
  交付：internal/observe.go —— 单帧页面模型提取，6 条陷阱/契约测试，变异矩阵 9 正例 + 2 反向
  实现自始至终未动（md5 2b7a138966854dc47bee54ced47c31f5）

Task 3: DONE_WITH_CONCERNS → commit ca8c771
  全量 go test ./... -v -count=1 → **PASS 63 / FAIL 0 / SKIP 0**（internal 25.143s），
  四条新用例各 ~1.55s（真导航真求值），连跑 3 遍 12/12 绿。
  变异实测四条**都是真闸门**：M-A 陷阱①→page_text 掉到 **10 字**（正是探针踩坑值）、
  M-B 陷阱②→**6/6 假阳性**（全是 ←div#host1）、M-C 陷阱③、M-D region 恒 body；各自转红。

Ruling C43: **批准它改 fixture（给 shadow.html 的 host 外包一层 `<main>`）。**
  它报的顾虑是对的：brief 的陷阱③断言在**冻结 fixture 上不可能通过** ——
  `shadow.html` 整页 landmark 数 = 0，于是正确实现（走 getRootNode().host 爬出 shadow）
  与错误实现（只走 parentElement）**都返回 body**，断言对两者一样红 = **空转**。
  它**没动断言一个字**，只补了触发条件 —— 这是「让测试有能力分辨」，
  与「改测试去迁就实现」正好相反。**批准。**
  代价若错：testdata 副本与 docs/probes 副本不再逐字节一致（已注释说明，且探针副本未动）。

Ruling C44（**更正我自己的规格**）：查证发现 —— 探针 README 里陷阱① ② 都有实测数值
  （10→141 字符、5/5 假阳性），**陷阱③ 只有一句「region 全部退化成 body」，没有数值**。
  而 R3 复跑时我看到 region=['body'] 并写下「那是 fixture 没有 landmark，所以是正常的」——
  **我当时注意到了却没有意识到：那句话的真正含义是这个修复从未被验证过。**
  ⇒ 已更正规格 §4.3 与探针 README：标明陷阱③的症状是**读代码推断**，非实测；
  并加一句元教训：**症状栏没有数值 = 它没被测过。**
  这是同一个家族病在我自己的文档上复发 —— 把推断当实测写。
  代价若错：无（更正后更保守，且 T3 已用变异补齐了实测）。

## Task 3 审查（reviewer: sonnet，审 2fea194..a80f5d6）

结论：**Approved**。审查方独立推演了 fixture 改动的分辨逻辑（未采信报告 dump），
核了：不影响其它测试（只有 observe_integration_test.go 读 testdata/）、observe.go 未动、
四条是真行为测试、无 localhost:8080 依赖、strconv 占位已删、唯一差异文件确认。
**skip-vs-pass 风险它读了运行日志解决**：63 PASS / 0 FAIL / 0 SKIP，四条 1.55–1.57s
（Task 2 字符串测试在同日志里 0.00s），且报告引的变异失败行号与真实行号逐一相符。

Ruling C45: **Important #1 要修** —— `observe_integration_test.go:47-49` 把**导航失败**当成 skip。
  浏览器连不上时 skip 合理；但导航到「测试自己刚起的 server」失败是缺陷，不是环境缺失，
  而它会把整个闸门悄悄变成四条绿 skip。改 `t.Fatalf`。
  （若实测发现偶发抖动，再加一次有界重试；不许退回 skip。）
  代价若错：极端环境下测试会红而非静默通过 —— 这正是要的。
Ruling C46: 一并收三条 Minor，同主题（让闸门没有暗门），不单开轮次：
  · 注释里带着未实测的数字（"修好后是 141" / "5/5"），实测是 142 / 6/6
    —— **正是 C44 刚在规格上更正过的那个病**，出现在测试自己的文档里
  · 缺前置守卫（若 actions 静默取到 0 个，两条 shadow 测试都空转通过）→ 加 `len(m.Actions) < 5` 守卫
  · 探针副本 `docs/probes/.../fixtures/shadow.html` 仍无 `<main>` —— 若将来有人从探针目录
    重新生成 testdata，陷阱③ 会退回「永久红且空转」，而下一个人多半会去**削弱断言**
    （本项目反复踩的那个坑）。处置：**不动探针副本**（它是历史记录），加 `testdata/README.md` 说明差异。
  代价若错：无（四条都是小改）。
⚠️(d) 141 vs 142 的差：探针的 `page_text_len_raw` 是各 root innerText 长度求和，
  测试量的是**拼接+归一化后**的 rune 数（join 加了分隔空格）—— 很可能是这个差。
  让实现者确认后把注释写准，不要照抄 brief 的数字。

Task 3 修复轮 1: DONE → commit b4f3928
  全量 63 PASS / 0 FAIL / 0 SKIP；TestObserve 9/9；四条集成各 1.55–1.56s。四项逐条实测：
  ① 导航失败改 t.Fatalf —— 指向死端口实测 FAIL + net::ERR_CONNECTION_REFUSED（不是 SKIP）。
     **未加重试**：本轮 20+ 次导航 0 失败，无抖动可吸收（没造不需要的机器）✓
  ② 141 vs 142 **查清**：探针 page_text_len_raw = 各 root 原文长度求和 [10,6,125] = 141；
     测试量的是 join(' ')+归一化后的 rune 数（+2 分隔空格、内部空白折叠，净 +1）= 142。
     遮挡同页实测 6/6（非 README 的 5/5 —— 那页就是 6 条 action）。注释与错误文案已按实测改正。
  ③ 加 requireShadowActions（≥5 动作且 ≥5 个 shadow_depth>=2）；**验证守卫生效**：
     把 SEL 改成匹配不到 → 两条都 FAIL（改前会静默绿）
  ④ 加 tools/cdp/internal/testdata/README.md；探针副本一字节未动
  observe.go 全程未改，每轮变异后 md5 还原一致。它还订正了自己首轮报告里两处与现状矛盾的数字。

Task 3 修复轮 1 复审: **all findings addressed，新增破坏 None**
  ① 只剩一处 skip（NewClient 那条，:46，grep 核过）；消息可诊断（带 URL + CDP 错误）
  ② 141 vs 142 的解释**对着探针源码核过**（observe.js:216/:222）—— 是探针代码的字面描述，非编造；
     算术被迫成立：141 + 2 分隔空格 = 143，归一化去 1 → 142
  ③ 守卫能触发（失败行号与 shipped 文件相符）且正常跑不误伤（6 条 action 全 shadow_depth=2）
  ④ README 三要素齐（差异/原因/警告）；探针副本未动（md5 核过其余 5 个 fixture）
  ⑤ 无断言被削弱：阈值 60/5/3/2 全在，无删除、无 early return，每处改动都更严

Ruling C47: 接受 residual（不进修复轮）：`NewClient` 那条 skip 也覆盖「Chrome 活着但**页面目标数为 0**」，
  那种情况仍会让四条测试变成绿 skip。判断：那是**合理的 skip**（没有页面可驱动 = 环境不可用），
  且 `-v` 下 SKIP 行是**可见的**、计数会变成 59 PASS / 4 SKIP —— 只要保持「永远看 SKIP 计数」就抓得到。
  记此备终审。代价若错：某次「全绿」其实是 4 条跳过的，被 SKIP 计数挡住。
  ⚠️ 这条也解释了为什么前一位实现者**刻意留一个 tab 不关** —— 那不是偷懒，是防这个。

Task 3: complete (commits 2fea194..b4f3928, review clean)
  交付：4 条真浏览器集成测试（light + 两层 shadow），带前置守卫、导航失败不被吞、fixture 差异有 README

Task 4: DONE_WITH_CONCERNS → commit d64ab93（observe_frames.go 新建 + 集成测试 +263 + testdata/README 小改）
  TestObserve* 10 PASS / 0 SKIP / 0 FAIL（跨源那条 0.78s）；go test ./... **64 PASS / 0 SKIP / 0 FAIL**
  （基线核对：HEAD 版本 63 PASS，差集恰好是新增那条 —— **没有测试被丢掉**）

Ruling C48（**更正我自己的 C9**）：C9 说「用 cdproto 的 `GetFrameTree()`，别自造 FrameNode」——
  函数存在、类型对、编译过、跑起来不报错，**但协议调用本身不返回 OOPIF（跨源）子帧**。
  实测（Chrome 150）：同源子帧报、跨源一个都不报；裸协议 JSON 里连 `childFrames` 键都没有；
  `Target.setAutoAttach` 后逐字节相同 → **不是时序，是协议行为**。
  照 C9 写出来的 ObserveAll 会**对所有跨源 iframe 静默视而不见**。
  实现者改用 `GetFrameTreeWithEvents`（同源帧树 ∪ DOM 穿透并集），并变异验证承重
  （换回裸 GetFrameTree → 精确红「合并结果里没有子帧的字段 #fn」）。
  **教训与今天第 8 次同族**：我验了「函数在不在、类型对不对」，
  **没验「协议调用返不返回需要的东西」** —— 而后者才是这条裁定的目的。
  → 我核依赖时只该问「它能不能给我要的东西」，不该问「它存不存在」。
Ruling C49: 接受它的两处有意偏离草稿：
  ① 单帧失败处理：草稿代码静默 `return nil`（而它自己的注释写着「不能静默吞掉」）。
     改为**主帧失败整体报错**（空模型+nil error 是假成功）、子帧失败不整体失败但
     记一条 `{kind:"frame-error", selector:<frameId>}` 进 obstructions。**优于草稿。**
  ② 端口（C3/C39）：选「服务层改写」—— 拦下 /outer.html 把写死的 8892 换成 r.Host 里的真端口，
     **换不上当场报错**，不退化成「没有子帧」假失败；页面结构仍一字不差来自 fixture。
     测试三段证据链：反证（主帧单帧观测看不见 #fn/#submit）+ 自证跨源（contentDocument === null）
     + 合并（带 ["main", <childId>] 路径）。**这个做法好**：它让「跨源」这件事被**自证**，不是假设。
Ruling C50（残余，记此备终审与 T9）：`GetFrameTreeWithEvents` 内部**静默吞掉 DOM 查询错误**
  （`client.go:259 return ft, nil`）→ 真发生的话 `ObserveAll` 会安静退化成「只有同源子帧」。
  根治要动 `client.go`（不在本任务文件清单，且有其它调用方）。
  裁定：**本轮不修**，但记成具名债 —— 症状（跨源帧静默消失）、位置、所需修法都要写清。
  代价若错：DOM 查询抛异常时跨源帧静默丢失；概率低但后果与 C48 同类。

## Task 4 审查（reviewer: sonnet，审 b4f3928..d64ab93）

结论：**Needs fixes**。机械合规全过（C9 照办、C1 移入、C3/C39 服务层改写且两条失败路径都响、
无手拼 JS、无 8080 依赖、observe.go/client.go 未被本 diff 动）。
但**任务的整个意义所在那条测试，证明「跨源」只靠一条未验、且时序敏感的断言**。

Ruling C51: **Important #1 要修** —— 它证明了两件事：
  ① 链上的「反证」环是**废的**：`__cdpRoots` 只走 querySelectorAll('*') + shadowRoot
     （shadow.go:17-28），**从不进入 contentDocument** → 主帧单帧观测看不见**任何** iframe 的内容，
     同源也一样。所以它当不了「同源报警器」。
  ② 仅剩的中间环（contentDocument === null）依赖未验前提：同源帧尚未 commit 文档时它是否也是 null？
  修法（它给的，更好）：**从子帧自己取 `location.href`，断言 host 与主帧不同** ——
  同时证明「跨源」与「子帧文档真的 commit 了」。
  并要求：**做同源变异验证**（把 want 改成 127.0.0.1 → 必须红）。之前那两次变异改的是**实现**，
  不是**夹具的源** —— 都没测到这条测试自己的主张。
  代价若错：无（换一条更硬的证据链）。

Ruling C52（**合并解决 #2 与 #3**）：**给 `PageModel` 加一个正当的诊断通道**，
  不再把错误塞进 `obstructions`。
  理由：③ 说 `obstructions` 被复用成错误通道（`selector` 兼装元素选择器与 frameId、
  `text` 兼装页面文字与错误消息，消费者忽略 kind 就会拿 frameId 去点）；② 说退化时**没有任何信号**。
  两者要的是同一个东西 —— **一个「观测者自己的问题」的通道**，与「页面上的东西」分开。
  而且这正是 Console（D14/D15）要显示给运营的：「这里我没看清」。
  实现：`PageModel` 加 `diagnostics: [{kind, detail, frame_path}]`；
  `frame-error` 与「iframe 数 ≥1 但枚举到 0 个子帧」的退化信号都进这里；kind 常量导出。
  ⚠️ 这是**契约新增**，规格 §4.3 我一并更新。
  代价若错：契约多一个字段（消费者可忽略）；换掉的是「一个字段两种含义」这种必然踩的坑。

Ruling C53: **Important #4 要修** —— 并集没去重保护：`collectExistingFrameIDs` 只算一次、
  `observeInto` 无 seen 集 → 重复 ID 会进树两次、被观测两次（actions/fields/ShadowRoots/PageText 翻倍）。
  扁平夹具测不出（重复的是子帧 `#document`，只有它**自身含 iframe** 时才会产生重复 —— 即嵌套）。
  修法：`observeInto` 里加 `seen map[string]bool`。
  代价若错：无（廉价守卫）。

Minor 一并收（都是注释/一行/文档，同批不单开轮）：
  #5 `frameEnumerationWait` 注释说错了等待位置（基线半是在等待**之前**取的）
  #6 `ObserveAll` 对主帧传真实 frameID 而非 `""` —— 与包内约定不符，且会在页面常驻一个
     isolated world、往 `c.frameCtxs` 塞一个导航时不会失效的缓存项。`if isMain { frameID = "" }`
  #7 代码注释把修法说成「唯一解」—— `Target.getTargets` 能直接枚举 OOPIF，只是「用现有助手
     的唯一解」；代码注释比报告活得久，要说准
  #8 支撑核心结论的原始协议 JSON 记在 `.superpowers/` 路径（workspace 会被删）→ 挪进
     `testdata/README.md`（本任务已改过它，且那正是「别踩这里」该待的地方）
  #9 `testdata/README.md` 超出 brief 文件清单 —— **接受**（纯文档改进，且已在报告声明）

Task 4 修复轮 1: DONE_WITH_CONCERNS → commit 9219e93（4 文件）
  TestObserve 10 PASS / 0 SKIP / 0 FAIL（7.03s，-count=3 全过）；go test ./... 64 PASS / 0 SKIP / 0 FAIL
  ★ **同源变异验收达成**：M-A（夹具 src 改 127.0.0.1 + 守卫原样）→ **红**；
    **M-B（同源 + 守卫降级为 Logf）→ 绿，且 fields/actions/shadow_roots/diagnostics 与跨源时逐项相同**
    —— **实测坐实了审查方的诊断**：判据 3 单独判不了源，那两条守卫是唯一挡板。
    这条「空转绿」风险从此是**测出来的**而非推的。
  已落地：主判据换成「子帧自己取 location.href，host 必须不同」（同时担保跨源+文档已 commit）；
  Diagnostics 通道 + 导出常量 DiagKindFrameError/DiagKindFrameBlind；obstructions 恢复只装页面遮挡物；
  守卫逐帧对账；seen 集；#5–#8 全收。

Ruling C54（规格同步，我来做）：§4.3 加 `diagnostics` 字段 + 一张「obstructions vs diagnostics」对照表，
  并写明 `frame-blind` 的触发条件。理由写进规格：**运营必须知道「AI 是没看清，还是看清了但做错了」**
  —— 那两件事的处置完全不同，所以两个通道不能混。
Ruling C55: 🔴 新顾虑（跨源帧内部子帧枚举不到）**接受为具名限制，本轮不修**（R18）。
  理由：① 它**已经不静默**（守卫兜成 frame-blind 诊断），这是关键性质；② 收进来要**逐 OOPIF target 取树**，
  是**新能力**不是本任务的 bug；③ 实际影响面窄（「跨源组件里再套一层」，漏斗/报价表单不常见）。
  代价若错：那类页面会漏掉孙子帧里的内容，但会在 diagnostics 里喊出来。
  另：`seen` 集实测**没复现出重复**（两种嵌套夹具 0 重复 ID）→ 是防御性的；`frame-error` 路径仍无确定性测法。

Task 4 修复轮 1 复审: **all findings addressed，无需要修的破坏**
  Finding 1 评为 **fails closed**：若 `EvalInFrame(childID,…)` 哪天静默退回主帧，
  `childHref == mainHref` 立刻红 —— **没有假绿路径**。旧的时序前提不再是承重环节。
  Finding 2：我点名的两个误报向量（display:none 的 iframe、加载失败的 iframe）**都不是向量**
  —— 它们照样拿到帧节点，计数仍 1:1。守卫的三个场景**按机制可区分**。
  Finding 3/4 与 Minor #5–#8 均确认落地。

Ruling C56（**T5 重定义**）：计划的 T5 已过时 —— 它假设 Go 侧 `stabilityOf()`/`looksRandom()`，
  但 Task 2 已把 `candidates()`/`stability()` 作为 **JS** 移植进 observe.go（:160/:174），
  那两个 Go 函数不存在也不需要。**但没有任何测试覆盖它们**。
  重定义为：**给选择器候选与稳定性评级加真浏览器行为测试**（不是实现 Go 助手）。
  内容：在 fixture 里放 stable `#id` / `data-testid` / 随机 hash class / 深层 nth-of-type 四种按钮，
  断言各自的 `stability` 值、`alternates` 有序且不冗余、**主选择器不采用随机 token**。
  理由：D3 说「选择器是 py 准不准的头号因素」，而它现在零测试。
  代价若错：T5 从「写实现」变成「写测试」，工作量更小；若判断错（其实需要 Go 助手），
  下一轮补上即可。

非阻塞笔记（记此备终审）：
  n1 两条否定断言被删（原「主帧 Observe("") 不得含 #fn/#submit」）—— 有意，但**现在没有任何断言
     保证单帧 Observe 保持单帧**。若将来有人让 Observe 内联 iframe，套件仍绿。低危。
  n2 守卫的比较是**双向**的（`n != len(ft.ChildFrames)`），比审查方建议的单向更强。
     两个非盲输入会让它说话：(a) **closed shadow root 里的 iframe**（JS 助手够不到、
     CDP 穿透路径够得到 → 内容其实拿到了，诊断却在喊）；(b) 快照后注入的 iframe。
     裁决：**保持双向** —— 过度警告（罕见场景）vs 漏报真实盲区，**漏报的代价高得多**，
     而本项目的立身之本就是「不要静默漏掉」。记此，真站上若频繁误喊再收窄。
  n3 `seen` 的提前返回会跳过重复节点的**整棵子树** —— 对今天的并集正确（产生不了重复节点），
     但若将来上「逐 OOPIF target 枚举」，两个来源都能到达的帧会**静默丢掉第二处的子节点**。

Task 4: complete (commits b4f3928..9219e93, review clean)
  交付：ObserveAll（跨帧枚举+合并，含去重）、diagnostics 通道、frame-blind 守卫（自身有测试会触发）、
  跨源判据换成「子帧自取 location.href 比 host」且 fails closed

Task 5: DONE_WITH_CONCERNS → commit ed74c24（3 文件 +506；observe.go 未改，变异后逐字节还原）
  TestObserve 15 PASS / 0 SKIP / **1 FAIL**；./... 79 PASS / 0 SKIP / 1 FAIL
  **那条 FAIL 是刻意的** —— 它找到实现缺陷后**没改小期望**，留红上报。变异 5 次全红。
  ★ 真发现：**RAND 够不着 6~7 位 base36 hash** —— brief 自己举的例子 `css-1x2y3z4` 漏网，
    首选成了 `button.css-1x2y3z4` 且评 **medium**（该 low）。两道形态都够不着
    （①要 hex ≥8 位、②要整串无 `-`）。D3 说选择器是 py 准确率头号因素 ——
    这个 hash 会被抄进 py，**站点发版即断**。
  ★ 同族：`candidates()` 的 `name` 落点是四处里**唯一没过 RAND** 的 → 随机 name 当首选且评 high。
  ★ 它还主动把关键结论同步进 `testdata/README.md`（因为 `.superpowers/sdd/` 是 gitignore 的，
    不能只留在报告里）—— 这个判断对。

Ruling C57: **修实现，不改测试**（那条 FAIL 就是验收）。修法要求：
  ① `RAND` 要够得着 `css-1x2y3z4` 这类 6~7 位 base36 hash
  ② `candidates()` 的 `name` 落点也要过 RAND
  ③ **必须双向钉边界**：不光测「该抓的抓到」，还要测「**不该抓的别抓**」——
     否则放宽 RAND 会静默开始拒绝正常 class（那会让 stability 全面变差、影响 agent 选择）
  偏置说明：**宁可过度检测**（假阳性的代价是退回结构路径，轻微）——
  假阴性的代价是**抄进 py、发版即断**，两者不对称。
  实现者提醒「emotion hash 6~7 位」是**本仓既有知识、未在真站核实**（那是 R5）——
  接受，先按形态做，真站校准留 R5。
  代价若错：RAND 过严 → 一些本可用的 class 选择器降级为结构路径。

记此备终审（不进本轮）：
  ③ 的两条设计观察：`alternates` 对「元素自身有稳定 id」的元素恒为空/无结构路径
     —— **最需要兜底的反而没兜底**；`stability()` 的 high 被 `/^#/` 放宽成「以 # 开头」，
     同形状 4 段路径带不带 id 表头差两档（它用特征化断言钉住了，未判成缺陷）。

Task 5 修复轮 1: DONE → commit dc4d2d8（4 文件 +128/−23；实现+测试同一 commit，因为是一个判据的两面）
  TestObserve **17 PASS / 0 SKIP / 0 FAIL**；./... **81 PASS / 0 SKIP / 0 FAIL**
  · RAND 加形态③：被 `-`/`_` 夹住、≥5 位、**字母数字来回交替 ≥2 次** → 认出 css-1x2y3z4。
    原两形态逐字保留。**「交替 ≥2 次」是核心决策**：正常类名的数字只有三种落法，都不产生来回；
    更简单的「混排+长度≥5」会把 `step1` 一家全误伤 —— 而多步表单正是本项目漏斗的形态。
  · `name` 落点补 `!RAND.test`（四处里唯一没过的一道）
  · **双向钉边界**：7 个「只带一个 class」的按钮（btn-primary/hero-banner/col-md-6/icon-24/
    step1/section1/text-2xl）+ name="step2"，断言**仍被采用**；随机 name 那条的空断言改成正向断言
  · **变异 3 次全红**：M6 RAND 改回原样（输出与首轮一字不差）；**M7 只撤 name 过滤 → 也红**
    （两处漏网**各自**被守）；**M8 RAND=/./（过度检测那一边）→ 反向边界那条红，8 个用例全报出**
    —— 证明「不该抓的别抓」不是声明

Ruling C58: 接受它的「交替 ≥2 次」判据与双向测试。它自己标明了两处残留：
  全字母 hash（`sc-bdVaJa`）与只翻转一次的（`css-abcdefg1`）认不出 —— 前者要引前缀表
  （站点知识，应由真站样本得出）→ 留给 R5。
  ⚠️ 它还主动标注「形态③ 的概率模型（7 位 base36 认出率 ≈77%）是**我算的、非实测**」——
  **这个自我标注本身就值得记**：本项目反复栽在「推断的数字当实测写」，它主动把两者分开了。

## Task 5 审查（reviewer: sonnet，审 9004add..dc4d2d8）

结论：**Approved** + 1 Important + 8 Minor + 3 ⚠️。
审查方逐例手推了正则：`css-1x2y3z4` 抓得到，7 个「不该抓」的 class 加 `step2` 全放得过；
`name` 那一行改动确认是**一行**、另三处落点逐字节未动；
**那个 77% 它独立重算过且分解正确**（全字母 10.25% + 只翻转一次 12.77%），并确认已声明为「算的、非实测」。
它还独立复核了 M8 的算术（`RAND=/./` → 7 个 class 错 + 1 个 name 错 = 声称的 8）。

Ruling C59: **Important #1 要修** —— 我要求「双向钉边界」，而实现者**对 class 双向、对 name 单向**，
且 `name` 恰是这次改动的落点。③ 在 name 上新危及 L→D→L 家族（`step2a`/`address1a`/`opt2b`
这类子字段命名）→ 被丢弃 → 退化位置路径，套件无感。
**修法与判断（审查方要求把判断写下来）：接受 name 也适用 ③（over-detection 偏置前后一致），
但必须双向钉住 —— 加 `name="a1b2c3"`（随机，必须丢）+ `name="step2a"`（人写的，接受被丢，
断言它确实退化成结构路径）。** 判断写进代码注释，别让它只活在 ledger 里。
理由：class 是框架生成的（hash 常见），name 是人写的（hash 罕见）——
所以对 name 用激进的 ③ 收益小、代价稍大；但**保持一条规则、一个偏置**比给 name 特例更可维护，
且退化的代价（回退结构路径）轻微。这条判断必须写下来，否则下一个人会以为是漏了。
代价若错：人写的 L→D→L 型 name 退化成结构路径。

Ruling C60: 一并收 4 条 Minor，同一主题（**测试文件里陈旧的数字与描述着修复前行为的注释** ——
  正是本项目反复栽的那类「看起来是实测的数字」）：
  · 陈旧数字：测试注释说「11 按钮 + 3 input = 14（实测 14）」，实际 fixture 已 18 按钮 → 21
    （14 是第一轮的真数据，第二轮加了 7 个按钮没刷新注释）
  · `selector.html` 两处注释描述的是**修复前**的行为（引了旧的两形态 RAND、说「name 这一路没有 RAND」）
    —— README 更新了而 fixture 没跟上，**fixture 现在与自己的 README 矛盾**
  · 形态① 现在**成了孤儿**：它曾是唯一捕获者的用例现在都被 ③ 抓 ——
    删掉①套件仍绿。加一个「只翻转一次的 hex」用例（如 `class="css-abcdef1234"`）把它重新隔离出来
  · 未披露的残留：③ 有 ≥5 位下限 → **≤4 字符的 hash 段**（`css-a1b2`）漏网，该进残留清单
代价若错：无（都是文档/一条用例）。

记此备终审（不进本轮）：
  m5 M8 是**粗糙**的变异（`RAND=/./` 也会弄红另一条测试）——它证明了反向测试**会响**，
     没证明它**必要**。更有信息量的变异是它明确否决的那个设计「混排+长度≥5」，
     那个只有反向测试能抓。
  m6 **RAND 的波及面超出那两个已测落点**：`pathSel` 的 id 检查（`:174`）现在会爬过 hash 型祖先 id
     （真实行为变化：`#app-1x2y3z4` 以前当路径锚点+high，现在产物结构路径）、
     `candidates()` 的选项组 scope（`:294`）与遮挡/关闭选择器（`:310-311`）继承全部 —— 三者都无断言。
  m7 措辞：我说「RAND 被 stability() 用」是**错的**（它用的是 `/^#/`、`\[(name|data-`、
     `:nth-of-type` 深度与 `^[a-z]+\.[a-z]`）—— 行为仍**间接**改变（hash id 元素现在落到深度分支，
     是改善）。报告 §7.1 四个词之前说「实测取舍」、之后承认是计算模型 —— 二者取一。

Task 5 修复轮 2: DONE → commit ee8b1de（4 文件 +133/−20）
  TestObserve **18 PASS / 0 SKIP / 0 FAIL**；./... **82 PASS / 0 SKIP / 0 FAIL**
  **observe.go 本轮逻辑零改动**（git diff -U0 非注释行为 0 行；RAND 与 name 过滤与轮 1 批准版逐字节相同）
  ① name 补成双向：fixture 加 `name="a1b2c3"`（随机→必须丢）+ `name="step2a"`（人写→接受被丢，
     **断言确实退化成结构路径**），合成一条测试。取舍按 C59 **写进代码旁**，并留了**交叉引用**：
     将来若收窄 ③ 让 step2a 不再被丢，那条测试会红并提醒同步改注释 ✓（防注释悄悄过期）
  ② 四条 Minor 全收，其中两条做得好：
     · 陈旧数字它**重新数了**（25 = 19 按钮 + 6 input，用 25×8=200 与日志逐条对上）——
       **没有照抄我给的 21**
     · 形态① 加了 `class="css-abcdef1234"`（只翻转一次，②要 ≥6 位数字、③要 ≥2 次交替，
       两者都够不着）把它隔离出来
  · 变异：M9 撤 name 过滤 → 那条测试**四个断言全开火**（两个方向都守）；
    M10 删形态① → **只红一条**，证明①不再是孤儿、隔离精确
  · 自曝过程失误并写教训：M10 第一次用 `sed` 改坏了 observe.go（正则尾部 `\|` = 空分支），
    因改前 cp 了备份、还原后逐字节相同 —— **含 `|` 的正则别用 sed 改**

Task 5 修复轮 2 复审: **all findings addressed，零新增破坏**
  审查方逐条堵死「会为错误理由通过」的路径：
  · 两个 name **只能**被 ③ 抓（a1b2c3：①要 ≥8 hex 它 6 位、②要 ≥6 位数字它 3 位；step2a：一个数字）
    → 断言不会因别的规则通过 ✓；收窄 ③ 则两个断言都会开火 ✓
  · 那个 25 **可独立重建**（11+3=14 → +7按钮+1input → +1按钮+2inputs = 19+6=25）✓ 是实测不是抄的
  · `css-abcdef1234` **只有 ① 够得着**（② 要 6+连续数字它有 4、③ 要来回交替它没有）✓
  · 无断言被削弱、无阈值改动、无索引漂移（新元素都插末尾，既有 nth-of-type(k) 全保值）✓

Task 5: complete (commits 9004add..ee8b1de, review clean)
  交付：选择器候选/稳定性评级的真浏览器测试（双向钉边界）、RAND 形态③（认 6~7 位 base36 hash）、
  name 落点补 RAND、形态①重新隔离、陈旧注释与数字全部刷新

Task 6: DONE_WITH_CONCERNS → commit 44b433a（3 文件 +750）
  go test ./... → **77 PASS / 0 SKIP / 0 FAIL**，退出码 0；cmd 单独 18 PASS / 0 SKIP / 0 FAIL
  TestObserveCommandEndToEnd 1.43s（真构建二进制 + exec + 断言退出码/JSON 契约/跨帧 frame_path）
  变异 4/4 咬得住：非 JSON 输出 / 吞错误(exit 0) / 只观测主帧 / 无浏览器（**FAIL 而非 SKIP**）。
  其中「只观测主帧」在 base.html 上测不出来 → 它专门加了跨帧那条测试。

Ruling C61: **① 要修**（空数组编码成 `null`）。这是**契约自相矛盾**：同一份 `PageModel` 里
  `alternates`/`frame_path`（来自 JS）为空是 `[]`，而 `diagnostics`/`option_groups`/`obstructions`
  （Go 侧构造）为空是 `null`。py 侧 `for d in model["diagnostics"]` 会 `TypeError` ——
  而这是**运行阶段唯一的接口**，且正好在最需要它的时候（回退）炸。
  它没动手是对的（契约面、不在它的文件清单）—— 我现在裁定：**修，让所有空列表都编成 `[]`**。
  代价若错：无（更一致的契约）。
Ruling C62: **② 是披露，不是待办** —— 它已经自备私有 Chrome 修好了。
  但留一条已知陷阱记进 ledger：**共享 9222 上并行跑两个测试二进制会互相改页面**，
  且症状是「单包绿、全量红」+「URL 不变但观测到别人的元素」，极具欺骗性。
  将来若再有人往这套件加测试，**别用共享浏览器**。
Ruling C63: ③a `--json=false` 仍吐 JSON —— **一个静默不做事的 flag 是本项目的病**，要处置：
  实现或删掉，二选一，但**不许留着不动**。倾向实现一个最小的人话摘要
  （计数 + 前几条动作）—— 因为 Console 那条线上，人话输出迟早要有。
  代价若错：多写十几行；换掉的是一个「看起来能切格式、实际不能」的开关。

Task 6 修复轮 1: DONE_WITH_CONCERNS → commit 80b7cd8
  go test ./cmd/ 20 PASS / 0 SKIP / 0 FAIL；./... **79 PASS / 0 SKIP / 0 FAIL**
  ① 空列表归一 `normalizeNilLists`，`Observe` 与 `ObserveAll` **两条路各归一一次**；
     测试从「数组或 null 都放行」**收紧成只放行 []**，并自造空页 `/__empty.html` 专守
     （缺陷只在空列表上现形，base.html 测不出来）✓
  ② `renderHuman` 实现（URL/title、计数、前 5 条动作 text+stability、遮挡 dismiss_selector、
     diagnostics kind+帧路径）；默认 JSON 路径一字未改 ✓
  变异 E（我点名的）：归一化退回 null → **三条测试同时红** ✓ 事后逐字节还原
  ★ **新发现 ③（更危险，本轮未修）**：单帧 `observe --frame-id X` 的动作 `frame_path`
    是 `observeJS` 写死的 `['main']`（observe.go:282/301）；合并那条路被 mergeFrameModel 覆盖，
    **单帧这条路没人覆盖** → iframe 里的元素被标成主帧 → **py 照它选帧会静默把点击发到主帧**。
    比 null 更阴：null 会炸得很响，这个**不炸、只是点错地方**。
    它的处置**完全正确**：修法要定契约，所以**刻意没在测试里断言它**，免得把错行为钉死 ✓

Ruling C64（**契约裁定**）：`frame_path` 的语义定死 ——
  **单帧 `Observe(frameID)` 给 `[frameID]`（frameID 为空 → `["main"]`）；
  `ObserveAll` 给从根到叶的完整路径 `["main", <childId>, …]`。**
  理由：单帧不知道祖先链，但**消费者要的正是「该把动作发给哪一帧」**——`[frameID]` 就是这个信息；
  需要完整路径的人应该用 `ObserveAll`。两种模式语义不同但**都自洽**（都回答「这条动作属于哪一帧」），
  且**都够消费者用**。修法落在 `Observe` 解析后的归一化里（与 `normalizeNilLists` 同处）。
  已同步进规格 §4.3。
  代价若错：有人需要单帧的完整路径时会拿不到 —— 但那本来就该走 `ObserveAll`。
Ruling C65: 那条**负面结论**记进 ledger：用 `outer.html`（iframe 指向死端口）逼非空 diagnostics
  **实测两次都失败** —— 加载失败的帧渲染成 Chrome 错误页，`Observe` 照样成功、diagnostics 仍为 `[]`。
  ⇒ `frame-error` 这条路径**至今没有确定性测法**（T4 已知、本轮再次确认），而 `frame-blind` 是真正在守的那条。
  这与「无浏览器时集成测试走 Skip」是同一族风险：**有些错误路径就是这么难触发**，所以更要靠守卫而非靠运气。

Task 6 修复轮 2: DONE → commit f501c3f
  go test ./cmd/ 21 PASS / 0 SKIP / 0 FAIL；./... **80 PASS / 0 SKIP / 0 FAIL**
  实现：`framePathFor` + `normalizeFramePaths`，**只落在 `Observe` 末尾那一次归一化**
  （与 normalizeNilLists 同一行返回）；`ObserveAll` 未动、**`observeJS` 一字未动**（照裁定）
  手工实测：`observe --frame-id 2CEB94DD…`（子帧）逐条报 `"frame_path": ["2CEB94DD…"]`（修复前是 "main"）✓
  · 子帧腿走**真二进制**（9 条断言）；主帧腿新增 Go API 层测试 —— ⚠️ **单帧主帧在 CLI 上不可达**
    （CLI 把 `--frame-id ""` 当「没传」→ 整页模式）
  · 变异 F（点名）：去掉归一化 → **子帧腿红**（9 条逐条报 `["main"], want ["<childId>"]`）
    ★ **负面结果**：主帧腿照绿 —— JS 默认值 `['main']` 恰与主帧契约相同
    ⇒ **真正咬住缺陷的是子帧那条腿**
  · 变异 G（补做）：去掉主帧特判 → **主帧腿红**（`[""], want ["main"]`），子帧腿照绿
    ⇒ 两腿各有各的守，都不是摆设
  · ★ 它把四处断言从 `%v` 换成 `%q` —— **`%v` 会把 `[""]` 印成 `[]`，看着像空切片**，
    它在变异 G 时亲眼见到。这是**测试输出保真度**问题：让错值看起来像对值的格式串
  · 它**没提交我的规格改动**（「不是我的改动，留给你处置」）✓ 处置正确
  · 已知缺口：`--frame-id ""` 在 CLI 上等价于「整页」，所以**单帧主帧不可达** —— 接受
    （消费者要么要整页、要么要某个子帧，单帧主帧是冷门需求）

## Task 6 审查（reviewer: sonnet，审 ee8b1de..f501c3f）

结论：**Approved** —— 零 Critical、零 Important、6 Minor（全潜在/测试卫生）。
审查方回答了六个定向问题，其中我点名的 ② 答案最干净：
  **收紧后的测试从不信任 Go 侧反序列化** —— 它把**原始 JSON 字节**与字面量 `[]` 比对
  （`cmd/observe_e2e_test.go:716` `got != "[]"`），另加 Go 侧非 nil 断言，
  格式化器**故意**把 nil 印成 `nil(会编成 null)`（因为 `%v` 对两种形状印得一样）。
  原话：「**这是唯一一处『反序列化进 PageModel 就会瞎』的地方 —— 实现者避开了它。**」
另：`normalizeNilLists` 覆盖 PageModel 全部五个 slice 字段、两个生产者；嵌套 slice 不可能为 nil；
  `frame_path` 归一覆盖 actions **与** fields、合并路径不受影响；
  e2e 隔离是真的（私有端口/profile、Setpgid + 杀进程组、TestMain 回收 —— **不留孤儿 Chrome**）；
  **`cmd/` 包里没有任何 `t.Skip`**（缺浏览器是 Fatalf 并列出试过的候选）→ SKIP 风险结构性为零。

Task 6: complete (commits ee8b1de..f501c3f, review clean)
  交付：`cdp observe` CLI（JSON + 人话两种输出）、空列表归一成 []、frame_path 按契约归一、
  真端到端测试（真跑二进制 + 私有 headless Chrome + 断言退出码与 JSON 契约）

Minor 六条（记此备终审 triage，不进修复轮）：
  m1 `Diagnostic.FramePath` 在两个归一化之外 —— 今天不可达（单帧 JS 只发八个键，
     `m.Diagnostics` 恒为 `[]`），但一旦产生诊断就会被标成 `["main"]`。加一行注释说明为何排除即可。
  m2 `renderHuman` 的计数与 `前 N / 共 M` / `还剩 K` **无测试** —— 审查方读代码确认为对，
     但「会不会印错数」目前靠肉眼看而非靠闸门。各加一行断言即可。
  m3 同帧所有 actions/fields **共享同一个 FramePath 底层数组** —— 今天无害（无人 append），
     但将来某个 action 做 append 会静默改到兄弟元素（本仓已付过这类共享状态的账）。
  m4 夹具量级跨任务硬编码（actions==6/fields==3/shadow_roots==2）—— 改夹具会让本套件红，
     而报错读起来像 CLI 回归。
  m5 `freePort()` 在 Chrome 绑定前就释放端口 —— 小 TOCTOU 窗口，但失败是响的（20s 超时 + Chrome 日志尾部）。
  m6 合并腿里还留了两个 `%v` 的 frame_path 格式化（`:554`/`:570`）—— 对二元 slice 不瞎，
     但与轮 2 的理由不一致；换 `%q` 零成本。

Task 7: DONE → commit 259f746
  internal 77 / cmd 38 / ./... **115 PASS / 0 SKIP / 0 FAIL**
  ★ **裁定②被它测成了事实**：变异 M3（把身份键换回选择器 = **计划里的实现**）在**真浏览器**上
    复现了谎报 —— **6 个元素全报出现+消失、actionable=true，而内容一字未变**。
    手工跑：6 个选择器全换（交集 0）→ `actionable=false` ✓
  判据：`Actionable = URLChanged || TextChanged || 身份多重集差`；身份键取内容与角色
    （动作 Text+Role+Region；字段 Label+Type+Placeholder **不含 Hint**（那是 el.name||el.id）；
     选项组 Role+选项文本 **不含 Scope**（它就是选择器））。**选择器只当抓手，不当判据。**

Ruling C66（**R19 具名限制**）：页面自带倒计时/轮播/库存数字时 `TextChanged` 每轮都真
  → 恒定 `actionable=true`（误判有进展）。它**拒绝加「忽略数字」过滤**，理由正确 ——
  那会抹掉 `Step 1 of 3 → Step 2 of 3` 这种最典型的真推进。
  **接受为具名限制**，留真站校准（R5 一档）。代价若错：那类页面上 py 会原地打转 —— 但它会被
  「轮数耗尽」这条更响的信号兜住，而不是静默。
Ruling C67: 文档漂移（`README.md`/`CLAUDE.md` 的子命令表**连 `observe` 都没有**）
  → **补，但放到终审的修复波**：它是**分支级**的文档一致性（连同 T6 那 6 条 Minor），
  一次改完比两轮改好。已明确告知实现者「要补」，不是「不管」。

## Task 7 审查（reviewer: sonnet，审 f501c3f..259f746）

结论：**Approved —— 但附条件 C1**；我按它给的验证方法自查后 **C1 确认成立 → 结论翻成 Needs fixes**。
其余全部肯定：判据里 diff 自身无选择器、改名免疫有纯函数 + 真浏览器双重测试带正向控制、
人名输出的修复有可证保证（textDelta 锚在首个差异 rune，两段 excerpt 不可能相等）、
`strings.Fields` 而非正则 `\s`（Go 的 `\s` 漏 U+00A0 而 JS 不漏，夹具里是真 NBSP 字节）。

Ruling C68（**我自查确认 C1**）：身份键里两个字段**确实是选择器派生的**：
  · `region`（observe.go）：`if (/hero|banner|jumbotron/.test(祖先的className)) return 'hero'`
  · `label`（observe.go:366-368）：`label[for="${el.id}"]` **排在 `closest('label')` 之前** —— id 耦合且优先级更高
  后果：重渲染改名**祖先 class** 或改 `input.id` → 身份翻转 → **内容没变却报 actionable=true**
  —— 正是裁定②要防的那件事，而当前 e2e **结构上看不到**（改名 JS 只改元素自己、从不改祖先；
  且它注入 aria-label 替代被删的 id/name = **刻意保住了真实重渲染会断的关联**）。
  **修法（两处都便宜、且保留两个消费者各自的收益）**：
  ① `label`：**重排优先级** —— `aria-label` → `closest('label')`（结构，不耦合）→ `label[for=id]`（降为最后手段）
  ② `region`：**landmark 优先于 class 启发式** —— 先扫完整条链找 header/nav/footer/aside/main，
     都没有才回落到 class 派生的 `hero`。这样有 landmark 的页面（真实页面的常态）region 全结构；
     只有「无 landmark 祖先」的页面才留 class 派生 —— 那条残留写进注释
  ③ **扩展 e2e**：改名 JS 要**连祖先 class 一起改**，并**打断 `for=`/`id` 配对**（不注入 aria-label 拐杖），
     断言 `actionable` 仍为 false。**若扩展后仍翻转 → 把那个字段从身份键里去掉。**
  代价若错：region 的 hero 粒度变粗（agent 侧感知略降，但换免疫）；label 解析多花一次 closest。

Ruling C69: **I1 修** —— `loadPageModel` 只拒**解析不了**的 JSON；`{}`（或 `cdp navi` 自己的输出
  `{"frame":{...}}`）会静默变成全零 PageModel → 活页面上每个元素都报 appeared → **假「有进展」**、退出 0。
  加判据：`URL == "" && len(Actions)+len(Fields)+len(OptionGroups) == 0` → 拒绝（真快照一定有 URL，哪怕 about:blank）。
Ruling C70: **I3 修** —— 帧加载失败时它的元素**从模型里消失** → appeared/disappeared 非空 → `actionable=true`，
  即**观测不全被算成「页面变了」，且偏向假进展**。而 `diagnostics` 在 `PageModel` 里有、`Diff` 里没有。
  `Diff` 目前**没有任何消费者**（T8 已挪计划二），所以现在加字段最便宜 —— **把 diagnostics 的条数/存在带进 Diff，
  并在人名输出里显示**。
Ruling C71: **I2 按「诚实限定 + 记规格缺口」处理，不改判据**：
  `PageModel` **根本没有字段值**（Field 只有 Label/Type/Placeholder），所以 `diff` **原理上判不了**
  「填进去了没有 / 勾上了没有 / 选了没有 / 只是高亮了」→ 这些步骤会报 `actionable=false` → **py 重试 → 重复提交**
  （正是记忆里「重复开火把菜单点回」那类）。真正的修法是**给模型加字段值**（契约变更）。
  → **本轮只改帮助文字**，把 `diff` 的能力范围说准（「判导航/组成变化，不判填写与选择」）；
  → **并记成 R20**：`PageModel` 缺字段值 = 规格级缺口，计划二补（Console 上运营也正要看这个）。
  代价若错：真站上「填完没推进」会被误判为重试。⚠️ 这条是**具名的、会咬人的**残留。
Ruling C72: M1（`--json` 帮助文字与实现矛盾 —— 正是「看起来能切格式」那类）+ M2（选项组键用可打印 `|`
  拼接，`["a|b"]` 与 `["a","b"]` 会塌成同一个键 → 静默漏报 · 其余地方都用 NUL 分隔）→ **一并修**（都便宜）。
M3–M8 记此备终审。

Task 7 修复轮 1: DONE → commit 597ffd9
  internal 81 / cmd 43 / ./... **124 PASS / 0 SKIP / 0 FAIL**
  C1 两侧都修（**没走到「整个字段删掉」**，取的是「只保留抗改名的那一半」）：
    观察者侧：`region()` 两趟扫（landmark + role=dialog 优先，class 派生 hero 兜底，残留写进注释）；
              `label` 重排为 aria-label → closest('label') → label[for=id]（最后手段）
    消费者侧：动作键走 `regionIdentity`（只认 landmark 那一半）；字段键去掉 `Label`
    代价（写进注释）：landmark↔hero 搬家看不见；无标签同型输入框塌成一个
  e2e 扩成**正控驱动**：整树 class 全换 + 打断 for/id（不补 aria-label），并断言那两个派生字段**真的变了**。
    真页面实测：region hero→body、Label 'ZIP Code'→''、6 选择器交集 0，而 **actionable=false** ✓
  变异：身份键换回原始 Region → e2e 红（disappeared=["#schedule-now"]）；
        字段键加回 Label → e2e 红（#zip 翻转）。**两条都只在扩展夹具下现形**
  I1 已修（`{}`/navi 输出 → exit 1，且反向用例保证空白页快照仍收下）
  I3 已带条数 + 人话警告，**真页面实测**「子帧被摘掉 → actionable=true + 7 disappeared + ⚠ 观测不全」
  I2 只改文字并注明 R20；M1 修了 diff 的 --json 文字（**顺带修了 observe 里一字不差的同一句**）；M2 改用 NUL 分段

Ruling C73: 接受它顺带修 `observe` 里那句同文（M1 是同一句复制了两处）—— 修一处留一处才是错的。
  代价若错：无（一行帮助文字）。
Ruling C74（**它自曝的操作失误**）：收尾用了过宽通配 `rm -rf /tmp/*.json`。
  已核实：报告与 SDD 目录不受影响；`.txt` 未波及（通配只匹配 .json）；
  但 `/tmp` 下 dev 属主的 .json 临时文件（含 09-14 实验的 payload_*.json）**可能已被清掉**。
  它主动记下「以免被当成『本来就没有』」—— 这个处置对：**失误要留痕，别让它变成未来的错误前提**。
  代价若错：重跑 09-14 那几个实验时 payload 要重建（desc_*.txt 还在，可重建）。

Task 7 修复轮 1 复审: **all findings addressed（C1/I1/I2/I3/M1/M2），零新增破坏**
  C1 评为「**耦合是被关掉了，不是被挪走了**」：第一趟扫 landmark 会短路 → className 永远造不出
    landmark 值；第二趟只能产出 hero/body，而这两个**都映射到同一个桶** → 任何 class 改名只在桶内移动。
  `Actionable` 完整追了一遍：URL / PageText / Text / Role / Type / Placeholder / 选项文本
    —— **没有一个输入依赖 id/class/for/name** ✓
  我问的「是不是用一种谎换另一种谎」它**构造出来量了**：不是。字段出现/消失仍改变多重集**计数**；
    盲区只有「同数量交换且 (Type,Placeholder) 相同」，多数还会被 TextChanged 接住。
    且「去掉字段只让两个键更容易相等 → 严格偏向『没推进』，再也不可能有假『有进展』」。
    **重复提交的风险也不直接可达**（提交按钮是 action，出现/消失会被计数）✓
  I1/I2/I3/M1/M2 全部双向测过；无测试被删/削弱、无阈值改动。

Ruling C75（**我的流程失误，记下**）：给复审的 diff 文件是**修复轮之前**生成的
  （`review-f501c3f..259f746.diff`），我**没为重审范围重新生成** `259f746..597ffd9`。
  复审方自己在工作树上复核了（mtime 对得上），但这违反规程「绝不在没有 diff 文件时派审查」。
  规程原话：审查方**不该**被迫去读工作树 —— 那会随实现者的后续改动漂移。
  处置：收尾时我把这条计入「我自己的失误清单」。代价若错：复审证据的基线不够硬。
  另：它声明「124 PASS」它**未独立验证**（只读任务、且套件会碰共享 9222）—— 如实。

Ruling C76（**遗留，交终审与计划二**）：
  · Q1(a) `role="dialog"` 是 landmark 名单里唯一的非标签名项；它是 ARIA 作者写的、不是 hash 生成的，
    但代码注释说它「不是生成物」**比可证的更强**
  · Q1(b) landmark 那一半对**固定的祖先链**才是结构的 —— portal/teleport 在重渲染中途把子树
    抬出 `<main>` 会翻 main→body 并被读成「有进展」。这是**拓扑假设**，比 C1 那类属性改名的风险弱得多，
    但要在计划二里拿真用 portal 的站量一次
  · Q1(c) `diff.go:189-190` 注释小不准（新增的**派生**区域值落进默认桶是安全的；真正的漂移风险是
    **新增的派生分支返回了六个 landmark 字符串之一**）—— 结论「两处保持同步」是对的
  · 小瑕：e2e 的契约字段清单不含两个新的 `diagnostics_*` 键（线名只由 struct tag 钉住）
  · **C67 仍未做**：README/CLAUDE.md 的子命令表连 observe 都没有 —— 归终审修复波

Task 7: complete (commits f501c3f..597ffd9, review clean)
★ **计划一的九个任务（T1–T7）全部完成。**

## 终审（reviewer: **opus**，审整条分支 3bd4329..597ffd9）

结论：**Needs a small fix wave —— 没有一条是正确性修复**。分支确实交付了计划声称的东西。
★ 跨任务：**没有重要契约被静默解除钉住**（它专门找过）—— `frame_path` 双腿都钉着、
  空列表归一**按原始 JSON 字节**钉着；唯一真缺口是 `Diagnostic.FramePath`，而它**不可达，不是错**。
★ **RAND 它独立重推导过**：7 种随机形态全抓、15 个正常 token 全放，`step2a/address1a/opt2b` 被抓
  —— 与 C59 声明的代价逐条相符。
★ D2 成立：`internal/` 不 import cobra、不写 stdout/stderr；两条子命令都是「解析 flag → 调内核 → 渲染」。

Ruling C77（**Important #1**）：**D11 的感知面零端到端断言**。`Action.Role/Tag/Visible/BBox/
  AboveFold/RelativeSize/PeerCount/ZIndex/Contrast/NearbyText` 与 `Field.Type/Required/Hint`
  在全套件里的断言数**每个都是 0**。JS→Go 绑定**按字符串键** → 任何一个键写错，字段静默变零值、
  124 条测试全绿。而 **`bbox` 是 R17 的载重字段**（截图与框对不上就没法「框选」）。
  → **修复波必修**：在 `TestObserveLightDOM` 加逐动作不变量（bbox 非零且在视口内、
  hero 按钮的 nearby_text 非空、header 链接的 above_fold 为真）。与 `requireShadowActions` 同族。
Ruling C78（**Important #2**）：Dockerfile `chmod +x A B && chmod +x entrypoint.sh 2>/dev/null || true`
  —— **A 失败则 B 永不执行**，`|| true` 只救退出码；而 `entrypoint.sh` 在 git 里是 100644
  → ENTRYPOINT 会 Permission denied(126)。终审在 /tmp 复现了 shell 语义。
  今天影响为零（`agent/` 为空，容器本也跑不起来），但**标记告诉恢复者的与真相相反**。
  → 修复波：拆开两条 chmod + 改正标记文字 + 补一句「镜像可构建但**暂不可运行**（等计划二的 agent/）」。
Ruling C79（**Important #3，是规格自己错**）：§4.3 曾写 `env`/`platform`/`maps_to`，代码里都没有。
  而 `platform.guess+confidence` 与 `maps_to` 是**认知**，**D11 恰恰禁止 observe 输出认知**；
  `env` 已由 §5.3 分配给 py 的 PROVENANCE（生成时写）。
  → **我已改规格**（不是加字段），并顺带记下 §4.4 的 `high` 那半条「或文本全局唯一」**至今未实现**
  （影响低，§5.1b 的 near/text 阶梯覆盖；但别留一句代码不认的规格）。
Ruling C80（**更正 C50**）：终审指出我把那条债记重了 —— `client.go:260` 返回 `ft, nil` 确实会让
  `ObserveAll` 退化成只剩同源帧，**但不是静默**：跨源 iframe 的**元素**仍在父帧 DOM 里，
  `checkFrameCoverage` 会为该帧喊 `frame-blind`。
  → 重新定性为「**内容缺失但已声明**」，不是「跨源帧静默消失」。计划二排优先级时按这个来。

终审修复波: DONE → commit 21017f2（10 文件 +382/−31；在控制器的 5dc0f47 之上）
  125 PASS / 0 SKIP / 0 FAIL；go vet 干净。7 条全部完成，**一行行为代码未动**
  （唯一例外是第 2 项那个 Dockerfile 缺陷本身）。

Ruling C81（**波中发现的新既有缺陷，留用户定**）：`cmd/root.go` 的 `PersistentPreRun` 判的是
  子命令 flagset → `Changed()` **恒 false** → **env 覆盖显式 `--port`**。
  实测：`CDP_PORT=<死端口> cdp --port <活端口> targets` 报**死端口**的连接错。
  后果：设了 `CDP_PORT` 的 shell 里跑 `go test ./...`，`cmd/` 的 e2e **静默打到别的浏览器上**
  （它第一轮 baseline 因此红过一条，而 `./cmd/` 单跑全绿）。
  **「显式 flag 被 env 覆盖」正是本项目那类病（flag 在说谎）。** 继承自 cdpcli，非本分支引入。
  → 按规程**终审后没有第二个修复波**，所以**不修，记成具名缺陷交用户**。
  它已把实测真相写进 `tools/cdp/CLAUDE.md` 并标注「与代码意图相反」。
  代价若错：有人 shell 里设了 CDP_PORT 时，`--port` 静默失效；测试会打到别的浏览器。

Ruling C82: 接受它**纠正了终审给的一条错断言** —— 「每条 action 的 bbox 在视口内」
  实测**不是正确不变量**（`#foot-learn` y=446 > 视口 437，折线下的元素本来就合法）。
  换成等价强度且环境无关的：非零/非负/在文档内 + **above_fold 与 bbox 自洽**。
  **这是「不照抄一个看起来合理的断言、而是实测它、再给同等强度的替代」** —— 正是本项目要的纪律。
  代价若错：无。
Ruling C83: `gofmt -l` 在 HEAD 上本就列 6 个文件（继承自 cdpcli，与 `3f70131` 同类），本波未碰 ✓
