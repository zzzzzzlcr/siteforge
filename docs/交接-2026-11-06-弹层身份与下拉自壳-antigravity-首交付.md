# 交接 · 2026-11-06：弹层身份 + 下拉自壳 + antigravity 首次交付

> 给下一个窗口：先读这一份。
> 工作区**本来就有一批未提交改动**（`git status` 会看到很多文件）——**不要** stash / checkout / 回滚。
> 本次会话的追加改动全在 `agent/template.py`、三个测试文件（见 §2）。
> **8099 / 8100 都已重启，两边都带全部改动**（旧交接里「8099 只带 lint」已经不成立）。

---

## 1. 一句话结论

- **弹层身份**已落地：`_modal_holds_target` 只认探针**点名的那一个模态**，promo 不再拦自己的清扫；**下拉自壳**的 `click` 与 `form` 都已在真站验过。
- 8100 最终一趟 `job-44324ae63dc6`：自测原话 **「跑通了（退出码 0，trace 里没有没做成的步）」**，交付 `forms-222/sites/antigravity.py`。
- **已推 FMR**：key `www.antigravity.tech/us/drone/antigravity-a1`，374042 字节，sha `be666fb9265f3c74…`，回读一致，`config_status=1`（启用）。
- 前一版 `job-299e89b9c77f` 的 3 条瑕疵是 **form 自壳修改之前渲染的产物**；同一载荷重跑后归零。

---

## 2. 本次改了什么

### 2.1 `agent/template.py`（骨架，随 `render` 进每一份新产物）

都在 `_POPUP_MACHINERY_SRC` / `SKELETON` 里，改的是**产物代码**。

1. **`_MODAL_BOX_JS` 给被点名的模态打身份**（~L757-790）
   - 每次探针进来先清全页 `data-sf-hit`（~L761），答出去之前给命中的容器打 `data-sf-hit=1`（~L789）。
   - 答复三格形状不动（`"<钩子名>|<选择器|TEXT>|<按钮文字>"` 是两套消费端与假 DOM 夹具的契约）。
2. **`_MODAL_HOLDS_TARGET_BODY` 只认被点名那颗**（~L837-869）
   - 新增 `hit` 解析；`flow` 标记只对 `hit` 自身/祖先/后代有效；`text`/`selector` 只在 `hit` 子树里找。
   - 真事链：旧判据扫**全页所有模态** ⇒ promo 被点名要清，可「这一屏的目标」在**底下表单模态**里 ⇒ 判成「目标在里面」⇒ promo 永不清 ⇒ `Content_promo__hcTsr 一直盖着`。
3. **`_cover_is_own_widget` 放行下拉自壳**（`~L4241-4278`）
   - 保留 select2 那条（`form + kind=select` + id 互嵌）。
   - 新增：`form` / `click` 动作里，目标自己的 id 带 `select|dropdown`，盖着的东西 class 也带 `select|dropdown` ⇒ 放行。
   - 真事：`#rc_select_0` 被 `div.RCSelect_v2_label__Qdn2I` 盖着 —— click 与 form 都发不出去。
4. `_perform` 里「自己的控件」那句日志改成按动作说「照填 / 照点」（~L4582-4590）。

### 2.2 测试

- `tests/popup_probe_driver.js`（~L573-650）：holds 页**先跑探针再跑判据**（与产物同序）；新增 `promoPage`（promo 在前、目标与 flow 在后的表单模态），钉「身份对不上就不许拦」。
- `tests/test_popup_hooks_js.py`（~L150-186）：新增 `otherModal` 三断言（`probe` 是模态行 / `hit=1` / `got=""`）。
- `tests/test_template.py`（~L2803-2884）：新增
  `test_a_click_on_the_select_widgets_own_label_is_not_blocked`、
  `test_a_form_fill_on_the_select_widgets_hidden_input_is_not_blocked`，
  含三条负控（非 select 目标 / 非 select 壳 / 非人形动作）。

---

## 3. 验证证据

- `.venv/bin/python -m pytest tests/test_popup_hooks_js.py tests/test_lint.py -q` → **140 passed**。
- `.venv/bin/python -m pytest tests/test_template.py -q -k "select_widgets_own_label or select_widgets_hidden_input or select_covered_by_its_own_widget or covered_element_is_not_clicked"` → **4 passed**。
- 真站第一趟（8100，`job-299e89b9c77f`）：闸口全按 continue，终局 `status=done`、`delivered=true`；自测退出码 0，68 行 trace，`"ok": false` **3 行**；第 9 步 `click #rc_select_0` ok=true（click 自壳真站验证）；全程没有 `Content_promo 一直盖着`。
- 真站第二趟（同载荷，`job-44324ae63dc6`）：**自测退出码 0、trace 里 0 条 `ok:false`**，`delivered=true`。
  - trace：`runtime-222/selftest/antigravity-20261106-081453/baseline/antigravity.baseline.trace.jsonl`（70 行）。
  - `RCSelect_v2_label` 仍在 4 行的 `note` 里出现，原话：`点了「没写名字的元素」（按下之后 <div class="RCSelect_v2_label__Qdn2I"> 盖了上来，这一次抬手没有交给它）` —— 这是**落点守卫的正常记录**（步骤 `ok=true`，下一行选项照点），不是遮挡失败。
  - 交付文件：`forms-222/sites/antigravity.py`（`/opt/siteforge/forms/sites`）。

- **FMR 推送（`upload/prepare` → `upload/commit`，job-44324ae63dc6）**：
  - key：`www.antigravity.tech/us/drone/antigravity-a1`（后端原本不认识这个键 ⇒ 新建/写满）
  - 374042 字节 / sha `be666fb9265f3c74dd35e924522427904f948e235dad03ebfcef0fb868ea0fd9`
  - 回读逐字节一致；`config_status=1`、`enabled=true`
  - 写前备份：`py-2d6a36bd30969f08725ad9d9b11b6341`

---

## 4. 当前卡点

**本刀范围内：无。** 第二趟 trace 70 行、`"ok": false` **0 行**；旧 `job-299e89b9c77f` 的两条瑕疵（`form rc_select_0` 第 10/14 步、`click Apply now` 第 11 步）在新产物上都没有复现。

还没验的（不是失败，照实记）：

- `STABILITY_RUNS=1`（`agent/selftest.py:157`）：同环境复跑没量；要量就 `selftest.run(..., stability_runs=3)`，会**多真提交几次**。
- 工具侧自测 ≠ 生产（代理出口 / 指纹 / 时序不是一套，规格 §10）。
- FMR 已推且那一行**启用**（`config_status=1`）；还差**生产侧按这个键真跑一趟**才算最终验（工具侧自测 ≠ 生产）。

对照：旧 `job-0ecaf3b90a6a`（`runtime-222/selftest/antigravity-20261106-073335/`）第 8 步原话
`div.Content_promo__hcTsr 一直盖着「没写名字的元素」……这一下没敢点` —— 本刀后消失。

---

## 5. 下一步（按优先级）

1. （可选）**稳定性复跑**：`agent/selftest.py:157` 的 `STABILITY_RUNS` / `run(..., stability_runs=3)`。⚠️ 每多一遍就多一次真提交，先确认。
2. **生产侧真跑一趟**：FMR key 已是 `www.antigravity.tech/us/drone/antigravity-a1`，让生产按它下载并执行一次；这是「接口下载的就是这份」的最终证。
3. （可选，旧账）**机械用卡上地址**：`agent/fix.py:1450 anchors_from_observed`。本趟 lint 已过（没撞 `job-669c375b51f9` 的 `lint_cap`），优先级最低。

---

## 6. 部署与复跑（这次实际用的）

- 两个部署都重启过（`docker restart siteforge-222 siteforge`），两边 `/health` 均 `ok=true`。
- 复跑（8100）：

```bash
curl -s -XPOST http://127.0.0.1:8100/run -H 'Content-Type: application/json' -d '{
 "url":"https://www.antigravity.tech/us/drone/antigravity-a1",
 "goal":"1.浏览到底部   2.点ABOUT ANTIGRAVITY  3.点Creator Recruitment  4.点APPLY NOW  5.填表单",
 "mode":"build","success_text":"APPLY SUCCESSFUL","env":{"proxy_country":"US"},"site":"antigravity"}'
```

- 闸口：轮询 `GET /job/<id>/live`，`status=waiting` 时
  `POST /job/<id>/reply {"action":"continue"}`。本趟就是这样驱动的。
- **不要**手开 Bit 窗口验证；8100 走 222，197 那台可能在跑真实广告任务。
- **真自测 = 往站方真提交一次**，别随手复跑。

---

## 7. 与本刀无关的既有失败（别追）

`tests/test_template.py` 全量（>20 分钟）里仍有旧交接 §7 的两条红：

- `test_actions_go_through_cdp`：产物里 `document.querySelectorAll` 命中 `document.querySelector` 禁用子串。
- `test_legacy_cdp_gaps_are_said_in_human_words`：trace 现在 4 行，测试钉 3 行。

本刀用 `-k` 子集验，没有整份跑。

---

## 8. 现场留下的东西

- 8100 最终 job：`job-44324ae63dc6`（done、delivered、自测 0 失败）。
- 自测证据：`runtime-222/selftest/antigravity-20261106-081453/`（baseline trace 70 行）。
- 前一版 job：`job-299e89b9c77f`（交付过，带 3 条瑕疵；已被新产物覆盖）。
- 交付产物：`forms-222/sites/antigravity.py`（**不是**仓库里的 `forms/sites/`；`forms/sites/antigravity.parked.py` 没动过）。
- FMR：key `www.antigravity.tech/us/drone/antigravity-a1`，sha `be666fb9265f3c74…`，374042 B；备份 `py-2d6a36bd30969f08725ad9d9b11b6341`；`config_status=1`。
- 一页速查（形状 → 判据 → 代码位置 → 测试）：`docs/速查-弹层与遮挡判据-2026-11-06.md`。
- 旧 job 仍在：`job-82c14d34db71` / `job-669c375b51f9` / `job-0ecaf3b90a6a`。
- 工作区未提交改动**未动**，未 commit、未 push。
