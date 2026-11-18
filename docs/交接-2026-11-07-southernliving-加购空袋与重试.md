# 交接 · 2026-11-07：southernliving「大量失败」复查 —— 加购没进袋 + 配置又被停用

> 站点键：
> `www.southernliving.com/featured/premium/a-new-take-on-sunday-supper-with-venita-aspen-spellegrino`
> （面板上 60 字符那条是 farm `_deployed_path_for()` 的 `[:60]` 截断；failDiag 用截断键查，
> formScript / formLog 用完整键。）

## 1. 一句话

- **11-06 那刀（直连购物袋页）是生效的**：11-06 当天 0 条失败，11-07 只有 **1 条**（`27058503`）。
- 那 1 条的新断点不是 View Bag，而是**购物袋是空的**：加购没真的加进去。
  截图 OCR 里是 `Saved For Later (0 Items)` + BEST SELLERS，没有商品行、没有 Checkout。
- 打开时这份配置**又被停用了**（`formScript?type=debug` → `status:0`，`formLog` → 404）。
  停用期间 worker 拿不到脚本 ⇒ 后面的单会静默失败（见 11-06 交接 §2）。
- 已改成「加购 + 购物袋 + Checkout 同一组」，**组重试会回到商品页重新加购**；
  已推 FMR 并启用（回读 200、`status:1`、源码逐行一致）。

## 2. 数据（FMR 实查，2026-11-07）

`failDiag`（site 用截断键，2026-10-01～11-08）：**29 条**，全部 `exit=no_success`、机器 `192.168.1.142`。
按天：10-27 5 条、11-04 4 条、10-30 4 条、11-02 3 条……**11-06 0 条、11-07 1 条**。

11-06 之后唯一一条 `27058503`（2026-11-07 09:19:48）关键行：

```
第 9 步：点了「Add to Bag」
等了 10 秒这一页还没到 readyState=complete（照旧往下走）
第 10 步：打开了 https://secure-oldnavy.gap.com/shopping-bag
第 12/13 步：button[data-testid="checkout-button"] 滚不动 / 等 45 秒没出来
第 13 步没做成 —— 现场：… "texts": ["Gap", "Old Navy", "Banana Republic"]
第 16 步：页面上没找到「Checkout」
**没走到成功**…
```

与 11-06 之前的死法不同：这次**没有** `View Bag` 那一步，直连袋页成功；断在袋页是空的。
另外，加购之后日志里**没有**「把『盖住整页的模态』点掉了」那一行 ——
11-06 的验证趟（222 冷会话）有这一行，说明那趟加购成功、确认弹层出来了。
所以这一条不是脚本判据写错，是**加购这一下没生效**。

## 3. 修法（只动产物）

`forms/sites/southernliving.py`：把 `product` 与 `shopping_bag` 两个状态**合成一个**
`product_bag`（9 步）：

1. `goto` 商品页（**新增**，重试时的起点）；
2. 选 `S` → 点 `Add to Bag`；
3. `goto https://secure-oldnavy.gap.com/shopping-bag`；
4. `wait` → 滚到 Checkout → 点 Checkout。

为什么这样改：模板的组重试是「把这一组从头再走一遍」，而旧结构里重试的是
`shopping_bag`（在空袋页上重走，只会在 Checkout 再失败一次）。
合成之后，Checkout 没找到 ⇒ 整组重试 ⇒ **回到商品页重新加购**，再进袋。
代价是正常路径会多一次商品页导航（约 10～20 秒）。

`forms-222/sites/southernliving.py` 已同步为同一份。

## 4. 验过（192.168.1.222 的 Bit，iPhone 390×844、dpr 3、冷会话）

| 趟 | 结果 |
|---|---|
| 正式版 · 冷启动（文章页起） | `RC 0`，14 步全做，Checkout 点击后等 3 秒出现成功文案（`Sign in or join now for free shipping`） |
| 强制失败版（购物袋 goto 的 url 置空） | 第 11 步失败 → 日志 `重试「product_bag」这一组（上限 1 次）` → **第 12 步是 `goto` 商品页** → 第 13～16 步重新选码 + 加购 → 再次失败，`没走到成功` |

第二趟证明：**组重试确实从商品页重新开始**，不是又在袋页上空转。

## 5. FMR（2026-11-07 09:4x 已推 + 已启用）

```
LOCAL 350107 bytes  sha256 304595492ccfc6d23349d5194468a15e24adb4f51ea8197d853781987b79c0b4
WRITE formScript/update → {"status":200,...,"sha256":"30459549…","config_id":223}
ENABLE formConfig/status → {"status":200,"msg":"success"}
NORMAL READ（不带 debug）→ 200，238184 字符（= 本地 UTF-8 字符数）
DEBUG READ → status:1，sha 30459549…，与本地逐行一致；含 `product_bag`，不含 `shopping_bag`
```

⚠️ 前面那次部署（11-06）读回来的 sha 是 `e88a6984…`（CRLF 版），与本地 LF 版内容逐行相同 ——
不是另一份脚本。

## 6. 还没解决的

1. **加购成功与否仍然没有直接判据**：现在是「袋页没有 Checkout ⇒ 重试一次」。
   如果两次都空袋，就如实失败（不会再被确认弹层卡住）。
2. 组重试上限仍是 1 次；若连续 3 单还挂在 `shopping_bag`/`checkout`，
   FMR 会再次自动停用（`status=0`）。**下一刀先看 failDiag 的断点，再决定要不要加第三次。**
3. 11-06 交接 §5 的两条（停用配置的 `formLog` / `formLogRank` 加 `type=debug`；
   worker「没有脚本」时写一条可诊断记录）仍然没做。
