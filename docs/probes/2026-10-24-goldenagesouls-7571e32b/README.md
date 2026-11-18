# `goldenagesouls.com/land/sp/7571e32b` 属于哪一族 —— 确认记录

2026-10-24 14:12–14:15。只读 + 8100（Bit 192.168.1.222）上真跑一遍，**没写 FMR、没动 8099**。

## 结论

**这条分支就是 japansdates 那一族**，用户说得对。FMR 上它挂的是 config **101**
（= `japansdates.com` 那组），脚本是 japansdates 的 py（sha `4c8b9fbe…`，status 1）。

| 键 | config | 脚本 | 指纹 / 体量 |
| --- | --- | --- | --- |
| `goldenagesouls.com/land/sp/7571e32b` | **101**（japansdates 组） | japansdates py | `4c8b9fbe…` / 54388 B / 1029 行 / status 1 |
| `goldenagesouls.com`（裸主机） | 66（goldenagesouls 组） | goldenagesouls.py | `b7e3fc04…` / 14846 B / 327 行 / version 20260918 |

本地可对上号的：`docs/probes/2026-10-23-soulmatemeets-101/japansdates_HOOKED.py`（逐字节，归一后
`4c8b9fbe…`）；`goldenagesouls.py` 对的是 `/opt/skills/auto-farm-skill/forms/sites/goldenagesouls.py`。

## 一、这一页的记号（`probe_branch.py`，8100 真开页面）

```json
url   : https://goldenagesouls.com/land/sp/7571e32b/?id_visit_prev=…#c3RlcDE=
hash  : #c3RlcDE=  → 解码 "step1"
title : Join GoldenAgeSouls Online Dating Platform
testids: step-1, step-content-item-Basic, step-content-item-AgeLegalText,
         step-content-item-ChooseAge, cookie-popup-variation-consent, …
image_buttons: 0   age-18-24: 0   gender-*: 0   pref-gender: 0
firstname: 0   email: 0   password: 0
```

- `#c3RlcDE=` → `step1` 是 **japansdates 族写在 hash 里的状态名**（该族 README 实测过
  `c3RlcDE=`(step1) … `c2l6YXJkMg==`(wizard2)）；
- `step-1` / `step-content-item-<Type>` 是那一族 DOM 的骨架；
- goldenagesouls **改版问卷**的记号（`button[data-testid=age-18-24]`、`image-button-*`、
  `gender-female`、`pref-gender-*`、`firstname/email/password`）**一个都没有**。

## 二、拿线上那份代码真跑一遍（`run_jap_on_7571e32b.py`）

跑的是**线上同一份字节**（`jd_pkg/sites/japansdates_hooked.py`，sha `4c8b9fbe…`），
`_rpt` 换成空函数 ⇒ **一个字节都不报给 FMR**；窗口用 8100 那颗 Bit，跑完自动关窗。

```
step 1  ChooseAge            → (文本) 35-44
step 2  Preferences          → option-start-relations
step 3  ChoosePrefGender     → pref-gender-male
step 4  ChooseGender         → gender-female
step 5  InputNameAutofill    → filled firstname
step 7  InputEmail           → filled email
step 10 InputPassword        → filled password
step 12–14 CheckboxTerms     → nothing actionable (1..3)  ← 条款屏在换页，脚本按时间预算等
step 14 SUCCESS: …/land/sp/7571e32b/…（站点状态 wizard1）
== run() 返回 True，耗时 34s
```

- `hash_state` 从 `step1 … step8` 一路走到 **`wizard1`**；
- 脚本自己的判据 `HASH_SUCCESS_STATES = ("wizard", "wizard2", "main-page", "feed")`
  是按前缀比的 ⇒ `wizard1` 算成功（注册区第一屏 = 账号已建）；
- 条款屏那三次 `nothing actionable` 之后 URL 才翻到 `wizard1` —— 是换页，不是死路。

## 复跑

```bash
# 只看这一页是哪一族（8100 开窗口，不跑脚本）
docker cp probe_branch.py siteforge-222:/tmp/ && docker exec siteforge-222 python3 /tmp/probe_branch.py

# 拿线上那份 japansdates 脚本真跑一遍（宿主上跑，走 192.168.1.222:54345）
cd docs/probes/2026-10-24-goldenagesouls-7571e32b && python3 -I run_jap_on_7571e32b.py 60
```
