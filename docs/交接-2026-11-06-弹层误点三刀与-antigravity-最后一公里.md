# 交接 · 2026-11-06：弹层误点三刀 + antigravity 最后一公里

> 给下一个窗口：先读这一份，不用翻上一段长对话。
> 工作区**本来就有一批未提交改动**（`git status` 会看到很多文件）——**不要** stash / checkout / 回滚。
> 本次会话的追加改动全在 `agent/lint.py`、`agent/template.py`、三个测试文件（见 §2）。

---

## 1. 一句话结论

- **lint_cap 环**已解开（`scroll` 辅助步不判地址）+ **三类弹层误点**已修（同意词锚定、隐藏副本弹层、弹层辅助步不重定位）+ **下拉开着不清弹层**。
- 8100 跑了三趟完整流程验证（`job-82c14d34db71` → `job-669c375b51f9` → `job-0ecaf3b90a6a`），三趟都没有交付脚本；卡点每趟往后挪了一格，现在停在**促销弹层**上（§4）。
- 8100 已重启、带全部改动；**8099 只带 lint 那一刀，`agent/template.py` 的全部改动都没进**（8099 最后一次重启在模板改动之前）——要用 8099 先 `docker restart siteforge`。

---

## 2. 本次改了什么

### 2.1 `agent/lint.py`（契约检查）

- 新增 `AUXILIARY_ACTIONS = ("scroll",)`（~L1014），并在三处跳过它：
  `_rule_i`（~L1063）、`no_basis_slots`（~L1824）、`check_unverified`（~L1752）。
- 理由：重放侧对 `scroll` 的失败**从不打回整组**（`template.py`「滚动是准备动作」那一段），
  所以它的地址不承重；原先 `judge-fragile-no-basis` 把它当承重步判，和模型死磕 3 轮 →
  真事 `job-044a12bb4ab0`（antigravity）`lint_cap`。

### 2.2 `agent/template.py`（骨架，随 `render` 进每一份新产物）

全部在 `_POPUP_MACHINERY_SRC` / `SKELETON` 两段字符串里，改的是**产物代码**，不是渲染器本身：

1. **`_MODAL_HOLDS_TARGET_BODY` 加「下拉开着」判据**（~L771-783）
   - 页面上只要有可见的 `[role=combobox][aria-expanded=true]` ⇒ 答 `1|open-dropdown`。
   - 放在 `!want&&!sels.length` 那道闸**之前**（这一步有没有目标都一样）。
   - 同段还修了一个真 bug：`t` 过 `norm`（压空白+casefold），`want` 没过 ⇒
     `t===want` 拿 `checkout+` 比 `CHECKOUT+`，**`1|text` 那一支从来没响过**；改成 `t===norm(want)`。
2. **`_modal_holds_target` 返回原因**（~L3743）：`""` / `text` / `selector` / `open-dropdown`；
   `_clear_obstructions`（~L3828）按原因说话：下拉开着 ⇒ **不清弹层**（那一下等于点页面别处，会把下拉点没）。
3. **`_is_overlay_step`**（~L656-682）：名字/地址里**同时**有「弹层词」（modal/dialog/popup/overlay/
   consent/cookie/gdpr/privacy）与「关闭/同意词」（close/dismiss/accept/agree/…）才算。
   - `_perform`（~L4538）里：这类步骤声明地址全失效 ⇒ **不按意图重定位**、直接软跳过；
     重定位会按 aria-label 找到页面别处同名的一颗（真事：`close modal button` → 导航里另一颗 → 点开移动端菜单）。
   - `_consent_step_handled(label, frame, step)`（~L3979）扩展为「同意步 or 弹层步」都软跳过。
4. **`_VIS_JS`（~L553-570）加两条**：`pointer-events:none` 与 `opacity<0.05` 都算**看不见**。
   - 真事：页面上叠着三份满屏 `div.Modal_modal__Abm7m`，两份 `opacity:0 + pointer-events:none`，
     关闭键的 `aria-label` 与真那颗一字不差 ⇒ 旧判据先挑中隐藏那颗 ⇒ `cdp click` 在落点取不到节点
     （拒绝点击）⇒ 真弹窗一直关不掉。
5. **`_MARK_CONSENT_BY_TEXT_JS`（~L1120）**：拼上 `_VIS_JS`，循环里 `if(!vis(b))continue;`
   —— 同名隐藏那些颗不许被标记（否则 `[data-sf-consent=1]` 点下去是空气）。
6. **`_MODAL_BOX_JS`（~L775）**：`if(!vis(mb))continue;` —— 隐藏的模态容器整份跳过。
7. **同意词锚定开头**（`_CONSENT_MODAL_JS` 的 `decW` ~L891、`_CONSENT_HOOK_JS` 的 `btnW` ~L966）：
   `/^\s*(?:yes,?\s+)?(?:i\s+)?(reject|…)/i`。
   - 真事：子串 `agree` 命中页脚 `USER AGREEMENT` 链接 ⇒ 被当同意键点 ⇒ 页面跳到用户协议页。
   - "Accept all" / "I agree" / "同意" 照过；"USER AGREEMENT" / "PRIVACY POLICY" 不过。

### 2.3 测试

- `tests/test_lint.py`：新增 `test_an_auxiliary_scroll_is_not_judged_by_its_address`、
  `test_the_auxiliary_exemption_is_only_for_action_scroll`（control：同址 click 照报）。
- `tests/popup_probe_driver.js` + `tests/test_popup_hooks_js.py`：
  - 探针从 27 格扩到 **30 格**（`USER AGREEMENT` × 2、隐藏副本模态 × 1）；
  - `_drive(..., holds_js=)` 新参数 + `--holds`：把 `_modal_holds_target_js` 放进假 DOM **真跑**
    （下拉开 / 没开 / 目标在模态里）；
  - 标记那一格加了「隐藏的同名控件在前」——必须标记到可见那颗（`marker.hidden is None`）。
- `tests/test_template.py`：新增
  `test_an_open_dropdown_keeps_the_modal_sweep_off`、
  `test_an_overlay_close_step_is_never_relocated`。

---

## 3. 验证证据

- `.venv/bin/python -m pytest tests/test_lint.py tests/test_popup_hooks_js.py -q` → **140 passed**。
- `tests/test_popup_hooks_js.py` 单独 → 7 passed（30 格，含正控）。
- `tests/test_template.py -k "overlay_close or open_dropdown or holds_the_step or consent"` → 全过。
- 真产物对照（用 `job-044a12bb4ab0` 冻结的 `observed`）：
  `lint.check(antigravity.parked.py)` → **0 violations / 0 slots / 0 unverified**；
  同一根地址改成 `click` → 1433 行照报 `judge-fragile-no-basis`（豁免只对 scroll）。

---

## 4. 当前卡点（8100 最新一趟 `job-0ecaf3b90a6a`，`end_reason=repair_stuck`）

trace（`runtime-222/selftest/antigravity-20261106-073335/baseline/*.trace.jsonl`）原话：

```
第 7 步 popup: 「盖住整页的模态」里有这一步要点的「name」—— 不当遮挡点掉
第 8 步 popup: 「盖住整页的模态」**刚才那一步就在里面做**（已经标成流程本身）—— 不当遮挡点掉
第 8 步 click「没写名字的元素」:
        div.Content_promo__hcTsr 一直盖着「没写名字的元素」……这一下没敢点
第 9 步 click「Apply now」: h2.Content_title__R4iA6 一直盖着「Apply now」
第 10 步 form「name」: 没找到……  连着 3 步没做成，收摊
```

两条：

1. **促销弹层被当成「流程模态」**：`_modal_holds_target` 的「刚才那一步就在里面做」标记
   （产物容器上的 `data-sf-flow=1`，`template.py:3938/3944`）把 promo 弹层也标成了流程 ⇒
   不清它 ⇒ `Content_promo` 盖着下一个目标。
2. **控件自己的 label 盖自己**：v1（同一趟的第一步）挂在第 9 步
   `div.RCSelect_v2_label__Qdn2I` 盖着 `#rc_select_3` —— 那个 label 就是下拉自己的可见壳。
   `_cover_is_own_widget`（`template.py:4227`）只放 `form + kind=select`，**click 没有这道豁免**。

同日报废的中间卡点（已解，供复核）：
- `job-82c14d34db71`：同意钩子点 `USER AGREEMENT` → 跳到用户协议页（同意词锚定后消失）。
- `job-669c375b51f9`：`lint_cap` —— 改稿把量到的 About 选择器换成自编的 `button.Btn_btn__qQnlD`；
  lint 卡上其实有量到的那根（`#page > footer … button:nth-of-type(1)`），模型三版都不照用。

---

## 5. 下一步（按优先级）

1. **给「流程模态」标记加身份**（`template.py:3938/3944`）：只有**上一步目标所在的那个模态**算流程；
   promo 类（`Content_promo` / `ServicePopWindow`）照旧按遮挡清掉。
2. **`_cover_is_own_widget` 扩到 click**（`template.py:4227`）：目标是 `rc-select` 的隐藏 input、
   盖着它的是同一格自己的 label（class 带 select/dropdown）⇒ 允许点。
3. （可选，治 `job-669c375b51f9` 那条）**机械地用卡上那根地址**：在 `agent/fix.py:1450
   anchors_from_observed` 里，若这一版候选**一条都不是量到的**而卡上有量到的候选 ⇒ 换成卡上的。
   等价于把 lint 那句「先把卡上那根拿来用」由系统执行，而不是指望模型听话；不放宽 lint。
4. 三条都改完，再在 8100 跑一趟完整流程（§6）才算验过。

---

## 6. 部署与复跑（这次实际用的）

- 两个部署：`siteforge`（8099，挂 `runtime/`）、`siteforge-222`（8100，挂 `runtime-222/`）。
  **改了 `agent/template.py` 必须重启容器**（服务启动时 import，热更不生效）。
- 开一趟（8100）：

```bash
curl -s -XPOST http://127.0.0.1:8100/run -H 'Content-Type: application/json' -d '{
 "url":"https://www.antigravity.tech/us/drone/antigravity-a1",
 "goal":"1.浏览到底部   2.点ABOUT ANTIGRAVITY  3.点Creator Recruitment  4.点APPLY NOW  5.填表单",
 "mode":"build","success_text":"APPLY SUCCESSFUL","env":{"proxy_country":"US"},"site":"antigravity"}'
```

- 闸口要人按「继续」：轮询 `GET /job/<id>/live`，`status=waiting` 时
  `POST /job/<id>/reply {"action":"continue"}`。本次三轮就是这样驱动的。
- **不要**手开 Bit 窗口做验证：`/run` 会自己开干净窗口；197 那台 worker 当时在跑真实广告任务
  （targets 里是别的站），手开/关窗口会干扰它。8100 走的是 222。
- 真自测 = 往站方**真提交一次**，别随手复跑。

---

## 7. 与本刀无关的既有失败（别追）

`tests/test_template.py` 全量（>20 分钟）里有两条**工作区里在改的那部分**留下的红：

- `test_actions_go_through_cdp`：产物里 `document.querySelectorAll` 命中了
  `document.querySelector` 的禁用子串（landing 那段 JS）。
- `test_legacy_cdp_gaps_are_said_in_human_words`：trace 现在 4 行，测试钉 3 行。

跑这一份建议用 `-k` 子集，别整份跑。

---

## 8. 现场留下的东西

- `forms/sites/antigravity.parked.py` **没动过**（还被 lint 打回的那一版；本次的 lint 豁免让它现在能过）。
- 会话里临时渲染的 `antigravity.try.py`、`runtime/_try_selftest.py`、`runtime/empty-form.json` 已删除。
- 8100 上三趟 job 都在：`job-82c14d34db71` / `job-669c375b51f9` / `job-0ecaf3b90a6a`；
  对应的自测证据目录在 `runtime-222/selftest/antigravity-*`。
