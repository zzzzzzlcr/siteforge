# blavity 广告弹窗（`data-click="close"`）—— 2026-10-10

运营报的：「这种 blavity 的网站偶尔会出现广告弹窗，需要关闭，不然任务会失败很多」。
用户当场给出唯一稳定标识：**`data-click="close"`**。

## 现场（真站实测，192.168.1.222 档案 `982d158b…`，视口 377×757）

弹窗不在页面里等着 —— 是**弹出来那一刻才建 DOM**的。它出现时的样子：

| 元素 | 状态 |
|---|---|
| `#bx-creative-1967264` | 可见，`(19,205)`，**340×347**，`z-index: 2147483645` ← 盖住正文的弹窗本体 |
| `#bx-close-inside-1967264` | 可见，`(251,225)`，**88×54**，`z-index: 2147483647` ← 关闭按钮 |
| 同页 `[data-click="close"]` 总数 | **6 个**（3 个 campaign × inside/outside 两个变体） |
| 其中可见的 | **只有 1 个**，其余 5 个是 0×0 / `display:none` / `visibility:hidden` |

⚠️ **campaign id（`1967264`）每次换** —— 只有 `data-click="close"` 这个属性稳定。
⚠️ 关掉之后整个 creative **从 DOM 里消失**（不是隐藏）。
⚠️ 子帧里另有一族 `data-click="<doubleclick 点击链>"`（GPT out_of_page 广告位），**不是** close，别去点。

**为什么不能直接 `cdp click '[data-click="close"]'`**：`cdp click` 命中多个时点的是
**文档顺序第一个**（`--strict` 只把歧义判成失败，不看可见性）⇒ 直接点 = 点空气，
与 afrotech「选中隐藏重复件」是同一种病（siteforge 缺陷单 2026-09-29）。

## 补丁（三处插入，CRLF 原样）

产物：FMR key `blavity.com`。原 `f34b3071…`（201125 字节）→ 现 `99991e4d…`（206005 字节）。

1. `_AD_POPUP_JS`（模块级常量）—— 挑出**可见**的 `[data-click="close"]` 并打 `data-af-close="1"` 标记。
   判据三条：`display/visibility`、`rect ≥ 8×8`、中心点 `elementFromPoint` 落在它自己或它的祖先/后代上。
2. `Filler._clear_ad_popup(why="")` —— 标记 → `cdp click --selector '[data-af-close="1"]'` → **再探一次复核**
   （cdp 回 ok 只说明命令发出去了；「没做成不许说做成」）。挑不出 ⇒ 静默返回。
3. 挂在 `_clear_obstructions()` **第一句** ⇒ 三个调用点（开跑前 / 每次导航后 / **每步动手前**）全带上。

手拼的只有「是哪一个」这个判断，**动作仍走 cdp 的拟人手势**（规格 §5.2 禁的是手拼动作）。

**成本**：每步 +1 次 eval，实测 **0.15~0.6 秒**（门铃 `_DOORBELL_JS` 对照 0.14 秒）。

## 验证矩阵

| # | 用例 | 结果 |
|---|---|---|
| 1 | 合成页：隐藏重复件**排在文档顺序前面** | 探针精确命中可见那个 ✓ |
| 2 | 合成页：点标记 | 页面自记 `CLICKED=bx-close-inside-pop`（点到的是对的那个）✓ |
| 3 | 合成页：点完再探 | 空（creative 已从 DOM 移除）✓ |
| 4 | 合成页：可见但**被盖住** | 探针返空、**不点**（宁可不做）✓ |
| 5 | 真站负向（无弹窗，跑**产物本体**） | 静默、零动作 ✓ |
| 6 | 真站正向（15:38 真弹窗在场） | `cdp click` 把它连 DOM 一起干掉 ✓（当时用的是具体 id `#bx-close-inside-1967264`） |
| 7 | **真站正向 × 补丁本体** | ✓ **2026-10-20 补验**：`/health/michael-jackson-vitiligo` 真弹窗（`bx-close-inside-1967264`，加载约 50s 后出现，88×54 @ (251,225)）上跑产物本体的 `_clear_ad_popup` —— 标记 → 点击 → 复核「没了」 |

第 7 条 2026-10-20 在另一篇文章上补验通过（真弹窗在场）：标记 → 点击 → 复核三环都走通。
1–4 验「选得对/不乱点」，5 验「不误报」，6–7 验「真弹窗点得掉、且是产物本体点的」。

## 踩过的坑（值得记）

- **裸 `return` 直接喂 `cdp eval --file` = SyntaxError ⇒ 返回恒为空**。
  产物里这段 JS 由 `_ev` 包成 `(function(){…})()` 才执行；CLI 直跑必须自己包
  （见 `ad_popup_wrapped.js`）。我第一版「负向对照通过」就是这么来的**假通过**，
  观察器也因此永远不会响 —— **是合成页把它逮住的**。
- 推送必须**按字节读**：`Path.read_text()` 会把 CRLF 全变 LF（实测 206005 → 202873 字节）。
  后端口径是逐字节（本次回读 206005 / CRLF 3132 一致）。

## 文件

| 文件 | 是什么 |
|---|---|
| `blavity.com_ORIGINAL_f34b3071.py` | 推之前线上那份（CRLF 3063） |
| `blavity.com_PATCHED_99991e4d.py` | 现在线上那份（CRLF 3132） |
| `patch_blavity.py` | 打补丁的脚本（三处锚点，CRLF 保留，幂等断言） |
| `verify_ad_popup.py` | 跑**产物本体**的 `_clear_ad_popup`（绑真 CDPHelper，不跑整条漏斗） |
| `push_blavity.py` | 推送（读后端现状 → 备份 → 写 → 回读逐字节核对；`--dry-run` 只读） |
| `ad_popup_wrapped.js` | 探针 JS（包好版，可直接喂 `cdp eval --file`） |
| `synth_ad_popup.html` | 合成页（复刻 6 候选 / 隐藏件在前 / 被盖住 三种情形） |
| `bl_url.txt` | 现场那条 URL |

## 复现第 7 条

```bash
# 开窗（0.0.0.0:61129 才有远程调试）
/opt/skills/auto-farm-skill/bit.sh open 192.168.1.222 <BIT_ID>
/opt/skills/auto-farm-skill/cdp navi "$(cat bl_url.txt)" --host 192.168.1.222 --port 61129
# 等弹窗（弹出来时下面这条会给出 JSON）
/opt/skills/auto-farm-skill/cdp eval --file ad_popup_wrapped.js --host 192.168.1.222 --port 61129
# 一有输出就跑产物本体（应打出一条 INFO，且点完复核为空）
python3 -I verify_ad_popup.py "ws://192.168.1.222:61129/devtools/browser/<id>" "复现"
```
