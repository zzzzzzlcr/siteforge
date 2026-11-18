# soulfuldate 5a4f738d：japansdates 的 cookie 弹层没关掉 —— 2026-10-20

用户报「`soulfuldate.com/land/sp/5a4f738d` 好像也有失败」。
一句话：**站已经在该在的 config 101（japansdates）上，但旧版 `_close_cookie()` 把「选择器没命中」
当成「点成功了」** —— 姓名屏才挂出来的 `.cookie-main`（Accept all）一直盖着 NEXT，
整单卡死在「How can users call you?」。

## 失败面

`formLog?site=soulfuldate.com/land/sp/5a4f738d` 会把**整个 config 组**的失败一起带出来
（diag 的 `site` 有 soulmatemeets e77eb1b0、thisromance……）；真正属于本站的最近两单：

| task_id | 时刻（+08） | 脚本 | 结果 |
|---|---|---|---|
| 26702743 | 2026-10-20 09:45 | `form_fill_goldenagesouls` | max_steps |
| 26697124 | 2026-10-20 07:49 | `form_fill_goldenagesouls` | max_steps |

两单日志同型：前 4 步正常（`option-find-friend` / `gender-female` / `option-woman`），
第 5 步 `filled firstname`，随后 **6 次 `nothing actionable`**：

```
step 6..11: nothing actionable (1..6) How can users call you? Please use English characters only
ERROR: stalled after 3 answers ... reporting honestly
```

（那时站还在 **config 66 / goldenagesouls**；本会话中面板已把它移到 **config 101**。）

## 现场（222，真站）

### golden 为什么点不动

到了姓名屏：

```js
document.querySelector('[data-testid="continue-button"]')
```

命中的是**隐藏的重复件**（每一步各有一颗），所以 golden 的 `nextVisible: false`，
它永远不会去点。可见的那颗 NEXT：

| 元素 | rect |
|---|---|
| `button[data-testid="continue-button"]`（可见，文字 `NEXT`） | `[54,609,268,53]` |
| `button.cookie-main__btn`（`Accept all`） | `[16,597,345,44]` |
| `elementFromPoint(NEXT 中心)` | **`BUTTON.cookie-main__btn` "Accept all"** |

也就是说：即使 golden 找到了可见 NEXT，坐标点击也会被 cookie 弹层接走。

### 点掉 cookie 之后页面是能走的

手工点 `Accept all`、再点可见 NEXT → 立刻推进到
`How's your mood today, Donald?`（`option-sad/neutral/...`）。
10-10 的探针也早给过结论（`../2026-10-10-soulfuldate-platform/`）：这个站是
**japansdates 平台**（`step-content-item-*`、`option-*`、无 `image-button-*`）。

### japansdates 旧版为什么也没关掉

`_close_cookie()` 旧实现：

```python
for sel in COOKIE_SELECTORS:
    try:
        self.cdp.click(sel)      # ← 元素不存在也不抛异常（common.CDPHelper 直接 return 子进程输出）
        self._dly(0.4, 1.0)
        return True              # ← 第一根选择器就 return 了
    except Exception:
        continue
```

`COOKIE_SELECTORS[0]` 是 `[data-testid="cookie-popup-common-agree-and-close"]`，这页不存在；
于是 `button[class*=cookie]` 与文案兜底**根本没走到**。

另外它只在 `run()` 开跑前调一次；而这页的 `.cookie-main` 是**姓名屏（signup-form）才挂出来**的，
开跑前页面上没有它。

## 修复（japansdates，三处）

1. `COOKIE_SELECTORS` 加 `.cookie-popup-consent button`；新增 `COOKIE_OVERLAYS`
   （`.cookie-popup-consent` / `.cookie-container` / **`.cookie-main`** / `.cookie-popup` /
   `#onetrust-consent-sdk` / `.ot-sdk-container`）——「还在不在」的复核容器；
2. `_close_cookie()` 重写：
   - 先 `_cookie_gone()` 廉价检查（没弹层只多一次 eval）；
   - 每根选择器走 **`_click_visible_css()`（点可见的那一颗）**，点完**复核 `_cookie_gone()`** 才 return；
   - 文案兜底（`accept|agree|allow|got it`）也加**可见性**判断；
3. `run()` 的每一步之前都调一次 `_close_cookie()` —— 弹层晚挂出来也轮得到。

## 真站验证（清 `cc_cookie` 后从 step1 重走）

```
step 6  [step-content-item-InputNameAutofill]: filled firstname
step 7  [step-content-item-InputNameAutofill] continue (How can users call you? ...)
step 8  mood -> option-neutral
step 11 age -> (文本) 25-34
step 18 filled email
step 21 filled password
step 23 勾选条款
step 26 SUCCESS: ...（站点状态 wizard1）
URL Report: /main-page?referrer=...&source=land&action=reg
exit 0
```

- 姓名屏那一刻 `.cookie-main` 在场，补丁自己关掉并继续 —— 这正是旧版死掉的那一步；
- 年龄屏走 `labels` 文案兜底（`25-34`）；
- 这次**真注册了一单**（成功态 `#d2l6YXJkMQ==` = wizard1），用于验收。

## FMR

```
key     japansdates.com
原      34111 B / CRLF 710 / sha 51cb930d300aa46b
新      37536 B / CRLF 768 / sha 41c74af2cbaf4afa
回读    37536 B / CRLF 768 / 一致 True
备份    py-847dba875f0b09ce905b1f1623c8e8ae
```

脚本是**按 config 组共享**的；推完回读：

```
japansdates.com                    -> 41c74af2
charmingtalks.com/land/sp/196c57bd -> 41c74af2
soulfuldate.com/land/sp/5a4f738d   -> 41c74af2
```

即 config 101 整组都拿到了新版。

## 文件

| 文件 | 是什么 |
|---|---|
| `japansdates_ORIGINAL_51cb930d.py` | 推之前线上那份（34111 B / CRLF 710） |
| `japansdates_PATCHED_41c74af2.py` | 推上去的那份（37536 B / CRLF 768） |
| `patch_japansdates_cookie.py` | 三处插入的补丁脚本（CRLF 原样、锚点唯一断言） |
| `run-20261020.log` | 222 真站整趟日志（含 SUCCESS 与 URL Report） |
| `fail-26702743.png` | 生产失败截图（姓名屏，输入框已填 Donald） |

## 备注

- `soulfuldate.com/land/sp/5a4f738d` 在本次会话中已由面板从 config 66 移到 **101**；
  这条 config 变更不在本仓的代码里。
- goldenagesouls 脚本自身也有同一类问题（`nextVisible` 拿隐藏重复件、`COOKIE_BTN`
  只认旧 testid）—— 这次没有动它；以后 config 66 的站再撞同样的屏，照着这一刀的写法补。
