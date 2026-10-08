# gowizard 线上失败 —— 量到的三条线，与各自的修法（**提案，未实施、未验证**）

**写这份东西的时候**：2026-09-20 中午。
**这是什么**：修法**提案**。三条修法**一条都还没写、没跑、没量**。
**这不是什么**：不是修复报告。里面每一个「修完会怎样」都是**推断**，不是实测。
**证据来源**：`/opt/skills/auto-farm-skill/logs/gowizard.log`（7 个生产单）+ `agent/template.py` 源码。
**⚠️ 生产脚本 `/opt/skills/auto-farm-skill/forms/_remote/www.gowizard.com_auto-warranty_.py` 是只读参考，不建议由我改。**

---

## 0. 先把量到的摆出来

### 0.1 七单的结局与入口

**七单全部从 `https://www.gowizard.com/auto-warranty/` 进** —— 「域名/目录不一样」这条**被证伪**。

| 单号 | 结局 | 页面上读到的免责声明 | 真正做了几步 |
|---|---|---|---|
| 26005787 | ✅ 成功 | A | 全程（含填 Full Name / Email / Phone） |
| 26023712 | ✅ 成功 | A | 同上 |
| 26032770 | ✅ 成功 | A | 同上 |
| 26005216 | ❌ | A | 17 步后卡死，**表单一个字没填** |
| 26015658 | ❌ | A | **第 11 步点「330i」失败**，之后全废 |
| **26004759** | ❌ | **全 24 步都是 B** | **0 步**（第 1 步就死） |
| **26033398** | ❌ | **全 24 步都是 B** | **0 步**（第 1 步就死） |

### 0.2 两种免责声明（**同一页、同一 URL**）

```
A（脚本判据认的）：「The listings featured 【are compensated and this】 influences their order on this site.」
B（脚本判据不认）：「The listings featured 【on this site are from companies from which this site receives compensation】.
                    This influences where, how and in what order such listings appear on this site.」
                                   ↓ 两句后面接的【完全一样】
                   「Advertising disclosure Best Extended Auto Warranty in September 2026 Take our short quiz…」
```

**⇒ 同一页，只有那一句换了写法。** 实测 72 趟里 3 趟撞上 B（≈4%）。

### 0.3 「卡住」的共同形状：**帧号冻结**

```
26005216 ❌   7 步（18→24）   活帧恒为 080A7732AC86    ← 一次都没换过
26015658 ❌  13 步（12→24）   活帧恒为 867CEF984273    ← 一次都没换过
26005787 ✅   1 步（18）      活帧 5CD04C1F8F92        ← 只卡一步就出来了
```

**对照（重要）**：成功单同样「账本死号 + 活帧新号」——
⇒ **帧「错位」不是病因，帧「没换过」才是。**

### 0.4 成功单是怎么出来的（源码级）

`agent/template.py:1130` 的 `_relocate`（日志里的「**换了第 3 个找法才找到**」）：

```python
"""最后一跳：重新 observe 当前页面，按 text + role + near 重定位（§5.1b）。"""
model = self._observe()          # ← 自己【重新看一眼页面】
if not model: return []
self._note_live_frames(model)    # ← 顺手把帧表刷新了
```

**两条路对「现在有哪些帧」的口径不一样**：

| 路 | 什么时候重看页面 |
|---|---|
| **读正文**（`_read_frames`） | 只在自认为「手上还好」时才看 —— **而那个判据是坏的**（见线②） |
| **找元素**（`_relocate`，第 3 个找法） | **每次都强制重看** |

```
成功单 26005787  第 19 步：填好了「Full Name:」（换了第 3 个找法才找到）   ← 这里刷新了帧表，之后全对
失败单 26005216  第 19~24 步：【整组跳过，根本没去找元素】                ← 因此从没触发第 3 个找法
                 第 25 步：点了「See My Match」（换了第 3 个找法才找到）  ← 到这儿才刷新，表是空的，晚了
```

**⇒ 救活一趟的不是设计，是「恰好先撞上一次找不到」这个意外。**

---

## 1. 线 ①：判据写死了一句**站点会变**的话

### 证据

生产脚本里**两处判据共用同一句**：

```python
{ 'name': 'auto_warranty',
  'when': { 'url_contains': 'https://www.gowizard.com/auto-warranty/',
            'text_contains': ['The listings featured are compensated and this']}},   # ← 写死 A
{ 'name': 'auto',
  'when': { 'url_contains': 'https://www.gowizard.com/auto/',
            'text_contains': ['The listings featured are compensated and this']}},   # ← 【同一句】
```

`auto_warranty` 是 `start` 之后的**第一个状态组** ⇒ 它一跳过，**「Reject All」「Get Free Quote」两个动作都没做** ⇒ 25 步里 24 步全跳过 ⇒ **0 次真实点击** ⇒ `no_success`。

**这就是 FMR 上 `start → no_success` 那个形状的成因。**

### 根子在哪

`when.text_contains` 是**生成期**从「那一趟观测到的页面正文」里取的一段子串（`browser_agent` 取 `page_text_head`）。
**取的时候没有量过这句话稳不稳定** —— 一次观测 → 当成恒真判据。**又是「同一个事实两个名字」，只是这次两个名字是站点自己起的。**

### 修法

**修法 A（快、糙）**：把那句放宽成两个变体共有的前缀 —— `'The listings featured '`。
- 代价：判据变松。但那个 `when` 的**作用**本来就是「进这个状态之前，确认还在这一页」—— `url_contains` 已经把 URL 钉死了，文案那半只负责「渲染完了」。
- ⚠️ 但这条**只治这一个站**，下一个 A/B 的站照样中招。

**修法 B（对、贵）**：**生成期多观测几次同一页，只保留每次都出现的子串当判据。**
- 落在 `browser_agent` 取判据那一处 + 产物模板的 `when` 生成处。
- 代价：生成期多几次 observe（探路本来就慢）；**而且过不了的那次观测意味着这个状态没法自动判** —— 那要**说出来**（「这个状态的判据不稳定，我没敢钉」），不能悄悄退化成 `url_contains` 一条。
- 这条同时是**类修**，不是实例修。

### 我还不确定的

- **A/B 的触发条件完全没量**。是随机的？按代理出口 IP 分的？按 cookie/州分的？——没量。**如果是按州分的，那「放宽判据」就不只是容错，而是必须的**（有的州法条要求长版披露）。

---

## 2. 线 ②：帧门槛被「广告帧」满足 ⇒ 永远不去找问卷帧

### 证据（`agent/template.py:782-796` + `:815`）

```python
if self.frames and (not self._model_is_fresh()
                    or not self.live_frames
                    or not self._live_frames_ok()):
    self._refresh_live_frames()          # ← 三门全假 = 永远不刷新

def _live_frames_ok(self):
    """手上记着的活帧**现在还活着吗**（读一眼就知道 —— 读得到就是活着）。"""
    for fid in self.live_frames:
        if self._frame_url(fid):
            return True                   # ← 任一帧「答得上」就算还好
```

**⇒ 只要活帧表里有一个「还答得上、但不是问卷」的帧（广告帧 / 残留帧），三门全假 ⇒ 永远不去找问卷帧 ⇒ 读正文只剩主帧（落地页）⇒ 按正文判据的状态全部静默跳过。**

### 这是**已知的形状**，套还在

`fix-live-frames-report.md`（2026-09-17 06:53）自己记的 R-68：

> 老门槛是「某个帧还读得到」，而**广告帧永远满足它**（R-68 量到的形状）：
> 于是再也不去找，读页面只剩主帧 + 广告帧，问卷正文永远读不到。

那次改动修的是「`live_frames` 跟着最近一次观测走（替换，不是累加）」—— **但 `_live_frames_ok()` 仍然是「任一帧答得上」，套只是换了个更干净的错帧。**

### ★ 死锁闭环（这是「卡 12 步」为什么出不来）

```
读正文读不到问卷
  → when 判据不匹配 → 【整组跳过】
  → 跳过 = 这一步根本不去找元素 → 不触发「第 3 个找法」（_relocate）
  → 帧表永远不刷新
  → 读正文永远读不到问卷
  → …（一直转到步数用光）
```

### ★ 第 0 步量完了（2026-09-20 中午）—— 三个帧的 URL **完全分得开**

从日志的「手里这些地址都不含它：…」那格取的实测值（那是 `_urls()` 的原样输出 = 主帧 + 每个活帧自己的 `location.href`）：

```
主帧      : https://www.gowizard.com/auto/?text=Sedan&touchpointId=…&instance=1#chameleon
问卷帧    : https://chameleon-na.www.gowizard.com/forms/7878/default/gowizard#iFrameId=mvfFormWidget-…
广告帧    : https://id-msp.newsbreak.com/sync-nbu?source=2&host=www.gowizard.com
```

**三者互不相同 ✅** —— 按 URL 比是分得开的。

**而且坏形状就在数据里**（出现 32 次）：

```
https://www.gowizard.com/auto/?…  ｜  https://id-msp.newsbreak.com/…
                                     ↑ 【问卷帧不在里面】—— 这就是卡死的形状
```

### ⚠️ 我原来的修法有个洞（已修）

```python
want = {self._frame_url(f) for f in self.frames} - {""}   # ← 错！
```
`self.frames` 是**探索期录的帧号，早死了** ⇒ `_frame_url` 对死号返回空串 ⇒ **`want` 恒为空集**。

**后果要说准**：空集**不是**「判据失效」那么简单 —— 它会让 `_live_frames_ok()` **恒为 False** ⇒ 三门槛恒真 ⇒ **每 3 秒（`LIVE_PROBE_EVERY`）就 `observe()` 一次，从头到尾**。
**碰巧能把 bug 压下去**（刷新够频繁总能抓到问卷帧），但**代价是整趟跑都在反复起进程**，而且**代码做的事和它 docstring 里写的判据不是一件事** —— 那正是这个仓库反复栽的那个形状（「名字说 A、做的是 B」）。**所以它必须改正，不能因为「反正也能跑」就留着。**

**真事实不在帧号里，在各状态的 `when.url_contains` 里** —— 而 `agent/template.py:859` 这段注释**早就量过并写下来了**：

> 实测 gowizard：**19 个状态里 16 个的 `url_contains` 是 `chameleon-…`**，而主帧的地址从头到尾是 `www.gowizard.com/auto/…`。只拿主帧比 → 那 16 组步骤**静默跳过**：产物看着跑完了，其实一步没走。

### 修法（改正后）

```python
def _wanted_frame_urls(states):
    """这条流程【认得】的那几个 URL —— 从各状态的 `when.url_contains` 收。

    ⚠️ 不能用 `self.frames`（账本帧号）去取 URL：那是探索期的号，早死了，
       读出来是空串。真事实在 `when.url_contains` 里。
    """
    out = []
    for st in states or []:
        u = ((st.get("when") or {}).get("url_contains") or "").strip()
        if u and u not in out:
            out.append(u)
    return out

def _live_frames_ok(self):
    """手上这几个活帧里，有没有一个**是我真正要读的那个**。

    光问「哪个帧答得上」不行 —— 广告帧永远答得上（R-68）。
    比的是 URL。⚠️ `self.live_frames` 【天然不含主帧】（`_note_live_frames` 里
    `if fid` 把那行滤掉了）—— 所以「主帧地址命中 `when.url_contains`」这件事
    **不会**把判据撑成真。
    """
    want = self._wanted_urls          # __init__ 里从 STATES 收一次
    for fid in self.live_frames:
        u = self._frame_url(fid)
        if u and any(w in u for w in want):
            return True
    return False
```

**落地条件已核**：`STATES` 是产物里的模块级常量（`agent/template.py:445`），类在 `__init__` 里已经走了一遍 STATES（`:725` 的 `self.frames = _frames_in_states(STATES)`）⇒ 多收一个 URL 集合是现成的路。

### 拿实测数据验一遍（两种形状都过）

| 形状 | `live_frames` | 判据结果 | 对不对 |
|---|---|---|---|
| **好**（成功单那一刻） | [问卷帧 `chameleon-na…`, 广告帧 `id-msp…`] | 问卷帧命中 `chameleon-…` ⇒ **True**（不刷新） | ✅ |
| **坏**（卡死那 6/13 步） | [广告帧 `id-msp…`] | `id-msp.newsbreak.com` 不命中任何 want ⇒ **False** ⇒ 去刷新 | ✅ **这就是修好的那一格** |

### 代价

- 门槛变严 ⇒ 刷新更频繁 ⇒ **更多的 `observe()`**。`LIVE_PROBE_EVERY=3s` 的节流还在，但真站上「每换一页多探一次」是要花时间的。
- **还有一样没量**：`_wanted_frame_urls` 收进来的那几个 URL 里，**会不会有一个恰好是主帧的地址**（比如某状态的 `when.url_contains` 写的是 `/auto/`）。**这不会破坏判据**（`live_frames` 不含主帧），但它意味着那个状态**没法靠帧 URL 判** —— 不影响本条修法。

---

## 3. 线 ③：一次点击失败 ⇒ 整单死

### 证据

```
26015658  第 11 步：页面上没找到「330i」，这一步没做成      ← 就这一下
          第 12~24 步：全部跳过（问卷没前进 ⇒ 每个 Progress: N% 判据全废）
          第 25 步：在【空表】上点「See My Match」
          填过的字段数 = 0
```

现有的兜底已经跑了：`_relocate`（第 3 个找法）找过、没找到 ⇒ 记一次失败 ⇒ `STUCK_LIMIT=3` 才早停。
**缺口是：失败之后没有「退一步重来」** —— 脚本是**纯线性**的，一步没成，后面全部错位。

### 修法

**一步失败后，重试它所在的整个状态组**（从这一组的第一个动作再来一遍）。
- 「下拉没选上 ⇒ 重开一次多半能选上」是这类站最常见的自愈。
- 代价：**重试 = 在真页面上多做几个真实动作**。按运营的口径（「能成功为啥还要继续」/「刷太多不太好」），这个代价要**算清楚再定**：重试一次 = 最多多做这一组的动作数（这一组通常 1–2 个）。
- ⚠️ 重试**要有上限**，而且**要进日志说人话**（「第 2 次重试这一组」），不能静默。

### 备选（更省，但更弱）

`STUCK_LIMIT` 现在的语义是「连着 3 步没成 ⇒ 早停」。**它没做到的是：把「这一组整体重来」和「继续往下磨」分开。** 折中版：**一个状态组内出现失败 ⇒ 这一组结束后回到组头**，而不是一路往下跳。

---

## 4. 放大器：**「跳过」在日志和面板上都是不可见的**

三条线都汇到同一个放大器：

> 一个状态组跳过后，后面依赖它的状态组**连环跳过**；而**跳过 = 不去找元素 = 帧表不刷新 = 永远出不来**。

**⇒ 「卡 1 步」和「卡 12 步」在日志上长得一模一样，结局都是「没走到成功」。运营在 FMR 上看到的只有 `no_success`。**

### 修法

**跳过要能累计成一个信号**：
- 连着 N 步「跳过」且**一步真动作都没做** ⇒ 这不是「这一页不像那个状态」，是**「这一页跟任何状态都对不上」** ⇒ 该**当场停 / 报**，不是磨完 25 步。
- 这一条**独立于线 ①②③**：就算三条都修好，将来还会有新的「对不上」，而**沉默的跳过**永远是最难查的那种。

---

## 5. 诚实清单：**我没量的**

1. **A/B 的触发条件** —— 完全没量（随机？IP？州？）。**这是三条里最要紧的一条没量的。**
2. ~~账本里那几个帧的 URL 长什么样、和广告帧分不分得开~~ —— **2026-09-20 量了**：三者 URL 完全分得开（见 §2），修法成立。
3. **重试一个状态组，真站上成功率是多少** —— 没量。可能重试也没用（比如那个下拉本来就点不开）。
4. **A/B 那两单是不是也刷了量** —— 量过：**0 次真实点击**。所以①这条线**不产生流量**，只烧单。
5. **我给的几种修法，一条都没跑过。**

---

## 6. 顺序建议

按「先量后改、先类后实例」：

```
第 0 步（不改代码，先量）  ：量 2（帧 URL 分不分得开）—— ✅ 【2026-09-20 做完】，修法成立
第 1 步（类修，收益最大）  ：线② 的门槛 —— 它同时解开死锁闭环
第 2 步（类修）            ：第 4 节的「跳过要出声」—— 让以后每一条线都可见
第 3 步（实例修）          ：线① 的判据（修法 A 先止血，修法 B 排后面）
第 4 步（要算代价）        ：线③ 的状态组重试 —— 涉及真站动作数，按运营口径定
```

**⇒ 现在可以从第 1 步开工**（线② 的门槛）。它动的是 `agent/template.py`，
**而那个文件现在没人在改**（Task 15 改的是 `browser_agent.py` / `graph.py` / `service.py` / `state.py`；
Task 14 改的是 `console.html` / `rounds.py`）—— 但要开工仍应与那两个 agent 的收口错开。

---

## 7. 不由我做的事

- **改生产脚本** `/opt/skills/auto-farm-skill/forms/_remote/www.gowizard.com_auto-warranty_.py`
  —— 那是**生产目录 = 部署目录，改完即生效**。要用户点头。
- **开真窗口验证** —— 有外部约束（「刷量会被领导叼」），开之前必须先问。
