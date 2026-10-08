blavity（key `blavity.com`）2026-09-30：点 Shopping **到了才算**。

底稿 sha256(前16) = 1def8ac7ab56b6fc（同日早些时候那版：动作类走 _action_cdp 绕开舰队的 30 秒）
推上去 sha256(前16) = f34b30719da6cee6

用户诉求：「把 bla 那个点 shopping 多点几次；实在判断点不到用 eval 也行」。
量到的真因（memory `click-before-page-ready`，2026-09-29 开窗实测）：
  新 tab **+2.26 秒**才出现、**+4.3 秒**地址才有内容，而 `_follow_new_tab` 是
  click 返回后 **0.07 秒**看那一眼 ⇒ **一定没有新 tab** ⇒ 必漏。
  ⇒ 不是「点得不够多」，是**没等**；照着盲点乱点只会再开几个 tab。

改动（**只挂在带 `until_url` 的步上**，其它点击一个字没变）：
  · 新常量 ARRIVE_WAIT_SECONDS / ARRIVE_TRIES / ARRIVE_POLL
  · 新方法 _wait_url_arrived（轮询 /json/list，HTTP GET，不起 cdp 进程）
  · 新方法 _click_until_arrived：点 → 等到 → 没到再点（上限 3）→ 才 eval 最后一招
  · _do / _perform 加 until_url 一路传下去
  · STATES 里「点了「Shopping」」那一步加 'until_url': 'https://shop.blavity.com/'
    （全文只此一处；Subscribe Free / 清障 / Add to cart / Check out 都没带）

eval 是**最后一招**，破了「动作一律走 cdp」（规格 §5.2）—— 用户点头。
⚠️ eval 里只用 `document.querySelector`：`cmd/eval.go` **不注入** `__cdpQ`，
   那个穿透助手在 eval 里根本不存在（2026-09-16 定位到行）。

离线验收（/tmp/bl_verify_shopping.py，四条）：
  A 新 tab 慢点出现 ⇒ 只点一下、不 eval、切前台、算做成
  B 一直没到      ⇒ 点满 3 下 + eval 一次，返回 Error（**不报假成功**）
  C 不带 until_url ⇒ 点一下、不轮询、不 eval（老行为不变）
  D eval 的形状   ⇒ querySelector + window.location.href 导航，无 __cdpQ

⚠️ 手改生成物 —— siteforge 重渲会冲掉（骨架里同一个 0.07 秒的毛病还在）。

════════ 2026-09-30 13:5x 追加（这一版才是最后推上去的）════════

推上去 sha256(前16) = f34b30719da6cee6（201125 字节，CRLF 3063 行）

在那之上又加了一格**成功判据**（用户 2026-09-30 点名）：
    SUCCESS_TEXTS = []  →  ['Thank you for subscribing!']
    SUCCESS_URLS  = ['shop.blavity.com']   （不变）
依据不是听来的 —— 是**在真窗口里直接问页面**读到的（13:48）：
    "HIT[H3] Thank you for subscribing!"
    原文：<h3 data-v-85e6024b="" class="text-[18px] font-inter font-bold mb-2">
            Thank you for subscribing! </h3>
而且它在 innerText 里 —— `page_signature()` 读的正是可见正文（跨帧）⇒ 一定生效。
⚠️ 已知代价：这段文案是**这个窗口订阅过的凭据** ⇒ 老窗口再跑一遍时它可能一进页面就在
   ⇒ 第 1 步就判成功（然后照旧走完全程）。与网址那条同类：判「状态在」，不判「这一趟做的」。

「加判据 ≠ 提前收工」—— 这一条是用户点名的口径，代码里本来就长这样：
    if self._succeeded() and not self._success_reported:
        self._rpt("success"); self._success_reported = True
        # ★ 不 return —— 记录成功后**继续做后续步骤**
走完全程才 `return self._success_reported`（3008）；中途连着 3 步没做成但已记成功
⇒「算成功收摊」（2949）。

════════ 222 真站验收（2026-09-30 13:45–13:50）════════
窗口：bit.sh open 192.168.1.197（**注意：siteforge.env 的 BIT_WORKER_IP 是 .197，不是 .222**）
跑法：python3 blavity.py --ws-url … --form-file /tmp/form_empty.json \
        --correlation-id selftest-blavity-shopping-20260930b --no-report --trace /tmp/bl_trace2.jsonl
日志：/tmp/bl_run2.log     trace：/tmp/bl_trace2.jsonl

结果：**全 11 步走完，最终判词「走完全程，之前已在购物页记录成功」**
   13:45:49 第 1 步 ✓   13:46:09 第 2 步 ✓（就是生产上 NameError 那一步）
   13:46:29 第 3 步 点 Subscribe Free ✓
   13:46:57 第 5 步 跳过（Subscribe Free 已不在 ⇒ 订阅成了）
   ★ 13:48:54 **到了（第 1 下）：https://shop.blavity.com/**   ← 新代码在真站上生效
   13:49:05 成功：判据成立了
   13:49:57/13:50:09/13:50:38 第 9/10/11 步（Add to cart / Check out）✓
   13:50:40 走完全程，之前已在购物页记录成功

⚠️ 两点如实记：
  · 222 那趟跑的是**不带**「Thank you for subscribing!」那版；这条是在推之前才加的，
    它只动了一个字面量，依据是同一窗口里直接读到的原文（上面）。
  · 第 5 步「跳过」的判词来自产物自己（「订阅成了它就不在了」），不是独立证据；
    独立证据是那段 h3 文案确实出现在页面上。

⚠️ 这一版是**手改生成物** —— siteforge 重渲会冲掉（`until_url`、成功文案、`_action_cdp`、
   `CDP_TIMEOUT_SECONDS` 四处都是手改）。骨架里同一个 0.07 秒的盲点还在。
