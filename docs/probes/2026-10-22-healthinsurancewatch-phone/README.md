# healthinsurancewatch 手机号：不再吃资料，固定 04 + 8 位随机 —— 2026-10-22

用户要求：**「healthinsurancewatch.com.au_the-set-and-forget-mistake-sweep 帮我改下这个对应脚本的手机号，
别用资料了就用 04 开头后面随机的格式」**。

## 站点键（后端那份）

```
healthinsurancewatch.com.au/the-set-and-forget-mistake-sweeping-seniors-is-this-health-insurance-trap-costing-you-800-a-year
```

- 后端 config **163**；这一组里**只有它一个站**（`formConfig/sites` 量的）。
- 脚本类型 **py**（不是 JSON 配置）：`formScript` 写之前 sha `a6ca371c24c872d8…`、269554 B。

## 改了什么（一行）

`FILLS['phone']`：

```diff
  'phone': { 'name': 'phone',
-            'source': 'phone',
+            'source': '__our_au_mobile__',
             'kind': 'value',
             'label': 'Mobile number',
             …
             'fallback': [{'random': 'phone'}],
             'sample': '0412345678'},
```

为什么这一行就够：`_fill_value()` 先按 `source` 读任务资料，读不到再走 `_shaped_like(sample)`
（2026-10-13 就是为这个站加的）。`__our_au_mobile__` 是**资料里永远没有的键**
（与 `docs/probes/2026-10-20-rebates-phone/` 同一口径），于是每趟都落到
`_shaped_like('0412345678')` ⇒ 通体数字 ⇒ **头 2 位照抄 `04` + 其余 8 位随机**。

- 资料里带 `phone` 也不再用它；
- `fallback` 的 `{'random': 'phone'}` 照旧留着，但正常情况下走不到（`shaped` 先返回）；
- 没有动 `name` / `label` / 选择器 / 任何别的行：`LINES 4060 → 4060，DIFF_LINES 1`。

## 真站验证（8100 / Bit 192.168.1.222）

| 项 | 值 |
|---|---|
| 窗口 | `982d158b41b14fbb87754667859b4719`（先关掉上一趟遗留的 Atticus 页再开干净窗口） |
| 入口 | 站的长路径落地页（cdp navi 打开后正文对得上标题） |
| 资料 | `form.json` 里 **`phone: 0499888777`**（故意给一个可辨认的号） |
| 跑法 | `--stop-at 13 --no-report`（第 13 步 = `#phone` 填值，第 14 步才是「Help me save」） |
| trace | step 13 `ok:true`、`selector_used:#phone`、`url: …/compare` |
| 日志 | `填好了「Mobile number」（值来自 **照真站收过的那个值的形状**编的）` |
| **页面上真读到的值** | **`0498331906`** —— 04 + 8 位随机，**不是**资料里的 `0499888777` |

**没有提交**（停在第 13 步，浏览器保持原状后由我们关掉；`pids/alive` 已确认空 `data`）。

> 注：这趟是把脚本放在 probe 目录里跑的，脚本按 `dirname(dirname(__file__))/cdp` 找 cdp ⇒
> observe 那一步报 `/company/siteforge/docs/cdp` 不存在（trace 里 `progress:null`）。
> 只影响「这一步有没有推进」的观测，**不影响填值**；生产那份在 `forms/sites/` 下，路径是对的。

## FMR（已推）

| | sha256 | 字节 |
|---|---|---|
| 推之前 | `a6ca371c24c872d8017b39d6afcaaefed3ff1f7025313b941f4eddb5411a5809` | 269554 |
| 推之后 | `c26101298a500c2d41144198f6c19a3206002a638c2869e7c3c00f5000b144e2` | 269566 |

- 写口：`POST /api/quest/formScript/update`（`status:1`，operator 记的是用户这次的要求）；
- 回执业务码 `200`、后端回的新指纹与本地一致；**回读**（正常读，不加 `debug`）逐字节一致、
  `__our_au_mobile__` 在正文里 ⇒ 线上就是这一份（不是「接口回 200」就算）。
- 留底 `fmr_before_a6ca371c24c8.py`（= 推之前线上那份，与 `healthinsurancewatch_ORIGINAL_…py` 相同）。

## 文件

| 文件 | 是什么 |
|---|---|
| `healthinsurancewatch_ORIGINAL_a6ca371c24c8.py` | 推之前线上那份（269554 B） |
| `healthinsurancewatch_PATCHED.py` | 改完、推上去的那份（269566 B） |
| `fmr_after_c26101298a50.py` | 推完**回读**下来的那份（与 PATCHED 逐字节相同） |
| `patch_phone.py` | 一行替换的补丁脚本（锚点唯一断言 + AST 语法闸 + 逐行 diff） |
| `push_fmr.py` | 推 FMR（留底 / 写 / 回读校验；`--apply` 才写） |
| `form.json` | 真站自测用的资料（`phone` 故意给 `0499888777`） |
| `trace.jsonl` | 真站跑到第 13 步的逐步记录 |

## 备注

- 本仓 `forms/sites/healthinsurancewatch.candidate.py`（2026-10-12）是**另一份更早的草稿**
  （6 个状态、没有 sample 值），不是线上这份（8 个状态）；这次没动它。
- 以后同类需求优先看脚本的 `_fill_value` 机制：`source` 换成资料里没有的键 + 一个真站收过的
  `sample`，就能只走形状、不读资料；不需要改 `_fill_value` 本身。
