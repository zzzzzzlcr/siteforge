# 回放影子测试：真实 observe dump × Intent Runtime

2026-10-21。验的是 `04c4740` 那份共享 Runtime 原型（`agent/browser_runtime.py`）
在**真实站点捕获的 PageModel** 上挑不挑得中，而不是只在合成 `demo.py` 上成立。

**探针产物，不是生产代码；不改 ledger，不碰 FMR。**

## 结论

| kind | cases | hit | wrong | abstain | miss |
| --- | ---: | ---: | ---: | ---: | ---: |
| advance | 8 | 6 | 0 | 2 | 0 |
| choose | 150 | 149 | 0 | 1 | 0 |
| fill | 7 | 6 | 0 | 1 | 0 |
| **合计** | **165** | **161** | **0** | **4** | **0** |

- 自动用例 161 例：**0 错选、0 未命中**。这一层的意思是「页面里确实有这个元素时，
  按语义找得到」，在 3 个真站的 dump 上成立。
- 4 个弃权全部是安全弃权（`ambiguous` / `not_found`，没有落到某个候选上）。
- 手工正例 blinkist 登录页 `advance("Log in")`：修 `advance` 前错选
  `Continue with Apple`（score 95），修后**变成弃权**——Apple 被第三方登录负分
  压到 15，但 Facebook 的 `Log in` 和 `Login with email` 同为 65，按规则不按
  DOM 序硬点。
- 所以：**修完后 165 例里 0 错选**；不是能力够了，而是「乱点」被压住了。
  剩下的问题是登录页分不清「Facebook 登录」和「邮箱登录」——provider 名只在
  selector/aria-label 里，旧 observe 不给 `aria-label`（blinkist 探针 U2 已判）。

## 数据来源

- 46 份真实 `cdp observe` dump，3 个站：wildnutrition（40）、blinkist（5）、
  georgiapower（1）。
- 20 份带 DOM 状态侧车（`state-*.json` / `r3-state-*.json`），提供 question 和
  选项真值；配对按文件名，**20/20 的 `url` 逐字相等**（写脚本核过）。
- 用例不发明真值：
  - `advance`：全页恰好一个 forward 词按钮（`next/continue/...`）才收；两个就不收。
  - `choose`：答案来自状态侧车里的 DOM 选项标签，`observe` 只负责找。
  - `fill`：只收 label/placeholder/hint 命中 email/password/zip/postal/phone 的字段。
  - 负例 3 条手工列出（见下），因为「页面上没有这个目标」无法从 DOM 自动推。
- 覆盖：20 份配对 dump 里 **149 个可标注选项全部用上，0 跳过**。

## 证据行

**修前唯一错选 / 修后弃权：**

```
manual:blinkist-login:advance | 修前 score 95 -> "Continue with Apple"
                              | 修后 score 15 -> ambiguous（不点）
expected button[data-testid="auth-submit-button"] ("Login with email")
```

修前选它的原因不是运气：`"continue"` 命中 forward word（+35），`"Log in"` 又出现在
它的 `nearby_text`（`Log inSign in` / `Log in with SSO`，+45），加 region main（+15）。
**question 上下文没有纠错，反而加固了错选。**

修后 `Continue with Apple` 被第三方登录负分压到 15；但 Facebook 的 `Log in` 和
`Login with email` 同为 65，所以弃权。**弃权比按 DOM 序点一个更安全**，这一条
也从 wrong 变成了 abstain。

**安全弃权：**

```
wildnutrition Q10 advance（整页只有 ← BACK，has_continue=false） -> ambiguous
wildnutrition Q10 choose("Vegan diet")（答案不在本屏）           -> ambiguous
georgiapower 地址屏 fill("email")（没有 email 字段）              -> not_found
```

**命中样例：** `fill("zip")` → `#input-167`（score 96）；quiz 选项
`"Better sleep"` → 对应 `li`（score 95），即使页脚/导航里有同名 `"Better sleep"` 链接。

## 这一轮修了什么（TDD）

`agent/browser_runtime.py` 的 `_score_advance`：

- 新增 `AUTH_VERBS` / `AUTH_PROVIDERS`；文本**同时**命中 auth 动词
  （`continue with` / `sign in with` / `log in with` / ...）和 provider
  （`apple` / `google` / `facebook` / `microsoft` / `sso` / ...）才扣 80 分。
  所以 `Continue with Apple` 被扣，`Continue with email` 不扣。
- 先红后绿：`tests/test_browser_runtime.py` 新增 3 条——邮箱登录 submit 胜过
  `Continue with Apple`；只有第三方登录且无 question 时弃权；
  `Continue with email` 仍可选中。
- 回归：165 例从 **1 wrong → 0 wrong**；自动用例 161 例不受影响。

## 真实形态发现

1. **第三方登录链接会伪装成前进（已修）。** 登录页的 `Continue with Apple` 和
   问卷页的 `Continue` 在模型里长得一样「像前进」，前者因为附近的 `Log in`
   还多拿 45 分。修后不再错选；登录页仍弃权，因为 Facebook 的 `Log in` 与
   邮箱登录同分，而 provider 名只在 selector/aria-label 里，旧 observe 不给
   `aria-label`。这属于观察层缺口，不该用 selector 白名单去补。
2. **旧 observe 根本没有 `disabled`。** `observe-certain-initial.json` 里
   `"disabled"` 出现 0 次；而 blinkist 探针 README 已证那一屏的 Continue
   在 DOM 里是 `disabled=true`。回放把它算成 hit——**hit ≠ 可执行**。
   wildnutrition/georgiapower 的新 dump 有 `disabled`，但字段版本不齐这件事
   本身就是适配层的风险。
3. **文本会被截断两处。** `observe` 的 action text 截到约 50 字符，状态侧车的
   li text 截到约 72 字符；长选项（`Bones, joints + muscles`、
   `Perimenopause + menopause...`）最初有 42 例（observe 侧）和 12 例
   （state 侧）对不上，靠唯一前缀匹配才恢复。生产 resolver 用 `answer in text`
   （答案短）不受影响，但任何「全文相等」的判据会踩。
4. **同名导航链接是真实的。** 11 例 quiz 选项在页脚/导航里有同名链接；
   回放里全部选中 `radio`（region main + role radio），没有选链接。

## 这个回放没证明什么

- **只 resolve，不 execute**：没有真的 click/form，没有 action 回执，
  也没有 fill 后的 value 回读。blinkist 的填值前后 observe 逐字节相同
  （旧 observe 不给 `value`），所以 fill 的「回读校验」在这里完全没验。
- 答案是 DOM 里的原样标签，等价于「已知答案找元素」；**不覆盖同义改写**
  （`yes` vs `Yes, I do`）和需要业务判断的选项。
- 没覆盖 shadow DOM、iframe、弹窗遮挡、滚动/折线；resolver 对 `fields`
  也没有 `occluded_by`（真实 dump 的 `fields[]` 没有该键，46/46）。
- 只有 3 个站，wildnutrition 占 40/46 份 dump，不能代表全部站点。
- `_driver_ok` 与生产按 `cdp returncode` 判成败的差异，要等 Phase 2
  适配层才会碰到，这次没验。

## 复现

```bash
python3 -m pytest tests/test_browser_runtime.py tests/test_intent_runtime_replay.py -q

python3 -m experiments.intent_runtime.replay \
  --root docs/probes \
  --json docs/probes/2026-10-21-intent-runtime-replay/replay.json
```

- 回放器：`experiments/intent_runtime/replay.py`
- 测试：`tests/test_intent_runtime_replay.py`（10 passed）+
  `tests/test_browser_runtime.py`（10 passed）
- 原始结果：`replay.json`（165 条 outcome，含 status/score/picked/expected）

## 下一步建议

1. Phase 2 适配层把 `cdp click/form` 的 returncode 显式转成
   `{"ok": rc == 0}`，并补一个「disabled 元素不可点」的契约测试。
2. 换 2-3 个已交付的 FMR 站（rebates/ruvira/belong）再抓一轮 dump，
   把 wildnutrition 的占比压下来；同时补 `aria-label` 的 observe 输出，
   登录页那类「分不清三方登录」的问题才有解。
3. 把 `choose` 的答案从「DOM 原标签」扩到同义改写，否则 149/149 的命中率
   高估了真实流程的难度。
