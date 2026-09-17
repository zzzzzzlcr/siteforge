# 修复报告（第十轮）：第一条 `why` 原文 + 停在哪一页 + trace 加法留尾

**状态**：DONE_WITH_CONCERNS —— 三件都做了（`why` 原文 ✓、停在哪一页 ✓、trace 加法 ✓）；
**三遍验收仍未过**（见 §4，不粉饰）
**commit**：`3f9c787`（本轮，只本地 commit，没 push）
**一行测试结论**：`pytest tests/ -q` → **402 passed / 12 skipped**（旧断言一条没放宽）

## 1. 第一件：那 17 步跳过的**第一条 `why` 原文**（逐字，不转述）

**A. 38 步那趟**（`runtime/selftest/gowizard-20260917-074157/baseline`，第一条跳过是第 50 步）：

```
正文里没有「Progress: 50% Sorry we are unable to match you」；页面上**实际看到的**开头那段是：
「___ The listings featured are compensated and this influences their order on this site. Advertising
disclosure Best Extended Auto Warranty in September 2026 Take our short quiz to find an Auto Warrant」；
读页面用的帧：账本 ['F05037969FE1B51C2FF73AE70DF017A8', 'FA00A83AA9F5FFCD866E1CDFA5B4B92A']
／ 活帧 ['08446B208FD9A71AB44DAF2B224B0FA3'] ／ 手上那份观测还是这一页的；
手上那份观测的正文开头：「___ The listings featured are compensated and this influences their order
on this site. Advertising disclosure Best Exte」
```

**B. 另一趟**（`…075432/baseline`，第一条跳过是第 4 步）：

```
正文里没有「The listings featured are compensated and this」；页面上**实际看到的**开头那段是：
「function OptanonWrapper() { }"use strict";!function e(n,t,o){function r(a,u){if(!t[a]){…」
读页面用的帧：账本 ['F0503796…', 'FA00A83A…'] ／ 活帧 （无） ／ 手上那份观测还是这一页的；
手上那份观测的正文开头：「funct…」
```

**两句话读出来的**：
- **A 那趟**：判据要的「`Progress: 50% Sorry we are unable to match you`」是**漏斗「匹配不上」那条分支** ✓
  —— 也就是说**那一趟的账本里根本没有成功那条路**（探索走到的是死胡同）。
  产物重放一条不存在的路，**当然见不到成功文案** —— 这一趟不是产物的错。
- **B 那趟**：页面上那一刻是**一段 `<script>` 源码**（`page_signature()` 与观测的 page_text
  都拿到了它）、而且**活帧是空的** —— 那是页面还在加载/被脚本占着的状态（第八轮那种环境形状）。

## 2. 第二件：它停在哪一页

**A 那趟**：最后一条真做成的步是

```
step            49
action          click
selector_used   button[data-testid="continuecta"]
frame_id        99E474D309BDC58000763CA8D7FD370E     ← 帧内（CDP 那侧拿到了）
progress        False
note            点了「Continue」（换了第 3 个找法才找到）；页面没有变化
```

之后（第 50~55 步）全被跳过，第一条要的正是**「Sorry we are unable to match you」**（上面那句）。
**所以那一刻页面上不是成功页** —— 差的是**整条成功路径**（那一趟的探索没走到它）。
对照：**另一趟 job（`job-040aa5cc8b11`）的探索真的走到了成功页**
（账本最后一条 observe 的正文头就是 `Good news - We've matched you! Your quote is on the way!` ✓）——
也就是说**能不能到终点，首先取决于账本里有没有那条路**。

## 3. 第三件：两个口子

1. **trace 加了尾部与成功布尔**（`agent/template.py`，**加法**：老键一个字没动）：
   `page_sig_tail`（尾部 240 字）与 `success_in_page`（成功文案在不在页面上）——
   这样「trace 里搜不到问卷/成功文案」与「判据真的没读到」能一眼分开（第九轮那个坑）。
   ⚠️ 这一格**没有钉子**（单跑绿、全量套件红：替身是模块级的、`success_in_page` 取的是
   这一步**之前**的页面文字，调用次序一变就翻面；宁缺勿骗，明说没有）。
2. **不加看门狗**（纪律，不是代码）：第八轮实测它会跟服务的窗口管理打架
   （`POST /run` 那一下服务本来就会给探路换干净窗口）。这一轮靠**闸口及时回话**
   （驱动脚本每 4 秒轮询、`waiting` 就回 `continue`）；真丢窗走 `POST /job/{id}/reopen`。

## 4. 三遍验收

见文末（这轮起跑时 window 是新的、闸口有人回话、没有看门狗）。

## 4. 三遍验收（最后读数）

这一轮的探索本身**是好的**：账本 64 步、**最后一条 observe 的正文头就是**
`Good news - We've matched you! Your quote is on the way!` ✓ —— 也就是说**那条路在账本里** ✓。

但自测那三遍（`runtime/selftest/gowizard-20260917-081006/`）：

```
baseline  执行  0 | 跳过 29 | 没做成 0 | success_in_page: False
rerun     执行  0 | 跳过  0 | 没做成 1 | success_in_page: False
第一条跳过的 why（逐字）：
  地址对不上：要含「https://www.gowizard.com/auto-warranty/」；
  手里这些地址都不含它：https://console.bitbrowser.net/?id=812b7d606f92438bb2b581edcf752a84&locale=zh&port=54345；
  读页面用的帧：账本 ['60E6542CF77624B0B739301EE9D98EF6'] ／ 活…
```

**读出来的**：产物跑起来时，**浏览器还停在 Bit 的工作台页**
（`console.bitbrowser.net/…`）—— 而账本第一个状态要的是入口页 →
**连 `goto` 那一步都没轮到**（第一组就被判成「不像」）→ 后面 29 步全跳过。
⚠️ 这是 R-F1 的**副作用**：自测前服务换了一个**干净窗口**（新开），
而新窗口的首页是 Bit 工作台 —— 账本里那个「起点状态」（探索时窗口停在工作台）
**只在探索那一趟的窗口上成立**（URL 里那串 `?id=…&port=…` 每开一次窗口都不一样）。
**这一格我这一轮没有修**（时间用完了 —— 会话已经很长），但它形状清楚、和前面修过的
「判据太严 → 整组静默跳过」是同一族：**换窗之后「起点状态」需要重认**。

**结论（一个字不放宽）**：三遍都**没有**见到 `Good news - We've matched you! …`
→ `delivered=false`、`py_path=null`，`forms/sites/` 里仍然只有 `.gitkeep`。
**「38 步 / 0 没做成」不算成功，「里程碑过了」也不算。**

## 5. 顾虑

1. **换窗之后起点状态失效**（§4 那条）—— 下一轮的第一件事。
   形状：`start` 状态的 `when` 指向「探索时那个窗口的首页」（Bit 工作台，URL 带一次性参数），
   而自测换了干净窗口 → 起点不成立 → `goto` 没跑到 → 全跳过。
   方向：起点状态要么**不设 `when`**（它就是「从哪儿开始」），要么 `when` 只认
   「不像任何站点页」这回事；**别**让「窗口首页」变成一条硬判据。
2. **能不能到终点，首先取决于账本里有没有那条路**：A 那趟（§1）账本里只有
   「Sorry we are unable to match you」那条分支 → 产物再对也见不到成功文案；
   而这一轮另一趟的探索真的走到了成功页 ✓。所以「探索走对路」与「产物能重放」**两件都要看**。
3. trace 的 `page_sig_tail` / `success_in_page` 已加（加法），但**没有钉子**（见 §3）。
4. 窗口寿命：这一轮几趟 job 都在 8~10 分钟内跑完自测（比第九轮那次 30 分钟好），
   但重试循环（draft→lint→selftest→diagnose）仍可能贴到窗口上限。
