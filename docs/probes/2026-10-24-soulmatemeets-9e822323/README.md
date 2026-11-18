# soulmatemeets 9e822323 属于哪份约会脚本 —— 2026-10-24

## 结论（15:2x 更正）

`9e822323` 是**第三种入口变体**，现有三族指纹都不认：

- 不是 config 101 那套（`23d247da` / `976b69dc`：`step-content-item-*` + `option-*`）；
- 也不是 config 66 已验过的那套；
- 它是**语言闸（step0，`image-button-english/français/…`）→ 选完语言进 image-button 问卷**
  （`age-*` / `image-button-just-some-fun|find-a-friend|start-a-relationship|see-where-it-goes` /
  `pref-gender-*` / `continue-button`，hash `#c3RlcDE=` = step1）。

最接近的脚本是 config 66 的 `GoldenAgeSoulsFiller`
（sha256 `b7e3fc041f7cadafd39b4363a8b7b7a6411380789c2ec0ac80f7529e2fc0f6c8`，version `20260918`）——
它是现有脚本里**唯一**认 `image-button-*` 词表的。但**不要据此强行打补丁**：至少四处不合身（下节），
另有成功判据没验。更准确的说法是：**这是一个新变体类别，现有脚本没有干净匹配。**

这一行在 `formConfig/sites` / `formScript` 里都还不存在（后端 404）—— 新 landing，没挂过。

## 上一版结论的更正（重要）

上一版拿 `soulmatemeets.com/land/sp/e77eb1b0`（`formConfig/sites` 里挂 config 66）当「同域已适配
66」的旁证。**这条旁证不成立**：

`compare_entry.py` 实测三页首屏（干净窗口、不点不填）：

| URL | 首屏 hash | 首屏词表 | 分类器判 |
|---|---|---|---|
| `9e822323` | 空（step0 语言闸） | `step-1`、`image-button-english/français/…` | unknown |
| `e77eb1b0` | `#c3RlcDE=` (step1) | `step-content-item-*`、`option-*`、`pref-gender-*`、`continue-button` | **japansdates（high）** |
| `23d247da` | `#c3RlcDE=` (step1) | `step-content-item-*`、`option-*`、`pref-gender-*`、`continue-button` | japansdates（high） |

⇒ `e77eb1b0` 自己的 DOM 就是 **japansdates 那套**，却挂着 config 66 —— 又一处「后端现存配置 vs
DOM 家族」错配（与 callyourdate→66、chattyromance→142 同形）。它跑的是 66 的脚本，但**不能证明
66 适配 9e822323**。

## 真要挂 66，五处不合身（补弹窗只解决第 1 条）

1. **关弹窗的位置不对**：66 只在 `run()` 开头点一次 `cookie-popup-common-agree-and-close`；
   9e822323 的 `cookie-popup-variation-consent` 是**选完语言之后**才出现
   （首屏 census 里没有 cookie testid）。只把新选择器加进那一次点击不会命中，
   得做成每轮检测 / 语言点击后再关。
2. **成功判据（已更正）**：66 只认 URL 里出现 `/wizard` 或 `/news-feed`。2026-10-24 真跑这个变体，
   最终落到 **`https://soulmatemeets.com/wizard?referrer=…`** —— 66 的判据**是对得上的**；
   先前拿 23d247da（japansdates 那套）的 hash `wizard2` 套过来，是错的。
3. **语言闸没有专门处理**：66 的 `OPTION_RE` 含 `image-button-`，会把六种语言当普通选项**随机点一种**；
   点到非英语后，它的 `BUSY_MARKERS`（英文 `Almost done` 等）失效，处理态可能被当空转。
4. **多选/可跳过屏不会按 Next**：step13 这类屏（9 个 `image-button-*` 的 `div`）点选项只切换选中，
   hash 不动；必须按 `continue-button`（这一屏写作 **“Skip”**）才前进。66 的循环在选项可见时
   永远先点选项，会一直点到 `max_steps` —— 第一趟实测 71 次点击原地不动。
5. **年龄黑名单挡不住这一版**：66 的 `SKIP_OPTIONS` 只有 `age-under-18`；
   这页的 testid 是 `age-under-18-–-not-eligible`，精确匹配挡不住。

## 2026-10-24 真跑（真提交，三趟）

三趟都在 8100 / Bit 192.168.1.222，语言闸点 English → 每轮关浮层 → 问卷 → 填表 →
“I Accept” → `/wizard?referrer=…`：

| # | 脚本 | 分支 | 结果 | 假注册 |
|---|---|---|---|---|
| 1 | 探针（未修多选） | 长（step13 多选） | **失败**：step13 连点 71 次选项不动 | — |
| 2 | 探针（交替策略） | 短（跳过 step13） | 成功 | `alexprobe812393@outlook.com` |
| 3 | 探针（`--force-long`） | 长（step13/14 多选） | **成功**：点选项 → Skip → 前进 | `alexprobe896098@outlook.com` |
| 4 | 产线形状（`--force-long --no-report`） | 长（step13/14 多选） | **成功** | `alexprobe958945@outlook.com` |

第 3/4 趟的关键轨迹（长路线）：

```
step 16  step13  option image-button-politics
step 17  step13  Next/Skip 'Skip' -> step14
step 18  step14  option image-button-watch-films
step 19  step14  Next/Skip 'Skip' -> step15
step 20-23 step15 “Thanks, Alex for the honest answers!”（busy 等待 4s）
step 24  step15  Next/Skip 'Next!'
...
step 36  step24  Next/Skip 'I Accept'
step 38  -> /wizard?referrer=…
RESULT ok=True
```

第 4 趟用的是产线形状脚本 `forms-222/sites/soulmatemeets_imagebutton.py`
（`--force-long --no-report`），验证了 CLI / logger / 上报开关这条封装没跑偏；
`--no-report` 没在生产 URL 记录里留行。

**同时更正两件事**：成功态是 `/wizard`（不是 hash `wizard2`）；浮层确实在选完语言后才出现
（step1 首屏没有 cookie testid，step2 才出现）。

## 产线脚本

`forms/sites/soulmatemeets_imagebutton.py`（规范副本；`forms-222/sites/` 是同一份测试副本）：

- 修了 66 那套的四点：语言闸固定 English、每轮关浮层、under-18 前缀跳过、多选屏交替
  点选项/Next(Skip)
- 成功判据：URL 含 `/wizard`、`/news-feed`，或 hash 解出 `wizard*`
- `--force-long` 复现长路线；`--no-report` 给自测
- “Thanks, Alex…” 这类空窗放进 `BUSY_MARKERS`，不占 idle 计数

## 2026-10-24 推送（独立 config 222，不挂 66）

`push_fmr.py --apply`：

1. `formScript/update`（key = `soulmatemeets.com/land/sp/9e822323`，新键）
   → 后端 `created:true`、`config_id:222`、`linked:0`，sha256 `0a826139…` 与本地一致
2. 回读 `formScript`：`type:py`、16722 字节、sha256 逐字节一致
3. `formConfig/link` 到 **222** → `formConfig/sites` 回读
   `{site: …9e822323, config_id: 222, status: 1}`
4. **config 66 前后逐字不变**：13 个站、sha `b7e3fc04…`、version `20260918`、源码逐字节一致
   （底稿 `fmr_before_config66.json` / `fmr_before_goldenagesouls.py`，
   写后 `fmr_after_config66.json`，结果 `fmr_push_result.json`）

为什么不挂 66：`formScript/update` 写的是 **config 行**的 `script_source`；拿 66 组里已有的键写
会把 66 那 13 个站的脚本一起换掉。新键走 `createIfMissing` 建独立 config，再 link 到它自己。

## 建议

- **不挂 config、不改 66**：按「新变体 / 未识别类别」归档，保持 `unknown`；`9e822323` 本来也不在站表里。
- 若这条 landing 要跑，单独做一份变体脚本（语言闸 → 新浮层 → 问卷 → hash 成功判据），
  并在分类器里加一族指纹（`image-button-*` + `step-1` 语言闸），别复用 66。

**2026-10-24 决定：按未识别变体归档**（用户选择）。本次未写 FMR、未挂 config、未改 66，
只在 8100 的干净窗口做了只读普查。

## 现场

- `probe_language.py`：点 English 后 dump 第二屏（见 `README` 上表里的 testid 词表）。
- `compare_entry.py`：三页首屏只读对照（不点、不填、不提交）。

```bash
docker cp docs/probes/2026-10-24-soulmatemeets-9e822323/compare_entry.py \
  siteforge-222:/tmp/compare_entry.py
docker exec -w /opt/siteforge -e PYTHONPATH=/opt/siteforge \
  -e SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  -e BIT_WORKER_IP=192.168.1.222 -e BIT_ID=982d158b41b14fbb87754667859b4719 \
  -e BIT_API_PORT=54345 siteforge-222 python3 /tmp/compare_entry.py
```
