# belong.com.au `/go/mobile` · 2026-10-20

- 来源 job：`job-e42fa72358ce`（8100，build，带话重跑；旧 job `job-7427b3e7cc41` 是 `repair_noop`）
- 产物：`forms-222/sites/belong.py`（318885 B，sha `de9e5404da4c107a…`）
- 自测：`runs=1 / passed=1 / submissions=1 / verdict=true`（2026-10-20T10:13:48Z；
  ⚠️ 这是**更早字节**的 verdict，后续 DOB/密码改动没有再跑真站提交）
- 关键修复：第 3 步 eSIM 点击的前两条选择器改成
  `input[name="simType"][value="ESIM"]` / `label:has(> input[name="simType"][value="ESIM"])`，
  旧 `div[data-testid="ESIM"]` 退到第 3；Next 前多了一步 `scroll`
- 资料：
  - first / last / email：吃任务资料；
  - DOB：最终版按站点要求**随机** `DD/MM/YYYY`、年龄 18-68
    （`FILLS['dob'].source='__random_dob_dmy__'`，fallback `dob_dmy`）；
  - password：最终版按站点规则**随机**（≥8 位、大小写、数字、`!@#$%^&*`；
    `FILLS['password'].source='__strong_password__'`，fallback `password` = `Test####!`）；
  - 这两格是**故意不吃资料**的（用户 2026-10-20 18:27 要求）；
    `_ALIASES["dob"]` 仍保留给别的站用（belong 用 source 哨兵绕开它）
- FMR：key `www.belong.com.au/go/mobile`，`config_id=202`，`status=1`；
  18:23 新建推 `f057b4cb…`，18:25 修 DOB 别名推 `36ab64b6…`，
  18:31 最终版推 `de9e5404…`（318885 B），回读 sha 与本地一致；
  旧版留底 `fmr_before_f057b4cb39ea.py` / `fmr_before_36ab64b683ba.py`
- 验证：DOB `dob_dmy` 500 次全部 `DD/MM/YYYY` 且年龄 18-68；
  password 500 次全部匹配 `^(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])(?=.*[!@#$%^&*]).{8,}$`
- ⚠️ 生产路由表 C15 仍未登记 —— FMR 有脚本不等于生产会调它；
  如果生产用的 landing URL 不是 `www.belong.com.au/go/mobile`，FMR 的 key 也要跟着改

## 2026-10-20 规则勾选实现后

- belong 的 DOB/密码不再需要手改产物：面板填「手机号前缀（04/0207）」、勾「生日随机 / 密码强随机」即可，
  规则在生成期烤进 `FILLS`（`rule`/`prefixes`/`date_format`）；旧产物不变。
- spec/plan 见 `docs/superpowers/specs/2026-10-20-rules-checkbox-design.md` /
  `docs/superpowers/plans/2026-10-20-rules-checkbox-plan.md`。
