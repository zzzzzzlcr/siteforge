# Atticus（site `try`）修站停下记录

2026-10-21。目标 `https://try.atticus.com/ssd/benefits`，在 8100（siteforge-222）上
修/重建。**未交付、未推 FMR**；盘上最终那版 py 备份在本目录 `try.draft.py`。

## 这次到底卡在哪

不是「文本框顺序变了」，而是**同一个 URL 在不同会话里返回两种落地屏**：

| 场次 | URL | 实际正文 | 控件 |
| --- | --- | --- | --- |
| 重建探路 19:22 | `.../survey/landing/default` | `What's your age?` | Age 输入 + Next |
| 自测 19:31 | 同一个 URL | `What's your ZIP code?` | ZIP 输入 + Next |

产物（19 状态版）在 `benefits` 里写死 Age：先点 Age、再填 `FILLS['number']`。
自测这趟页面是 ZIP 变体，Age 的第 1 个 selector `input[placeholder="Age"]` 等了
45 秒没找到，退回第 3 个泛选择器 `input.MuiInputBase-input.MuiOutlinedInput-input` ——
它命中的其实是 ZIP 框，于是把年龄 58 填进了 ZIP；Next 当然不动。
两次 45 秒白等 + 反复 Next 重试，把自测拖到 **600 秒超时**。

## 已经做出来的部分

重建探路是成功的：`job-920c8bac4b47` 第 72 步见到成功文案
（`reached_success`，379 秒，真提交 1 次），账本完整。由它写出的 21 状态稿包含：
入口点击 → Age/ZIP 两条落地分支 → zip-first-state → 后续问卷屏 → 联系方式
（first/last/email/phone）→ Submit。

在闸上补的 A/B 修复已经生效：

- `zip_landing`：`landing/default` + `text_contains "What's your ZIP code?"`
  → fill `postcode` + Next
- `benefits`：加了 `text_contains "What's your age?"`，保留 Age 分支
  （否则它没有 when，会永远先匹配，ZIP 分支轮不到）

**已知遗留**：`benefits` 的 steps 第一条还留着入口的
`click "See if you qualify for benefits"` —— landing/default 的 Age 屏上没有这个链接。
lint 闸上点名要删，但改稿那双手连着两回交回逐值相同的稿（空转闸），这一条没删掉。

## 手动自测（备份稿，19:49）

在干净 Bit 窗口上直接跑备份稿（已删掉 `benefits` 里那条多余入口 click），
`--no-report`，一次真浏览器：

- 这趟落地页是 **Age 变体**；`zip_landing` 正确跳过，`benefits` 分支执行了
  `click Age → fill Age → click Next`（第 4–6 步），Age 的
  `input[placeholder="Age"]` 这次找到了，填值也成功。
- 但点完 Next **URL 仍是 `landing/default`**（同一页换屏），而后续状态的 `when`
  全是 `/survey/claim/...` ⇒ 第 7 步起 34 步全跳过；38 步里只做了 4 步，exit 1。
- 没走到联系方式屏，**没有提交**（这趟没有新的站方提交）。
- 结论：不是「Age 填错了」，而是这是一条**单页漏斗** —— 同一个 URL 连续换屏。
  线性 STATES 表按顺序只走一遍，过了 `zip_landing` 就不会回头再看同一 URL 的
  下一屏；这类站该用**循环型** `try.loop.py`（每轮重读页面、有什么填什么），
  而不是表格式。你的判断「按落地页正文判 Age/ZIP」对**第一屏**成立，但后续屏
  也在同一 URL 上，表格式的线性顺序接不住。

## 循环型自测（20:14）

干净窗口、入口页、`try.loop.py`（09-28 那份循环型）、`--no-report`：

- 前置点「See if you qualify」→ 到了 landing/default。
- 第 1 步填 ZIP（`zipcode`）→ Next → “Atticus got 334 New Yorkers…” → 第 5 步填 Age
  （`:r0:`）→ Next → “Disability benefits provide monthly checks”。
- 第 8–10 步连点 3 次 Next，日志里页面还是 “Disability benefits provide monthly
  checks”；`SAME_NEXT_LIMIT=3` 触发收摊（exit 1）。
- 但收摊后看窗口，页面其实已经推进到
  `.../survey/claim/age-zip-first-seeking-benefits-for` ——
  “Who are you seeking disability benefits for? Myself / Family member or friend /
  Patient or client”。也就是说 **Next 是有效的，只是这一屏换页比 0.6–1.8s 的
  迭代间隔慢**，第 3 次点击后页面才变，护栏先收了。
- 没走到联系方式屏，**没有提交**。
- 结论：循环型是正确形状（比表格式多走了两屏），卡的是 `SAME_NEXT_LIMIT`/换页
  等待的时序，不是选择器。下一步要么把 `SAME_NEXT_LIMIT` 调大，要么改成「点完
  Next 先等正文/URL 变化，再计一次同屏」，再跑一遍。

## 为什么停

- 修站补丁器**不能可靠地加/删 step**：两次明确的闸上指令（插 ZIP 填值、删多余入口点击）
  都返回了逐值相同的 `states`，被「一处都没改」闸拦下。
- 自测的 600 秒上限被两次 45 秒的 selector 等待吃掉，诊断只能拿到「超时」。
- `form` 填值只报 `ok`，**不回读 value**：填错框（58 进 ZIP）在 trace 里是一条
  `ok=true, progress=false`，看起来像「Next 有问题」，实际是填错了元素。

## 这件事能处理吗

**能**，但要落在正确的层：

1. 同一 URL 多屏/多变体 ⇒ 产物要按**正文**分支（`when.text_contains`），不能只按 URL。
   这次两条分支已经证明写得出来。
2. 泛兜底选择器（`input.MuiInputBase-input...`）必须**要求唯一命中**，或者按
   label/placeholder/所在问题块收窄；否则它会在 A/B 变体里静默填错框。
3. `form` 之后要**回读 value**（这一格有没有真变成目标值）；只判 `ok` 不够。
4. 自测里 selector 第一次等 45 秒太长：A/B 变体下它直接拖垮 600 秒预算。
5. 修站补丁器对「加/删 step」的空转，是**修复能力**的缺口，不是这个站的问题。

## 任务与备份

- 重建：`job-920c8bac4b47`（explore 成功、draft 21 状态、selftest 超时、已停）
- 早先修站：`job-fc49588e09e7`（修出 `default_age`，`zip_first_state` 缺 ZIP 填值，
  空转收场）；`job-714afd1da12e`（停在 lint，未继续）
- 备份：本目录 `try.draft.py`（21 状态，含 `zip_landing`；`benefits` 仍有那条多余点击）
- 容器备份：`/opt/siteforge/runtime/try.draft-20261021.py`
- 未推 FMR、未改 8099。
