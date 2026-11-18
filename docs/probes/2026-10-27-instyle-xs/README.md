# instyle「Emmy Rossum」文章失败 —— 2026-10-27

用户：「帮忙看看这个 `www.instyle.com_emmy-rossum-october-2026-instyle-digital-iss` 大量失败的」
（随后补了一句「看起来像是有弹窗，不知道之前脚本有考虑这个没」）。

站点键（FMR 里那一行的真身）：

```
www.instyle.com/emmy-rossum-october-2026-instyle-digital-issue-12146289
```

⚠️ 面板上那个 60 字符形态是 farm 落盘时 `_deployed_path_for()` 的 `[:60]` 截断
（`ad-task.py`），failDiag 的 `site` 是拿这个文件名回填的 —— 按它去查 `formScript` /
`formLog` 都会 404，查后端要拿完整键。

## 失败记录（failDiag 里共 6 条，2026-10-26 ~ 10-27）

| 单号 | 时间（CST） | 机器 | exit | 死法 |
|---|---|---|---|---|
| 26870507 | 10-26 13:23 | 192.168.1.142 | stuck | 第 8 步 `#CartDrawer-Checkout` 等 30~45 秒不出现 |
| 26889319 | 10-27 06:54 | 192.168.1.142 | stuck | 同上 |
| 26895970 | 10-27 09:26 | 192.168.1.142 | stuck | 同上 |
| 26889206 | 10-26 06:50 | 192.168.1.142 | unknown | 第 4 步 `cdp scroll '#header-logo_1-0'` 90 秒超时 |
| 26895277 | 10-27 09:19 | 192.168.1.142 | unknown | 同上 |
| 26895679 | 10-27 09:22 | 192.168.1.142 | unknown | 同上 |

## 结论一：尺码 S 售罄（3 单 stuck）

产物把尺码**写死成 S**（`STATES` 第 4 步 scroll / 第 5 步 form / `FILLS['field']`，
三处都是 `input[name="Size"][value="S"]`）。失败现场页面正文：

```
Size  Select a size  XS  S - Out of stock  M - Out of stock  L - Out of stock  XL - Out of stock
... Out of stock   SOLD OUT
```

2026-10-27 用代理查 Boden 的 Shopify 商品数据
（`GET https://us.boden.com/products/women-knitted-henley-t-shirt-camel-navy-stripe-k1302neu.js`）：

| 尺码 | available |
|---|---|
| **XS** | **true** |
| S / M / L / XL | false |

`S` 被勾上之后 Add to bag 变成 SOLD OUT（禁用）⇒ 购物车抽屉不打开 ⇒ `#CartDrawer-Checkout`
永远不出现 ⇒ 第 8 步等 30~45 秒失败 ⇒ 连续 3 步早停。

⚠️ 关键：**页面默认选中的就是 XS**（服务端 HTML：`<option selected value="…">XS - $43.99</option>`），
按钮在默认状态下是 `Add to bag` 且没 disabled —— 是脚本主动把默认值改成了售罄的 S。

## 结论二：点完卡片新 tab 没跟上（3 单 unknown）

`cdp scroll '#header-logo_1-0'` 的 90 秒超时**不是 Boden 的元素** —— Boden 的 logo 是
`id="logo"`，`_1-0` 是 Dotdash/mntl 的 id 形状（southernliving 产物里就有
`#mntl-card-list-items_1-0`）。说明这 3 单第 4 步还在 instyle 文章页上：

- 点卡片（01:21:26）→ 第 4 步命令同秒发出，而新 tab 实测 **2.26 秒**才冒头、4.3 秒才有地址；
- `_follow_new_tab` 在 `cdp click` 返回后**立刻**看一次 ⇒ 永远看不到 ⇒ 返回空串；
- 下一步在文章页找 `input[name="Size"]` ⇒ 找不到 ⇒ `_relocate` 因 target 没有文字身份
  （把握 0.50）挑到了文章页头图链接 ⇒ 拟人滚动跨整页 > 90 秒 ⇒ `TimeoutExpired`。

## 修法（只动产物，不动骨架）

`forms/sites/instyle.py`：

1. 三处 `input[name="Size"][value="S"]` → **`value="XS"`**。
2. 第 3 步（点商品卡片）加 `'follow_new_tab': True`；`_follow_new_tab` 在该步
   **轮询等新 tab**（`TAB_FOLLOW_SECONDS = 8.0`，每 0.4 秒看一次），并优先选地址含
   `FOLLOW_URL_HINTS = ("us.boden.com",)` 的那个 —— 同一次点击还会弹一个 taboola 广告 tab
   （`newscasthk.com/swiper6/`），按「最新」挑会切到广告页上。
3. `_perform` 每步把 `self._follow_new_tab_wait` 按账本上的 `follow_new_tab` 重设，
   `_do` 只在 click 那一支读 —— 普通点击（加购、同意、结账）一秒都不多等。

改动前的完整产物留档：`instyle.before.py`（= FMR 上原来那份，按行尾归一后
sha `150ef1fe7204fbe2…`）。

## 验过 / 没验到

真站（192.168.1.197 的 Bit 窗口，Windows 桌面档案）：

- ✅ **新 tab 跟进**：第 3 步点击后，第 4 步的 trace `url` 是
  `https://us.boden.com/products/women-knitted-henley-t-shirt-camel-navy-stripe-k1302neu`
  —— 改动前这一步有 3/6 会留在文章页上。
- ❌ **尺码 XS 的整单**：这个窗口是**桌面 UA**，Boden 给的是另一套版式
  （`fieldset.size-selector` 是 `display:none`，可见的是自定义下拉
  `#dmpg-size-dd-select`），所以 `input[name="Size"][value="XS"]` 在 DOM 里是 0×0，
  `cdp form` 按纪律拒绝点它。生产那 6 单全是 **iPhone UA**（failDiag 现场里写着
  `CriOS/131`），那套版式下 radio 可见 —— 生产日志里同一格 `value="S"` 的
  `cdp form --check` 是**成功**的，XS 与 S 是同一个元素、同一种操作。
- 试过用 CDP `Emulation.setUserAgentOverride` 逼近移动版式：服务端按 iPhone 渲染、
  客户端 `navigator.userAgentData.mobile` 仍是 false ⇒ 页面抖回桌面版式；
  而且 CF 对「Windows 指纹 + iPhone UA」直接拦。**结论：桌面档案窗口做不了移动版式验证。**

顺带证实了用户说的弹窗：文章页有 **Dotdash newsletter 弹层**
（`INS_Overlay_Celebrity_News_Desktop_30sec`，加载 30 秒后弹出）。骨架的「盖住整页的模态」
钩子理论上接得住（`role=dialog`、87%×81%），但那一单里关闭键是 `display:none`
（iframe 创意是 `about:blank`，没加载出来）⇒ 钩子选中不了。**它不是这 6 单失败的原因**
（生产单都在 12 秒内点完卡片），但跑得慢或重试时会挡住。

## FMR（2026-10-27 已推 + 已启用）

```
LOCAL bytes 319100 sha256 8f7e86903b1c5616…
BEFORE site=…/emmy-rossum-october-2026-instyle-digital-issue-12146289
       sha256 743fef8b… status=0   norm==expect(150ef1fe…) True
WRITE {"ok": true, "status": 200, "msg": "success"} → 回执 sha 8f7e8690… 一致
AFTER（正常读 404，status=0）→ POST formConfig/status {"site":…,"status":1}
       → {"status":200,"msg":"success"}
AFTER_ENABLE 正常读 200：sha 8f7e8690…、equals_local True
formConfig/sites：config_id 219、status 1
```

## 残余风险

- **XS 库存**：XS 是当前唯一有货尺码；XS 卖完之后这一格还会再挂。根治要么换一张卡片/商品，
  要么让产物按「第一个可买尺码」选（骨架目前不支持，得改生成器）。
- **桌面版式**：本产物只按移动版式录的（生产窗口全是 iPhone），桌面档案下尺码选择用不了。
- **newsletter 弹层**：真实生产窗口里创意若加载出来，关闭键可见时现有钩子能接住；
  创意空白（`about:blank`）时无人可点。
- 文章 URL **必须带 `utm_campaign`/`utm_source`**：裸链会落到站点的
  「Uh oh, we broke something」错误页（2026-10-27 实测）。
