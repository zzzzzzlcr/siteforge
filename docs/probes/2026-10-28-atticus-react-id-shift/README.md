# Atticus `try.atticus.com/ssd/benefits`：联系屏 React 自动 id 漂移 ⇒ 值整排错位（2026-10-28）

站点键：`try.atticus.com/ssd/benefits`（config 162 的唯一站点）。

## 失败单（最新，26829734，2026-10-25 00:03 CST）

前 7 单的历史见 `docs/probes/2026-10-24-atticus-benefits/README.md`。这一单是
**`c847e829`（zip 原生 setter 修复）之后**跑的，仍然挂，死在 `How can we reach you?`。

`fail_img` 现场（https://fmr.3tkj.cn/oss/farmer/screenshot/fail/2026/10/25/6adcd73a920b9.png）：

```
First name : Carter
Last name  : candelarioalexander@)      ← 邮箱进了姓氏
Email      : 2125550147                 ← 电话进了邮箱（红字 invalid e-mail）
Phone      : 62                         ← 年龄进了电话（红字 too short）
Submit     : 灰的
```

日志（同一趟）：填的是 `:r2:` / `:r3:` / `:r4:` / `:r5:`，而账本里是
`:r1:`=First … `:r4:`=Phone —— **整排 +1**。

## 根因

`READ_PAGE_JS` 的 `tid()` 优先用 `id`（`\u0004` 前缀），而 Atticus 的联系屏是 React，
自动 id `:r1:`…**会随屏漂移**；`value_for()` 又拿账本里的 id 去对 ⇒ 每一格拿到**前一格**的值。
占位符（`First name` / `Last name` / `Email address` / `Phone number`）是稳定的，
但读取器没机会用它。

## 修法（模板 + 产物两处）

`tid()`：自动 id（`^:r[0-9a-z]+:$`）**不算身份** —— 有 placeholder/aria-label 就按那个认，
稳定 id（`zipcode` / `qv3-fn`）照旧优先。

- 模板：`agent/template_loop.py` 的 `DEFAULT_READ_JS`（以后重渲也带上）；
- 产物：`forms-222/sites/try.loop.py` 的 `READ_PAGE_JS`（线上这份）。
- 对后端那份 diff 只有 `READ_PAGE_JS` 这一行；`+49` 字节。

## 验过

1. **单测（先红后绿）**：`tests/test_loop_template.py::test_react_auto_ids_do_not_win_over_stable_placeholders`
   —— node + 假 DOM（id `:r2:`…`:r5:` + 稳定 placeholder）真跑 JS。
   修前 tokens = `\u0004:r2:`…（红），修后 = `\u0002First name`…（绿）。
   `tests/test_loop_template.py` **54 passed**（2m53s）。
2. **合成页（真浏览器）**：`/tmp/atticus_contact.html`（同样 4 格 + Submit 校验）
   —— 产物的 `read()` → `value_for()` → `fill()` 全链路：
   ```
   tokens: \x02First name, \x02Last name, \x02Email address, \x02Phone number
   fill James / Carter / james.carter.58@example.com / 2125550147 → 全 True
   AFTER: vals 全对，Submit disabled=false
   ```

## FMR（2026-10-28 15:21 已推 + 已启用）

```
LOCAL  forms-222/sites/try.loop.py  81466 bytes  sha256 d037b034332853aa…
BEFORE raw bed3c41f…（归一 c847e8294e09f9e2…，81417 字节）status 0
WRITE  {"ok": true, "status": 200, "msg": "success"} → 回执 sha d037b034…
AFTER  allow_disabled 读：d037b034…、81466 字节、status 0
       → POST formConfig/status {"site":…,"status":1} → {"status":200,"msg":"success"}
AFTER_ENABLE 正常读 200：d037b034…、81466 字节、含 `:r[0-9a-z]` 自动 id 判据
```

## 8100（192.168.1.222）真站整单（2026-10-28 15:31）

跑法：`run_full_try.py`（本目录）在 `siteforge-222` 容器里开 .222 的 Bit 窗口，
从广告落地页 `https://try.atticus.com/ssd/benefits` 整条跑，`no_report=True`。

| 趟 | 结果 |
|---|---|
| 1 | 45s，`rejection/legal-representation`（随机答到「hire a lawyer? Yes」→ 拒信页）——**没到联系屏** |
| 2 | **108s，`survey/qual/post-conversion-ah`（成功）** —— 到了联系屏并通过 |

第 2 趟的关键日志：

```
step 16: 填了 First name / 填了 Last name        ← \x02 占位符 token
step 49: 填了 Email address / 填了 Phone number   ← 正是 26829734 填错位的那两格
step 50 [25%] How can we reach you, James? -> Is my information secure?
step 52: Next（How can we reach you, James?）
RUN_DONE ok=True  108s
SUCCESS(url): .../survey/qual/post-conversion-ah
```

对比失败单 26829734 同屏：邮箱进姓氏、电话进邮箱、年龄进电话、Submit 灰着 →
现在四格各就各位、页面往下走了。

⚠️ 第 2 趟 step 53 有一句「已经答过了、但页面上没有认得出的下一步」——那是
提交后的收尾（下一次读到 URL 已进 `post-conversion`），不影响判据。

## 下一单的判据

- 日志里 `How can we reach you?` 那一屏填的 token 应该是 `\x02First name` 这类
  **占位符**（不再是 `:r2:`）；
- 截图里四格各就各位（First=名 / Last=姓 / Email=邮箱 / Phone=电话），无红字；
- Submit 能点下去，离开那一屏。
