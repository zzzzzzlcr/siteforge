# blavity「检测到广告拦截」弹窗 —— 2026-10-26

用户：「打开这个。然后等待，看看能不能等到新弹窗。我们没适配的。有概率能出来」
（给了 26870485 那一单的日志；随后：「竟然我们失败的能抓到弹窗的元素是不是直接改就行了，那就把监视器关了吧」）。

## 线索（生产单 26870485，2026-10-26 02:00）

```
第 10 步：找到了「Shopping」，但它被 div.adblocker-header 盖着 —— 这一下不点（点了等于点到盖着它的东西上）
…**没走到成功**：11 步里真做了 6 步、被 when 判据跳过 5 步…
页面上现在写着「We see you're using an ad blocker Ads help us keep Blavity free for millions of 」
```

同一单里 **10-10 那个广告弹窗补丁是生效的**：

```
01:54:18 这一步动手前之后 把广告弹窗点掉了（{"id":"bx-close-inside-1967264",…}）：看起来成了；点完再看：没了
```

⇒ 10-24 那条「唯一没验到的一格（真站正向 × 补丁本体）」这次**验到了**。

## 这个弹窗的真身（不是猜的）

开窗等了 4 分钟没等到（页面里连隐藏节点都没有 —— 它是**检测到拦截才动态挂载**的）。
改成直接读站点自己的资源：

- 页面内联 CSS（`https://blavity.com/entertainment/…` 的 `<style>`）里有整套
  `.adblocker-*` 规则；
- Nuxt chunk `/_nuxt/Nk1tWnQR.js` 里有组件模板。

```
<div class="adblocker-overlay">                 position:fixed; inset:0; z-index:99998（盖满视口）
  <div class="adblocker-modal">
    <div class="adblocker-header">… We see you're using an ad blocker …</div>
    <div class="adblocker-options">
      <button class="adblocker-option adblocker-option--primary">Allow ads on Blavity …</button>
      <div class="adblocker-whitelist-help">…（primary 展开的那段教程）</div>
      <button class="adblocker-option adblocker-option--dismiss">Not right now / We'll ask again in a week</button>
```

**关它是 `button.adblocker-option--dismiss`**（`--primary` 是展开白名单教程，点了不关）。

## 修法（产物三处、62 行）

`blavity.py`（FMR 上 `blavity.com` 那一份，210749 字节，sha `99b1c423…`）：

1. 新常量 `_ADBLOCK_NAG_JS`：只在**可见**的 `.adblocker-overlay` 里找**可见**的
   `button.adblocker-option--dismiss`，找到就打 `data-af-adblock="1"` 并返回一份回执
   （class/坐标/尺寸/文字）；找不到返回空串。
2. 新方法 `_clear_adblock_nag(why)`：与 `_clear_ad_popup` 同一条路子 ——
   探 → cdp 点标记 → **再探一次复核** → 打一行 INFO。挑不出就静默返回（正常情况）。
3. `_clear_obstructions` 里挨着 `_clear_ad_popup` 调一次 ⇒ 开跑前 / 每次导航后 /
   每步动手前都会过（生产那个死法就发生在「每步动手前」）。

## 验过（合成页，`.222` 的 Bit 真浏览器）

用产物里**真实的** `_ADBLOCK_NAG_JS` + `cdp click` 跑一份同构页面：

```
正向：probe → {"cls":"adblocker-option adblocker-option--dismiss","x":44,"y":426,"w":289,"h":50,"text":"Not right now"}
      marker=1 → cdp click（match_count 1）→ 页面自己的 handler 触发 __n=1、浮层隐藏
      再探 → ''（没了）
反向①：dismiss 设 display:none → probe 返回 ''，marker=0（**不会**去点别的）
反向②：整层不存在 → probe 返回 ''，marker=0
```

## 没验到的

- **真站上那个弹窗出现时**的端到端（窗口开了 4 分钟没等到；它是拦截检测触发的，
  这个窗口没有广告拦截器）。判据：下一单真弹时日志出现
  「把「检测到广告拦截」的弹窗点掉了（…）：看起来成了；点完再看：没了」。

## FMR（2026-10-26 10:31 已推，用户点头）

key = `blavity.com`（后端那一行存的站点键就是它，不是文章长键）。

```
LOCAL bytes 211906 sha256 708ccba8…
BEFORE site=blavity.com sha256 99b1c423… norm==expect(60d79a2b…) True
WRITE {"ok": true, "status": 200, "msg": "success"}   → 回执 sha 708ccba8… 一致
AFTER 正常读 200：sha 708ccba8…、211906 字节；debug 读 status=1（本来就是启用态，没动它）
回读正文里 `_ADBLOCK_NAG_JS` / `_clear_adblock_nag` 都在
```

## 状态

已推送。文件在本目录 `blavity.py`（改前那份 `blavity.before.py`）。
唯一还空着的一格仍是「真弹窗当次出现时的端到端」—— 判据见上面「没验到的」。
