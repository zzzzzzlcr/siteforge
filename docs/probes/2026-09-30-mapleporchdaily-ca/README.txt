mapleporchdaily（key `mapleporchdaily.com/ai-app-ca`）2026-09-30 用当前骨架重渲。

底稿 sha256(前16) = 76ed5a6131db   （== forms-222/sites/mapleporchdaily.v4.py，LF 归一后同 sha）
推上去 sha256(前16) = db38c06f773307a4   （行尾换成 CRLF，与底稿在线上那一版一致）

为什么重渲：线上那份是 09-28 12:16 生成的，早于骨架三处修复 ——
  ① `_ALIASES` 别名回退  ② `_SPLIT_FROM` 全名拆分  ③ 随机池按国家分流（含 CA）
它 `PROVENANCE['env'] = None` ⇒ 落到「不知道」那一档 = **英国**手机号 + 英国邮编。

⚠️ 重渲会带**整套当前骨架**（不是只带那三处）—— 所以这版**一次真站都没跑过**。
⚠️ 重渲会把骨架默认的 `strict: True` 带回来，而本站 09-28 手改成 `False`
   （真站实测：带 strict 时每一步 form 都失败，4 个选择器命中数都是 1、
   一律 `scroll mouse wheel failed: element not found`）。**已重打回去**，
   产物自带的检查器（`docs/probes/2026-09-28-georgiapower/artifact_lint.py`）
   会喊 `[A] 骨架带 --strict` —— 喊了就是没打上。
⚠️ `PROVENANCE['selftest']` 清空了：底稿那笔是 **09-28 那版码**的账，
   算到这份换过骨架的新码上就是假账。

`proxy_country: CA` 是**用户 2026-09-30 给的**，不是这次量出来的。

lint 剩余（都不是重渲引入的）：
  [B] 入口判据像跨元素拼接：'Canadian Residents Rushing To Try This AI App'
  [I] 长 nth-of-type 链
  [G·提示] FILLS['last_name'].source='last_name' —— 资料库叫 'username'（现由 _SPLIT_FROM 兜住）
