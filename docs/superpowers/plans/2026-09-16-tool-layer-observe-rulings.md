# 计划一执行期间的裁定清单

2026-09-16。执行 `2026-09-16-tool-layer-observe.md` 期间，控制方替用户做的**全部 86 条决定**，
按时间顺序，每条附**代价若错是什么**。

> 这些决定原本记在 `.superpowers/sdd/` 的 ledger 里，而那个目录按规程在收尾时会被删除。
> 裁定不属于 git 历史（它们是执行期的判断，不是代码），所以单独落成这份文档。

**读法**：挑你不认同的翻。「代价若错」那一行就是我当时认为的最坏后果。

---

**1.** 在 /company/siteforge 的 master 上原地实施，不建 worktree — 本会话被 配置为原地工作（harness 明确要求 skip worktree），且该仓库是新建的本地仓库、 无 remote、目前只有文档。
  ↳ 代价若错：若用户其实想要分支隔离，需要 rebase。

**2.** 宿主 go 在 /usr/local/go/bin 不在 PATH — 每次 dispatch 注入 export PATH=/usr/local/go/bin:$PATH。
  ↳ 代价若错：无（纯环境操作）。

**3.** Task 3 的 TestObserveCrossOriginFrameMerge 移到 Task 4。 计划让 T3 「用 t.Skip 标住」跨源那条 —— **但 Skip 救不了编译错误**： 引用未定义的 c.ObserveAll() 会让整个 internal 包编不过，T3 想跑的三条 单帧测试也一起跑不了。跨源测试本来就是 T4 交付物的验收，归 T4 更合理。
  ↳ 代价若错：T3 的测试文件少一条，T4 补上即可。

**4.** Task 7 的 cmd/diff.go 按 Task 6 同形补全（flags 测试 + 完整代码）， 代码随 dispatch 下发。计划里那一步只写了「加 CLI」没有代码没有测试， 违反计划的 No Placeholders 自查。
  ↳ 代价若错：多写十几行胶水代码。

**5.** Task 4 的跨源测试**动态生成** outer 页（端口取自 httptest 的 srv.URL）。 计划里的 fixtures/outer.html 硬编码 src="http://localhost:8892/inner.html"， 而 httptest.NewServer 用随机端口 → iframe 必然加载不到，测试会以 「没有子帧」的形式假失败（而不是报错），最难查。
  ↳ 代价若错：测试写法稍繁。

**6.** Task 9 只做 Step 1/4/5（查硬编码点、容器构建验证）。 **Step 2/3 改生产仓库 common.py 被 parked** —— 那是本工作区之外的副作用， 且改的是正在跑生产订单的那份代码，属四个停止条件之一。 等用户明确批准再动。
  ↳ 代价若错：债务清理晚一轮，无功能影响。

**7.** Task 1..7 期间 Dockerfile 不含 cdp-mcp 构建行（计划已明说）—— 接受过渡态，T8 补回。
  ↳ 代价若错：无，Dockerfile 在此期间本就不该能构建全。

**8.** Task 8 若 proxy.golang.org 拉不到 MCP SDK，换 goproxy.cn。
  ↳ 代价若错：无。

**9.** Task 4 的计划文本写错了帧树类型。计划里用的是自造的 `FrameNode` （`f.ID` / `f.Children`），而 HEAD 里 `GetFrameTree()` 返回的是 cdproto 的 `*page.FrameTree`（字段是 `.Frame`（`*page.Frame`，含 `.ID`/`.URL`）与 `.ChildFrames []*page.FrameTree`）。**不要新建 FrameNode 类型** —— 直接遍历 cdproto 的结构，少一层无谓的转换。Task 4 派发时带上这条。
  ↳ 代价若错：多写一个冗余类型定义。 已核实的依赖（HEAD 均在）： internal/client.go:116 func (c *Client) GetFrameTree() (*page.FrameTree, error) internal/client.go:132 func (c *Client) GetFrameTreeWithEvents(wait time.Duration) internal/shadow_integration_test.go:22 func shadowTestEndpoint() (string, int) internal/shadow.go:55 func withPierce(js string) string internal/shadow.go:48 var __cdpQA

**10.** Ruling C8 (settled): proxy.golang.org **不可达**（实测 dial tcp i/o timeout）。 goproxy.cn 可用 —— MCP SDK 已实测拉取并构建通过。 **每个 Go dispatch 都要带**：export PATH=/usr/local/go/bin:$PATH 以及 GOPROXY=https://goproxy.cn,direct（拉新依赖时必需）。 cdpcli 既有依赖已在 /home/dev/go/pkg/mod 缓存里，Task 1 无需网络。 Dockerfile 里我原本就写了 ENV GOPROXY=https://goproxy.cn,direct —— 已对。
  ↳ 代价若错：无。

**11.** Ruling C10 (Task 2 的换装口径): 探针 docs/probes/2026-09-16-observe-r3/observe.js 自带一套自实现的穿透（顶部 `var RS=[]` 递归 walk + `function qsa(sel)`）。 迁进 internal/observe.go 时**两处都要换成内核助手**（pierceJS 暴露的正是 这两个，一一对应，不是重写）： qsa(sel) → __cdpQA(sel) （选择器查询，全 root 合并） RS → __cdpRoots(document) （root 列表，page_text 逐 root 收集要用） **不要再自己 walk 一遍 shadow root** —— 那正是 shadow.go 注释警告的 「不必各自重写一套」，也是 Global Constraints 里那条。 陷阱测试只断言了 `__cdpQA` 出现，抓不到重复的 roots walk，所以这条靠 dispatch 交代。
  ↳ 代价若错：多一份重复遍历，行为不变但留了坏样板。 另：`composedAncestors`（三条陷阱里的合成树祖先链）探针里是自带的， 内核没有 —— 这个**保留自实现**，它不属于「穿透查询」那一类。

**12.** Ruling C11 (Task 2 的调试姿势): HEAD 里 `EvalInFrame`（internal/client.go:262） **不注入穿透** —— 它直接 `runtime.Evaluate(js)`，原样下发。withPierce 只出现在 client.go 的 485/527/549 三处动作助手（GetElementCenter / IsElementVisible / ScrollIntoView）和 form.go 的 8 个选择器入口。 两个后果，都要交代给 Task 2 的实现者： 1. `observeJS()` **必须自己套 withPierce**（计划的骨架里已经这么写了 ✓）。 重复注入是幂等的（shadow.go 注释：「重复求值只是重新定义一次」），无害。 2. **不能用 `cdp eval` 调试 observe 的 JS** —— 那条路没有注入，`__cdpQA` 会是 `ReferenceError: __cdpQA is not defined`。要调试只能走 `internal.Client.Observe()`（或写个临时 Go 测试）。 不知道这条会浪费很多时间去怀疑「助手名写错了」。 这条也正是根因 R2（cdp eval 不注入）在 Go 层的同一个形状： 注入了的只有那几个动作助手，通用求值入口一直没有。
  ↳ 代价若错：无（纯交代）。

**13.** tools/cdp/.claude/settings.json 保留但从 cdpcli 的语境里剥出来 —— 内容只是 开了 gopls-lsp 等 4 个插件，对 Go 工作有用；但它属于 cdpcli 的开发配置， siteforge 应当**自己决定**要不要。实施时在 siteforge 根放一份自己的 .claude/， 而不是让它随代码树漂进来。
  ↳ 代价若错：无。

**14.** gobwas/ws 的 // indirect 在迁移落地后要跑 go mod tidy 修正。
  ↳ 代价若错：无。 ★ 停下等用户定 R7（这是四个停止条件之一：工作区之外的副作用）★

**15.** Ruling R7 (用户裁决，方案 B): 先把 WIP 落到 cdpcli 的一个分支再迁。 执行：/company/cdpcli 新建分支 `wip/oopif-iframe-ax`，commit `780fa90`。 提交内容 = 7 个已跟踪改动文件 + 4 个此前游离在 git 外的测试文件。 **未提交 .mcp.json** —— 那是开发者本地的 chrome-devtools-mcp 客户端配置， 进仓库会把「用哪个 MCP server」强加给所有人。 master 未动（仍 eddee5f）、未推送（用户定的规矩）。 commit message 里如实写了「这是在制品、非完成态」+ 已知未完成项 （gobwas/ws 未 tidy、未跑全量测试）—— 不粉饰。
  ↳ 代价若错：那份 WIP 的作者若本不想让它进 git，需要 revert 这个分支（本地，无成本）。

**16.** 计划 Task 9 指向了错的文件。站点 py 脚本 import 的是 **forms/common.py**， 不是 form_executor/common.py： forms/common.py:14 CDP_PATH = "/opt/skills/auto-farm-skill/cdp"（硬编码、无 env） form_executor/common.py:14 CDP_PATH = os.environ.get("CDP_PATH", "/company/cdpcli/cdp") **不阻塞产物运行** —— 产物跑在容器里，而该硬编码路径在容器里存在（md5 1df52111298f）。 但 Task 9 若要「收掉 cdp 路径硬编码」，真正的目标文件是 forms/common.py。
  ↳ 代价若错：Task 9 改了个对产物无关的文件，硬编码依旧。

**17.** **计划二必须补一步「加路由表」** —— ad-task.py:260+ 那张 `(["域名"], "forms/sites/<name>.py", "<slug>")` 表，不加的话产物永远调不到。 siteforge 规格与计划一/二都没覆盖这一步。
  ↳ 代价若错：产出的 py 在容器里躺着没人调。

**18.** 上线仍是人工（docker cp 到 /opt/skills/auto-farm-skill/forms/sites/）—— 与规格 §11「不做自动上线」一致，非缺口，但要在计划二的验收清单里写明。
  ↳ 代价若错：（未记 —— 这是缺陷）

**19.** Ruling C16 (修订 — 用户纠正): 上线**不是**长期人工 docker cp。目标是闭环 「产物 → 后台配置 → 系统读取」，用户明确说「这是后面要做的闭环」。 我原来记窄了。 现状核实（2026-09-16）： - py 站点脚本**没有任何后台下发通道** —— ad-task.py:2597 `site_script = _match_site_route(current_url, log)` 是**本地 63 行路由表** - 只有 **JSON config** 有通道：ad-task.py:1337 `http://192.168.1.51:6060/api/quest/formConfig?site=<key>` - `fmr.3tkj.cn` 的路由全是任务与上报（task_apply / formMessage / formLog / screenshot / task/switch / siteLimit），**不下发脚本** - ⚠️ 已知分叉（记忆 2026-09-12 标记为硬阻塞，本次未复验）： **ad-task 读本地 6060，运营后台写 fmr.3tkj.cn** —— 两边不通 对 siteforge 的含义（影响计划二，不是现在做）： 1. 产物需要一个**注册/身份**概念，不只是一个文件路径 —— 域名 → 脚本标识 → 版本，否则后台无从配、worker 无从读 2. 「注册」要和文件一起产出，而不是事后再补一步（C15 的路由表就是这个缺口的 最小形态：现在是本地表，将来是后台一条记录） 3. JSON config 那条路是这个闭环的**现成先例，也是现成的坑**（6060/fmr 分叉） —— siteforge 设计注册机制时应当先解掉那个分叉，否则新通道会重复它 4. 与规格 §11「不做自动上线」的关系：§11 说的是**第一版**不做，长期目标是闭环。 计划二的验收清单应写明：第一版人工，注册信息按将来能迁到后台的形态产出。
  ↳ 代价若错：计划二把「注册」当补丁而非一等公民，将来接后台要返工。

**20.** **现在就修那条红 spec（Task 1b），不记为欠债。** 理由：siteforge 立项的理由就是消灭脏基线（规格 §1.2）。第一天带红测试进去， 后面每个任务的「全绿」信号都被污染，终审还要额外 triage。诊断已完成 （该 spec 用 NewClient 取活动页、自己不导航 → 整包跑时被别的测试留下的页面 污染），修法明确：让它自带导航到已知 fixture 页，自成隔离。 实现者的排除工作已足够支撑这个判断（单独跑过/整包失败、与来源分支逐字节同、 真实页面上 CLI 实跑嵌套帧正确）。
  ↳ 代价若错：小绕路；但它换来的是后续 8 个任务可信的「全绿」。

**21.** 实现者指出我的指令第 3 步期望值写错了（`grep -ac evalInOOPIFFrame` 应为 1，实测 3 —— 调用点+注释+定义各一行）。**它没改代码去迁就错误期望，是对的。** 检查意图（OOPIF 能力确实迁进来了）已满足。记此以正视听。
  ↳ 代价若错：无。

**22.** Task 8 要恢复的 Dockerfile 位置是 **4 处**，不是 2 处 —— stage 1 的 cdp-mcp 构建行、stage 2 的 `COPY --from=tools /out/cdp-mcp`、 以及 `chmod +x /usr/local/bin/cdp-mcp` 与相关注释。 实现者指出「COPY 源缺失会让 build 无条件失败」，故 stage 2 也必须连带注释 —— 这是对 C7 的正确扩展。Task 8 派发时必须带上这 4 处的完整清单（在报告里）。
  ↳ 代价若错：Task 8 漏恢复一处，镜像构建失败（会被立刻发现，非静默）。

**23.** **授权修那一行。** 三条理由：① 影子验证证明测试一直是对的 （只改代码、测试不动就绿）；② `Name` 空会让任何「按名字认帧」的调用方失效， Task 2 `observe` / Task 4 `ObserveAll` 正好踩；③ 它是偶发竞态，不修就留个 随机红的套件。
  ↳ 代价若错：改了采纳代码的一行，但已被影子验证覆盖。

**24.** **不在 cdpcli 里修同一个 bug**（不扩大范围），但要**记一笔待回灌上游** —— 该 bug 在 `wip/oopif-iframe-ax` 分支上同样存在。实现者已在报告里写了。
  ↳ 代价若错：上游带着一个已知 bug 长期存在（已记录，不会丢）。

**25.** Task 1b 的验收是 **53/0/0**（PASS/SKIP/FAIL），**SKIP 必须为 0** —— cdpcli 集成测试在无浏览器时走 t.Skipf，跳过和通过长得一样。 这条是我给的验收标准，不是实现者提的。
  ↳ 代价若错：（未记 —— 这是缺陷）

**26.** 改规格 §11 —— 原写「不做 farmer 的 failures 接口本身（是前置依赖，另立项）」 **是过时结论**（接口 2026-09-10 已建）。「修」入口**不要重复建取证层**，接 fail-script 现成的。
  ↳ 代价若错：若那接口实际不可用（401 需 token，token 未验），修入口要补鉴权 —— 届时再议。

**27.** 「修」的 `explore` 应以**旧 py 的行走路线为起点**（拿旧路线在当前页面跑一遍， 看哪一步开始对不上），不是白纸探索。这是新站没有的优势。已写进 §6.1。
  ↳ 代价若错：无。 用户问：「后续能不能也把 json 修复的加进去，麻烦话就后续再完善」

**28.** 加，放 Phase 2（第三个入口）。核实：产线 py 63 + **json-configs 69**， 而 JSON 修复分三层，**第一层已存在** —— form_executor/auto_fixer.py 约 10 条 确定性规则（_fix_field_types/_fix_field_placeholder/_fix_button_eval/_remove_form_id/ _fix_success/_fix_loop_until/_fix_missing_wait…），0 LLM。 siteforge 要补的是**第 2 层（选择器失效）**，而它**不需要 agent 写代码** —— 只要 observe/diff 找新选择器 + 改 JSON 一个字段 + 跑一遍验证。 比产 py 便宜：产物是数据（schema 校验即可，不用 lint）、改动局部（人审容易）、 旧 JSON 在手、auto_fixer 已做掉机械那半。 升级判据复用 §1.1 已有边界：需要 JSON 表达不了的原语（分支/跨轮状态/换策略）→ 升 py。
  ↳ 代价若错：Phase 2 的两个入口共用 observe/diff，若 JSON 那路实际需要额外能力， 在 Phase 2 开工时再评估。已写进 §6.1 + §13.1。

**29.** Ruling C27（含一处**我自己的更正**）：JSON 修复在 siteforge 侧，但要与 auto_fixer 划清关系。 更正：我上一条说 auto_fixer 是「执行期」—— **错了**。核实 form_executor/json_pipeline.py:286： 它在 `json.loads(content)`（LLM 刚生成完）之后、Step 2 浏览器验证之前调用， 是**产出期的生成物清洗器**，不是运行时修复器。 故两者是**串行**：产出 JSON → auto_fixer（纯规则）→ siteforge 补的那层 → 验证 → 人审 → 上线。 siteforge **不要重写 auto_fixer 的规则**，只补「规则判不了、必须看着页面才知道」的那层。 另一条闭环：人审通过的修法反复出现后**沉淀成 auto_fixer 的新规则** —— 确定性规则是 人工纠正的终点（不要钱、不会自信地错，呼应 R0/§6.5）。已写进 §6.1。
  ↳ 代价若错：无。

**30.** Ruling C28（**我自己的记账错误，更正**）：我先前写「验收二 go test ./... → PASS 53 / SKIP 0 / FAIL 0」 并据此判定「SKIP 为 0，不是假绿」。**那个判断只对了一半。** 审查方抓到：internal/close_integration_test.go 与 targets_integration_test.go 都以 `//go:build integration` 开头 → `go test ./...` **根本不编译它们**（排除 4 条： TestCloseTarget / TestCloseNonExistentTarget / TestListPageTargets / TestListPageTargetsWithActive）。 算术是铁的：57 个顶层 Test（cmd 13 + internal 44）− 4 = **53**，正好等于报告的 PASS 数。 **教训：build tag 排除 ≠ skip —— `go test` 的 SKIP 计数看不见不编译的测试。**
  ↳ 代价若错：后续任务继续把「53 绿」当成全量覆盖的信号。

**31.** 修复轮 1 三项 —— ① 更正报告 §R8.5 的不实陈述 + 跑 `-tags integration` 量出真实覆盖； ② 补注释 Dockerfile:66-67 与 :76（C7 的另两处，恢复清单从 4 处变 **6 处**）； ③ gofmt 修 `client.go:325-328` 的缩进回归（`3e6ded5` 引入，非继承）。
  ↳ 代价若错：无（三项都是小改）。 Minor（记此备终审 triage，不进修复轮）： m2 测试套件与 localhost:8080 dev server 耦合 —— 10 条测试丢弃 Navigate 错误， 在没那个 server 的机器上 FAIL 而非 skip；SKIP:0 还依赖 Chrome 至少有一个开着的页面 m3 Brief Step 4 的 tools/cdp/internal/testdata/ 最终没落进仓库（fixture 仍在 docs/probes/ 下） m4 tools/cdp/docs/ 带进 28 个文件（~6.5k 行），3 个含原作者机器路径（/home/ansible/…） m5 tools/cdp/.dockerignore 惰性（构建上下文是仓库根）

**32.** Ruling C30（控制方主动核查，非审查方提出）：cdpcli 集成测试的外部依赖已查清。 `internal/` 里 10 条测试导航到 `http://localhost:8080/{carwarranty,mui-datepicker,react-select}`， 服务方是 **/company/mock-server（另一个仓库）**，cdpcli 自己没有 fixture server。 这些测试**丢弃 Navigate 的错误**，所以在没跑 mock-server 的机器上会 **FAIL 而非 skip** → **`go test ./...` 的「全绿」不可从 git 复现** —— 对一个以「消灭脏基线」立项的项目， 这是必须显式化的依赖，不能靠"这台机器上恰好有"。 处置（不扩大 Task 1 范围）： - **Task 3/4 的新测试必须自带 fixture** —— 计划里 Task 3 已用 `httptest.NewServer(http.FileServer(http.Dir("testdata")))` ✓ 干净，保持。 Task 3 派发时要写明：**不得新增对 localhost:8080 的依赖**。 - **Task 9（债务清理）加两项**： ① 把「集成测试需要 /company/mock-server 跑在 :8080」写进 `tools/cdp/README.md` （或让它 skip 而非 FAIL —— 二选一，派发时定） ② 把 R3 的四档 fixture 从 `docs/probes/…/fixtures/` 落进 `tools/cdp/internal/testdata/` （reviewer Minor #3；Task 4 的跨源回归测试要用它）
  ↳ 代价若错：Task 9 多两项小活；不做的代价是每个任务的「全绿」都只在本机成立。

**33.** Ruling C31（**实现者第二次纠正我**）：我说「gofmt -l 那 6 个文件都是缺尾换行」—— **错**。 实测 cmd/snapshot.go、internal/form.go、internal/form_test.go、internal/snapshot_bdd_test.go **有**尾换行（后者 6 个 hunk 是 import 顺序/单行 if 拆行），只有 cmd/eval.go、cmd/root.go 真缺。 所以我引的「snapshot_bdd_test.go 仍缺尾换行 → 反证测试没动」**不成立**； 测试确实一行没动，是靠**逐字节同一性**证明的。结论对、证据错，两条分开记。
  ↳ 代价若错：无（已更正）。

**34.** Ruling C32（控制方主动核查，为 Task 2 去风险）：Task 2 的 `Observe()` 取值路径已验通。 `EvalInFrame`（internal/client.go:440）末尾 `json.Unmarshal(remoteObj.Value, result)` —— JS 返回字符串时 `remoteObj.Value` 是**带引号的 JSON 编码串**，解进 Go `string` 会自动去引号， 所以 `var raw string; EvalInFrame(...); json.Unmarshal([]byte(raw), &m)` **成立** ✓ 顺带解释了今天反复见到的双重转义：`cmd/eval.go` 解进 `json.RawMessage`（原样字节、保留引号）， 所以 CLI 输出是带引号的字符串，要解两次。 另看清跨源帧机制：chromedp 路径先试，**任何错误**都回退 `evalInOOPIFFrame`（裸 websocket）。 Task 2 派发时带上这条，省得实现者怀疑取值方式。
  ↳ 代价若错：无。

**35.** Ruling C33（控制方自查计划弱点，提前定调）：计划 Task 2 的三条「陷阱测试」是**字符串断言** （`observeJS()` 里必须/不得出现某些子串），因此**可被一段注释糊过去** —— 写个 stub 加一行 `// 我们用 __cdpQA，不自己 walk，用 .children 收文本，composedAncestors 处理祖先链` 就能让四条全绿。 裁定：**接受这个弱点，不加强 Task 2 的测试**。理由： - Task 2 是纯单元层，没有浏览器；行为验证在 Task 3（真浏览器四档） - **Task 3 的三条正是按陷阱写的**：TestObserveShadowPageTextIsNotEmpty / TestObserveShadowElementsNotFalselyOccluded / TestObserveShadowRegionNotAllBody —— 那才是真闸门 - 若 Task 2 的字符串测试绿而 Task 3 的行为测试红，说明陷阱没真实现 —— **这个组合本身就是诊断信号**，派 Task 3 时要写明这一点
  ↳ 代价若错：Task 2 通过但实际没做到，被 Task 3 拦下（可接受，不会漏到下游）。

**36.** Ruling C34（**修订 C33**）：C33 说「接受陷阱测试可被糊过去这个弱点」—— 那是在**没有证据**时说的。 现在有变异实证：**四条里有两条是空转的**。修订为：**收口这两条**。 实现者给了最小改法：把断言从「整个脚本含 composedAncestors」改为 **「`occludedBy` 函数体内含它」**（同理 region 那条断在 region 函数体内）。 这把它从「可能空转」变成「真的在测」。
  ↳ 代价若错：测试措辞更脆（依赖函数名），但换来的是它真能失败。 这正合 R0 的缓解方向：**让错误有办法被发现** —— 变异测试就是最好的自查工具。

**37.** 修复轮同时把 **R3 四档 fixture 搬进 `tools/cdp/internal/testdata/`**。 理由：Task 3 的 brief 写着「Task 1 Step 4 已搬入」，**那是错的** —— fixture 只在 `docs/probes/2026-09-16-observe-r3/fixtures/`，`git log --all` 里没有。 不搬的话 Task 3 **会 FAIL 而不是 skip**（它的 httptest 以 testdata 为根）。 一条 `cp` + 提交即可。这也兑现了 C30 与 reviewer Minor#3。 已知限制（记此备 skill 能力边界与终审，不进本轮修复）： L1 容器内查询不穿嵌套 shadow：选项组内的 querySelectorAll、`closest('label')`、 遮挡物内找按钮 —— 都是「少收」不是静默出错。R3 探针在嵌套 shadow 页上仍拿到 option_groups=1，说明常见情况够用；更深嵌套会漏。 L2 `contrastBand` 的背景回退走 `parentElement`，shadow 里常得空值 —— 契约里这个字段会常为空。 L3 `actions[].visible` 恒为 true（只从可见集筛出），信息量为零 —— 要么去掉，要么在 skill 能力边界里写明「observe 只返回可见元素」。
  ↳ 代价若错：（未记 —— 这是缺陷）

**38.** Ruling C36（用户 2026-09-16 定）：**运营入口归 siteforge 自己**（D14），不长在 /sites 也不长在 farmer。 理由：/sites 在 auto-llm-script 容器里，而 §8.2 已标它「后续单独决定是否退役」—— 往待退役的东西上加功能 = 往错的基线上改；farmer 是正式后台，改动面大。 siteforge 骨架已预留：Dockerfile 装 fastapi/uvicorn，entrypoint.sh 起 uvicorn agent.service:app。 **取数层不重写** —— /sites 的 triage 本就是 import fail-script 的 （diagnosis.datasource.HttpSource + diagnosis.triage），siteforge 照样 import。 §6.1 与 §13.1 已同步：Phase 1 = 最小页面 + 两个 CLI 作为底层（不再是「不做 UI」）。
  ↳ 代价若错：Phase 1 多一个页面的工作量；但换来运营真能用，且不碰待退役的容器。

**39.** Ruling C37（控制方建议，待用户定）：计划一 T3–T7 完成后 **`observe`/`diff` 即可用的 CLI**， T8（MCP）要等 agent 接入才需要、T9 是收债。建议 **T7 后停下验最短路径** （拿一个真挂的站手工串一遍 observe→产 py→跑），而不是把 T8/T9 做完再说。 理由：用户验的是「运营能不能用」，不是「工具全不全」。 未获批准前按原计划继续。
  ↳ 代价若错：（未记 —— 这是缺陷）

**40.** Ruling C37（**用户已批准**）：计划一做到 **T7 停**，T8/T9 挪到计划二。 T1–T7 完成 → observe/diff 是可用的 CLI（本计划的实际交付点）。 T8(MCP) 挪计划二（agent 没接进来之前没有消费者）； T9(债务清理) 挪计划二（收的是测试依赖 mock-server、fixture 落 testdata 这类债，等真使用者出现）。 T7 之后先**验最短路径**：拿一个真挂的站 → ①fail-script 取证据 ②cdp observe 看模型对不对 ③据 observe 产 py ④跑 ⑤给人看（模型 + py + 结果）。 目的：验「运营能不能用」，不是「工具全不全」；并暴露本计划所有纸上成立的假设。 已写进计划文档的「执行顺序调整」节。
  ↳ 代价若错：T8/T9 延后，agent 接入前无法用 MCP（但那时也不需要）。

**41.** 接受它加的 `stripJSComments`。C33/C34 的病根就是「字符串断言可被注释糊过去」， M6 实测证明不剥注释则修复本身仍可被糊 —— 保留是自洽的。
  ↳ 代价若错：断言多依赖一层剥离逻辑（已由 M6 变异覆盖）。

**42.** Ruling C39（**它独立重新发现了 C3**）：fixture `outer.html` 的 iframe 是硬编码绝对 URL `http://localhost:8892/inner.html`，而 Task 3 用 httptest（随机端口）→ 子帧连不上 → Task 4 的跨源测试会红。**这正是我预检时裁定的 C3**（"Task 4 跨源测试动态生成 outer 页， 端口取自 srv.URL"）。它的出路建议（运行时改写 src / 改用 outer_same.html 相对路径） 与 C3 同向。Task 3/4 派发时必须带上这条。 它自查出的过程失误（复测时相对路径覆盖了测试文件、还原静默失败 → 一轮数据是旧文件跑的） 已从 HEAD 还原并**全部重测**，工作树与 HEAD 逐字节一致 —— 如实披露，记一笔。
  ↳ 代价若错：（未记 —— 这是缺陷）

**43.** **Important #1 要修**（不能推给 Task 3）。审查方的洞察是对的： **C10 是结构性约束，行为测试盖不住** —— 就算有人重新加回自实现的 walk，集成测试照样过。 所以那条断言是 C10 的**唯一**闸门，而它空转。这与 C34 是同一类病，不修不自洽。 修法（比审查方给的更精确，因为它的 `"function __cdpRoots"` 与源码文本不符 —— 实际是 `var __cdpRoots = function(root) {`）： · C11/注入证据：`strings.Count(js, "__cdpRoots = function") == 1` （删掉 withPierce → 0 → 红；双重注入 → 2 → 红） · C10/正文证据：断言**正文里**含 `__cdpRoots(document)` 与 `__cdpQA(`， 且正文**不含** `.shadowRoot`（自实现 walk 的特征） · 实现者须先用 mutation 验证这三条真能变红，再落地
  ↳ 代价若错：断言与内核文本耦合更紧（内核改实现则测试要跟着改）—— 但换来 C10 第一次有真闸门。

**44.** Important #2（陷阱①的 `r.innerText` 禁令因 r→rt 重命名而失效）同修。 同一类病（空转断言），而且就在同一文件。让禁令不依赖变量名。
  ↳ 代价若错：无。 Minor 3–7 记此备终审（整脚本作用域断言与内核文本耦合 / contrastBand 仍走 parentElement / JS 键名与 struct tag 无绑定 / page_text 截断无信号 / jsFuncBody 不跳注释）。

**45.** 接受它把 `observeBody` 也过 `stripJSComments` —— 与 C38 同因（不剥注释则断言可被注释糊）。 另：它自查出的「配平失败 → 静默变弱」记进 Minor 清单（终审 triage），本轮不修。
  ↳ 代价若错：（未记 —— 这是缺陷）

**46.** **批准它改 fixture（给 shadow.html 的 host 外包一层 `<main>`）。** 它报的顾虑是对的：brief 的陷阱③断言在**冻结 fixture 上不可能通过** —— `shadow.html` 整页 landmark 数 = 0，于是正确实现（走 getRootNode().host 爬出 shadow） 与错误实现（只走 parentElement）**都返回 body**，断言对两者一样红 = **空转**。 它**没动断言一个字**，只补了触发条件 —— 这是「让测试有能力分辨」， 与「改测试去迁就实现」正好相反。**批准。**
  ↳ 代价若错：testdata 副本与 docs/probes 副本不再逐字节一致（已注释说明，且探针副本未动）。

**47.** Ruling C44（**更正我自己的规格**）：查证发现 —— 探针 README 里陷阱① ② 都有实测数值 （10→141 字符、5/5 假阳性），**陷阱③ 只有一句「region 全部退化成 body」，没有数值**。 而 R3 复跑时我看到 region=['body'] 并写下「那是 fixture 没有 landmark，所以是正常的」—— **我当时注意到了却没有意识到：那句话的真正含义是这个修复从未被验证过。** ⇒ 已更正规格 §4.3 与探针 README：标明陷阱③的症状是**读代码推断**，非实测； 并加一句元教训：**症状栏没有数值 = 它没被测过。** 这是同一个家族病在我自己的文档上复发 —— 把推断当实测写。
  ↳ 代价若错：无（更正后更保守，且 T3 已用变异补齐了实测）。

**48.** **Important #1 要修** —— `observe_integration_test.go:47-49` 把**导航失败**当成 skip。 浏览器连不上时 skip 合理；但导航到「测试自己刚起的 server」失败是缺陷，不是环境缺失， 而它会把整个闸门悄悄变成四条绿 skip。改 `t.Fatalf`。 （若实测发现偶发抖动，再加一次有界重试；不许退回 skip。）
  ↳ 代价若错：极端环境下测试会红而非静默通过 —— 这正是要的。

**49.** 一并收三条 Minor，同主题（让闸门没有暗门），不单开轮次： · 注释里带着未实测的数字（"修好后是 141" / "5/5"），实测是 142 / 6/6 —— **正是 C44 刚在规格上更正过的那个病**，出现在测试自己的文档里 · 缺前置守卫（若 actions 静默取到 0 个，两条 shadow 测试都空转通过）→ 加 `len(m.Actions) < 5` 守卫 · 探针副本 `docs/probes/.../fixtures/shadow.html` 仍无 `<main>` —— 若将来有人从探针目录 重新生成 testdata，陷阱③ 会退回「永久红且空转」，而下一个人多半会去**削弱断言** （本项目反复踩的那个坑）。处置：**不动探针副本**（它是历史记录），加 `testdata/README.md` 说明差异。
  ↳ 代价若错：无（四条都是小改）。 ⚠️(d) 141 vs 142 的差：探针的 `page_text_len_raw` 是各 root innerText 长度求和， 测试量的是**拼接+归一化后**的 rune 数（join 加了分隔空格）—— 很可能是这个差。 让实现者确认后把注释写准，不要照抄 brief 的数字。

**50.** 接受 residual（不进修复轮）：`NewClient` 那条 skip 也覆盖「Chrome 活着但**页面目标数为 0**」， 那种情况仍会让四条测试变成绿 skip。判断：那是**合理的 skip**（没有页面可驱动 = 环境不可用）， 且 `-v` 下 SKIP 行是**可见的**、计数会变成 59 PASS / 4 SKIP —— 只要保持「永远看 SKIP 计数」就抓得到。 记此备终审。
  ↳ 代价若错：某次「全绿」其实是 4 条跳过的，被 SKIP 计数挡住。 ⚠️ 这条也解释了为什么前一位实现者**刻意留一个 tab 不关** —— 那不是偷懒，是防这个。

**51.** Ruling C48（**更正我自己的 C9**）：C9 说「用 cdproto 的 `GetFrameTree()`，别自造 FrameNode」—— 函数存在、类型对、编译过、跑起来不报错，**但协议调用本身不返回 OOPIF（跨源）子帧**。 实测（Chrome 150）：同源子帧报、跨源一个都不报；裸协议 JSON 里连 `childFrames` 键都没有； `Target.setAutoAttach` 后逐字节相同 → **不是时序，是协议行为**。 照 C9 写出来的 ObserveAll 会**对所有跨源 iframe 静默视而不见**。 实现者改用 `GetFrameTreeWithEvents`（同源帧树 ∪ DOM 穿透并集），并变异验证承重 （换回裸 GetFrameTree → 精确红「合并结果里没有子帧的字段 #fn」）。 **教训与今天第 8 次同族**：我验了「函数在不在、类型对不对」， **没验「协议调用返不返回需要的东西」** —— 而后者才是这条裁定的目的。 → 我核依赖时只该问「它能不能给我要的东西」，不该问「它存不存在」。
  ↳ 代价若错：（未记 —— 这是缺陷）

**52.** 接受它的两处有意偏离草稿： ① 单帧失败处理：草稿代码静默 `return nil`（而它自己的注释写着「不能静默吞掉」）。 改为**主帧失败整体报错**（空模型+nil error 是假成功）、子帧失败不整体失败但 记一条 `{kind:"frame-error", selector:<frameId>}` 进 obstructions。**优于草稿。** ② 端口（C3/C39）：选「服务层改写」—— 拦下 /outer.html 把写死的 8892 换成 r.Host 里的真端口， **换不上当场报错**，不退化成「没有子帧」假失败；页面结构仍一字不差来自 fixture。 测试三段证据链：反证（主帧单帧观测看不见 #fn/#submit）+ 自证跨源（contentDocument === null） + 合并（带 ["main", <childId>] 路径）。**这个做法好**：它让「跨源」这件事被**自证**，不是假设。
  ↳ 代价若错：（未记 —— 这是缺陷）

**53.** Ruling C50（残余，记此备终审与 T9）：`GetFrameTreeWithEvents` 内部**静默吞掉 DOM 查询错误** （`client.go:259 return ft, nil`）→ 真发生的话 `ObserveAll` 会安静退化成「只有同源子帧」。 根治要动 `client.go`（不在本任务文件清单，且有其它调用方）。 裁定：**本轮不修**，但记成具名债 —— 症状（跨源帧静默消失）、位置、所需修法都要写清。
  ↳ 代价若错：DOM 查询抛异常时跨源帧静默丢失；概率低但后果与 C48 同类。

**54.** **Important #1 要修** —— 它证明了两件事： ① 链上的「反证」环是**废的**：`__cdpRoots` 只走 querySelectorAll('*') + shadowRoot （shadow.go:17-28），**从不进入 contentDocument** → 主帧单帧观测看不见**任何** iframe 的内容， 同源也一样。所以它当不了「同源报警器」。 ② 仅剩的中间环（contentDocument === null）依赖未验前提：同源帧尚未 commit 文档时它是否也是 null？ 修法（它给的，更好）：**从子帧自己取 `location.href`，断言 host 与主帧不同** —— 同时证明「跨源」与「子帧文档真的 commit 了」。 并要求：**做同源变异验证**（把 want 改成 127.0.0.1 → 必须红）。之前那两次变异改的是**实现**， 不是**夹具的源** —— 都没测到这条测试自己的主张。
  ↳ 代价若错：无（换一条更硬的证据链）。

**55.** Ruling C52（**合并解决 #2 与 #3**）：**给 `PageModel` 加一个正当的诊断通道**， 不再把错误塞进 `obstructions`。 理由：③ 说 `obstructions` 被复用成错误通道（`selector` 兼装元素选择器与 frameId、 `text` 兼装页面文字与错误消息，消费者忽略 kind 就会拿 frameId 去点）；② 说退化时**没有任何信号**。 两者要的是同一个东西 —— **一个「观测者自己的问题」的通道**，与「页面上的东西」分开。 而且这正是 Console（D14/D15）要显示给运营的：「这里我没看清」。 实现：`PageModel` 加 `diagnostics: [{kind, detail, frame_path}]`； `frame-error` 与「iframe 数 ≥1 但枚举到 0 个子帧」的退化信号都进这里；kind 常量导出。 ⚠️ 这是**契约新增**，规格 §4.3 我一并更新。
  ↳ 代价若错：契约多一个字段（消费者可忽略）；换掉的是「一个字段两种含义」这种必然踩的坑。

**56.** **Important #4 要修** —— 并集没去重保护：`collectExistingFrameIDs` 只算一次、 `observeInto` 无 seen 集 → 重复 ID 会进树两次、被观测两次（actions/fields/ShadowRoots/PageText 翻倍）。 扁平夹具测不出（重复的是子帧 `#document`，只有它**自身含 iframe** 时才会产生重复 —— 即嵌套）。 修法：`observeInto` 里加 `seen map[string]bool`。
  ↳ 代价若错：无（廉价守卫）。 Minor 一并收（都是注释/一行/文档，同批不单开轮）： #5 `frameEnumerationWait` 注释说错了等待位置（基线半是在等待**之前**取的） #6 `ObserveAll` 对主帧传真实 frameID 而非 `""` —— 与包内约定不符，且会在页面常驻一个 isolated world、往 `c.frameCtxs` 塞一个导航时不会失效的缓存项。`if isMain { frameID = "" }` #7 代码注释把修法说成「唯一解」—— `Target.getTargets` 能直接枚举 OOPIF，只是「用现有助手 的唯一解」；代码注释比报告活得久，要说准 #8 支撑核心结论的原始协议 JSON 记在 `.superpowers/` 路径（workspace 会被删）→ 挪进 `testdata/README.md`（本任务已改过它，且那正是「别踩这里」该待的地方） #9 `testdata/README.md` 超出 brief 文件清单 —— **接受**（纯文档改进，且已在报告声明）

**57.** Ruling C54（规格同步，我来做）：§4.3 加 `diagnostics` 字段 + 一张「obstructions vs diagnostics」对照表， 并写明 `frame-blind` 的触发条件。理由写进规格：**运营必须知道「AI 是没看清，还是看清了但做错了」** —— 那两件事的处置完全不同，所以两个通道不能混。
  ↳ 代价若错：（未记 —— 这是缺陷）

**58.** 🔴 新顾虑（跨源帧内部子帧枚举不到）**接受为具名限制，本轮不修**（R18）。 理由：① 它**已经不静默**（守卫兜成 frame-blind 诊断），这是关键性质；② 收进来要**逐 OOPIF target 取树**， 是**新能力**不是本任务的 bug；③ 实际影响面窄（「跨源组件里再套一层」，漏斗/报价表单不常见）。
  ↳ 代价若错：那类页面会漏掉孙子帧里的内容，但会在 diagnostics 里喊出来。 另：`seen` 集实测**没复现出重复**（两种嵌套夹具 0 重复 ID）→ 是防御性的；`frame-error` 路径仍无确定性测法。

**59.** Ruling C56（**T5 重定义**）：计划的 T5 已过时 —— 它假设 Go 侧 `stabilityOf()`/`looksRandom()`， 但 Task 2 已把 `candidates()`/`stability()` 作为 **JS** 移植进 observe.go（:160/:174）， 那两个 Go 函数不存在也不需要。**但没有任何测试覆盖它们**。 重定义为：**给选择器候选与稳定性评级加真浏览器行为测试**（不是实现 Go 助手）。 内容：在 fixture 里放 stable `#id` / `data-testid` / 随机 hash class / 深层 nth-of-type 四种按钮， 断言各自的 `stability` 值、`alternates` 有序且不冗余、**主选择器不采用随机 token**。 理由：D3 说「选择器是 py 准不准的头号因素」，而它现在零测试。
  ↳ 代价若错：T5 从「写实现」变成「写测试」，工作量更小；若判断错（其实需要 Go 助手）， 下一轮补上即可。 非阻塞笔记（记此备终审）： n1 两条否定断言被删（原「主帧 Observe("") 不得含 #fn/#submit」）—— 有意，但**现在没有任何断言 保证单帧 Observe 保持单帧**。若将来有人让 Observe 内联 iframe，套件仍绿。低危。 n2 守卫的比较是**双向**的（`n != len(ft.ChildFrames)`），比审查方建议的单向更强。 两个非盲输入会让它说话：(a) **closed shadow root 里的 iframe**（JS 助手够不到、 CDP 穿透路径够得到 → 内容其实拿到了，诊断却在喊）；(b) 快照后注入的 iframe。 裁决：**保持双向** —— 过度警告（罕见场景）vs 漏报真实盲区，**漏报的代价高得多**， 而本项目的立身之本就是「不要静默漏掉」。记此，真站上若频繁误喊再收窄。 n3 `seen` 的提前返回会跳过重复节点的**整棵子树** —— 对今天的并集正确（产生不了重复节点）， 但若将来上「逐 OOPIF target 枚举」，两个来源都能到达的帧会**静默丢掉第二处的子节点**。

**60.** **修实现，不改测试**（那条 FAIL 就是验收）。修法要求： ① `RAND` 要够得着 `css-1x2y3z4` 这类 6~7 位 base36 hash ② `candidates()` 的 `name` 落点也要过 RAND ③ **必须双向钉边界**：不光测「该抓的抓到」，还要测「**不该抓的别抓**」—— 否则放宽 RAND 会静默开始拒绝正常 class（那会让 stability 全面变差、影响 agent 选择） 偏置说明：**宁可过度检测**（假阳性的代价是退回结构路径，轻微）—— 假阴性的代价是**抄进 py、发版即断**，两者不对称。 实现者提醒「emotion hash 6~7 位」是**本仓既有知识、未在真站核实**（那是 R5）—— 接受，先按形态做，真站校准留 R5。
  ↳ 代价若错：RAND 过严 → 一些本可用的 class 选择器降级为结构路径。 记此备终审（不进本轮）： ③ 的两条设计观察：`alternates` 对「元素自身有稳定 id」的元素恒为空/无结构路径 —— **最需要兜底的反而没兜底**；`stability()` 的 high 被 `/^#/` 放宽成「以 # 开头」， 同形状 4 段路径带不带 id 表头差两档（它用特征化断言钉住了，未判成缺陷）。

**61.** 接受它的「交替 ≥2 次」判据与双向测试。它自己标明了两处残留： 全字母 hash（`sc-bdVaJa`）与只翻转一次的（`css-abcdefg1`）认不出 —— 前者要引前缀表 （站点知识，应由真站样本得出）→ 留给 R5。 ⚠️ 它还主动标注「形态③ 的概率模型（7 位 base36 认出率 ≈77%）是**我算的、非实测**」—— **这个自我标注本身就值得记**：本项目反复栽在「推断的数字当实测写」，它主动把两者分开了。
  ↳ 代价若错：（未记 —— 这是缺陷）

**62.** **Important #1 要修** —— 我要求「双向钉边界」，而实现者**对 class 双向、对 name 单向**， 且 `name` 恰是这次改动的落点。③ 在 name 上新危及 L→D→L 家族（`step2a`/`address1a`/`opt2b` 这类子字段命名）→ 被丢弃 → 退化位置路径，套件无感。 **修法与判断（审查方要求把判断写下来）：接受 name 也适用 ③（over-detection 偏置前后一致）， 但必须双向钉住 —— 加 `name="a1b2c3"`（随机，必须丢）+ `name="step2a"`（人写的，接受被丢， 断言它确实退化成结构路径）。** 判断写进代码注释，别让它只活在 ledger 里。 理由：class 是框架生成的（hash 常见），name 是人写的（hash 罕见）—— 所以对 name 用激进的 ③ 收益小、代价稍大；但**保持一条规则、一个偏置**比给 name 特例更可维护， 且退化的代价（回退结构路径）轻微。这条判断必须写下来，否则下一个人会以为是漏了。
  ↳ 代价若错：人写的 L→D→L 型 name 退化成结构路径。

**63.** 一并收 4 条 Minor，同一主题（**测试文件里陈旧的数字与描述着修复前行为的注释** —— 正是本项目反复栽的那类「看起来是实测的数字」）： · 陈旧数字：测试注释说「11 按钮 + 3 input = 14（实测 14）」，实际 fixture 已 18 按钮 → 21 （14 是第一轮的真数据，第二轮加了 7 个按钮没刷新注释） · `selector.html` 两处注释描述的是**修复前**的行为（引了旧的两形态 RAND、说「name 这一路没有 RAND」） —— README 更新了而 fixture 没跟上，**fixture 现在与自己的 README 矛盾** · 形态① 现在**成了孤儿**：它曾是唯一捕获者的用例现在都被 ③ 抓 —— 删掉①套件仍绿。加一个「只翻转一次的 hex」用例（如 `class="css-abcdef1234"`）把它重新隔离出来 · 未披露的残留：③ 有 ≥5 位下限 → **≤4 字符的 hash 段**（`css-a1b2`）漏网，该进残留清单
  ↳ 代价若错：无（都是文档/一条用例）。 记此备终审（不进本轮）： m5 M8 是**粗糙**的变异（`RAND=/./` 也会弄红另一条测试）——它证明了反向测试**会响**， 没证明它**必要**。更有信息量的变异是它明确否决的那个设计「混排+长度≥5」， 那个只有反向测试能抓。 m6 **RAND 的波及面超出那两个已测落点**：`pathSel` 的 id 检查（`:174`）现在会爬过 hash 型祖先 id （真实行为变化：`#app-1x2y3z4` 以前当路径锚点+high，现在产物结构路径）、 `candidates()` 的选项组 scope（`:294`）与遮挡/关闭选择器（`:310-311`）继承全部 —— 三者都无断言。 m7 措辞：我说「RAND 被 stability() 用」是**错的**（它用的是 `/^#/`、`\[(name|data-`、 `:nth-of-type` 深度与 `^[a-z]+\.[a-z]`）—— 行为仍**间接**改变（hash id 元素现在落到深度分支， 是改善）。报告 §7.1 四个词之前说「实测取舍」、之后承认是计算模型 —— 二者取一。

**64.** **① 要修**（空数组编码成 `null`）。这是**契约自相矛盾**：同一份 `PageModel` 里 `alternates`/`frame_path`（来自 JS）为空是 `[]`，而 `diagnostics`/`option_groups`/`obstructions` （Go 侧构造）为空是 `null`。py 侧 `for d in model["diagnostics"]` 会 `TypeError` —— 而这是**运行阶段唯一的接口**，且正好在最需要它的时候（回退）炸。 它没动手是对的（契约面、不在它的文件清单）—— 我现在裁定：**修，让所有空列表都编成 `[]`**。
  ↳ 代价若错：无（更一致的契约）。

**65.** **② 是披露，不是待办** —— 它已经自备私有 Chrome 修好了。 但留一条已知陷阱记进 ledger：**共享 9222 上并行跑两个测试二进制会互相改页面**， 且症状是「单包绿、全量红」+「URL 不变但观测到别人的元素」，极具欺骗性。 将来若再有人往这套件加测试，**别用共享浏览器**。
  ↳ 代价若错：（未记 —— 这是缺陷）

**66.** ③a `--json=false` 仍吐 JSON —— **一个静默不做事的 flag 是本项目的病**，要处置： 实现或删掉，二选一，但**不许留着不动**。倾向实现一个最小的人话摘要 （计数 + 前几条动作）—— 因为 Console 那条线上，人话输出迟早要有。
  ↳ 代价若错：多写十几行；换掉的是一个「看起来能切格式、实际不能」的开关。

**67.** Ruling C64（**契约裁定**）：`frame_path` 的语义定死 —— **单帧 `Observe(frameID)` 给 `[frameID]`（frameID 为空 → `["main"]`）； `ObserveAll` 给从根到叶的完整路径 `["main", <childId>, …]`。** 理由：单帧不知道祖先链，但**消费者要的正是「该把动作发给哪一帧」**——`[frameID]` 就是这个信息； 需要完整路径的人应该用 `ObserveAll`。两种模式语义不同但**都自洽**（都回答「这条动作属于哪一帧」）， 且**都够消费者用**。修法落在 `Observe` 解析后的归一化里（与 `normalizeNilLists` 同处）。 已同步进规格 §4.3。
  ↳ 代价若错：有人需要单帧的完整路径时会拿不到 —— 但那本来就该走 `ObserveAll`。

**68.** 那条**负面结论**记进 ledger：用 `outer.html`（iframe 指向死端口）逼非空 diagnostics **实测两次都失败** —— 加载失败的帧渲染成 Chrome 错误页，`Observe` 照样成功、diagnostics 仍为 `[]`。 ⇒ `frame-error` 这条路径**至今没有确定性测法**（T4 已知、本轮再次确认），而 `frame-blind` 是真正在守的那条。 这与「无浏览器时集成测试走 Skip」是同一族风险：**有些错误路径就是这么难触发**，所以更要靠守卫而非靠运气。
  ↳ 代价若错：（未记 —— 这是缺陷）

**69.** Ruling C66（**R19 具名限制**）：页面自带倒计时/轮播/库存数字时 `TextChanged` 每轮都真 → 恒定 `actionable=true`（误判有进展）。它**拒绝加「忽略数字」过滤**，理由正确 —— 那会抹掉 `Step 1 of 3 → Step 2 of 3` 这种最典型的真推进。 **接受为具名限制**，留真站校准（R5 一档）。
  ↳ 代价若错：那类页面上 py 会原地打转 —— 但它会被 「轮数耗尽」这条更响的信号兜住，而不是静默。

**70.** 文档漂移（`README.md`/`CLAUDE.md` 的子命令表**连 `observe` 都没有**） → **补，但放到终审的修复波**：它是**分支级**的文档一致性（连同 T6 那 6 条 Minor）， 一次改完比两轮改好。已明确告知实现者「要补」，不是「不管」。
  ↳ 代价若错：（未记 —— 这是缺陷）

**71.** Ruling C68（**我自查确认 C1**）：身份键里两个字段**确实是选择器派生的**： · `region`（observe.go）：`if (/hero|banner|jumbotron/.test(祖先的className)) return 'hero'` · `label`（observe.go:366-368）：`label[for="${el.id}"]` **排在 `closest('label')` 之前** —— id 耦合且优先级更高 后果：重渲染改名**祖先 class** 或改 `input.id` → 身份翻转 → **内容没变却报 actionable=true** —— 正是裁定②要防的那件事，而当前 e2e **结构上看不到**（改名 JS 只改元素自己、从不改祖先； 且它注入 aria-label 替代被删的 id/name = **刻意保住了真实重渲染会断的关联**）。 **修法（两处都便宜、且保留两个消费者各自的收益）**： ① `label`：**重排优先级** —— `aria-label` → `closest('label')`（结构，不耦合）→ `label[for=id]`（降为最后手段） ② `region`：**landmark 优先于 class 启发式** —— 先扫完整条链找 header/nav/footer/aside/main， 都没有才回落到 class 派生的 `hero`。这样有 landmark 的页面（真实页面的常态）region 全结构； 只有「无 landmark 祖先」的页面才留 class 派生 —— 那条残留写进注释 ③ **扩展 e2e**：改名 JS 要**连祖先 class 一起改**，并**打断 `for=`/`id` 配对**（不注入 aria-label 拐杖）， 断言 `actionable` 仍为 false。**若扩展后仍翻转 → 把那个字段从身份键里去掉。**
  ↳ 代价若错：region 的 hero 粒度变粗（agent 侧感知略降，但换免疫）；label 解析多花一次 closest。

**72.** **I1 修** —— `loadPageModel` 只拒**解析不了**的 JSON；`{}`（或 `cdp navi` 自己的输出 `{"frame":{...}}`）会静默变成全零 PageModel → 活页面上每个元素都报 appeared → **假「有进展」**、退出 0。 加判据：`URL == "" && len(Actions)+len(Fields)+len(OptionGroups) == 0` → 拒绝（真快照一定有 URL，哪怕 about:blank）。
  ↳ 代价若错：（未记 —— 这是缺陷）

**73.** **I3 修** —— 帧加载失败时它的元素**从模型里消失** → appeared/disappeared 非空 → `actionable=true`， 即**观测不全被算成「页面变了」，且偏向假进展**。而 `diagnostics` 在 `PageModel` 里有、`Diff` 里没有。 `Diff` 目前**没有任何消费者**（T8 已挪计划二），所以现在加字段最便宜 —— **把 diagnostics 的条数/存在带进 Diff， 并在人名输出里显示**。
  ↳ 代价若错：（未记 —— 这是缺陷）

**74.** **I2 按「诚实限定 + 记规格缺口」处理，不改判据**： `PageModel` **根本没有字段值**（Field 只有 Label/Type/Placeholder），所以 `diff` **原理上判不了** 「填进去了没有 / 勾上了没有 / 选了没有 / 只是高亮了」→ 这些步骤会报 `actionable=false` → **py 重试 → 重复提交** （正是记忆里「重复开火把菜单点回」那类）。真正的修法是**给模型加字段值**（契约变更）。 → **本轮只改帮助文字**，把 `diff` 的能力范围说准（「判导航/组成变化，不判填写与选择」）； → **并记成 R20**：`PageModel` 缺字段值 = 规格级缺口，计划二补（Console 上运营也正要看这个）。
  ↳ 代价若错：真站上「填完没推进」会被误判为重试。⚠️ 这条是**具名的、会咬人的**残留。

**75.** M1（`--json` 帮助文字与实现矛盾 —— 正是「看起来能切格式」那类）+ M2（选项组键用可打印 `|` 拼接，`["a|b"]` 与 `["a","b"]` 会塌成同一个键 → 静默漏报 · 其余地方都用 NUL 分隔）→ **一并修**（都便宜）。 M3–M8 记此备终审。
  ↳ 代价若错：（未记 —— 这是缺陷）

**76.** 接受它顺带修 `observe` 里那句同文（M1 是同一句复制了两处）—— 修一处留一处才是错的。
  ↳ 代价若错：无（一行帮助文字）。

**77.** Ruling C74（**它自曝的操作失误**）：收尾用了过宽通配 `rm -rf /tmp/*.json`。 已核实：报告与 SDD 目录不受影响；`.txt` 未波及（通配只匹配 .json）； 但 `/tmp` 下 dev 属主的 .json 临时文件（含 09-14 实验的 payload_*.json）**可能已被清掉**。 它主动记下「以免被当成『本来就没有』」—— 这个处置对：**失误要留痕，别让它变成未来的错误前提**。
  ↳ 代价若错：重跑 09-14 那几个实验时 payload 要重建（desc_*.txt 还在，可重建）。

**78.** Ruling C75（**我的流程失误，记下**）：给复审的 diff 文件是**修复轮之前**生成的 （`review-f501c3f..259f746.diff`），我**没为重审范围重新生成** `259f746..597ffd9`。 复审方自己在工作树上复核了（mtime 对得上），但这违反规程「绝不在没有 diff 文件时派审查」。 规程原话：审查方**不该**被迫去读工作树 —— 那会随实现者的后续改动漂移。 处置：收尾时我把这条计入「我自己的失误清单」。
  ↳ 代价若错：复审证据的基线不够硬。 另：它声明「124 PASS」它**未独立验证**（只读任务、且套件会碰共享 9222）—— 如实。

**79.** Ruling C76（**遗留，交终审与计划二**）： · Q1(a) `role="dialog"` 是 landmark 名单里唯一的非标签名项；它是 ARIA 作者写的、不是 hash 生成的， 但代码注释说它「不是生成物」**比可证的更强** · Q1(b) landmark 那一半对**固定的祖先链**才是结构的 —— portal/teleport 在重渲染中途把子树 抬出 `<main>` 会翻 main→body 并被读成「有进展」。这是**拓扑假设**，比 C1 那类属性改名的风险弱得多， 但要在计划二里拿真用 portal 的站量一次 · Q1(c) `diff.go:189-190` 注释小不准（新增的**派生**区域值落进默认桶是安全的；真正的漂移风险是 **新增的派生分支返回了六个 landmark 字符串之一**）—— 结论「两处保持同步」是对的 · 小瑕：e2e 的契约字段清单不含两个新的 `diagnostics_*` 键（线名只由 struct tag 钉住） · **C67 仍未做**：README/CLAUDE.md 的子命令表连 observe 都没有 —— 归终审修复波
  ↳ 代价若错：（未记 —— 这是缺陷）

**80.** Ruling C77（**Important #1**）：**D11 的感知面零端到端断言**。`Action.Role/Tag/Visible/BBox/ AboveFold/RelativeSize/PeerCount/ZIndex/Contrast/NearbyText` 与 `Field.Type/Required/Hint` 在全套件里的断言数**每个都是 0**。JS→Go 绑定**按字符串键** → 任何一个键写错，字段静默变零值、 124 条测试全绿。而 **`bbox` 是 R17 的载重字段**（截图与框对不上就没法「框选」）。 → **修复波必修**：在 `TestObserveLightDOM` 加逐动作不变量（bbox 非零且在视口内、 hero 按钮的 nearby_text 非空、header 链接的 above_fold 为真）。与 `requireShadowActions` 同族。
  ↳ 代价若错：（未记 —— 这是缺陷）

**81.** Ruling C78（**Important #2**）：Dockerfile `chmod +x A B && chmod +x entrypoint.sh 2>/dev/null || true` —— **A 失败则 B 永不执行**，`|| true` 只救退出码；而 `entrypoint.sh` 在 git 里是 100644 → ENTRYPOINT 会 Permission denied(126)。终审在 /tmp 复现了 shell 语义。 今天影响为零（`agent/` 为空，容器本也跑不起来），但**标记告诉恢复者的与真相相反**。 → 修复波：拆开两条 chmod + 改正标记文字 + 补一句「镜像可构建但**暂不可运行**（等计划二的 agent/）」。
  ↳ 代价若错：（未记 —— 这是缺陷）

**82.** Ruling C79（**Important #3，是规格自己错**）：§4.3 曾写 `env`/`platform`/`maps_to`，代码里都没有。 而 `platform.guess+confidence` 与 `maps_to` 是**认知**，**D11 恰恰禁止 observe 输出认知**； `env` 已由 §5.3 分配给 py 的 PROVENANCE（生成时写）。 → **我已改规格**（不是加字段），并顺带记下 §4.4 的 `high` 那半条「或文本全局唯一」**至今未实现** （影响低，§5.1b 的 near/text 阶梯覆盖；但别留一句代码不认的规格）。
  ↳ 代价若错：（未记 —— 这是缺陷）

**83.** Ruling C80（**更正 C50**）：终审指出我把那条债记重了 —— `client.go:260` 返回 `ft, nil` 确实会让 `ObserveAll` 退化成只剩同源帧，**但不是静默**：跨源 iframe 的**元素**仍在父帧 DOM 里， `checkFrameCoverage` 会为该帧喊 `frame-blind`。 → 重新定性为「**内容缺失但已声明**」，不是「跨源帧静默消失」。计划二排优先级时按这个来。 终审修复波: DONE → commit 21017f2（10 文件 +382/−31；在控制器的 5dc0f47 之上） 125 PASS / 0 SKIP / 0 FAIL；go vet 干净。7 条全部完成，**一行行为代码未动** （唯一例外是第 2 项那个 Dockerfile 缺陷本身）。
  ↳ 代价若错：（未记 —— 这是缺陷）

**84.** Ruling C81（**波中发现的新既有缺陷，留用户定**）：`cmd/root.go` 的 `PersistentPreRun` 判的是 子命令 flagset → `Changed()` **恒 false** → **env 覆盖显式 `--port`**。 实测：`CDP_PORT=<死端口> cdp --port <活端口> targets` 报**死端口**的连接错。 后果：设了 `CDP_PORT` 的 shell 里跑 `go test ./...`，`cmd/` 的 e2e **静默打到别的浏览器上** （它第一轮 baseline 因此红过一条，而 `./cmd/` 单跑全绿）。 **「显式 flag 被 env 覆盖」正是本项目那类病（flag 在说谎）。** 继承自 cdpcli，非本分支引入。 → 按规程**终审后没有第二个修复波**，所以**不修，记成具名缺陷交用户**。 它已把实测真相写进 `tools/cdp/CLAUDE.md` 并标注「与代码意图相反」。
  ↳ 代价若错：有人 shell 里设了 CDP_PORT 时，`--port` 静默失效；测试会打到别的浏览器。

**85.** 接受它**纠正了终审给的一条错断言** —— 「每条 action 的 bbox 在视口内」 实测**不是正确不变量**（`#foot-learn` y=446 > 视口 437，折线下的元素本来就合法）。 换成等价强度且环境无关的：非零/非负/在文档内 + **above_fold 与 bbox 自洽**。 **这是「不照抄一个看起来合理的断言、而是实测它、再给同等强度的替代」** —— 正是本项目要的纪律。
  ↳ 代价若错：无。

**86.** `gofmt -l` 在 HEAD 上本就列 6 个文件（继承自 cdpcli，与 `3f70131` 同类），本波未碰 ✓
  ↳ 代价若错：（未记 —— 这是缺陷）
