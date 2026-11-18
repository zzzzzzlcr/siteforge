# innovatingcanada.ca 订阅弹层「永远等不到」—— 2026-10-24

用户：「www.innovatingcanada.ca_industry-and-business_canada-nuclear 这个也看看」。

站点键（FMR 清单里那一条，`config_id 164`，一直启用）：
`www.innovatingcanada.ca/industry-and-business/canada-nuclear-future-2026/why-canadas-nuclear-choice-is-also-a-supply-chain-choice`

失败记录：10-14 起共 12 条，全是 `exit=stuck`（最近一条 `26805949`，10-24 03:03，CA）。

## 结论

脚本要填的是 **WPMU Hustle module 6** 的订阅弹层
（`#hustle-field-first_name-module-6` / `#hustle-field-email-module-6` / `button.hustle-button-submit`）。
弹层是**按浏览器频控**的：

| 项 | 值（弹出的配置里读到的） |
|---|---|
| 触发 | `trigger: time`，`on_time_delay = 25 秒`（另有滚动 20% / 点击 / 退出意图） |
| 关掉一次 | `no_show_all`，**3 小时**不再出；cookie `hustle-hide-module-6` |
| 订阅成功 | `after_optin_expiration = 365 天`；cookie `inc_optin_*` |

舰队**每台机器一个 Bit profile、跨任务复用**（`bit_id` 来自机器配置）⇒ 一次成功之后
这个 profile 上这个站就再没有弹层了。12 条失败全是同一个形状：

```
第 1 步**跳过**：正文里没有「our 18 partners」…
第 1 个选择器**没找到**（不是被盖着）… 等 38.9 秒还是没出来
第 2 步：没找到「field」这个输入框，没填成
… 连着 3 步没做成，收摊
```

线上截图（`prod_fail.png`，取自 `failDiag.fail_img`）里页面是好的、被滚到了 Categories 区，
**没有弹层**。

## 量到的四件事（8100 真站）

1. **干净会话第一次打开**：弹层 15~35 秒后可见（几次量的结果）；
2. **点一次「Close this module」** → 写 `hustle-hide-module-6`，再开 **70 秒都不出**；
3. **删掉这个 cookie + 真重开** → 弹层 18~23 秒又出来；
4. ⚠️ **`cdp navi` 到同一个 URL 是空操作**（页面里 `window.__sfmark` 原样还在）——
   所以「清完 cookie 重开」必须**先 `about:blank` 再回原地址**，否则等于没重开。
   第一版修法就是踩了这个：日志写着「重开了」，弹层仍然不出。

另外：弹层容器 `#hustle-popup-id-6` 里也有一个**隐藏的 `Thank You!` 成功态**——
拿 eval 强行 `display:block` 会在没提交时就把成功判据读成真（试过一版，已弃）。
所以最终**不强行打开弹层**，只清 cookie + 真重开，让它按站点自己的逻辑出。

## 修法（产物）

`forms-222/sites/innovatingcanada.py`（FMR 那份的本地件）：

- `COOKIE_CLEAR_PREFIXES = ("hustle-", "inc_optin")` + `_clear_site_cookies()`：
  开跑前清掉这个站自己的弹层抑制 cookie（只清这两类，别的 cookie 不动）；
- 清完**先开 `about:blank` 再回原地址**（真重载；见上面第 4 条）；
- 把 firstday 那一套 `_modal_holds_target` 补进来：**这一步要填的框就在弹层里** ⇒
  通用「盖住整页的模态」钩子**不点关**（原来它会把弹层点掉，还顺手写 3 小时抑制 cookie）；
- 开跑前 / 导航后那两处 `_clear_obstructions` 没有 target ⇒ **模态那一行不问也不点**，
  真正的遮挡物留到下一步动手前按 target 判。

## 验过

模拟生产那一类 profile（新会话 + 只有客户端抑制 cookie）：

```
03:13:00 清掉了这个站的弹层抑制 cookie：hustle-hide-module-6
03:13:11 清完 cookie 真重开了同一页：…
03:13:22 第 1 个选择器**没找到**… 先等一等：最多 45 秒
03:13:33 等了 9.6 秒，它出来了 —— 还是这一根地址做成的
03:13:34 第 2 步：填好了「field」…
03:13:38 第 3 步：填好了「email」…
```

完整一趟（debug + submit）：

```
03:12:11 第 2 步：填好了「field」
03:12:27 第 3 步：填好了「email」
03:12:33 第 4 步：点了「没写名字的元素」
03:12:33 成功：判据成立了（页面上那串字 / 网址那一截）
EXIT=0
```

## FMR（2026-10-24 03:15 已推）

`push_fmr.py --apply`（本目录）：

```
LOCAL forms-222/sites/innovatingcanada.py bytes 263559 sha256 1b094b73e6c1c055…
BEFORE site=…/why-canadas-nuclear-choice-is-also-a-supply-chain-choice type=py sha256=fcdec2f7…
WRITE {"ok": true, "verdict": "ok", "status": 200, "msg": "success", …}
AFTER  site=… type=py sha256=1b094b73e6c1c055…（与本地逐字节一致，行尾归一后亦一致）
```

那一行**本来就是启用的**（正常读能读到），不需要再动 `formConfig/status`。

## 还差的

- 这两个钩子（清 cookie / 模态 target 保护）是**产物级**的；`agent/template.py` 里
  已经有 firstday 那套 `_modal_holds_target`，但**没有** `COOKIE_CLEAR_PREFIXES` 这一格，
  而且开跑前那次 `_clear_obstructions()` 仍然会点模态。以后**重渲这个站**会丢这两个钩子。
- 站点的弹层是「一台浏览器只收一次」的生意：清 cookie 让每个任务当新访客 —— 这是
  用户 2026-10-24 点头的口径（「那推送吧」）。要更「正」的话是每单换新 profile（舰队侧）。
