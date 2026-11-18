# 交接 · 2026-11-06：southernliving「没有任何 step / 查不到日志」+ 加购后直连购物袋页

> 给下一个窗口：先读 §1、§4。站点键：
> `www.southernliving.com/featured/premium/a-new-take-on-sunday-supper-with-venita-aspen-spellegrino`
> （面板上 60 字符那条是 farm `_deployed_path_for()` 的 `[:60]` 截断，不是另一个站。）

---

## 1. 一句话

- **脚本没坏、也不是「没上报」**：11 条失败（11-01～11-05）的原因行都在 FMR 的 `failDiag` 里。
- 看得见的是 **0 条 step**，因为 **FMR 把这份配置自动停用了（`status=0`）**；worker 拿不到脚本 ⇒
  一个 form 动作都不跑 ⇒ 没有 `ai_form_step`、没有 `report_form_log`、也没有 `failDiag`。
- 已**重新启用**（`status=1`）；失败原因的行立刻又能查到了（§3 的证据）。
- 失败的**唯一断点**是「加购之后的 View Bag」：11 条里有 10 条断在这里。已把这一击换成
  **直连 `https://secure-oldnavy.gap.com/shopping-bag`**，222 上真站验过 3 趟（§4），已推 FMR。

---

## 2. 为什么「没有任何 step 上报」且「查不到日志」

链子（每一环都有量到的证据）：

1. **脚本在某些趟会在 Add to Bag 之后找不到 View Bag**（§3）。同样的断点连发 ⇒
   FMR 的 `AiFormConfigFailRate`（当日连续失败 3 次）把 `form_configs.status` 置成 **0**。
2. 停用之后，farm 的 `ad-task.py::_fetch_remote_script()` 请求
   `GET /api/quest/formScript?site=<key>` **不带 `type=debug`**。后端对停用配置回
   `{"status":404,"msg":"script not found"}`（这是本次实测：
   `formScript?...` → 404；同一条加 `&type=debug` → 200 + 226532 字符源码 + `status:0`）。
3. `resolve_site_script()` 因此拿到 `None`。本地 `SITE_ROUTES` 里**没有 southernliving**
   （`grep southernliving scripts/ad-task.py` 一条都没有）⇒ 返回 `None`，不是 `REMOTE_UNAVAILABLE`。
4. 流程继续到 pre-scan 分支，没有匹配类型 ⇒
   `[Form Fill] No pre-scan type match, skipping form fill` → 切单、`return False`。
   **前面没有 `report_form_log`、后面没有 failDiag** ⇒ 面板上就是「没有任何 step」。
5. FMR 的失败读口（`formLog` / `formLogRank`）解析配置时只认 `status=1`；停用期间这个键回
   **404 config not found**。siteforge 的 `/failures` 于是走「同域兄弟键」兜底，摆出来的是
   `…/original-plus/security-for-your-style-adt` 的 1 条旧单 —— 与本键 11 条失败无关。
   **这就是「查不到日志」：不是没上报，是读口在被停用的配置上读不到。**

> 重新启用之后，`GET /failures?site=<键>&type=debug&since=2026-11-01` 立刻回 **11 条**，
> 每条都有 `failDiag` 行（§3 引了原文）。

---

## 3. 11 条失败的共同断点（原文）

任务号（`since=2026-11-01`，全部 `exit=no_success`，机器 `192.168.1.142`）：
`26977621, 26988900, 26988920, 26988931, 26995085, 26997021, 27006414, 27006519, 26999654, 27012769, 27012792`

10 条的 `failDiag` 里都是同一句（第 10/15 步）：

```
第 10 步没做成 —— 现场：{… "texts": ["Old Navy", "Open in the Old Navy app", "OPEN"], …}
第 10 步：页面上没找到「View Bag」，这一步没做成
```

两次选择器都试过、也都等了 4~6 秒：

```
第 1 个选择器没成：a.atb-confirmation-modal__view-bag-link
第 2 个选择器没成：a[href*="shopping-bag"]
```

重试「product」整组（点 S → 点 Add to Bag）之后仍然没有确认弹层；随后
`shopping_bag` 的判据不成立（地址还是 `oldnavy.gap.com/browse/product.do…`），
最后 `没走到成功`。

现场差异都落在 **Add to Bag 之后的那一屏**：
- `…&vid=1#pdp-certona-content`、`CUSTOMERS ALSO VIEWED`、`Open in the Old Navy app / OPEN`、
  甚至 `overlay: div#onetrust-banner-sdk`（26977621；那一趟点击落到了**另一个商品**
  `pid=9166890520003`）。
- 26997021 是唯一一条 13 步都做了的：仍然没有成功（View Bag 后没落到购物袋页）。

结论：**加购确认弹层在该站某些版式/慢网/移动版下根本不出现**，脚本却把「点 View Bag」当成
进购物袋的唯一入口。

---

## 4. 本次改了什么（已推 FMR）

底稿：**系统 job-9326581358b6 的产物**（`SKELETON_SHA = de9ba10f2641d00f`，当前 11-06 骨架；
10-28 那份是 `f41404fe9d4923fd`）。这份底稿的 `STATES` 与 10-28 那版语义相同，仍然点 View Bag。
本次只改 `STATES` 里 `product` 这一组的最后一步：

```python
# 改前（点了确认弹层里的 View Bag；弹层不来就整组垮）
{ 'action': 'click',
  'note': '点了「View Bag」（加购确认弹层没出现时，退回页头购物袋链接）',
  'target': { … 'selectors': [ 'a.atb-confirmation-modal__view-bag-link',
                               'a[href*="shopping-bag"]'],
              'text': 'View Bag'}}],

# 改后（加购后直接去购物袋页；地址固定，不依赖弹层）
{ 'action': 'goto',
  'note': '加购后直接去购物袋页（不依赖「加购确认弹层」：这个弹层在某些版式 / 慢网下不出现，'
          '而购物袋页的地址是固定的）',
  'url': 'https://secure-oldnavy.gap.com/shopping-bag'}],
```

除这一步外，`STATES` 与底稿语义一致（唯一差异是底稿给 `goto`/`wait` 带了空 `target: {}`）。

**真站验证（`192.168.1.222`，siteforge-222 的 Bit 窗口）**：
- 两趟**原脚本**（一趟热会话、一趟 `fresh_open()` 冷会话）：`RC 0`，13 步全 `ok:true`，
  终局 `secure-oldnavy.gap.com/checkout`，正文含 `Sign in or join now for free shipping`。
- 两趟**改后脚本**（旧骨架版一次：`south-cand-20261106-113706`；job 底稿版一次：
  `south-cand-20261106-114107`，均为 `fresh_open()` 冷会话）：`RC 0`，第 10 步 `goto` 直接到
  `secure-oldnavy.gap.com/shopping-bag`，`Added to bag … Size: S Qty: 1` 在正文里，
  第 13 步 Checkout 落 `…/checkout?mlink=1,1,BAG_ENCORE_CHECKOUT`，成功判据成立。
- 证据目录（都在 222 容器内）：`/opt/siteforge/runtime/selftest/` 下
  `south-probe-20261106-112625`（原脚本冷）、`south-probe2-20261106-113021`（原脚本热）、
  `south-cand-20261106-113706`（旧骨架改后）、`south-cand-20261106-114107`（最终版改后）。

**FMR 写入**（`formScript/update` + `status:1`）：
- 本地 `350147` 字节，sha256 `5c292516887912fd…`
- 回读逐字节一致（LF），`sha256` 相同；正常读（不带 debug）回 **200** ⇒ 已启用
- 写前版本：`0106c2c3…`（本刀第一版：旧骨架 + 同一步改动）
- 重新启用（`formConfig/status`）发生在写入之前，operator 写明本次原因；启用即可让
  `AiFormConfigFailRate` 的连败计数从头开始（它丢弃配置最后一次改动之前的行）。

**worker 下载验证**（auto-farm 容器内直接调 `resolve_site_script`）：
```
[RemoteScript] 用后端的脚本（问 …/a-new-take-on-sunday-supper-with-venita-aspen-spellegrino
  → 命中同一键） version=?, 350147 字节 →
  …/forms/_remote/www.southernliving.com_featured_premium_a-new-take-on-sunday.py
BYTES 350147 SHA 5c292516887912fd HAS_GOTO_BAG True
```

---

## 5. 还没解决的 / 下一刀

1. **停用期间的失败仍然不可读**（本刀只恢复了这一个站）。根子是 farmer 的
   `formLog` / `formLogRank` 对停用配置一律 404；`formScript` 有 `type=debug`，这两个读口没有。
   建议 farmer 侧给这两个读口加同样的 `type=debug`（`resolveConfig($site, true)`），
   让「被自动停用的站」的失败与原因仍然查得到。
2. **worker 侧「没有脚本」这一支太安静**：`No pre-scan type match, skipping form fill` 只是
   切单，不写 formLog / failDiag。建议在 `resolve_site_script` 返回 `None` 且本地无路由时，
   至少写一条可诊断的记录（例如把「后端无脚本 / 配置停用」写进失败原因）。
3. **加购本身仍可能静默失败**（尺寸变体更新慢时 Add to Bag 没加进去）。改了直连袋页后，
   这种趟会停在「空购物袋 → Checkout 没成功文案」，仍是**如实失败**，不会再被弹层卡住；
   如果连续 3 次再触发自动停用，就先看 `failDiag` 里败在 `shopping_bag` 还是 `checkout`。
4. 本刀只在 222 上真站验了 3 趟；生产 worker（`192.168.1.142`）按接口下载就是新这份，
   下一单即可见真章。

---

## 6. 现场留下的东西

- 本地产物：`forms/sites/southernliving.py` 与 `forms-222/sites/southernliving.py` 同一份
  （来自 job-9326581358b6，350147 B，sha `5c292516887912fd…`；只改了 product 组最后一步）。
- FMR：键同上，sha `5c292516887912fd…`，350147 B，已启用。
- 失败记录（已恢复可读）：`since=2026-11-01` 共 11 条；`failDiag` 行 id 848/853/854/855/857/863/868/869/870/872/876。
- 222 容器里的探针脚本（临时，可删）：`/opt/siteforge/forms/sites/southprobe.py`、
  `southcand.py`、`/tmp/south_probe*.py`、`/tmp/south_cand_probe.py`。
