blavity（key `blavity.com`）2026-09-30 手改：动作类 cdp 调用绕开舰队那份 common.py 的 30 秒。

底稿 sha256(前16) = 345b18b27a5fe194（= 09-29 那份 11 步统一产物）
推上去 sha256(前16) = 1def8ac7ab56b6fc  （194158 字节；后端回执指纹与本地一致）

为什么：生产今天 11 条失败里 9 条是 cdp 调用超时 —— 8 条是第 2 步
`cdp form '#newsletter-email'` `timed out after 30 seconds`。那 30 秒不在产物里，
在**舰队那份 `forms/common.py` 的调用点上**（7 处全是写死的 timeout=30）。
而 cdp 的 ScrollToElement 是「最多 20 轮拟人滑动」，`#newsletter-email` 在侧栏
（文档 59% 处）属于远元素 —— 同一份产物第 1 步 scroll 实测 42 / 51 / 53 秒。

改动（全部在产物里 ⇒ 跟 FMR 下发，到得了 .142）：
  1. 新增 CDP_TIMEOUT_SECONDS = 90
  2. _cdp / _run 的默认上限 → 该常量
  3. 新增 _action_cdp()：返回形状照抄 common.py:_clean_value（成功只认 stdout）
  4. _do 的 click 调用点 → _action_cdp
  5. _do 的 form 调用点 → _action_cdp（并删掉已够不着的 TypeError 退路）
  6-7. scroll 的两条调用点（主路 / 像素老形状）→ _action_cdp
  8-9. 清障那两处 click（判官路 + legacy 路）→ _action_cdp
  10. _run 默认值
  11. 两处写着 60 秒的过期注释

验收（离线；判据 = 生产报错原文，不是我写的期望值）：
  form  argv 逐字相同 / click argv 逐字相同 / 带帧号时参数顺序与 common.py 一致 /
  cdp 起不来 → 软失败（_ok 判否），不抛异常
  → /tmp/bl_verify.py

⚠️ 这是**手改生成物** —— siteforge 重渲会冲掉。根治要让骨架默认这么做。
⚠️ 只治 blavity。治本是把 forms/common.py（CDP_TIMEOUT 90）铺到舰队。
⚠️ 推的时候 enable=False ⇒ **没碰启用状态**（站是用户自己停的）。
