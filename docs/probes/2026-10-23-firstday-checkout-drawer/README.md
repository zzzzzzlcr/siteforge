# firstday.com 加购之后结账按钮消失 —— 2026-10-23

用户：「点击 add to cart，点击 checkout；选不到 SECURE checkout，因为自测的时候 SECURE
消失了，但是只要求 checkout 他也会要求选 SECURE checkout」
任务：`job-863ba9a731d7`（8099，10-22 14:52 起跑，`selftest_cap` 收的尾）。

## 结论（真站复现坐实）

**站点自己的购物车抽屉被通用「盖住整页的模态」钩子当遮挡点掉了** —— 而下一步要点的
`CHECKOUT+` 就在抽屉里。

| 项 | 值（2026-10-23 真站量到） |
|---|---|
| 抽屉容器 | `aside#main-nav.js-menu.js-menu--right.ab-cart-drawer[role=dialog][aria-modal=true]`，rect `[0,10,377,757]`（视口 `377×757`） |
| 关闭键 | `button.firstday-cart__flyout-close.js-menu__close`，文字 `Close shopping cart`（24×24） |
| 结账按钮 | `button#checkoutButton`，**可见那颗文字是 `CHECKOUT+`**，rect `[20,640,337,48]` |
| 同名隐藏那颗 | `button#checkoutButton`，文字 `SECURE CHECKOUT`，rect `[0,0,0,0]`，`display:none` 一类 |
| 成功判据 | 点 `CHECKOUT+` 之后到 Shopify checkout 页，正文里有「Yes, keep me up to date on tips and exclusive offers from First Day…」 |

`_MODAL_BOX_JS` 的三条窄口径（模态语义 + 宽高都 ≥80% 视口 + 一颗可见的 close 控件）
**抽屉全中** ⇒ 每次点完 `Add To Cart`、下一步动手之前，`_clear_obstructions` 都把抽屉点关。
自测的 trace 原话（`job-863ba9a731d7`，三版稿都一样）：

```
这一步动手之前之后 把「盖住整页的模态」点掉了（[data-sf-consent="1"]，'Close shopping cart'）
第 5 步/第 6 步：页面上没找到「CHECKOUT+」，这一步没做成
```

## 复现（8100 的 Bit，192.168.1.222:61129）

`before-fix.trace.jsonl`：用 10-22 那版 `forms/sites/firstday.py` 原样跑一遍。

```
第 2 步：点了「Add To Cart」…
这一步动手之前之后 把「盖住整页的模态」点掉了（…'Close shopping cart'）…
第 3 步：页面上没找到「CHECKOUT+」，这一步没做成
```

对照：手动点 `Add To Cart` 之后直接点 `#checkoutButton`，能到 checkout 页、正文含成功判据
⇒ **因果成立**：挡路的是我们自己的弹层钩子，不是站点、不是资料、不是代理。

## 修法（已改骨架 + 产物，已推 FMR）

判据：**这一步要点的东西就在这个模态里 ⇒ 它不是遮挡，是流程本身 —— 不点关。**

- `agent/template.py`：新增 `_MODAL_HOLDS_TARGET_BODY` / `_modal_holds_target_js(target)` /
  `_MODAL_HOOK_NAME`；`_clear_obstructions(why, target=None)` 在答「盖住整页的模态」时再问
  一句「这一步的 target（text + selectors）在不在这个模态里」，在 ⇒ 不点、记一行 `ok=None`
  的 popup 账；`_perform` 把 `target` 传进去。
- `forms/sites/firstday.py` / `firstday.candidate.py`：同一处补丁（产物是 10-22 那版骨架渲的，
  没有整体重渲）。
- 只挡「盖住整页的模态」这一行：同意层照旧先做决定（它里面不会有下一步要点的东西）。
- 目标既没 text 也没 selectors ⇒ 返空，照旧清弹层（不猜）；探针读炸 ⇒ False（不许变成新的失败点）。

## 验过

`after-fix.trace.jsonl`（同一台 8100 Bit、干净重开 firstday.com）：

```
第 2 步：点了「Add To Cart」…
认出了「盖住整页的模态」（TEXT）—— 但这一步要点的「CHECKOUT+」就在它里面：…不点
第 3 步：点了「CHECKOUT+」
等到了：成功判据成立了
```

测试：

- `tests/test_template.py::test_a_modal_that_holds_the_step_target_is_not_dismissed`（新，先红后绿）
- `tests/test_template.py -k 'popup or modal'`：7 passed
- `tests/test_popup_hooks_js.py`：6 passed
- `tests/test_loop_template.py -k 'popup or machinery'`：2 passed
- `tests/test_lint.py`：126 passed
- 整套 `tests/test_template.py`：**156 passed / 3 failed**。三条里两条
  （`test_actions_go_through_cdp`、`test_legacy_cdp_gaps_are_said_in_human_words`）
  在**净树 HEAD** 上同样红（拿 `git archive HEAD` 摊出来的树跑过 A/B，与这次改动无关）；
  第三条是 `fixtures/reference_site.py` 陈旧 —— 改模板就该重渲，已按用例提示
  `python3 tests/test_template.py` 重生，单跑已绿。

## 还差

- `agent/template_loop.py` 的 `clear_popups()` **还没有**这道判断（循环型那套的调用处
  没传 target）；firstday 是表型，不受影响，但以后循环型站遇到购物车抽屉会踩同一脚。
- 产物没有整体重渲（`SKELETON_SHA` 仍是 `af1e560d19524b8e`，比当前骨架旧）——
  这次只补了这一处；要重渲另开一趟。

## FMR（2026-10-23 20:15 已推）

`push_fmr.py --apply`（本目录）：

```
LOCAL forms/sites/firstday.py  bytes 318892  sha256 977c00ecadf4c1490b6820080834013be27db74fdc684cf00853ace491a0a311
WRITE ok status 200 msg success
AFTER site=firstday.com type=py sha256=977c00ec…（与本地逐字节一致，行尾归一后亦一致）
DEBUG_READ status=1
CONFIG   {"site":"firstday.com","config_id":216,"status":1}
```

写之前 `formConfig/sites` 有 310 个站、没有 firstday；写完变成 311 个，
`firstday.com` 的 config 行是 `config_id 216 / status 1`（脚本与 config 都启用了）。
`firstday.candidate.py` 与 `firstday.py` 逐字节相同。
