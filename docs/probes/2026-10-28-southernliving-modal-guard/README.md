# southernliving premium 文章：加购确认弹层被自己人点掉 —— 2026-10-28

站点键（FMR 里那一行的真身）：

```
www.southernliving.com/featured/premium/a-new-take-on-sunday-supper-with-venita-aspen-spellegrino
```

⚠️ 面板上那个 60 字符的形态是 farm 落盘时 `_deployed_path_for()` 的 `[:60]` 截断；
failDiag 用这个截断键能查到记录，**要图得按 `task_id` 查**（列表口不带 `fail_img`）。

## 失败记录（10-26 12:14 起，7 单同形）

| 单号 | 时间 | 结果 |
|---|---|---|
| 26876863 | 2026-10-26 12:14 | `no_success` |
| 26870529 | 2026-10-27 01:55 | `no_success` |
| 26870536 | 2026-10-27 03:46 | `no_success` |
| 26870546 | 2026-10-27 06:45 | `no_success` |
| 26895287 | 2026-10-27 10:32 | `no_success` |
| 26905832 | 2026-10-27 17:44 | `no_success` |

（全部 `.142`；`26905832` 还多一种：文章页首屏空白，属于加载链路，不是本条。）

死法逐字同形：

```
第 9 步：点了「Add to Bag」
这一步动手之前之后 把「盖住整页的模态」点掉了（[data-sf-consent="1"]，'close modal'）
第 1 个选择器没成：a.atb-confirmation-modal__view-bag-link
第 2 个选择器没成：a[href*="shopping-bag"]
第 10 步：页面上没找到「View Bag」，这一步没做成
```

## 结论：骨架的「整页模态」钩子把**流程本身**点掉了

Old Navy 加购成功后的确认弹层是 `div.fds_modal__container`：

- 移动版式下**盖满视口**（宽高都是 100%）；
- 关闭键是 `<button aria-label="close modal">`；
- 而下一步要点的 `a.atb-confirmation-modal__view-bag-link` **就在这个弹层里**。

`southernliving.py` 自带的弹窗探针（`_POPUP_PROBE_JS`）在这块真弹层上实测回：

```
盖住整页的模态|TEXT|close modal
```

钩子挂在**每一步动手之前**（`_perform` → `_clear_obstructions`）⇒ 点 View Bag 之前，
弹层先被自己人关了；View Bag 永远找不到。兜底 `a[href*="shopping-bag"]` 还会先命中
无障碍跳转 `href="#shopping-bag"`（只滚到底，不换页）。

**为什么 10-26 本机三趟是绿的**：那趟是 `.222` 的宽窗口，弹层没到视口 80%，钩子不触发；
生产 `.142` 是移动尺寸 ⇒ 每单必中。**这是版式差异，不是站点改版。**

## 修法：把上游 2026-10-23 的守卫移植进产物

`agent/template.py` 已有 `_modal_holds_target`（firstday 购物车抽屉那单）：
`target` 给了、且这一步要点的东西**就在模态里** ⇒ **不点关**（"那不是遮挡，是流程本身"）。
这份产物 10-24 在 `.222` 生成时没有这段（那边模板旧），所以原样搬进来，4 处：

1. `_MODAL_HOLDS_TARGET_BODY` + `_modal_holds_target_js` + `_MODAL_HOOK_NAME`；
2. `_modal_holds_target()` 方法；
3. `_clear_obstructions(why="", target=None)` 里加守卫（只挡「盖住整页的模态」这一行）；
4. `_perform` 调用点传 `target=target`。

对后端现值 diff：`+88 / −3`，只有这 4 处；`py_compile` 绿。

## 验过（合成页）

`/tmp/synth_modal_test.py`（headless chrome 9223 + 本地合成页）：

| 用例 | 结果 |
|---|---|
| 目标在模态里（View Bag） | 弹层**留下**，日志：`这一步要点的「View Bag」**就在它里面**：点掉它等于把这一步自己删掉，不点` ✅ |
| 目标在模态外（背景按钮） | 弹层照旧被点掉 ✅ |
| 同页跑**未打补丁**那份 | 弹层被点掉（红）—— 量表没被放宽 |

⚠️ **真站整单没跑**（用户 2026-10-27 拦下了那次真站端到端）；这一版先推，看生产下一单。

## FMR（2026-10-28 15:10 已推 + 已启用）

```
LOCAL  /company/siteforge/forms/sites/southernliving.py
       bytes 325913  sha256 96c83eb99719ed67…（归一 96c83eb9…）
BEFORE raw d7e79b25…（归一 e6321309a6db3a20…，320462 字节）status 0
WRITE {"ok": true, "status": 200, "msg": "success"} → 回执 sha 96c83eb9…，与本地一致
AFTER（allow_disabled 读）sha 96c83eb9…、325913 字节、status 0
      → POST formConfig/status {"site":…,"status":1} → {"status":200,"msg":"success"}
AFTER_ENABLE 正常读 200：sha 96c83eb9…、325913 字节、含 `_modal_holds_target`
```

⚠️ **生产整单还没验**：下一单落到这个键时，判据是日志里不再出现
`把「盖住整页的模态」点掉了`，而是
`认出了「盖住整页的模态」……但这一步要点的「View Bag」**就在它里面**：不点`，
随后 View Bag 点击成功、落在 `secure-oldnavy.gap.com/shopping-bag`。
