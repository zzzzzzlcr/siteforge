# 同意钩子 tag 白名单 + 复核口径 —— 2026-10-19 真站验收

**一句话**：把 `div`/`span` 放进三条同意钩子（+「按文字打标记」）的 tag 白名单后，
`compareinsulation` 的 CookieScript 同意层**真被关掉了**，复核如实返空（trace `ok:true`）——
这是 spec §5.3「循环型真站能关掉同意层、并如实复核」的**第一条正向证据**。
同日第二小刀又修了复核口径：只认可点行（`_REPORT_ONLY` 不算「还有」）+ 1s/0.2s 短轮询
（同意层异步消失），两站最终都 `ok:true`（§2.5）。

## 0. 机器 / 窗口 / 产物

- **真站**：Bit **`192.168.1.222`**（容器 `siteforge-222` / **8100**）。8099 没碰。
- **窗口**：09:35 开（compareinsulation）→ 09:37 换窗（wildnutrition）→ 09:38 关；
  09:49 第三窗（hook-2-only 变体）→ 09:50 关；10:26 第四窗（复核口径首测，看到异步假阴性）→
  10:36 第五窗（最终 compareinsulation）→ 10:37 第六窗（最终 wildnutrition）→ 10:38 关。
  每趟都 `bit.sh close` 过，事后没有残留窗口。
- **出口**：当前 `gost :1081` 链是 **US**（`gate-us.ipoasis.com`），**没有切链**。
  与 2026-10-16 那一趟同一条思路（「出口国家没量、也没切链」）；这一趟同样**不声称**国家口径。
- **产物**（都是**当前骨架**重渲的）：
  - 循环型：`/tmp/ci_rerender.py`（154,030 B）—— 按 2026-10-16 实测 §2.1 的绕闸脚本重渲；
  - 固定步骤表：`forms-222/sites/wildnutrition.render2.py`（291,736 B）—— `tools/rerender_site.py`。
- **代码改动**：`agent/template.py` 的 `_POPUP_MACHINERY_SRC` 里四处同源 tag 闸：
  `_MODAL_BOX_JS` / `_CONSENT_MODAL_JS` / `_CONSENT_HOOK_JS` / `_MARK_CONSENT_BY_TEXT_JS`。
  新加的 `div`/`span` 还有两道附加闸：**≤40 字短文本**（防散文）+ **只认叶子**
  （子元素里带文字的是 wrapper，点它是点空气）。
  ⚠️ 其中 `_CONSENT_MODAL_JS` 整块是工作区里别人未提交的 Fides 块 —— 本刀提交**不含它**；
  §1.2b 的变体专门验证「去掉第 1 行」也能关掉，给干净检出兜底。

## 1. compareinsulation（循环型，正面）

页面：`https://check.compareinsulation.io/`（首载，`#cookiescript_injected` 在场）。

### 1.1 点之前：四条钩子拆开问（`hook_by_hook.py`）

```
盖满视口的同意弹层   '#cookiescript_reject|DECLINE ALL'
同意条              '#cookiescript_accept|ACCEPT ALL'
盖住整页的模态       ''
广告 creative       'TEXT|×'
_CONSENT_BOX_JS（还有没有同意类容器）: '1'
```

现场几何（`consent_facts.py`）：

```
#cookiescript_injected   fixed [0,542,362,215]  (视口 377×757 ⇒ 高只占 28%)
#cookiescript_accept     DIV "ACCEPT ALL"  [15,683,157,38]
#cookiescript_reject     DIV "DECLINE ALL" [182,683,165,38]
```

### 1.2 产物本体（`verify_popup.py ... loop`，**不带** `--entry-url`，用同一屏）

```
[probe] e2e=0.279s page=1.0 ans='盖满视口的同意弹层|#cookiescript_reject|DECLINE ALL'
[探到] 交给产物本体 `clear_popups()`
INFO form_fill_loop: tag-whitelist-2026-10-19之后 把「盖满视口的同意弹层」点掉了
      （#cookiescript_reject，'DECLINE ALL'）—— cdp 回话：看起来成了
[复核] 再问一遍同一张表：'<空>'
```

trace（`tag_whitelist_ci.trace.jsonl`）：

```json
{"step": 0, "action": "popup", "ok": true, "progress": null, "soft": true,
 "note": "点完复核：这一屏没有它了（探之前有、探之后没有）",
 "progress_why": "点完复核：这一屏没有它了（探之前有、探之后没有）"}
```

### 1.2b 去掉第 1 行（模拟**干净检出**：提交里没有那块 Fides 钩子）

从同一份产物里删掉 `("盖满视口的同意弹层", _CONSENT_MODAL_JS),` 这一行，换一个新窗口
重跑同一屏：

```
[probe] e2e=0.086s page=2.7 ans='同意条|#cookiescript_accept|ACCEPT ALL'
[探到] 交给产物本体 `clear_popups()`
INFO ... 把「同意条」点掉了（#cookiescript_accept，'ACCEPT ALL'）—— cdp 回话：看起来成了
[复核] 再问一遍同一张表：'<空>'
```

trace（`tag_whitelist_ci_hook2.trace.jsonl`）：`ok:true`。
⇒ **提交里的第 2 行单独就够关掉这一屏**；干净检出上不依赖工作区里那块 Fides 钩子。

### 1.3 点之后（独立复核）

```
盖满视口的同意弹层   ''
同意条              ''
盖住整页的模态       ''
广告 creative       ''
_CONSENT_BOX_JS: '1'
```

`consent_facts.py`：`#cookiescript_injected` 的 rect 变成 `[0,0,0,0]`，
`#cookiescript_injected_wrapper` 的 `display` 变成 `none`；`Get Started` 的落点 `self_at:0`
（不再被盖着）。**同意层是真的关了**，不是「探针哑了」。

### 1.4 ⚠️ 这一趟暴露的两件事（都已在代码里收掉）

1. **wrapper 的合并文字也很短**：真站上 accept/reject 外面还有一层
   `#cookiescript_buttons`，它的 `innerText` 是 `"ACCEPT ALL\nDECLINE ALL"`（22 字）——
   ≤40 的短文本闸**拦不住**它，钩子会先挑中 wrapper、点下去是点空气。
   ⇒ 新加的 `div`/`span` **只认叶子**（子元素里带文字的是容器，跳过）。
2. **`_CONSENT_MODAL_JS` 的 `full()` 锚会命中 `body`/`html` 这类满视口元素**：真正注入的
   `#cookiescript_injected` 只有 28% 高，按设计第 1 行应该卡在尺寸闸；
   但 `all` 里包含 `body`/`html`，它们天然满视口 + `boxLabel` 里有 cookie 字样 ⇒
   第 1 行照样答应，而且点的是 `#cookiescript_reject`（它本来就优先点拒绝）。
   （现场能过 `full()` 的容器只有 `body`/`html`；其余 CookieScript 容器都 ≤215px 高。）
   **这一趟没改这个锚**（改了就是另一刀）：它点对了对象、复核也过了；
   但下一刀应该把 `body`/`html` 从「盖满视口的同意弹层」的容器候选里排掉，
   否则这一行在别的站上可能扫到页面其他「decline/accept」文字。

## 2. wildnutrition（固定步骤表，对照）

页面：`https://www.wildnutrition.com/pages/what-supplements-should-i-take`（新窗口）。

```
[probe#1] e2e=1.210s page=11.5 ans='盖满视口的同意弹层|TEXT|Accept All'
[探到] 交给产物本体 `_clear_obstructions()`
INFO ... 把「盖满视口的同意弹层」点掉了（[data-sf-consent="1"]，'Accept All'）
     —— 点完复核：这一屏**还有**「广告 creative」（探之前有、探之后还有）—— 这一下没能关掉
```

点完再拆四条：

```
盖满视口的同意弹层   ''
同意条              ''
盖住整页的模态       ''
广告 creative       'TEXT|Close filters drawer'
_CONSENT_BOX_JS: ''
```

⇒ **同意层关了**（第 1/2/3 行都返空，`_CONSENT_BOX_JS` 也返空）；
trace 的 `ok:false` 是**第 4 行「广告 creative」**造成的 —— 那一行是 `_REPORT_ONLY`
（Ruling 30：只报不点），它的 `'Close filters drawer'` 是站点自己的筛选抽屉关闭键，
**不是同意层**。这是**既有的复核口径问题**（C3 的复核把只报不点那一行也算成「还有」），
不是本刀引入的；本刀只把它的形状量清楚了。

trace（`tag_whitelist_wn.trace.jsonl`）：

```json
{"step": 0, "action": "popup", "ok": false, "progress": null, "soft": true,
 "note": "点完复核：这一屏**还有**「广告 creative」（探之前有、探之后还有）—— 这一下没能关掉",
 "progress_why": "点完复核：这一屏**还有**「广告 creative」（探之前有、探之后还有）—— 这一下没能关掉"}
```

## 2.5 复核口径（同一天的第二小刀）

### 2.5.1 真站（最终两趟）

- **compareinsulation**（新窗，完整骨架）：`_CONSENT_MODAL_JS` 排掉 `body`/`html` 后，第 1 行返空；
  第 2 行答 `同意条|#cookiescript_accept|ACCEPT ALL` → 点掉 → 复核 `<空>`，trace `ok:true`
  （`recheck_poll_ci.trace.jsonl`）。
- **wildnutrition**（新窗）：第 2 行答 `同意条|TEXT|Accept All` → 点掉 → 复核 `ok:true`
  （`recheck_poll_wn.trace.jsonl`）—— 第 4 行 `Close filters drawer` 不再被算成「还有」。
- ⚠️ **关键现场**：compareinsulation 点 accept 之后，**紧接的一次再探仍看到它**（同意层异步消失）；
  立即判 False 会假阴性。复核改成 `1.0s` / `0.2s` 的**短轮询**；返空或只答 `_REPORT_ONLY` 立即收。
- `_CONSENT_BOX_JS` 仍答 `'1'`：那是 `#cookiescript_badge`（36×36 可见徽标），它只被探针/诊断引用，
  **不在产物路径上**；本刀不动。

### 2.5.2 单测

- node 18 格（新增「满视口 body 不许当容器」）；template/loop 的复核用例各加
  「只答 `_REPORT_ONLY` ⇒ True」与「第一次还答、紧接着答空 ⇒ True」两格。

## 3. 单测（先红后绿）

- `tests/popup_probe_driver.js`：11 → **18 格**。新增 5 条正控
  （CookieScript div、wrapper 合并文字短、无 id 的 span、模态里的 span 关闭键、
  模态里包在 wrapper 里的 span）+ 2 条负控（同意容器里的长散文；满视口的 `body` 不许当容器）。
  wrapper 那格是**真站逼出来的形状**：改之前它返 `#cookiescript_buttons`，
  改之后返 `#cookiescript_accept`；模态 wrapper 那格同理（返 `TEXT|Close`，不是 `#close-wrapper`）。
- `tests/test_popup_hooks_js.py`：3 passed（18 格探针 + 正控 + `_MARK_CONSENT_BY_TEXT_JS`
  真跑，div 无 id 也能打上 `data-sf-consent="1"`）。**宿主 / 8100 容器 / 干净 HEAD+本刀补丁
  三处都跑过**，全绿。
- `tests/test_loop_template.py`：**39 passed**（宿主 + 8100 容器）。
- `tests/test_template.py -k "consent or popup or hook_table or modal"`：**13 passed**
  （宿主 + 8100 容器）。
- 整份 `tests/test_template.py`：**1 failed / 144 passed**（1870.23s）。唯一红的是
  `test_reference_fixture_is_current` —— **既知 fixture 取法代价**（交接单 §②：fixture 不提交、
  压着别人 390/21 行；模板一动就要重渲染，这次不重渲），不是本刀的功能失败。
  ⚠️ 这次整份是在**第一小刀**代码上跑的；第二小刀的复核用例（含异步短轮询）已随聚焦 13 条复跑。

## 4. 复现命令

```bash
# ⚠️ 一律 8100（Bit 192.168.1.222）；8099 运营在用，别碰
BIT_ID=$(grep -m1 '^BIT_ID=' /company/siteforge/siteforge.env | cut -d= -f2- | tr -d '"'"'"' \r')
WS=$(/opt/skills/auto-farm-skill/bit.sh open 192.168.1.222 "$BIT_ID")
cd /company/siteforge/docs/probes/2026-10-16-loop-cover

# 循环型：按 2026-10-16 实测 §2.1 的绕闸脚本重渲 /tmp/ci_rerender.py
/opt/skills/auto-farm-skill/cdp navi "https://check.compareinsulation.io/" \
    --host 192.168.1.222 --port 61129
python3 -I hook_by_hook.py /tmp/ci_rerender.py "$WS"
python3 -I verify_popup.py /tmp/ci_rerender.py "$WS" loop \
    --why "tag-whitelist-2026-10-19" --trace /tmp/tag_whitelist_ci.trace.jsonl
python3 -I hook_by_hook.py /tmp/ci_rerender.py "$WS"
/opt/skills/auto-farm-skill/bit.sh close 192.168.1.222 "$BIT_ID"

# 固定步骤表：换窗、重渲、navigate、等待并清
WS=$(/opt/skills/auto-farm-skill/bit.sh open 192.168.1.222 "$BIT_ID")
/opt/skills/auto-farm-skill/cdp navi "https://www.wildnutrition.com/pages/what-supplements-should-i-take" \
    --host 192.168.1.222 --port 61129
python3 -I verify_popup.py /company/siteforge/forms-222/sites/wildnutrition.render2.py "$WS" fixed \
    --wait 300 --interval 20 --why "tag-whitelist-wn-2026-10-19" --trace /tmp/tag_whitelist_wn.trace.jsonl
python3 -I hook_by_hook.py /company/siteforge/forms-222/sites/wildnutrition.render2.py "$WS"
/opt/skills/auto-farm-skill/bit.sh close 192.168.1.222 "$BIT_ID"
```

## 5. 残余（别读成「都验过了」）

- `_CONSENT_MODAL_JS` 的 `full()` 命中 `body`/`html` —— **第二小刀已排掉**（工作区 Fides 块内；
  最终真站：第 1 行返空、第 2 行点掉，见 §2.5）。
- `_CONSENT_BOX_JS` 点完仍答 `'1'`：那是 `#cookiescript_badge`（36×36 可见徽标），不是「弹层还在」；
  它只被探针/诊断引用，**不在产物路径上**，本刀不改。
- wildnutrition 的 C3 复核把 `_REPORT_ONLY` 行算成「还有」—— **第二小刀已修**；复核现在只认可点行，
  并加 1s/0.2s 短轮询（§2.5）。
- ≤40 字与「只认叶子」都是**接受的赌**：文字超过 40 的真实控件、或文字直接挂在
  带图标子元素的 div 上、且没有可点的叶子，都会漏（漏的后果是如实「没敢点」，不是点错）。
