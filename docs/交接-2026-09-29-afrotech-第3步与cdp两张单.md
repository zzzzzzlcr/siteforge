# 交接（2026-09-29）：afrotech 卡在第 3 步 —— 与 cdp 的两张单

**一句话**：afrotech 那条链的**判据**和**滚动**今天都修好了，第 1、2 步生产实测已通过；
卡点推进到**第 3 步「点订阅按钮」**，真因是 cdp **选中了一个 0×0 的隐藏重复件**（不是弹层）。
**站现在是停用状态**（用户 09-29 下午自己停的：三个 key `行status=0`、普通读 404）。

## 一、生产实测（任务 `26238922`，09-29 14:16）

```
14:15:16 开始（6 步）
14:15:54 第 1 步 滚进视口      ✔ 38 秒（以前 30 秒就 TimeoutExpired —— 靠"主帧走 _cdp，上限 60 秒"这处手改）
14:16:06 第 2 步 填 email      ✔ 12 秒
14:16:41 第 3 步 cdp click 'button[aria-label="Subscribe to Newsletter"]'
          → timed out after 30 seconds        ✘ 整单 exit=unknown
```

## 二、第 3 步的真因（开窗实测，与 cookie 弹层无关）

| 量到的 | 值 |
|---|---|
| 同名按钮有几个 | **2 个**（都在 light DOM，文档顺序） |
| **#1（`__cdpQ` 选中的）** | `rect=[0,0,0,0]`、`offsetParent=false`、`getClientRects().length=0`、祖先有 `[hidden]/[aria-hidden]` |
| #2（页脚真身） | `rect=[321,17319,42,42]`、`offsetParent=true`、在 `<footer>`（第 21 屏） |
| 页面停在页脚时，**真身**的 `|视口∩矩形|/|矩形|` | **1.0（完整可见）** |
| 同一状态跑老选择器 `cdp scroll 'button[aria-label=…]'` | **46.9 秒**，且把页面从 `scrollY=17348` **拖到 8041**（方向反了） |
| 换 `footer button[aria-label=…]` | 远的时候 24.1 秒（方向**对**）／**已在视口 0.9 秒** |

**机理**（`/company/cdpcli`）：

1. `internal/shadow.go:38 __cdpQIn` = 取 **querySelector 的第一个匹配**，**不看可见性、不看尺寸**；
2. `internal/client.go:875 IsElementVisible`：`visibleArea = (min(right,vw)-max(left,0)) * (min(bottom,vh)-max(top,0))`，
   `totalArea = width*height` —— 元素 0×0 时是 **`0/0 = NaN`**，`NaN >= 0.8` **恒假**（且该算式对视口外矩形不做 clamp，会算出负数）；
3. `ScrollTouch`（`:946`）靠 `centerY < vh/2` 判方向 —— 隐藏件 `centerY = 0` ⇒ **永远"在上方"** ⇒ 每轮手势都把页面往远处拖 ⇒ 20 轮**结构上不可能收敛**；
4. `ScrollIntoView`（`:886`）用 `__cdpQ` 打的是**同一个隐藏节点** = 空操作；`ClickElement` 末尾 `GetElementCenter` = (0,0)。

⚠️ **最坏的一档**：把调用方上限放宽（我们 09-29 做的 `CDP_TIMEOUT 30→60`）⇒ 20 轮跑完、点 (0,0)+偏移 =
**页面左上角**、回执里 `x/y/match_count` 一切正常 ⇒ **假成功**（不报错、不重试、日志好看）。
**所以那个 60 秒止血在 afrotech 上不该单独上**（30 秒上限时至少是"响的失败"）。

## 三、次因（用户 09-29 提醒的那条，已量实）

长滚之后**有时**冒出 Quantcast 同意弹层：

```
DIV.qc-cmp-cleanslate   position:fixed  z-index=2147483647  rect=[0,0,375,812]   ← 盖住整个视口
  BUTTON#accept-btn     "AGREE"    rect=[22,521,345,37]
  BUTTON#disagree-btn   "DISAGREE" rect=[22,630,345,39]
手势起点（上滑 x≈0.75vw）：281,808 / 281,792 / 281,752  →  三点全落在这个 DIV 上（触摸被吃）
它还把页面 shadow root 从 2 个变成 45 个
```

它是**第二个病、不是主因**（弹层不在场的那一窗，老选择器照样 46.9 秒），但它必须进
"动手前先清障"那一环（**新骨架有同意弹层钩子，afrotech 这份产物没有覆盖 Quantcast**）。

## 四、今天推了什么 / 没推什么

| | 内容 | 状态 |
|---|---|---|
| **已推 FMR**（key `afrotech.com`） | ① 文章入口判据**域名级**（`https://afrotech.com/` + `SUBSCRIBE TO NEWSLETTER`）；② 主帧 `scroll` 改走产物自己的 `_cdp()`（上限 60 秒、超时变软失败） | 已生效（任务 26238922 的第 1、2 步就是它的战果） |
| **已停用** | odell 那篇文章的 key（`formConfig/status` status=0）—— 用户之后把**三个 key 都停了** | 用户操作 |
| **候选规避（没推）** | 第 3 步选择器加 `footer` 限定：`footer button[aria-label="Subscribe to Newsletter"]` | **等用户点头** |
| **两张缺陷单（已交）** | 见下 | 等 cdp 线修 |

⚠️ ① ② 都是**手改生成物**（siteforge 重渲会冲掉）。

## 五、三条路（用户挑）

1. **只推那个 `footer` 限定选择器** —— 按今天的数据，光这一处就够让那条链跑绿
   （第 2 步填完时页面正在页脚 ⇒ 第 3 步 0.9 秒可期）。**代价**：第三处手改、只治这一站、弹层在场时仍可能被挡。
2. **等 cdp 侧真修**（单子已齐，站先停着）。
3. **连"清障 Quantcast"一起补进产物**（最稳，改动最大）。

## 六、去哪看

- 缺陷单：`docs/缺陷单-2026-09-29-cdp滚动卡死.md`（远元素 20 轮，按距离分流）
  与 `docs/缺陷单-2026-09-29-cdp选中隐藏重复件.md`（**本页二、三条的完整版** + 建议修法）
- 探针留档（数字可复现）：`docs/probes/2026-09-29-afrotech-domain/`
  `af_click_probe.py`（三步耗时）· `af_btn_probe.py`（按钮矩形/弹层/手势起点）·
  `af_agree_probe{,2}.py`（同名按钮清单 + 选择器对照）
- 产物留档（最新已推那版）：同目录 `afrotech.domain.py`

## 七、动手前先知道的口径

- **开真窗口必须先问**（"刷量会被领导叼"）；探测完把 Bit 窗口关掉。
- 探针里 `cdp eval` 的回执**可能是被包了两层的 JSON 字符串** —— 解析要兼容，否则会**静默跳过**（今天漏掉过一次"点 AGREE"）。
- 探针 JS 先 `node --check`；打印别截断（今天各栽一次，各白烧一个窗口）。
- 截图别走 `forms/common.py` 的 `screenshot()`（它指着没有这条子命令的 `/company/cdpcli/cdp`），直接调
  `/opt/skills/auto-farm-skill/cdp screenshot`；而且它对 375×812/dpr3 的窗口会报"比例对不上"。
