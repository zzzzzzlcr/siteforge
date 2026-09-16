# Task 1 报告：迁入 cdpcli 并验证跨源帧能力没丢

**Status: BLOCKED** —— Step 4（关键验收）用新建的二进制**失败**。已按 brief Step 5 / 控制方指令**立即停下**，未做 Step 6/7，未写任何规避代码。

日期：2026-09-16　　执行人：task-1 agent

---

## 1. 做了什么

严格按 brief 的 7 个 Step 顺序执行，**Step 5 触发后停止**。

| Step | 内容 | 结果 |
|---|---|---|
| 1 | 记录部署二进制基线的能力指纹 | ✅ 两项断言全中 |
| 2 | 从 git HEAD 复制源码到 `tools/cdp` | ✅ 无 WIP 残留 |
| 3 | 构建 + 跑既有测试 | ✅ 构建成功，测试全绿（真跑非 skip） |
| 4 | 用**新建**二进制重跑跨源帧验收 | ❌ **失败** |
| 5 | 失败即停 | ⏹️ 已停 |
| 6 | 对齐 Dockerfile | ⛔ **未执行**（见 §5） |
| 7 | 提交 | ⛔ **未执行**（见 §5） |

### Step 1：基线能力指纹（两项断言均符合预期）

```bash
mkdir -p /tmp/siteforge-baseline
cp /opt/skills/auto-farm-skill/cdp /tmp/siteforge-baseline/cdp
md5sum /tmp/siteforge-baseline/cdp    # 应为 1df52111298f5c3ebed8104ec6591c77
grep -ac __cdpQ /tmp/siteforge-baseline/cdp   # 应为 21
```

实测输出：

```
1df52111298f5c3ebed8104ec6591c77  /tmp/siteforge-baseline/cdp
21
```

与 brief 预期逐字一致，基线可信。

### Step 2：从 git HEAD 复制

```bash
cd /company/siteforge && mkdir -p tools/cdp
cd /company/cdpcli && git archive HEAD | tar -x -C /company/siteforge/tools/cdp
```

WIP 残留检查：`find tools/cdp -name '*.patch' -o -name '*wip*'` → **无输出**，干净。

复制时 `/company/cdpcli` 的 git 状态（决定了本任务的风险面）：

- HEAD = `eddee5f fix: 选择器解析穿透 shadow DOM …`
- 已修改未提交：`build.sh`、`cmd/{click,eval,navi,scroll}.go`、`internal/client.go`、`internal/snapshot_bdd_test.go`
- 未跟踪：`internal/{batch,click,client}_integration_test.go`、`internal/client_test.go`、`.mcp.json`

### Step 3：构建与既有测试

```bash
cd /company/siteforge/tools/cdp
go build -o cdp main.go
go test ./... 2>&1 | tail -20
```

实测：

```
BUILD OK
-rwxrwxr-x 1 dev dev 12761162  9月 16 10:32 cdp
?   	cdp	[no test files]
ok  	cdp/cmd	0.005s
ok  	cdp/internal	16.751s
```

`cdp/internal` 耗时 16.7s 说明集成测试**真的跑了**（不是无浏览器 Skip）。单独确认 shadow 那组：

```
=== RUN   TestShadowFixtureIsActuallyHidden          --- PASS (0.97s)
=== RUN   TestGetElementCenterPiercesShadowRoot      --- PASS (0.91s)
=== RUN   TestFillTextPiercesShadowRoot              --- PASS (1.63s)
=== RUN   TestClickPiercesShadowRoot                 --- PASS (1.01s)
=== RUN   TestSelectorBuildersPierceShadowRoots      --- PASS (0.00s)
```

→ **HEAD 里的 shadow DOM 穿透能力是完整的**（`eddee5f` 已进 git，没丢）。

### Step 4：关键验收 —— 失败

起 fixture 服务（`tools/cdp/internal/testdata/`，8892），先确认双源可达：

```
127.0.0.1:8892/outer.html -> HTTP 200
localhost:8892/inner.html -> HTTP 200
```

（`/etc/hosts` 里 `127.0.0.1 localhost`，`python3 -m http.server` 绑 0.0.0.0，所以 IPv4 双源都通；环境无 proxy 变量。这一步是刻意先验的 —— 双源可达正是「跨源」的前提，避免把环境问题误判成能力丢失。）

正式跑 brief 的命令：

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

`frames.py` 正常列出两帧（外层 + 跨源子帧），子帧 id 拿到：

```
C2E04E7B44825D45C9D637FD8B872446	http://127.0.0.1:8892/outer.html
5183DFB659F4D8AECBC0D9420F32BA14	http://localhost:8892/inner.html
```

**eval 返回值（Step 4 实测输出）：**

```
$ /company/siteforge/tools/cdp/cdp --host 127.0.0.1 --port 9222 \
      eval --frame-id 5183DFB659F4D8AECBC0D9420F32BA14 "location.href"
Error: JS exception: failed to get frame context: failed to create isolated world
for frame 5183DFB659F4D8AECBC0D9420F32BA14: No frame for given id found (-32602)
EXIT=1
```

预期 `"http://localhost:8892/inner.html"`，**实际是错误退出**。

---

## 2. 测了什么、结果

### 2.1 受控差分：把「能力丢失」和「我环境搭错」区分开

Step 4 失败后，为让 BLOCKED 报告可决断，做了受控对照（**不改变 Step 4 的官方结论 —— 官方结论用新二进制，FAIL**）：同一帧、同一条命令，换基线二进制跑。

```
=== CONTROL: /opt/skills/auto-farm-skill/cdp (HEAD+WIP), 同一 frame ==
"http://localhost:8892/inner.html"
EXIT=0
```

→ 基线（HEAD+WIP 构建）返回值**逐字等于 brief 要求的字符串**。fixture、服务、Chrome、双源、命令全部正确，**能力确实只存在于 WIP 里**。

### 2.2 边界定位：丢的**只有** OOPIF 那一档

| 帧类型 | 新二进制（HEAD-only） | 基线（HEAD+WIP） |
|---|---|---|
| 主帧 | ✅ | ✅ |
| **同源** iframe | ✅ `"http://127.0.0.1:8892/inner.html"` | ✅ |
| **跨源 (OOPIF)** iframe | ❌ `No frame for given id found (-32602)` | ✅ `"http://localhost:8892/inner.html"` |

同源对照用 `outer_same.html`（iframe `src="inner.html"`，同源）→ **通过**。所以不是「所有帧 eval 都坏了」，而是**精确地只有跨源/OOPIF 那一条路没了**。

### 2.3 R3 四档 fixture 逐档（新二进制）

| 档 | 页面 | 新二进制结果 |
|---|---|---|
| 1 | `base.html` light DOM | ✅ shadow_roots=0 text_len=163 actions=6 fields=1 obs=1 |
| 2 | `shadow.html` 两层嵌套 shadow | ✅ shadow_roots=2 text_len=141 fields=3 groups=1 |
| 3 | `outer.html` **主帧** | ✅ 但 text_len=12 / actions=0 —— 只有外层壳 |
| 3+4 | `outer.html` **跨源子帧** | ❌ **失败** |
| 4 | `inner.html` 跨源 + 套两层 shadow | ❌ **失败**（子帧进不去） |

档 1 / 2 与 R3 README 记录的预期数值一致（163 / 141 / fields 3 / groups 1）。档 3、4 的内容**全在跨源子帧里**，而 R3 README 明确写着这一档「需**逐帧 eval 再合并**（同源策略）」—— 也就是说 **observe 契约对跨源子帧 eval 是硬依赖**，不是可选优化。

### 2.4 根因（机械层面确认）

- 迁移后的 `internal/client.go` 里 `grep -n "evalInOOPIFFrame\|attachToTarget\|gobwas"` → **完全不存在**
- `tools/cdp/go.mod` 里 `github.com/gobwas/ws v1.4.0 // indirect` —— HTML 标记为 indirect，说明 **HEAD 从没有直接 import 过它**；WIP 才引入直接调用
- 失败点是 `page.CreateIsolatedWorld(frameID)` 在**父 target** 上对 OOPIF frameID 报 `-32602`。HEAD 的 `EvalInFrame` 只用 `GetFrameOrCreateContext` 这一条路，**没有 fallback**；WIP 给 `EvalInFrame` 补的正是 `if err != nil && frameID != "" { return c.evalInOOPIFFrame(...) }`
- 二进制字符串指纹把差异钉死：

| 指纹 | 基线 (HEAD+WIP) | 新 (HEAD-only) |
|---|---|---|
| md5 | `1df52111298f5c3ebed8104ec6591c77` | `b35c382f033cce5ea2974981dfe4de72` |
| `__cdpQ`（shadow 能力） | 21 | **21**（未丢） |
| `evalInOOPIFFrame` | 2 | **0**（丢了） |
| `OOPIF eval` | 5 | **0**（丢了） |

**结论：控制方预先写下的判断被实测精确证实** —— 迁移的是 git HEAD，丢掉的正是未提交 WIP 里的 `evalInOOPIFFrame` / OOPIF 能力。`__cdpQ` 计数两边都是 21，所以丢的**不是** shadow 那部分。

---

## 3. 改了哪些文件

**没有任何提交。** siteforge 的 git 状态干净地只有：

```
?? tools/
```

工作区里的产物（未提交）：

| 路径 | 说明 |
|---|---|
| `tools/cdp/**` | 从 `/company/cdpcli` **git HEAD** 复制，**逐字节等同**（见 §4.1） |
| `tools/cdp/internal/testdata/*.html` | Step 4 要求搬入的 6 个 fixture（新增，非 cdpcli 内容） |
| `tools/cdp/cdp` | `go build -o cdp main.go` 产物；已被 siteforge `.gitignore` 覆盖 |
| `Dockerfile` | **未改** |
| `/tmp/siteforge-baseline/cdp` | Step 1 的基线留档 |

`/company/cdpcli` 与 `/opt/skills/auto-farm-skill` **只读，未改动一个字**。

---

## 4. 自查发现

**4.1 没有把 WIP 带进来，也没有误改** —— 把 HEAD 重新解到 `/tmp/verify-head` 与原树对比：

```bash
diff -r --brief /tmp/verify-head /company/siteforge/tools/cdp -x cdp -x testdata
```

**无输出** → 迁入的树与 cdpcli 的 `git HEAD` **逐字节一致**，无夹带、无手改。

**4.2 未提交 = 没有把假声明写进历史。** brief Step 7 给的 commit message 是
`feat: 迁入 cdp 工具层（git HEAD，不含 WIP）—— 并验证跨源帧能力未丢`，
其中「**并验证跨源帧能力未丢**」现在与事实相反。即便不考虑 Step 5 的「停下」，
这条 message 也不该按原样提交。这也是我选择不提交的独立理由。

**4.3 顺带被 `git archive HEAD` 拉进来的文件**（brief 的预期清单只列了
`main.go cmd internal go.mod go.sum build.sh verify.sh`，但 `git archive` 会带**整个** HEAD 树）：`.claude/`、`CLAUDE.md`、`README.md`、`.gitignore`、`.dockerignore`、`docs/`（30 个 plan/spec 文件）。

其中两处值得reviewer注意：

- `tools/cdp/CLAUDE.md` —— 嵌套 CLAUDE.md，会让**以后任何在 `tools/cdp/` 下工作的会话**自动加载 cdpcli 的旧项目指令
- `tools/cdp/.claude/settings.json` —— 内容是 `enabledPlugins` 开 4 个插件（gopls-lsp / superpowers / claude-md-management / code-simplifier），会**作用到这个子树**

这是 brief 逐字命令的必然产物，我没有自行删改（不自行发挥）。请裁决是否要清掉。

**4.4 清理已做完**：fixture 服务已停（8892 已释放，`curl` 返回不可达），我开的所有 :8892 tab 已用 `/json/close/<id>` 关净（复查剩余 :8892 tab = none）。

---

## 5. 为什么没做 Step 6 / Step 7

- **Step 5**：「若跨源帧失败 —— 停下来，不要继续」+
  **控制方 ruling**：「**若 Step 4 失败，立刻停下**……这条要交给用户定夺」。
- Step 6（Dockerfile）与 Step 7（提交）都会产出**新的持久状态**，而 R7 的两种走法
  （「迁 HEAD+WIP」/「先把 WIP 提交」）都会让当前这棵 HEAD-only 树**被重做或作废**。
  在用户定夺前动它们属于自行发挥，且提交会固化 §4.2 那条假声明。
- 因此我停在 Step 5，**保留工作区现场**（文件都在，未丢任何工作）。

---

## 6. 顾虑与 R7 选项

**核心顾虑**：这个失败不是「少了个 nice-to-have」。R3 README 写明跨源那档「需逐帧 eval 再合并」，
而档 3/4（含最难的「跨源套两层 shadow」）**内容全部在跨源子帧里**。也就是说
HEAD-only 的工具层**无法 observe 任何带跨源 iframe 的页面** —— 而这正是 siteforge 立项要解决的主场景。
按现状把工具层定为 HEAD-only，等于把一个已知的静默缺口固化进 Task 2+ 的地基
（失败时表现为 `-32602` 报错，不会静默出错，但能力直接缺失）。

**R7 选项（供用户定夺，我未擅自选）**：

- **R7-A：迁 HEAD + 未提交 WIP**（`internal/client.go` 等 7 个已改文件 + 4 个未跟踪测试文件）。
  - 有现成的可行性证据：部署基线 `1df52111298f5c3ebed8104ec6591c77` **就是 HEAD+WIP 构建**，
    而它在此处的 Step 4 返回了要求的字符串 —— 该路线**由构造即可行**，无需再试。
  - 注意：WIP 里 `go.mod` **未改**，`gobwas/ws` 仍标着 `// indirect`（注释陈旧但可正常构建）；
    若要干净，需跑一次 `go mod tidy`，那会改动 `go.mod`/`go.sum`，**超出一个纯文件复制**。
  - 还需决定：WIP 里那 4 个**未跟踪**的测试文件（`internal/{batch,click,client}_integration_test.go`、
    `internal/client_test.go`）要不要一并迁入 —— 它们连在 cdpcli 里都没进 git。
- **R7-B：先在 cdpcli 把 WIP 提交（或拆成 OOPIF 那个 commit），再从新的 HEAD 迁**。
  - 更干净，且能顺带修掉「部署物 = HEAD+WIP」这个长期漂移（该漂移本身已被记忆库记为风险）。
  - 代价：要先在 `/company/cdpcli` 落一个提交 —— 我**没有**碰那个仓库。

**另外两个较小的顾虑**：

1. §4.3 的 `CLAUDE.md` / `.claude/settings.json` 是否要清掉（会影响 `tools/cdp/` 子树的会话行为）。
2. Step 3 里 `go test ./...` 的绿灯**不能**被读作「迁移无损」—— 它盖的是 shadow/主帧，
   恰好**没有**任何集成测试覆盖跨源 OOPIF 帧eval（否则本任务 Step 4 之外就该报红）。
   若 R7 定为 A/B，建议后续补一个跨源帧的集成测试，把这个缺口钉住。

---

## 附：复现步骤（清理已做，需要时一键重跑）

```bash
export PATH=/usr/local/go/bin:$PATH
cd /company/siteforge/tools/cdp/internal/testdata && nohup setsid python3 -m http.server 8892 >/tmp/s.log 2>&1 &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8892/inner.html   # 期望 200

CDP=/company/siteforge/tools/cdp/cdp
ID=$(curl -s -X PUT "http://127.0.0.1:9222/json/new?http://127.0.0.1:8892/outer.html" \
     | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
sleep 4
$CDP --host 127.0.0.1 --port 9222 active $ID
CHILD=$(python3 /company/siteforge/docs/probes/2026-09-16-observe-r3/frames.py \
        | awk -F'\t' '/inner.html/{print $1; exit}')
$CDP --host 127.0.0.1 --port 9222 eval --frame-id "$CHILD" "location.href"
#   HEAD-only  -> Error: ... No frame for given id found (-32602)   EXIT=1
#   HEAD+WIP   -> "http://localhost:8892/inner.html"                EXIT=0
curl -s http://127.0.0.1:9222/json/close/$ID
pkill -f 'http[.]server 8892'
```

---
---

# 第二轮（R7 裁决后重做）

**Status: DONE_WITH_CONCERNS** —— Step 4 关键验收**通过**（返回值逐字符合要求）。保留一条顾虑：`go test ./...` 有 1 个失败，为**来源分支既有**、非本迁移引入（已定位到根因，见 §R5）。

日期：2026-09-16　　提交：`e09e298`

## R1. R7 裁决与新来源

用户裁决：迁移来源由「cdpcli 的 git HEAD」改为「cdpcli 的新分支」。

- 分支：`wip/oopif-iframe-ax`
- commit：`780fa9088ddc297dcf2c3b68d9555a2bdb6c019e`
  （`wip: OOPIF iframe AX 采集 + 跨源帧求值（落盘存档，非完成态）`）
- `master` 未动（仍在 `eddee5f`），未推送
- 此前游离在 git 外的 4 个测试文件已一并提交

第一轮的判断（迁 HEAD 会丢 `evalInOOPIFFrame`）由此得到处置：不再迁 HEAD。

## R2. 做了什么

| 步 | 内容 | 结果 |
|---|---|---|
| 1 | `rm -rf tools/cdp`（清掉纯 HEAD 产物） | ✅ |
| 2 | `git archive wip/oopif-iframe-ax \| tar -x -C tools/cdp` | ✅ |
| 3 | 确认 OOPIF 能力在 + 4 个测试文件在 | ✅（一处数字与预期不符，见 §R6.2） |
| 4 | 构建 + `go test ./...` | 构建 ✅ / 测试 **1 处红**（既有，见 §R5） |
| 5 | **Step 4 关键验收** | ✅ **通过** |
| 6 | Dockerfile（C7） | ✅ |
| 7 | `go mod tidy`（C13） | ✅ |
| 8 | 提交 | ✅ `e09e298` |
| 9 | 清理 + 报告 | ✅ |

执行前先核对了来源分支确实存在且 `master` 未被改动（`git log -1 wip/oopif-iframe-ax` → `780fa90`；`git log -1 master` → `eddee5f`）。

## R3. Step 4 关键验收 —— 通过

fixture 服务起在 `/company/siteforge/docs/probes/2026-09-16-observe-r3/fixtures/`（8892），先验双源可达（跨源的前提）：

```
127.0.0.1:8892/outer.html -> HTTP 200
localhost:8892/inner.html -> HTTP 200
```

正式跑 brief 的 Step 4 命令（用**本次新构建**的 `tools/cdp/cdp`）：

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

实测输出：

```
TAB_ID=977C7EF8876182479D2967FDE36DED40
frames.py:
  977C7EF8876182479D2967FDE36DED40	http://127.0.0.1:8892/outer.html
  E511B77312508F2578EAE5D4621CE6C8	http://localhost:8892/inner.html
CHILD=E511B77312508F2578EAE5D4621CE6C8

$ cdp --host 127.0.0.1 --port 9222 eval --frame-id E511B77312508F2578EAE5D4621CE6C8 "location.href"
"http://localhost:8892/inner.html"
EXIT=0
```

**返回值逐字等于 brief 要求的 `"http://localhost:8892/inner.html"`。** 第一轮同一命令在此处报 `-32602`，本轮通过。

### R3.1 R3 四档 fixture 全过（同一二进制，`observe.js` 实跑）

| 档 | 页面 | 结果 |
|---|---|---|
| 1 | `base.html` light DOM | ✅ shadow_roots=0 text_len=**163** actions=**6** fields=**1** obs=1 |
| 2 | `shadow.html` 两层嵌套 shadow | ✅ shadow_roots=**2** text_len=**141** fields=**3** groups=**1** |
| 3 | `outer.html` 主帧 | ✅（text_len=12 / actions=0 —— 内容在跨源子帧里，符合预期） |
| 3+4 | `outer.html` **跨源子帧** | ✅ shadow_roots=2 text_len=125 actions=6 fields=3 groups=1 |
| — | 合并（逐帧 eval 再合并） | ✅ actions=6 fields=3 groups=1 shadow_roots=2 |

档 4（跨源 iframe **套**两层 shadow，最难那档）恢复可用。档 1/2 的数值与 R3 探针 README 记录的预期一致（163 / 141 / fields 3 / groups 1）。

## R4. Step 6：Dockerfile（C7）

改了**两处**（都加 `Task 8` 标记）：

1. **stage 1 构建行** —— 入口由 `./cmd/cdp` 改为根目录 `main.go`，并按 C7 只保留 `cdp` 一行：

```dockerfile
# 入口是仓库根目录的 main.go(cobra 根命令在 cmd/ 下), 不是 ./cmd/cdp
# Task 8: cdp-mcp 上线后在此补回第二行 —— go build -ldflags="-s -w" -o /out/cdp-mcp ./cmd/mcp
RUN go build -ldflags="-s -w" -o /out/cdp main.go \
 && /out/cdp --help >/dev/null \
 && echo "工具层构建成功"
```

2. **stage 2 的 cdp-mcp COPY** —— 注释掉并标记 Task 8。

第 2 处是**必要的连带改动**，理由：brief 给「只保留一行」的理由是「否则本任务结束时代码库处于不可构建状态」，而 stage 2 原本有
`COPY --from=tools /out/cdp-mcp /usr/local/bin/cdp-mcp`。**COPY 源文件不存在会让 build 无条件失败**，只改 stage 1 会让这个理由落空。C7 说的是「别自作主张补上第二行」，没有禁止让 stage 2 自洽，故按「保持可构建」处理。

**刻意没动**的两处 cdp-mcp 提及（都不影响构建，避免扩大 diff）：

- `RUN chmod +x /usr/local/bin/cdp /usr/local/bin/cdp-mcp ... || true` —— 末尾有 `|| true` 兜底，不会让 build 失败
- `ENV CDP_MCP_BIN=/usr/local/bin/cdp-mcp` —— 只是环境变量，无引用方（Task 8 才消费）

Task 8 恢复时需要一并处理的清单就是上面 4 处。

**已本地验证** build 命令本身可用（复刻 stage-1 的 RUN）：

```
$ go build -ldflags="-s -w" -o /tmp/out-cdp main.go && /tmp/out-cdp --help >/dev/null
工具层构建成功  (build + --help 都通过)
```

并确认旧写法指向的目录确实不存在（即原 Dockerfile 是坏的）：`cmd/cdp` ABSENT、`cmd/mcp` ABSENT。

## R5. Step 7：`go mod tidy`（C13）

按要求带 GOPROXY（本机 proxy.golang.org 不可达）：

```bash
cd /company/siteforge/tools/cdp
GOPROXY=https://goproxy.cn,direct go mod tidy
```

结果符合 C13 预判：

```diff
--- a/go.mod
+++ b/go.mod
 7a8
+	github.com/gobwas/ws v1.4.0
 21d21
-	github.com/gobwas/ws v1.4.0 // indirect
```

- `gobwas/ws` 由 `// indirect` 转为**直接依赖**（`cdpSend` 直接 import 它）
- **`go.sum` 逐字节未变**（该模块本就在 go.sum 里，只是标记问题）

跑完复验：

- `go build -o cdp main.go` → **BUILD OK**
- `go test ./...` → 与 tidy **前完全一致**（仍是那 1 个既有失败，无新增失败）

> 注：指令第 7 步写「确认 `go test ./...` 仍绿」—— 这条**前提不成立**：该来源分支的 `go test ./...` 本来就非绿（原因与 tidy 无关，见 §R6）。tidy 前后对比一致，可确认 tidy 未引入任何回归。

## R6. 测试：`go test ./...` 不是绿的（既有问题，已定位）

### R6.1 现象与定位

唯一失败：`TestSnapshotBDD`（ginkgo 套件）的
`snapshot frame enhancement / nested iframes / "discovers nested iframes via DOM with proper hierarchy"`
（`internal/snapshot_bdd_test.go:86`）。套件内 4 个 spec：3 过 1 败。

除它之外**全部通过**（50+ 个 test function 全绿），其中包括这次随分支进来的 4 个测试文件：
`TestDisconnect_*`、`TestBatchCommands_NoHang`、`TestClickElement_SmallButtonOnClick` 等。

**关键定位实验（决定性）：**

| 跑法 | 结果 |
|---|---|
| `go test ./internal/ -run TestSnapshotBDD`（整个套件） | ❌ FAIL |
| `go test ./internal/ -run TestSnapshotBDD --ginkgo.focus="discovers nested iframes"`（**只跑该 spec**） | ✅ **ok 4.666s** |

**同一段代码、同一个浏览器，单独跑通过、整包跑失败** → 根因是**测试用例间的共享状态污染**，不是被迁移能力的缺陷。

### R6.2 不是迁移引入的（两条独立证据）

1. **逐字节同一性**：`diff -r /tmp/verify-branch tools/cdp` 只报三处差异 —— 我构建的 `cdp` 二进制、C12 删掉的 `.claude`、C13 改过的 `go.mod`。测试文件与源码**与来源分支逐字节相同**。
2. **确定性**：隔离连跑两次都失败，非偶发。

### R6.3 我做了哪些排除（避免误判）

- **不是环境问题**：同一构造在**两个真实页面上用 CLI 实跑**（`snapshot` → 正是 `GetFrameTreeWithEvents` 这条路径），嵌套都正确：
  - `base.html` → depth1 `outer-frame` / depth2 `inner-frame` ✅
  - `localhost:8080/mui-datepicker` → depth1 `outer-frame` / depth2 `inner-frame` ✅
  → 被验能力本身**是好的**，失败只在"整包跑"这个条件下出现。
- **不是"活动标签页依赖"**：我一度假设该 spec 依赖环境里恰好活动的那个 tab（它用 `NewClient` 取活动页且自己**不导航**），于是把活动页设为我已验证可用的 `base.html` 再跑 → **仍然 FAIL**。该假设**被我自己证伪**，故不作为结论。

### R6.4 结论与处置

- 性质：**来源分支既有的测试隔离缺陷**（该 WIP 的 commit message 自己就写着「落盘存档，**非完成态**」）。
- **我未修**：修它属于新增逻辑，超出本任务「只搬运 + 验证」的范围，且会改动来源分支的测试。
- 需要控制方裁决：Task 2+ 会在这份代码上加 `observe.go`，带着一个红测试进 Task 2 会持续污染"全绿"这个信号。建议二选一 —— 要么在 Task 2 开工前单独修这个 spec 的隔离（让它自带导航到已知页面），要么明确把它记为既有欠债。

## R7. 改了哪些文件 / 提交

提交 `e09e298`：`feat: 迁入 cdp 工具层（来源 cdpcli wip/oopif-iframe-ax@780fa90）`，67 个文件（39 个代码/配置 + 28 个从 cdpcli 带过来的 docs）。

- 新增：`tools/cdp/**`（来源分支内容）
- 修改：`Dockerfile`（仅 §R4 那两处）
- 提交者身份沿用仓库既有身份 `lcr <lcr@3tkj.cn>`（该仓库没配 user.name/email，我用 `git -c` 逐次传入，**未写进任何配置文件**）

提交后 `git status` 干净。

## R8. 自查发现

1. **与来源分支逐字节一致**（§R6.2）—— 只有三处**刻意**差异：C12 删 `.claude`、C13 改 `go.mod`、构建产物 `cdp`。
2. **C12 已落实**：`tools/cdp/.claude/settings.json` 已删；`tools/cdp/.claude/` 目录随之为空，一并移除（git 本就不跟踪空目录）。`tools/cdp/CLAUDE.md` 按 C12 **保留**。已确认提交里不含任何 `.claude`。
3. **二进制未入库**：`tools/cdp/cdp` 被 `.gitignore` 命中（`git check-ignore` 确认），未进提交。
4. **指令第 3 步的数字与实测不符**：指令写 `grep -ac evalInOOPIFFrame internal/client.go` **应为 1**，**实测为 3**。逐行是：
   - `479: return c.evalInOOPIFFrame(frameID, js, result)`（调用点）
   - `484: // evalInOOPIFFrame executes JS in a cross-origin iframe ...`（注释）
   - `486: func (c *Client) evalInOOPIFFrame(targetID string, js string, result interface{}) error {`（定义）
   
   即**检查的意图（OOPIF 能力在不在）满足**，但**给出的期望值不对**（`-c` 数的是行数，注释行也算）。我未改动任何代码去迁就这个数字，照实记录。
5. **4 个测试文件确认都在**（`client_test.go`、`batch_integration_test.go`、`click_integration_test.go`、`client_integration_test.go`），且都在本次 `go test` 中实际跑到并通过。
6. **随代码树进来的 `.gitignore` / `.dockerignore` / `README.md`**：来源分支的 `.gitignore` 含 `cdp`（正好帮我们忽略二进制）、`.dockerignore` 含 `.claude/settings.local.json`。与第一轮同类现象，属 brief 逐字命令的必然产物，未自行删改。

## R9. 顾虑

1. **（本次唯一实质顾虑）** `go test ./...` 非绿，见 §R6 —— 既有、非本迁移、已定位为测试隔离问题、未修，请裁决处置方式。
2. 指令第 7 步「`go test ./...` 仍绿」的前提不成立（同 §R6），已在提交信息里如实记明。
3. Task 8 恢复 cdp-mcp 时要一并处理的 4 处清单见 §R4。

## R10. 清理

- fixture 服务已停，**8892 已释放**（`curl` 不可达、`ss` 无监听）
- 我开过的所有 `:8892` tab 已全部 `/json/close/<id>` 关净，复查剩余 = none
- `/company/cdpcli` **只读取用，未改动**（仍在 `wip/oopif-iframe-ax`，master 未动、未推送）
- `/opt/skills/auto-farm-skill` 未改动

---
---

# Task 1b：修那条红 spec —— 先更正我自己的诊断

**Status: NEEDS_CONTEXT**（不是 DONE）—— 我拿到了**直接证据**，推翻了我第二轮给出的根因。

**结论一句话：这不是测试隔离问题。是被测代码 `collectFramesFromDOM` 把 iframe 的 `name` 丢了。** 测试一直是对的，代码是错的。

**我**没有**改被测代码**（你明确禁止了），**也没有**留下「改测试去绕开」的版本（那等于把 bug 盖住）。当前工作树里 **Task 1b 没有任何改动落地**，等你的授权。**验证过的修复是一行**，见 §T6。

## T1. ⚠️ 先更正记录：我第二轮的「测试隔离」结论是错的

第二轮 §R6.3 我写「根因是测试用例间的共享状态污染（测试隔离缺陷）」，并据此推掉了「活动标签页依赖」这个假设。**那两条结论都不对。**

正确的根因是：**代码在把 DOM 发现的帧并入帧树时，构造的 `cdp.Frame` 只填了 `ID` 和 `URL`，没有填 `Name`**（`internal/client.go:322-328`）。测试按 `Name` 找自己的 fixture 帧，于是找不到。

也就是说：**spec 单独跑绿、整包跑红，跟测试隔离无关**，是**竞态**——

- 整包跑时（机器更忙），`GetFrameTreeWithEvents` 里那句**开头的** `GetFrameTree()` 跑在 inner 帧还没建好之前 → 基线里没有 inner
- 4 秒后 DOM 穿透发现了 inner → 因为不在基线里，走 `mergeFrameIntoTree` 并入 → **用的是 `collectFramesFromDOM` 造的帧，`Name` 是空的**
- 单独跑时（更闲），开头的 `GetFrameTree()` 恰好赶在 inner 建好之后 → inner 已在基线里 → 不触发并入 → `Name` 保住 → 绿

你上一轮说我「诊断已经够清楚了」——**不够清楚，而且方向是错的**。抱歉，这条更正要紧，因为按错方向修（改测试）会正好把真 bug 盖住。

## T2. 直接证据（instrument 那条 spec 拿到的一次 dump）

我临时在该 spec 里加了一段诊断（**已删，见 §T7**），同时打印「基线帧树」和「GetFrameTreeWithEvents 返回的帧树」：

```
[baseline] depth=0 name=""            id=E1563F8F… url="about:blank"
[baseline] depth=1 name="outer-frame" id=E55E01B9… url="about:srcdoc"
[baseline] depth=2 name="inner-frame" id=B798990E… url="about:blank"     ← 基线有名字
[withev]   depth=0 name=""            id=E1563F8F… url="about:blank"
[withev]   depth=1 name="outer-frame" id=E55E01B9… url="about:srcdoc"
[withev]   depth=2 name=""            id=B798990E… url="about:blank"     ← 同一个 id，名字没了
```

**同一个 frame ID（`B798990E…`），层级也完全正确（depth=2，确实是 outer 的孩子），只有 `Name` 从 `"inner-frame"` 变成了 `""`。**

层级是对的 → 断言失败**纯粹**因为 `Name` 被抹掉。缺陷定位到 `internal/client.go`：

```go
frames = append(frames, domFrame{
    parentID: ownerFrameID,
    frame: &cdp.Frame{
        ID:  node.FrameID,
        URL: nodeGetAttr(node.Attributes, "src"),   // ← 只填了 src
    },                                              // ← name 属性没取
})
```

`nodeGetAttr(node.Attributes, "name")` 本来就能拿到——同一个函数下面就在用它取 `src`。

## T3. 验证过的修复（**一行**，在 /tmp 影子里验的，仓库未动）

```diff
 			frame: &cdp.Frame{
-					ID:  node.FrameID,
-					URL: nodeGetAttr(node.Attributes, "src"),
+					ID:   node.FrameID,
+					URL:  nodeGetAttr(node.Attributes, "src"),
+					Name: nodeGetAttr(node.Attributes, "name"),
 				},
```

**两条独立的影子验证**（都在 `/tmp`，仓库零改动）：

| 影子 | 内容 | `TestSnapshotBDD` | `go test ./...` |
|---|---|---|---|
| `/tmp/fixtest` | 这行修复 + 我改过的测试 | **4 Passed / 0 Failed** | **53 PASS / 0 SKIP / 0 FAIL** |
| `/tmp/fixtest2` | 这行修复 + **原封不动的原始测试** | ✅ `ok 4.228s` | — |

**`/tmp/fixtest2` 是关键**：用的是**未经任何修改的原始 spec**，只加这一行代码就绿了 → **测试本身没问题，不需要任何"隔离"改动**。

## T4. 我做了什么 / 没做什么

| 动作 | 状态 |
|---|---|
| 定位真根因（instrument spec + dump） | ✅ §T2 |
| 验证一行修复（两个 /tmp 影子） | ✅ §T3 |
| 删除临时诊断代码 | ✅ §T7 |
| **改被测代码**（`collectFramesFromDOM`） | ⛔ **未做** —— 你明确说「不要改被测逻辑」，我不自行越过 |
| **改测试去绕开**（按 Name 找帧改成就层级/URL） | ⛔ **未做** —— 那等于把真 bug 盖住，与「消灭脏基线」的立项目的相反 |
| 我上一轮加的「Navigate 到 about:blank」测试编辑 | **已回滚**（`git checkout`，与提交版本逐字节相同）。它是基于错误诊断写的，且注释里写着现在已知为假的解释，不能留 |

`git status` 里那一条 `M docs/superpowers/specs/2026-09-16-siteforge-design.md` **不是我改的**（并行编辑），我没碰它。

## T5. 验收命令与计数（按你要求，含 skip 纪律）

环境：你重启的 headless Chrome（`Chrome/150.0.7871.124`，`/home/dev/.cache/chrome-cdp-9222`）。

先说环境事故：上一轮我跑到一半时 **9222 上的 Chrome 崩了**（`/tmp/chrome.log` 在 `0916/104517` 有一组 crashpad 记录；那个实例从 9 月初一直跑着，profile 是 root 的 `/tmp/chrome-cdp-profile`）。**我早先那次失败的套件跑（10:44:34）在崩溃（10:45:17）之前**，所以那些结果是在活着的浏览器上得到的，不是崩溃导致的假象。我没有自己去拉一个 Chrome 起来——那会占住 9222，可能把你原本那个（带登录态的）浏览器顶掉。谢谢你重启。

**当前仓库（原始测试 + 未改代码）的真实现状：**

```
$ go test ./... -v -count=1 | tee /tmp/t1b-full.log
PASS: 52
SKIP: 0        ← 确认没有任何一条是靠跳过换来的
FAIL: 1
?   	cdp	[no test files]
ok  	cdp/cmd	0.005s
FAIL	cdp/internal	14.674s
```

**Q3（集成测试是真跑不是 skip）—— 已核对，全部真跑**（有真实耗时，skip 不会有耗时）：

```
--- PASS: TestBatchCommands_NoHang (1.07s)
--- PASS: TestClickElement_SmallButtonOnClick (4.66s)      ← 真浏览器点击
--- PASS: TestShadowFixtureIsActuallyHidden (0.37s)
--- PASS: TestGetElementCenterPiercesShadowRoot (0.34s)
--- PASS: TestFillTextPiercesShadowRoot (0.90s)
--- PASS: TestClickPiercesShadowRoot (0.46s)
```

ginkgo 侧：`Ran 4 of 4 Specs … 3 Passed | 1 Failed | 0 Skipped`。

**所以：不是假绿，是真红——红在那 1 条，原因是代码 bug，不是跳过、不是环境。**

## T6. 我需要你裁决的一句话

`collectFramesFromDOM` 丢掉 iframe 的 `name`，**是否按 §T3 修那一行？**

- **授权修**：我改这一行 + 跑两条验收 → 应为 `53 PASS / 0 SKIP / 0 FAIL`，然后单独 commit（message 说明：修的是**采纳来的那份 WIP 的既有代码缺陷**，非本次迁移引入，附修前 52/0/1、修后 53/0/0 的输出）。
- **不授权**：那 `go test ./...` 就**到不了全绿**，这条红会带进 Task 2。需要你明确「记为既有欠债」，我不擅自决定。

我倾向**授权修**，理由：它不是「改行为来迁就测试」，而是**代码本来就要填的字段没填**——同一个 `nodeGetAttr` 就在旁边取 `src`；丢掉 `name` 会让下游（Task 2 的 `observe`/`diff`、以及任何按名字认帧的调用方）拿不到帧名。而且**原始测试一行不改就能绿**，这正说明测试是对的。

## T7. 自查

1. **临时诊断代码已删净**：`grep -n "TEMP-DEBUG" internal/snapshot_bdd_test.go` → 无输出；该文件与提交版本 `diff -q` **逐字节相同**。
2. **仓库无 Task 1b 改动落地**：`git status` 只有那条不是我改的 docs。
3. **未改被测代码**：`diff /tmp/verify-branch/internal/client.go tools/cdp/internal/client.go` → 无差异。
4. **`/tmp/fixtest`、`/tmp/fixtest2`** 只是影子验证，不在仓库内。
5. 本轮**没有提交**——我没有能如实写「已修好且全绿」的提交可做。等 §T6 裁决。

---
---

# Task 1b 第二轮：授权后修复 + 全绿

**Status: DONE**　提交：`3e6ded5`

## U1. 落地的那一行

```diff
--- a/tools/cdp/internal/client.go
+++ b/tools/cdp/internal/client.go
@@ -322,8 +322,9 @@ func collectFramesFromDOM(node *cdp.Node, ownerFrameID cdp.FrameID) []domFrame {
 		frames = append(frames, domFrame{
 			parentID: ownerFrameID,
 			frame: &cdp.Frame{
-					ID:  node.FrameID,
-					URL: nodeGetAttr(node.Attributes, "src"),
+					ID:   node.FrameID,
+					URL:  nodeGetAttr(node.Attributes, "src"),
+					Name: nodeGetAttr(node.Attributes, "name"),
 				},
 		})
```

`1 file changed, 3 insertions(+), 2 deletions(-)`。没有碰测试，没有碰别处。

## U2. 验收一：`go test ./internal/ -run TestSnapshotBDD -v`

```
$ go test ./internal/ -run TestSnapshotBDD -count=1 -v
Ran 4 of 4 Specs in 4.195 seconds
SUCCESS! -- 4 Passed | 0 Failed | 0 Pending | 0 Skipped
ok  	cdp/internal	4.203s
```

**4 Passed / 0 Failed / 0 Skipped**。

## U3. 验收二：`go test ./... -v | tee /tmp/t1b-full.log` + 三个计数

```
$ go test ./... -v -count=1 2>&1 | tee /tmp/t1b-full.log
PASS: 53
SKIP: 0
FAIL: 0
?   	cdp	[no test files]
ok  	cdp/cmd	0.005s
ok  	cdp/internal	15.357s
```

**`53 / 0 / 0`**，与影子预算**逐字相符**。

| | 修前 | 修后 |
|---|---|---|
| PASS | 52 | **53** |
| SKIP | 0 | **0** |
| FAIL | 1 | **0** |

**SKIP 仍是 0** —— 这条我按你的要求单独盯了：绿不是靠跳过换来的（集成测试逐条核过有真实耗时，见 §T5）。

## U4. 竞态类 bug，加跑稳定性

原 bug 是偶发的（§T2 的竞态），所以单独加跑：

```
=== BDD suite x5 ===
  run1: ok  	cdp/internal	4.205s
  run2: ok  	cdp/internal	4.198s
  run3: ok  	cdp/internal	4.207s
  run4: ok  	cdp/internal	4.214s
  run5: ok  	cdp/internal	4.207s
```

**5/5 全绿**，不再是「有时候绿」。

## U5. 提交

`3e6ded5 fix: collectFramesFromDOM 漏填 Name —— DOM 并入的帧丢了 iframe 的 name`

单独一个 commit，只含 `tools/cdp/internal/client.go`。message 里写进了你给的三条理由（真 bug 非测试问题 / 竞态性质 / 影响面在下游 Task 2 与 Task 4），并附修前 `52/0/1` 与修后 `53/0/0` 的输出。

> 注：提交时发现历史里多了一个 `1c5f8e9 docs: 人的位置 …`（并行提交，不是我的）。我的 Task 1 迁移提交 `e09e298` 仍是祖先、完好。那条 docs 的改动我全程没碰。

## U6. 待回灌上游（请记进 ledger）

**同一个 bug 在 `cdpcli` 的 `wip/oopif-iframe-ax` 分支上同样存在** ——
`internal/client.go` 的 `collectFramesFromDOM` 一样漏填 `Name`。
本次**刻意不在 cdpcli 里改**（不扩大范围），留待回灌。

## U7. 清理

- fixture 服务已停，**8892 已释放**（`curl` 不可达、`ss` 无监听）
- 我诊断时开的 `:8892` tab 早已关净
- 9222 上现存一个 `http://localhost:8080/mui-datepicker` 页面 —— 那是**测试套件自己**导航留下的（spec 会 `Navigate` 到它）。**我刻意没关**：关掉后 Chrome 页面目标数为 0，后续 `ListPageTargets` 拿不到页 → 集成测试会走 `Skip` → 又变成假绿。
- `/tmp/fixtest`、`/tmp/fixtest2` 两个影子留在原地供你复核，可随时删
- 仓库 `git status` 干净

---
---

# Task 1 修复轮 1：审查三条

**Status: DONE**　提交：`9247aea`（2 files changed, 7 insertions(+), 4 deletions(-)）

## W1. ① 更正 §R8.5 —— 这是一处不实陈述，而且是个盲区

**我原先写「4 个随分支进来的测试文件都在这次 `go test` 里跑过并通过」——不实。**

`internal/close_integration_test.go:1` 与 `internal/targets_integration_test.go:1` 都以
`//go:build integration` 开头，`go test ./...` **不编译它们**。被排除的 4 条：

`TestCloseTarget`、`TestCloseNonExistentTarget`、`TestListPageTargets`、`TestListPageTargetsWithActive`

**「53」的算术**（已复核）：

```
cmd:      13  个顶层 func Test
internal: 44  个顶层 func Test
合计:     57
57 − 4（build tag 排除）= 53   ← 与 PASS: 53 完全对上
```

**我的盲区（值得单独记一笔）**：我被要求盯 `SKIP` 计数，我报 `SKIP: 0` —— **那个 0 是真的**，
但它**证明不了「全跑了」**。build tag 排除的测试**不是 skip，是不存在**：`go test` 的
SKIP 计数压根看不见它们。**「没有 SKIP」与「没有缺跑」是两件事。** 我把前者读成了后者。

### W1.1 补跑 `-tags integration`（两条，如实记录）

**只有 1 个 page target 时：**

```
$ go test -tags integration ./internal/ -v -count=1
PASS: 43   SKIP: 1   FAIL: 0
--- SKIP: TestCloseTarget (0.00s)
    close_integration_test.go:18: 需要至少 2 个 page target，请打开多个 tab 后重试
--- PASS: TestCloseNonExistentTarget (0.00s)
--- PASS: TestListPageTargets (0.00s)
--- PASS: TestListPageTargetsWithActive (0.01s)
ok  	cdp/internal	15.147s
```

**开第二个 tab 后（满足它自己声明的前置条件）：**

```
$ go test -tags integration ./internal/ -v -count=1
PASS: 44   SKIP: 0   FAIL: 0
--- PASS: TestCloseTarget (0.04s)
--- PASS: TestCloseNonExistentTarget (0.00s)
--- PASS: TestListPageTargets (0.00s)
--- PASS: TestListPageTargetsWithActive (0.01s)
ok  	cdp/internal	18.418s
```

**结论**：那 4 条**编得过、跑得动、全过**。唯一的那次 SKIP 是 `TestCloseTarget` **自己显式声明**的
前置条件（需 ≥2 个 page target），**不是静默排除**，凑齐条件即 PASS。

## W2. ② C7 剩的 2 处 —— 已按裁决补上

`Dockerfile` 原先还有两处**活的** cdp-mcp 引用，现都加了 `# Task 8:` 标记（与其余两处同风格）：

```diff
+# Task 8: cdp-mcp 尚未构建 —— 此处的 /usr/local/bin/cdp-mcp 要到 Task 8 才存在
+#         (缺文件不影响 build: 末尾有 || true 兜住)
 RUN chmod +x /usr/local/bin/cdp /usr/local/bin/cdp-mcp \
     && chmod +x /opt/siteforge/entrypoint.sh 2>/dev/null || true
...
 # agent 的 MCP 门
+# Task 8: cdp-mcp 尚未构建 —— 该路径指向的二进制要到 Task 8 才存在
 ENV CDP_MCP_BIN=/usr/local/bin/cdp-mcp
```

我原先「避免扩大 diff」的理由审查方认为成立但不足以越过裁决 —— 接受，已按裁决办。

### W2.1 Task 8 恢复清单（补进 §R4，定点化到行号）

| # | 位置 | Task 8 要做什么 |
|---|---|---|
| 1 | `Dockerfile:28-31` RUN | **加回**第二行 `go build … -o /out/cdp-mcp ./cmd/mcp` |
| 2 | `Dockerfile:29` 标记 | 删标记注释 |
| 3 | `Dockerfile:61-62` | **取消注释** `COPY --from=tools /out/cdp-mcp …` + 删 61 的标记 |
| 4 | `Dockerfile:66` 标记 | 删标记注释（`chmod` 那行本身**不用改**，二进制存在后自然生效） |
| 5 | `Dockerfile:78` 标记 | 删标记注释（`ENV` 那行本身**不用改**） |

即 **4 个引用点 / 6 处要动** —— 其中只有 2 处（#1 #3）是真要恢复代码，另 4 处是删标记。

## W3. ③ gofmt 回归 —— 已修，并附一处**对你那个对照的更正**

**回归属实，且是我引入的**（不是继承的）：`3e6ded5` 改的那个字面量多缩进了一个 tab。
`gofmt -w internal/client.go` 只碰这一处，**纯缩进、无逻辑变化**：

```diff
 			frame: &cdp.Frame{
-					ID:   node.FrameID,
-					URL:  nodeGetAttr(node.Attributes, "src"),
-					Name: nodeGetAttr(node.Attributes, "name"),
-				},
+				ID:   node.FrameID,
+				URL:  nodeGetAttr(node.Attributes, "src"),
+				Name: nodeGetAttr(node.Attributes, "name"),
+			},
```

`gofmt -d internal/client.go` 现在为空；`gofmt -l` 不再列 client.go。

**但有一处要更正你的对照**：你说「`gofmt -l` 另外报的 6 个文件**都是**缺尾换行…其中
`snapshot_bdd_test.go` 仍缺尾换行」。**实测不是这样**：

| 文件 | 尾字节 | gofmt hunks |
|---|---|---|
| `cmd/eval.go` | **无尾换行** | 2 |
| `cmd/root.go` | **无尾换行** | 1 |
| `cmd/snapshot.go` | 有尾换行 | 1 |
| `internal/form.go` | 有尾换行 | 1 |
| `internal/form_test.go` | 有尾换行 | 1 |
| `internal/snapshot_bdd_test.go` | **有尾换行** | 6 |

`snapshot_bdd_test.go` **是有尾换行的**，它的 6 个 hunk 是 import 顺序、单行 `if` 拆行之类的
上游写法（`gofmt -d` 首屏可见），**不是缺尾换行**。只有 `cmd/eval.go`、`cmd/root.go` 真的缺尾换行。

**这不影响你的结论**：`snapshot_bdd_test.go` 确实**一行没动** —— 但那是靠**逐字节同一性**
证明的（`git diff` 对该文件干净、且与 `/tmp/verify-branch` 的导出逐字节相同），
**不是**靠尾换行。结论对、所引证据不成立，两条分开记。

这 6 个文件**本轮一律没碰**（上游风格忠实继承，动它们会污染迁移忠实性这条证据）。

## W4. 验收（两条都跑，带 PATH）

```
$ go test ./... -v -count=1
PASS: 53   SKIP: 0   FAIL: 0
?   	cdp	[no test files]
ok  	cdp/cmd	0.005s
ok  	cdp/internal	14.601s

$ go test -tags integration ./internal/ -v -count=1     # 开第二个 tab 后
PASS: 44   SKIP: 0   FAIL: 0
ok  	cdp/internal	18.418s
```

标准跑的 53 与 `-tags` 跑的 44 + `cmd` 13 = 57 = 仓库全部顶层 `func Test`。
**两条命令的「53」与「44」现在解释得清了**（§W1）——这正是审查方要补的那块。

## W5. 自查

1. 本轮只动 2 个文件：`Dockerfile`（4 行注释）、`tools/cdp/internal/client.go`（3+/3− 纯缩进）。
   **测试文件、docs、其余 gofmt 文件一律没碰。**
2. `gofmt -d internal/client.go` → 空。
3. Dockerfile 现有 **6 处** cdp-mcp 标记（§W2.1 表），全部带 `Task 8:` 前缀，风格一致。
4. 迁移忠实性复验：与 `wip/oopif-iframe-ax` 导出对比，现在只差 `go.mod`（C13 tidy）与
   `client.go`（就是 `Name` 行 + 它的缩进），其余逐字节相同。
5. 我的三个 commit 在历史上仍为祖先：`e09e298`（迁移）、`3e6ded5`（Name 行）、`9247aea`（本轮）。
   期间历史里多出的 docs 提交（`1c5f8e9`、`f60511b`、`aa07d44`）**都不是我的**，我全程没碰。
6. page target 数：跑完 `-tags` 后回到 1 个（`TestCloseTarget` 把我加的第二个 tab 关掉了，
   状态自然复原）。**仍刻意保留 ≥1 个页面目标** —— 0 个会让后续集成测试走 `Skip` 变假绿。
