# youmetalks b4dd2154：条款屏勾不上、勾上也不走 —— 2026-11-07

## 结论

`youmetalks.com/land/sp/b4dd2154` 生产单连续失败（最近 27042691 / 27053769，均 `max_steps`
停在 `step-content-item-CheckboxTerms`）。不是网络/代理，是**条款屏的两个记号脚本都没认**：

1. **勾选框在视口外**：`[data-testid="legal-checkbox"]` 本身在 DOM 里、`display` 也可见，
   但 `getBoundingClientRect().x = -9974`。`cdp form --check` 因此拒绝点击
   （「它中心的整摞里没有它」）；生产脚本的兜底也是 `cdp click`（label / input），同样
   点不到。→ `_check_terms()` 永远 False，日志里连「勾选条款」都没有。
2. **继续键不叫 `continue-button`**：勾选框旁边那颗可见按钮是
   `<button data-testid="registration-button" class="btn btn_terms">I Accept</button>`
   （勾上后 class 增加 `btn_animate btn_active btn_enabled`）。`READ_PAGE_JS` 只认
   `continue-button`，所以 `nextVisible=false`；`labels` 里又只有四条法律链接
   （`terms/privacy/arbitration` 被 `DENY_TEXT_RE` 正确拦掉），于是每轮都
   `nothing actionable` 到 45s。

失败现场（FMR `diag`）完全对得上：

```
step=step-content-item-CheckboxTerms next=False legal=False options=[]
labels=['Terms of Use,', 'Privacy Policy,', 'Payment and Refund Policy,', 'Arbitration Agreement.']
inputs=[]
```

## 修法（已上线）

线上脚本 `youmetalks...`（config 247）两处：

1. `_check_terms()` 末尾加第三道兜底：`e.click()`（JS 合成点击）。本屏实测有效 ——
   勾上后 `checked=true`、label class 变 `terms__label_checked`。
2. `READ_PAGE_JS` 把可见的 `[data-testid="registration-button"]` 也算作 `nextVisible`，
   并打 `data-sf-next="1"`，走主循环第 4 步已有的 `_click_visible_css` 兜底。

`japansdates_before.py` = 写前原文（sha `37a2cbe0…`），
`japansdates_patched.py` = 写后原文（sha `4d49e06a…`，62366 B，CRLF 保留）。

## 验证

在 `siteforge-222` 的 Bit 窗口上，用**写进去的同一份字节**真跑（`run_patched.py`，
`_rpt` 换空函数，不写 FMR）：

```
step 13: 勾选条款
step 14 [CheckboxTerms] continue
step 15 [CheckboxTerms] continue
SUCCESS: https://youmetalks.com/land/sp/b4dd2154/…（站点状态 wizard1）
== run() 返回 True，耗时 55s
== hash #d2l6YXJkMQ==（wizard1）
```

写回：

```
POST /api/quest/formScript/update  → status 200
读回 sha256 = 4d49e06a9947641eb6b8e1cb0569f031b225f1ac0c8b239dc40c74acb82b5347
```

## 证据文件

| 文件 | 内容 |
| --- | --- |
| `terms_dom.json` | 条款屏 DOM：input 在 x=-9974；兄弟 `label[for=terms-agree].terms__label` |
| `terms_before.json` / `terms_after.json` | 勾选前后可见按钮：`registration-button`「I Accept」，勾选后 `btn_enabled` |
| `probe_terms.py` | 到达条款屏、dump DOM、试 `cdp form --check`（被拒） |
| `probe_terms_js.py` | 只做 `e.click()`，确认能勾上但页面仍无 `continue-button` |
| `probe_terms_buttons.py` | 勾选前后按钮全量 dump（找到 `registration-button`） |
| `run_patched.py` | 用补丁后的源码真跑，返回 True |
| `japansdates_before.py` / `japansdates_patched.py` | 上传前后完整源码 |

## 风险与边界

- config 247 另有 `lovesmoments.com/land/sp/09ffde85`、`meetheage.com/search/online`、
  `miaromance.com/ma`、`allfeellove.com/land/sp/24020051` 四个站。补丁是**纯增量**
  （多认一个 `registration-button`、多一道 JS click 兜底），不改变其他站已有的继续路径；
  但本次只在 youmetalks 上真跑过。
- 10-30 那批 allfeellove 失败是另一个原因（同意条误点，已由 2026-10-30 的
  `_dismiss_consent` 修复）；11-07 的 youmetalks 失败与它无关。
