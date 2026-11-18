# 约会站分类器：真站只读抽查 — 2026-10-22

命令（**8100 / Bit 192.168.1.222**；`siteforge.env` 里那格 worker 是 197，必须显式覆盖）：

```bash
set -a; . /company/siteforge/siteforge.env; set +a
export SITEFORGE_CDP_BIN=/company/siteforge/tools/cdp/cdp
.venv/bin/python -m agent.classify '<url>' \
  --worker 192.168.1.222 --bit-id 982d158b41b14fbb87754667859b4719 --json
```

**未跑 `--apply`**：`fmr.3tkj.cn` 上 `/api/quest/formConfig/link` 仍是 404（后端本地仓库有实现
`df4938d`，等部署）。所有抽查都是只读。

## 结果

| URL | 平台 / config | 置信度 | 关键证据 | 点 Start |
|---|---|---|---|---|
| `soulmatemeets.com/land/sp/23d247da/` | **japansdates / 101** | high | `step-content-item-*`、`option-*`、`legal-checkbox`、`continue-button`、hash `#c3RlcDE=`→`step1` | 否 |
| `soulfuldate.com/land/sp/a64f40a4/` | **japansdates / 101** | high | 同上 | 否 |
| `callyourdate.com/land/sp/5549e30e/` | **japansdates / 101** | high | 同上（见下面「后端现存配置」） | 否 |
| `chattyromance.com/land/sp/81d0a481/` | **japansdates / 101** | high | 同上（后端现存配置是 142） | 否 |
| `goldenagesouls.com/` | **goldenagesouls / 66** | high | `option-man`、`gender-male`、`birth-date`、`terms__checkbox`、`reg-form__body`；**没有** `step-content-item-*` | 否 |
| `www.wildnutrition.com/` | **unknown**（exit 2） | — | 非约会页 | 否 |

## 三次真站纠正（都写回了代码/规格）

1. **census 不能只看可见 testid**：soulmatemeets 首屏的 `option-*` / `continue-button` / `legal-checkbox`
   大多是隐藏的，只看可见 testid 会判成 `unknown`。现在 `testids` / `classes` **不按可见性过滤**。
2. **goldenagesouls 的指纹原来写错了**：真站是 `option-man` / `gender-male` / `birth-date` /
   `terms__checkbox` / `reg-form__body` 这套，**不是** `age-*` / `image-button-*`。
3. **开窗后 CDP 目标还没通**：`fresh_open()` 刚回来就 navi，会导航在空白页上（census 空标题/空 testid）。
   现在先 `wait_for_window`（轮询 `JSON.stringify(location.href)`），导航后再 `wait_for_census`；
   census 仍为空 → exit 3（窗口失败），不再误报 `unknown`。

## 后端现存配置 vs DOM 家族（一个需要人看的发现）

`formConfig/sites` 里：

- `callyourdate.com/land/sp/5549e30e` → **config 66**（`GoldenAgeSoulsFiller`）；
- `chattyromance.com/land/sp/81d0a481` → **config 142**（`DelachatFiller`）；
- 但这两页的 DOM 都是 **japansdates 那一套**（`step-content-item-*` / `option-*` / `legal-checkbox` /
  `continue-button` / hash `#c3RlcDE=`），与 soulfuldate 10-10 那次「错配到 datewhirl、真该用
  japansdates」是同一类。

⇒ 分类器按 **DOM 家族**推荐 **japansdates / 101**。这和「后端现在把这两页挂在哪」不一致；
**没有**自动改这两行（`--apply` 没跑）。要不要把它们改到 101，由人看过再决定。

## 2026-10-22 17:23 `--apply` 端到端（cupidfeel）

用户给的新 landing：`https://cupidfeel.com/land/sp/df41954d/`（后端原本没有这一行）。

- 分类：japansdates / 101（high；证据同 soulmatemeets）。
- 第一次 `--apply` 回 **HTTP 500**，回读确认**没有半写**。原因：后端审计表
  `form_config_site_log` 没建（提交 `df4938d` 自己写明「需手工建表」），控制器把写映射与
  写审计放在同一事务 ⇒ 缺表回滚。
- 建表后重跑：**`created=true`**，回读 `cupidfeel.com/land/sp/df41954d → config 101, status 1`。
  其它 cupidfeel 行未动（697c1214→142、ef697977→66、cupidfeel.com→148）。

## 还没验到的

- **delachat**：当前指纹（`terms__button`）在抽到的 config-142 站上没有命中——那页是
  `step-content-item-*` 变体，被归到 japansdates 了。真正的 `terms__button` 页还没遇到；
  这条指纹暂时保留为**未真站验证**。
