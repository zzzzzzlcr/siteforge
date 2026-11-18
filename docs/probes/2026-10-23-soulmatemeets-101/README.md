# 23d247da 挂进 config 101 后为什么「大量失败」—— 2026-10-23

用户：「这个 soulmatemeets.com/land/sp/23d247da 关联的配置101大量失败被我去掉了，看看啥情况」

## 结论（真站复现坐实）

**站点新加了一种 cookie 同意浮层变体，config 101 的 `japansdates.py` 不认识它**：

| 项 | 值（2026-10-23 真站量到） |
|---|---|
| 浮层根 | `div[data-testid="cookie-popup-variation-consent"]`，class `cookie-popup-variation-consent` |
| 按钮 | `[data-testid="cookie-popup-consent-accept-all"]`（Accept all，`cookie-legal-button`）／`cookie-popup-consent-reject-all` |
| 脚本认识的 | `COOKIE_OVERLAYS` 里只有 `.cookie-popup-consent` / `.cookie-container` / `.cookie-main` / `.cookie-popup` / onetrust —— **`.cookie-popup` 不匹配 `cookie-popup-variation-consent`**（CSS class 是整词匹配） |

`_close_cookie()` 第一句是 `if self._cookie_gone(): return False`；因为浮层不在
`COOKIE_OVERLAYS` 里，`_cookie_gone()` 回 **"gone"** ⇒ **关闭分支从来没执行过**。

浮层是 `position:fixed`，正盖在答题卡片上。性别那屏
（`step-content-item-ChoosePrefGender`，选项 `pref-gender-male/female`）量到：

```
document.elementFromPoint(选项中心) = button[data-testid=cookie-popup-consent-accept-all]
                                      ← 不是卡片
```

于是脚本每轮都「点到了」选项（`cdp form --check` 把 radio 勾成 checked=True /
class 带 `pref-gender__item_checked`），页面**纹丝不动**（hash 一直 `#c3RlcDM=` = step3）；
同屏连出 3 次被判成「多选题」，不再点，等到 `IDLE_TIMEOUT=45s`，按 `max_steps` 报失败。

**每一趟都是同一条路** ⇒ 挂进 101 之后 100% 失败。

## 真站证据

### ① 用线上原文跑一遍（`run_prod.py`，sha `41c74af2…`，与 FMR 读口逐字节一致）

```
step 3 年龄屏 -> (文本) 65+
step 4 Preferences -> option-find-friend
step 5 Preferences -> option-have-fun
step 6 ChoosePrefGender -> pref-gender-male
step 7 ChoosePrefGender -> pref-gender-male   ← 同一屏重来
卡住诊断 step=step-content-item-ChoosePrefGender options=['pref-gender-male','pref-gender-female']
答了 5 题后卡住 46s @ …/soulmatemeets.com/land/sp/23d247da/?id_visit_prev=…#c3RlcDM=
run() 返回 False
```

### ② 浮层点掉之后同一屏立刻前进（`probe_after_consent.py`）

```
点 [data-testid="cookie-popup-consent-accept-all"] → 浮层 no-element
click_option(pref-gender-male) → True，hash #c3RlcDM= → #c3RlcDQ=（step4）
click_text('Man') → 到 step-content-item-InputNameAutofill（hash #c3RlcDU=）
```

⇒ **因果成立**：挡路的就是这个浮层，不是选项机制、不是资料、不是代理。

DOM dump 都在 `prefgender/`（`before_click.json` / `c1_after_accept.json` / `c2_after_male.json`）。

### ③ 上一趟（10-22）为什么说「点得动、状态真往前走」

`docs/probes/2026-10-22-soulmatemeets-23d247da/drive_probe.py` 在点 `pref-gender-male`
**之后没有再 read 一次**就收尾了 —— 正好停在「点了、但还没看结果」的那一格。
那次结论对前三屏成立，对第四屏是**没量到**。

## 后端侧对得上

- `formLog?site=soulmatemeets.com/land/sp/23d247da` 现在 **404**（用户已把挂载去掉，
  config 不在了 ⇒ 那 5 单的 failDiag 也读不到了）。
- `formLogRank?date=2026-10-23` 那一行：`fail=5, streak=5, config_id=null, note=没有配置`
  —— 失败是今天连着 5 趟，与「每趟都卡同一屏」一致。

## 修法（未改生产，等人点头）

`japansdates.py` 两处，都是加白名单：

```diff
 COOKIE_SELECTORS = (
+    '[data-testid="cookie-popup-consent-accept-all"]',   # 2026-10-23 23d247da 变体
     '[data-testid="cookie-popup-common-agree-and-close"]',
     …
 COOKIE_OVERLAYS = (".cookie-popup-consent", ".cookie-container", ".cookie-main",
-                   ".cookie-popup", "#onetrust-consent-sdk", ".ot-sdk-container")
+                   ".cookie-popup", ".cookie-popup-variation-consent",
+                   "#onetrust-consent-sdk", ".ot-sdk-container")
```

- 两处都要改：只加 selector 没用（`_cookie_gone()` 先把整个关闭分支短路掉）；
  只加浮层识别的话，现有 `button[class*=cookie]` 也能点到 Accept all，但**显式 testid 更稳**
  （同一浮层里还有 Customize / Do Not Sell，按 class 取第一颗有运气成分）。
- 影响面：config 101 全组共用这一份脚本（10 个站点键，同域还有
  `soulmatemeets.com/land/sp/976b69dc`）。同域大概率同病，只是今天它没有新失败单。
- 验证路：补丁 → 清掉 consent（或换一颗干净 profile）→ 真站走到 wizard2/注册完成再收
  （这一步会**真提交**，要人点头）。

## 已执行（用户 11:28 选方案 1）

| 步 | 结果 |
|---|---|
| 补丁 | `japansdates_PATCHED.py`，**保留 CRLF**，只多 5 行；sha `35444610d226…` / 37926 B；`py_compile` 过 |
| 推 FMR | 用 `soulmatemeets.com/land/sp/976b69dc` 写（写的是 **config 101 那一行**，不是新开配置）；回执 `status:200`，回读 **10 个键全部 = `35444610…`** |
| 挂回 | `form_config_link(23d247da, 101)` → `created:true`；`formConfig/sites` 回读 `config_id:101, status:1`；`formScript?site=…23d247da` 读到 `35444610…`（37926 B，两个 sentinel 都在） |
| 补丁 A/B（真浏览器、合成浮层） | 旧版 `_cookie_gone()=True`（认不出）→ `_close_cookie()=False`（不动手）；新版 `=False` → `_close_cookie()=True`，按钮 clicked=1、浮层消失 |
| 真浮层因果 | 已先量到：真 accept-all 点掉浮层 → 同一屏选项点击立刻前进（`#c3RlcDM=` → `#c3RlcDQ=`） |

**没能整趟重放真浮层**：这颗 Bit profile 已点过 Accept all，`Network.clearBrowserCookies` +
`Storage.clearDataForOrigin` 清光后浮层仍不回来（同意显然记在服务端/更外面），所以最终验证
靠上面两条合起来：真浮层「点掉就前进」+ 合成浮层上的新旧 A/B。**下一趟生产任务就是真验证**，
看 `formLog` 里 23d247da 还出不出现、日志里有没有那句「卡住诊断 …ChoosePrefGender」。

仍未做的：方案 2（把 `_POPUP_HOOKS` 的通用识别搬进平台脚本）。当前这版只认**这一个**变体。

## 方案 2 也做了（2026-10-23 11:45，已上线）

**改了哪里**：`japansdates.py` 内嵌通用同意钩子，调用点改成
`_dismiss_consent() or _close_cookie()`（老 selector 表留作兜底）。判据从
`agent/template.py` 的 `_POPUP_MACHINERY_SRC` **按 AST 取原文**（`build_hooked.py`），
只搬 `_POPUP_HOOKS` 前两行：满视口同意弹层 + 同意条。换浮层名字不用再改表。

**本平台实测后加了两道窄闸**（不加上去会真误点，证据见下面的验收）：

| 闸 | 为什么 |
|---|---|
| 同意条只认 `position: fixed \| sticky` 的容器 | 页脚里带 "Privacy Policy / Cookie Policy"，而 "Arbitration Agreement" 命中 `btnW` 的 `agree` 子串 ⇒ 静态页脚会被当成同意条点掉。真浮层 CSS 就是 `position: fixed` |
| 按文字打标记只认**可见的叶子** | 页面有同文字的容器/隐藏件时，原来会先标记到它们，真按钮反而没点（容器 div 的 innerText 也等于 "Accept all"） |

**验收（真浏览器、合成 DOM，`test_dismiss_consent.py`）**：

```
0_empty              False  没误报
A_real_variant       True   真形状（cookie-popup-variation-consent）被点掉
B_renamed_bar        True   换个 class 名也认得出
C_full_consent_modal True   满视口同意弹层（点的是 Reject all）
D_business_dialog    False  "Special offer / Accept offer" 不动
E_close_only_dialog  False  只有 Close、没有同意语境 不动
F_static_footer      False  静态页脚（"Arbitration Agreement"）不动
```

`smoke_hooked.py`：干净页 `_dismiss_consent()`=False（探针一次 **73 ms**），随后正常走到
性别屏并前进（`step3 → step4`）。没有提交任何东西。

**推送**：sha `4d8859da7e81…` / 51519 B；config 101 十个键 + `23d247da` 回读全部一致；
23d247da 仍是 `config_id:101, status:1`。

**没做的**：
- `agent/template.py` 本身**没改** —— 上面那两条 caveat（静态页脚误认、标记到容器/隐藏件）
  在 siteforge 产物里仍然存在；要不要回补是另一个决定（会动所有产物，得全量回归）。
- 别的平台脚本（goldenagesouls 等）没接这套钩子。

## 第二轮：14:xx 还在失败 —— 不是 cookie，是「同一题」认错了（2026-10-23 15:47 已上线）

13:57 / 14:01 / 14:08 三单（26800520 / 26800522 / 26800681）的 diag：cookie 那屏**已经过了**
（step 1..15 都动了），死在另一处：

```
step 14 ... The -> option-other
step 15 ... continue
卡住诊断 step=step-content-item-Preferences next=False … options=['option-watch-films', …]
step 16..44: nothing actionable … 45s
```

**真因**：`run()` 用 `head[:40]` 判「同一题」，而 `read()` 的 `head` 是 **body.innerText 的前 120 字**
—— 这平台每一屏都从同一行 chrome 开头（`1 2 3 4 Your profile Preferences Finish up …`），
40 字全落在那一行里 ⇒ **不同的问题被当成同一题**。连着两屏 Preferences 就 `repeat>=2`、
判成「多选题」，从此不再点选项，直到 45 秒收摊。夜里那题本身没毛病。

修了三处（都在 `build_hooked.py` 里固定下来）：

| 改 | 为什么 |
|---|---|
| 「同一题」改按**选项集合**认（没有选项才退回 head） | 上面那条 |
| 多选屏：已选中 1+ 且 continue 可见 ⇒ **先按 Next**；挑选项时排除已选中的 | 防止随机再点到已选中的那一颗、把勾去掉（下一轮 Next 随即消失） |
| 重试闸 `repeat >= 2` → `>= 6` | 真站量到**点击会漏**：input 已 checked 但页面不动很常见，第二下才过。两点就收摊太急 |

另外把**满视口同意弹层**的判据收窄了（真站量到误伤）：它原来把整页/页脚（innerText 带
"Privacy Policy / Cookie Policy"、里面有 "Arbitration Agreement"）和**条款屏**（`#terms-agree`
"I Accept"）当成同意弹层，每轮都去点。现在要求容器 `position: fixed|sticky`、
「同意语境」只看容器自己的 id/class/aria-label；强的 `Allow All / Reject All` 那条保留。

**验收（真站、真提交到 wizard1）**：

- 短路径 3/3 成功（修复前 1/2 会卡在 Yes/No 屏）；
- 长路径（强制 pref-gender-female，即生产那三单的路线）2/2 成功，含夜里那题；
- `test_dismiss_consent.py` 0/A–F 仍全过。

**推送**：sha `4c8b9fbe52ca…` / 54388 B；10 键 + `23d247da` 回读一致。

**再验一遍「后端下发的那一份」**（`run_from_fmr.py`，16:12 / 16:14）：从
`formScript?site=…23d247da` **现取**（与 `ad-task.py` 同一个口），逐字节落盘、校验 sha
一致，然后在 222 的 Bit 上真跑：

| 路线 | 结果 |
|---|---|
| 自然挑选项（短路径） | `wizard1`，42 秒 |
| 强制 `pref-gender-female`（生产三单那条长问卷） | `wizard1`，113 秒；夜里那题 step 16/18 都是「已选中 1 个，按 continue」过去的 |

两次都是真提交。第一趟 `--force-long` 2 秒就退是**驱动里的 lambda 写错**（`orig(opts)[0]`
取了字符串首字符），不是脚本；修好重跑即上表。

## 文件

| 文件 | 是什么 |
|---|---|
| `japansdates_41c74af2.py` | 线上 config 101 原文（FMR 读口取回，sha 41c74af2…） |
| `fmr_before_41c74af2.py` | 写前留底（逐字节与上一行相同） |
| `japansdates_PATCHED.py` | 方案 1 补丁版（sha `35444610…`，CRLF 保留） |
| `japansdates_HOOKED.py` | 最终版（通用同意钩子 + 多选/认题修复，sha `4c8b9fbe…`，已推 FMR） |
| `build_hooked.py` | 从 `agent/template.py` 取钩子原文、加窄闸、生成 `japansdates_HOOKED.py` |
| `test_dismiss_consent.py` | 钩子验收：0/A–F 七个合成浮层 |
| `smoke_hooked.py` | 冒烟：干净页不误报 + 正常流程仍前进 |
| `run_hooked.py` / `run_forced_long.py` | 整趟真跑（短路径 / 强制生产那条长路径），`_rpt` 换成空函数 |
| `probe_perfect_evening.py` / `probe_continue_timing.py` | 夜里那题：卡点复现 / 按 Next 前后逐秒观察 |
| `probe_first_prefs.py` / `probe_yes_no.py` | 单选屏点击不推进的四种点法对照 |
| `diag_consent_candidates.py` / `diag_mark.py` | 误认页脚 / 标记落点的那两支诊断（只读） |
| `jd_pkg/` | 原文 / 补丁版 / 钩子版 + `common.py`（真站跑用的那套 CDPHelper） |
| `run_prod.py` | 用原文 `run()` 跑一遍（`_rpt` 换成空函数，不向 FMR 报任何东西） |
| `probe_prefgender.py` | 走到性别屏 dump DOM（选项 / 浮层 / 点击中心点是谁） |
| `probe_after_consent.py` | 点掉浮层再点选项 —— 因果验证 |
| `verify_patch_synthetic.py` | 新旧两版在同一真浏览器上对合成浮层做 A/B（`_cookie_gone` / `_close_cookie`） |
| `verify_patched.py` | 想用真浮层重放的那支（浮层已不再出现 ⇒ 该支走不通，留作记录） |
| `push_fmr.py` | 推脚本（默认只读，`--apply` 才写）+ 逐键回读 |
| `link_fmr.py` | 把 23d247da 挂回 101 + 回读脚本 sha |
| `prefgender/` | 上面几支留下的 DOM 快照 |
| `run_prod.log` | `run_prod.py` 的完整日志 |
