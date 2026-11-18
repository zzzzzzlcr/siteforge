# blavity Quantcast US Privacy 首访弹层 —— 2026-10-20

用户报的：「好像有新的弹窗要点」。现场在 Bit **192.168.1.222**（8100 那台）真站量的。
一句话：**不是 cookie 横幅，是一次性的 US Privacy 选择层**；产物原来的三级 consent 判据
**全对不上**，所以脚本在弹层还在的时候照常点下一步，点了等于点在遮罩上。

## 现场（视口 377×757）

触发用的 URL（Taboola）：

```
https://blavity.com/new-york-student-sentenced-to-year-in-dubai-prison?bna=1&...&utm_campaign=50132464#tblci...
```

第二篇文章（这次只出广告弹窗）：

```
https://blavity.com/health/michael-jackson-vitiligo?bna=1&...&utm_campaign=50132476#tblci...
```

### 弹窗顺序

1. **Quantcast US Privacy 首访弹层**（清掉首访标记后、加载约 **25–35 秒**才建 DOM）：

   | 项 | 值 |
   |---|---|
   | 容器 | `div.qc-cmp-cleanslate`，**全屏** `z-index: 2147483647` |
   | 文案 | `Blavity - Do Not Process My Personal Information…` |
   | 关闭件 | `button.qc-usp-close-icon`（`aria-label="Close"`，38×38 @ (319,52)，**无 id**） |
   | 另一个按钮 | `CONFIRM`（无 id，文字不在接受词表里） |

2. 点 X 之后（X = 保存当前选择）→ **同一个容器**里换成确认框：

   | 项 | 值 |
   |---|---|
   | 文案 | `Your privacy choices have been saved. If you clear your cookies, your preference will be forgotten.` |
   | 唯一按钮 | `button[aria-label="Close success modal"]`（文字 `OK`，**无 id**；class 是哈希 `css-2m0v4k`） |

3. 点 OK → `div.qc-cmp-cleanslate` 消失；剩下自家 cookie 条 `#cookie-popup`
   （`button.cookie__bar__buttons__button--accept`，产物账本里已有这一步）。
4. 广告弹窗（第二篇）：见文末「第二篇文章」—— 是 2026-10-10 那一族。

### 能重触发首访弹层的存储（实测）

```json
{
  "blavity-choice-first-visit-shown": "1",
  "_cmpShown": "true",
  "_acceptanceState": "All",
  "vue-cookie-accept-decline-cookie-popup": "accept",
  "cookie_usprivacy": "1NNN",
  "cookie_gpp": "DBABLA~BVQqAAAAAACQ.QA"
}
```

清掉这些 localStorage 键（`usprivacy` / `IABGPP_HDR_GppString` 一并清）→ reload，
弹层会在 ~25–35 秒后重新出现。**生产每单都是新窗口、清 cookie ⇒ 每单都会遇到它。**

## 原产物为什么点不掉（三道门全对不上）

产物：FMR `blavity.com`，改前 sha `99991e4d…`。

| 判据 | 第一个弹窗（US Privacy） | 第二个（saved 确认框） |
|---|---|---|
| 门铃 `_DOORBELL_JS` | **返回 `""`** —— `qc-cmp-cleanslate` 不含 `cookie|consent|gdpr|privacy`，正文前 200 字里也没有这些词 ⇒ `_clear_obstructions` 直接 return，判官都不叫 | 会响：`text|DIV|qc-cmp-cleanslate css-16yqgc4`（文字里有 `cookies`） |
| 判官 `observe` | 其实看得见：`kind: "overlay"`、`selector: div.qc-cmp-cleanslate`、`dismiss_selector: button.qc-usp-close-icon`（`unique: true`） | 认成 `kind: "cookie-banner"`、`dismiss_selector: #qc-cmp2-usp > … > button:nth-of-type(1)`（`unique: true`，就是 OK） |
| `_consent_banner_selector` | **只认 `kind == "cookie-banner"`**；这个是 `overlay` ⇒ 即使门铃响也会说「没有同意横幅，不点」 | 认对了容器 |
| `_CONSENT_BUTTON_JS` | 只认「`accept|agree|allow|got it` 文字 + **稳定 id**」；X / CONFIRM 都不满足 | OK 不在接受词里、**无 id** ⇒ 挑不出，不点 |
| 旧 `_CONSENT_PROBE_JS`（legacy） | 返回 `""`（要求按钮有稳定 id） | — |

结论：**第一个弹窗完全看不见；第二个门铃响了、判官也认了，但按钮挑不出来。**
这不是判据写错 —— 这一层本来就不在「cookie 同意」那条语义里，它是一次性隐私选择。

> 旁证：`observe` 对第一个弹窗的归类是 `overlay`（不是 `cookie-banner`），
> `dismiss_selector` 给的恰恰就是 `button.qc-usp-close-icon`（唯一）。
> 也就是说「判官看对了、落点也给对了」，只是产物那条路不肯接 `overlay`、也不肯点无 id 的 Close。

## 补丁（产物本体，三处插入）

FMR key：`blavity.com`。

1. **`_PRIVACY_CHOICE_JS`** —— 只在 `div.qc-cmp-cleanslate` 里找**可见**的：
   - `button.qc-usp-close-icon` → `"close"`
   - `button[aria-label="Close success modal"]` → `"ok"`
   - 都没有 → `""`
2. **`Filler._clear_privacy_choice()`** —— 点 X → 再探 → 点 OK → 再探；
   每下都复核（cdp 回 ok 只说明命令发出去了），最多 3 轮，挑不出就**静默返回**。
   **没有**把 `close` 加进通用接受词 —— 只对 `qc-cmp-cleanslate` 这一族生效。
3. 挂在 **`_clear_obstructions()` 第一句** —— 开跑前 / 每次导航后 / 每步动手前三个调用点都带上。

### 字节与指纹

| | 字节 | CRLF | sha256（前 16） |
|---|---|---|---|
| 改前 | 206005 | 3132 | `99991e4db4960f13` |
| 改后 | 210749 | 3201 | `99b1c4234d3c113a` |

FMR 写后回读：210749 B / CRLF 3201 / **一致 `True`**。
写前备份：`py-0fcb4df5b9183a75a0765cf5b774f5b7`（可回滚）。
本目录的 `blavity.com_PATCHED_99b1c423.py` 就是推上去的那份；
改前的 `99991e4d…` 在 `../2026-10-10-blavity-ad-popup/blavity.com_PATCHED_99991e4d.py`。

## 验证矩阵

| # | 用例 | 结果 |
|---|---|---|
| 1 | 首访弹层在场（`close`） | 点 X → `ok` → 点 OK → `""`（层消失）✓ |
| 2 | 只剩 saved 确认框（`ok`） | 点 OK → `""` ✓ |
| 3 | 负向：页面上没有这一层 | 探针 `""`，**零动作** ✓ |
| 4 | 整条漏斗 | **没跑**（不真提交） |

都是用**产物本体的方法**（`Filler._clear_privacy_choice` 绑真 `CDPHelper`、真 `cdp`）
在真站上跑的，不是复刻的等价物。见 `verify_privacy_choice.py`。

⚠️ 弹层是**延迟出现**的（25–35 秒）。钩子挂在「每步动手前」，所以它一冒出来，
下一步前就会被清掉；如果恰好在某一步动作的中途弹出，那一下可能点到遮罩 ——
这由原有的 `_covered_by` 兜住，下一步前再清。

## 第二篇文章（`/health/michael-jackson-vitiligo`）

同一趟里另开的窗，弹窗是 **2026-10-10 那一族广告 creative**，不是新种类：

- 加载约 **50 秒**后建 DOM：`4 close / 31 bx`
- 可见的那一个：`#bx-close-inside-1967264`，`data-click="close"`，`88×54 @ (251,225)`
- 截图：绿色的 Apple Sports 广告（右上 `CLOSE` + X）
- `observe`：`kind: "overlay"`、`selector: #bx-shroud-1967264`、
  `dismiss_selector: #bx-close-inside-1967264`（`unique: true`）、`text: "close dialog"`
- 用**产物本体的 `_clear_ad_popup`** 跑：标记 → 点击 → 复核 `""`（没了）✓
  —— 顺手补上了 2026-10-10 README 验证矩阵**第 7 条**（真站正向 × 补丁本体）的缺口。

## 文件

| 文件 | 是什么 |
|---|---|
| `blavity.com_PATCHED_99b1c423.py` | 推上 FMR 的那份（210749 B / CRLF 3201） |
| `patch_privacy.py` | 三处插入的补丁脚本（CRLF 原样、锚点唯一断言） |
| `verify_privacy_choice.py` | 跑产物本体 `_clear_privacy_choice` 的验证器（真 CDPHelper） |
| `usp-modal-1.png` | 第一个弹窗（US Privacy，X 在右上） |
| `usp-modal-2-saved.png` | 点 X 后的「choices saved」确认框（OK） |
| `after-consent-clean.png` | 点完 OK + cookie ACCEPT 后的干净页面 |
| `ad-popup-1967264.png` | 第二篇文章的 `bx-` 广告弹窗（Apple Sports） |

## 还没做的

- `agent/template.py` / 系统侧的钩子**没改**（用户指示「系统等会」）——
  以后新生成的产物不会自动带这一刀，需要把同一段搬进模板。
- 没跑整条漏斗，没在 8099 生产上验证；只在 222 真站验了清障方法本体。
