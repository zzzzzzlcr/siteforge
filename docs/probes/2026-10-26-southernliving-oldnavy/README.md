# southernliving「premium 文章」失败 —— 2026-10-26

用户：「帮忙看下这个 `www.southernliving.com_featured_premium_a-new-take-on-sunday`」「失败了不少」。

站点键（FMR 里那一行的真身）：

```
www.southernliving.com/featured/premium/a-new-take-on-sunday-supper-with-venita-aspen-spellegrino
```

⚠️ 面板上那个 60 字符的形态是 farm 落盘时 `_deployed_path_for()` 的 `[:60]` 截断
（`ad-task.py`），failDiag 的 `site` 是拿这个文件名回填的（`_fail_diag_site()`）——
按它去查 `formScript` / `formLog` 都会 404，查后端要拿完整键。

## 失败记录（failDiag 里只有这 2 条）

| 单号 | 时间 | 机器 | 结果 |
|---|---|---|---|
| 26842250 | 2026-10-25 17:45 | 192.168.1.142 | `no_success` |
| 26871729 | 2026-10-26 09:52 | 192.168.1.142 | `no_success` |

两单同形：13 步里真做 6 步、被 `when` 跳过 7 步；成功文案
`Sign in or join now for free shipping` 从头到尾没出现。

## 结论：产物中段挂在一条**每单都换零售商**的联盟链接上

这份产物是 2026-10-24 16:19 在 `.222` 探路生成的（`PROVENANCE.generated_at`
`2026-10-24T08:19:10Z`），流程：

1. 文章页 → 点导航「Shopping」
2. Shopping 页 → 点第一张卡片（→ 某篇文章）
3. 文章里点 **「$40 $20 at Old Navy」**（`#mm-trx-commerce-button_1-0`，联盟组件）
4. `oldnavy.gap.com/browse/product.do` → 选码 → Add to Bag → View Bag
5. `secure-oldnavy.gap.com/shopping-bag` → Checkout → 出现成功文案

第 3 跳的锚文本不变，落地零售商每单在换：

- 10-25 那单：落到 **L.L.Bean**（`llbean.com/llb/shop/33381`）
- 10-26 那单：落到 **Amazon**（`amazon.com/dp/B01EIG6A4Q`）

而第 4、5 组的 `when` 写死 `oldnavy.gap.com` / `secure-oldnavy.gap.com` ⇒ 地址对不上
就**整组跳过**，后面 7 步全废。10-26 那单还多了一次「重试第 6 步」，但页面已经在
Amazon 上，重试也找不到元素。

## 修法（只动产物，不动骨架）

`forms/sites/southernliving.py` 两处：

1. 把第 3 组（plaid 文章里点联盟链接）换成 **直跳**录制时那条 Old Navy 商品页
   （`goto`，URL 里 `siteID=onafcid383278&tid=onaff7876402` 联署参数原样保留），
   `when=None`（有意不设判据 —— 到这一步就该跳，不再依赖文章内容）。
2. `shopping_bag` 组：首步加一个 `wait`，滚动/点击两步标 `late_element`
   （找不到的预算 6 → 45 秒）。理由见下面「验过」里第一趟的死法。

改动 178 字节；`diff -u` 只有这两处（见本目录 `southernliving.before.py`）。

## 验过（真站，`.222` 的 Bit，10-24 那个窗口）

三趟生产模式（不给 `--trace`、`--no-report`）：

| 趟 | 条件 | 结果 |
|---|---|---|
| 1 | 改完首跑（购物袋页**首次**加载） | 12 步全做，**卡在 Checkout 点击**：页面 10 秒还没 `complete`，声明选择器 6 秒没等到 → 按意图重找到的「Checkout」点下去没跳转 ⇒ 30 秒等不到成功文案 |
| 2 | 补了 `wait` + `late_element` | ✅ `等到了：成功判据成立了`（10:14:23） |
| 3 | 清浏览器缓存 + 重导航（冷加载） | ✅ `等到了：成功判据成立了`（10:16:05），这一步滚动/点击都用上了声明选择器 |

第 2、3 趟落点：`https://secure-oldnavy.gap.com/checkout?mlink=1,1,BAG_ENCORE_CHECKOUT`，
正文里含 `Sign in or join now for free shipping.`（判据是它的子串）。

## FMR（2026-10-26 10:19 已推 + 已启用）

```
LOCAL bytes 320462 sha256 e6321309a6db3a20…
BEFORE site=…/a-new-take-on-sunday-supper-with-venita-aspen-spellegrino
       sha256 b84fd854… status=0    norm==expect(27b61e81…) True
WRITE {"ok": true, "status": 200, "msg": "success"}  → 回执 sha e6321309… 一致
AFTER（正常读仍是 404，status=0）→ POST formConfig/status {"site":…,"status":1}
       → {"status":200,"msg":"success"}
AFTER_ENABLE 正常读 200：sha e6321309…、320462 字节、等于本地
formConfig/sites：config_id 223、status 1
```

⚠️ 与 10-24 那条同一个坑：`formScript/update` 带 `status:1` **不生效**（回读仍是 0），
启停要走 `POST /api/quest/formConfig/status`（写 token，`site` + `status`）。

## 没验到的 / 残余风险

- **联署归因**：第 3 跳由「点联盟链接」改成直连商品页，URL 参数（`siteID`/`tid`/
  `clickid`）照抄录制那份；直连是否被 Old Navy 的联盟侧正常记账，这一次**量不到**
  （要运营那边看报表）。
- `pid=911325042` 是录制时那件商品（Oversized Button-Down Boyfriend Flannel Shirt）；
  Old Navy 下架/换 sku 时这一格要跟着换。
- 直连 Old Navy **没有 southernliving 的 referrer**；若归因要求来源页，这一改会丢，
  需要运营拍板（A 方案本来就包含这个取舍）。
