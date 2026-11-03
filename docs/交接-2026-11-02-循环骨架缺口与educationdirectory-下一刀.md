# 交接（2026-11-02）：educationdirectory 已交付，循环骨架剩下的缺口

> 给下一个 agent：先读完这一页再动手。**上下文已经很长，这里就是压缩后的现状 + 待办**。
> ⚠️ 工作区本来就有大量未提交改动（`git status` 里 40+ 文件）。本刀只动了：
> `agent/template_loop.py`、`tests/test_loop_template.py`。**没有 commit，也没有回滚别人的改动。**

---

## 一、现在的确定状态（别重新验一遍）

**已交付（8100 / `siteforge-222`）：**

| 项 | 值 |
|---|---|
| job | `job-1d4fbff412cc`，`end_reason=delivered`，`delivered=true` |
| 产物 | `/opt/siteforge/forms/sites/educationdirectory.py`（宿主 `forms-222/sites/educationdirectory.py`） |
| sha256 | `e8e3824a7cfc3a924e112833ca1dee767ba86d5af5756b49a738a126b9814d7f` |
| PROVENANCE | `selftest.passed=true`、`runs=1`、`submissions=1`、`at=2026-11-02T06:39:03Z` |
| 自测 trace | `runtime-222/selftest/educationdirectory-20261102-063713/baseline/educationdirectory.baseline.trace.jsonl` |
| 这次自测的输入 | `form_data={"phone":"3125550142"}`（**只在自测时给**；产物运行时仍按 `FILLS.phone.source="phone"` 取任务资料） |

trace 实况：答 6 题（含 `rn_license` / `teaching_certificate` 两组追加题）→ 填 school 3 格 → 填 contact 5 格（phone 走「任务资料」）→ 点 `Agree & See My Schools` → 点站点弹出的 `Use ZIP 10118` → Next → **success**。

**服务：**

- `siteforge`（8099）与 `siteforge-222`（8100）都已 `docker restart`，两边 `/opt/siteforge/agent/template_loop.py` sha 相同（`56eabeac…` 之后又改过，以当前文件为准）。
- 单测：`python3 -m pytest tests/test_loop_template.py -q` ⇒ **74 passed**。

**8099 尚未交付新产物**：`forms/sites/educationdirectory.py` 还是 13:50 那趟失败的未验版本；`forms/sites/educationdirectory.handoff.md` 也是旧失败单。8100 的 `forms-222/sites/educationdirectory.handoff.md` 同样是上一趟失败的残留，**别拿它当现状**。

---

## 二、这一刀改了什么（都带回归，别再重写）

文件：`agent/template_loop.py`（渲染循环骨架的那份）。

1. **同屏多组题**
   - `DEFAULT_READ_JS` 多交一格 `unanswered`（还没选过的组）。
   - 包在 `label` 里的 radio/checkbox 交 `\x05文字\x06name\x07value` token（`BY_FIELD`）。
   - `pick_option` 认 `BY_FIELD` 的文字段；`click` 新增 `BY_FIELD` 分支（按 name/value 找 input → 点它的 label）。
   - 主循环 `answered_sig` 带上 `unanswered`，一组答完签名才变；只从 `unanswered` 挑。
   - 回归：`test_a_screen_that_adds_required_follow_ups_is_answered_group_by_group`
2. **`\x04` = id 或 name**
   - `fill()` 的 `BY_ID` 分支：先 `[id="…"]`，`element not found` 再 `[name="…"]`。
   - 回归：`test_an_input_that_only_has_a_name_is_filled_by_name`
3. **原生 `<select>`**
   - `_is_select(sel)` 检出；`_fill_and_check` 走 `cdp form --select`，且不做回读比较（select 的 value ≠ 显示文字）。
   - 没值可填时 `value_for` 返回 `SELECT_ANY`；`_any_select_option(sel)` 从页面上挑第一个可选项。
   - 回归：`test_a_native_select_is_filled_with_select_not_value`、`test_a_select_with_no_value_picks_a_real_option_from_the_page`
4. **同意层误判**
   - 读取器 `consent` 只看 id / class / aria-label，且必须 `position: fixed|sticky` 或 `role=dialog`。
   - 回归：`test_the_consent_hint_reads_identity_not_footer_text`（页脚 / 内联 `.lp-consent` / fixed 真弹层三例）
5. **提交按钮**
   - `<button type="submit">` 直接算 `nextVisible`（原来 `Agree & See My Schools` 既不匹配 Next 关键词，又被同意层排除规则扔掉）。
   - 回归：`test_an_explicit_submit_button_is_the_next_action`

**故意没改的**：电话取值分支。试过「账本 `EXAMPLES`/`sample` 优先于池子」，会把
`test_a_phone_fill_takes_the_profile_unless_the_script_pins_an_area_code` 打红（2026-10-08 的
「没资料、没前缀 ⇒ 池子随机」口径）。已回滚，见下面 P0-2。

---

## 三、还没补上的缺口（按优先级）

### P0-1 生成侧 `field-N` 命名（真缺口，源码在 `agent/browser_agent.py`）—— ✅ 2026-11-02 已补

现场：同一站不同趟探路，FILLS 键有时是 `field-4 / field-5 / field-8`，而 `target.selectors`
里明明有 `input[name="zip_code"]` / `select[name="education_level"]`。

后果：`source=field-4` 取不到生产资料；现在靠 `value_for` 的 `sample` 兜底和「下拉挑第一个」
让产物能跑，但它**回放的是探路那个值**，不是这一单的数据。

历史单据：`docs/缺陷单-2026-10-12-取值名退成field-N-尾段对不上.md`（`_selector_tail` 尾段
唯一才认的那一版）。这次说明它**还有邻居没盖住**：
观测给的是带 `label` 的池子，模型/探路给的是 `[name=…]` 或位置链，仍会退成 `field-N`。

要求：
- 在 `_find_element` / 起名那一段补一档：**选择器里的 `[name=…]` / `[id=…]` 可作身份**
  （但只在这两个属性在观测池里唯一时认；不唯一 ⇒ 不认，别把两格认成一格）。
- 验收：真形状造两格，一格 `input[name="zip_code"]`、一格 `select[name="education_level"]`，
  探路写 `[name=…]` / 位置链两种写法都得到语义名；`FILLS[*].source` 能落回资料列名。
- 回归：`tests/test_browser_agent.py` 现有尾段用例旁边加新用例；不要放松「唯一才认」。

**已做**：`_find_element` 新增「`[name=…]` / `[id=…]` 在池子里唯一」一档（rank 4/5，含 `#id`
写法；不唯一 ⇒ 不认）；`_fill_name` 在语义种类认不出时用像资料列名的 `hint`
（`education_level`）当名字，不透明号照旧不许。回归：
`test_a_unique_name_or_id_attribute_in_the_selector_is_identity`、
`test_a_semantic_name_attribute_becomes_the_source`、
`test_a_positional_selector_still_gets_the_semantic_name`。
**追加（2026-11-02 自测逼出来的）**：同一地址在不同状态各记一次时，`field` 会抢在语义名
前面（`value_for` 按 FILLS 顺序认第一格）—— `Journey.fills()` 现在把 `field` / `field-N`
排到语义名后面；回归 `test_a_named_fill_is_tried_before_an_unnamed_duplicate`。
`tests/test_browser_agent.py` ⇒ 271 passed, 1 skipped。

### P0-2 电话号的国家形状（`agent/template_loop.py::_pool` / `value_for`）—— ✅ 2026-11-02 已补

现场：`job-2fd0b26d40f2` 第 10 步填了 `01148106686`（池子的英国 11 位），US 站压成
`(011) 481-0668` ⇒ 回读少一位 ⇒ 重填两遍判「没填上」。这趟是靠 `form_data.phone=3125550142`
绕过的。

要求（两条老口径不许破）：
- 2026-10-22：资料给了电话先用资料；`prefix` / `from_profile:false` = 点名现编。
- 2026-10-08：没资料、没前缀 ⇒ 池子随机，**不**回放账本同一个号。
- 新口径：站点国家/号码形状要参与决定（来源可以是站点国家、ZIP、或运营 `rules.phone_prefixes`）。
- 验收：US 站、任务资料没电话 ⇒ 10 位 US 形状；UK 站照旧；pinned 用例逐字不变。
- 回归：扩 `test_a_phone_fill_takes_the_profile_unless_the_script_pins_an_area_code`。

**已做**：`_pool` 先取站点国家（`PROVENANCE["env"]["proxy_country"]`，没有就看资料里的五位
ZIP）；US/CA ⇒ 10 位 NANP `NPA-555-01xx`，AU ⇒ `04…` 10 位，认不出/GB ⇒ 原英国 11 位座机；
`prefix` 仍压过一切。回归已在既有那条用例里扩了 US + ZIP 两段。
`tests/test_loop_template.py` ⇒ 76 passed。
**追加（8099 `job-81033dc64c8d` 那趟）**：`PROVENANCE["env"]` 是 `None` 时，国家还能从
**页面自己**读——phone 的 placeholder `(555) 555-5555`（NANP）或账本里那个真号的形状；
回归 `test_a_us_phone_placeholder_decides_the_country_without_the_proxy`。

### P0-3 交付 ≠ 生产能跑（C15 路由表）

交付那句自己写着：「这份 py 还不能被生产调起来，要在路由表里登记（C15，计划四）」。
仓里没有这张表（`agent/graph.py` 只提到计划）。下一步要确认后端/计划四那边登记
`educationdirectory`，否则生产 worker 不会调它。这件事**不在本仓**，别在 `agent/` 里找。

**2026-11-02 核实**：路由表在 `/opt/skills/auto-farm-skill/scripts/ad-task.py::SITE_ROUTES`，
当前**没有** `educationdirectory.net` 条目；`/opt/skills/auto-farm-skill/forms/sites/`
也**没有**那份 py。要动生产仓 + 部署，等用户拍板（与 P1-2 同一件事）。
**2026-11-02 用户口径**：路由表那一套「好像已经退了」⇒ 本刀**不动**生产仓、
不改 `SITE_ROUTES`、不部署；等确认后再另起。

### P0-4 只验了基线一遍

这趟 `allow_skips=["rerun","delay","viewport","country"]`，另外 4 类扰动没验。
稳定之后按默认（只跳 country）跑全 5 遍；第 4 遍要 `set_viewport=true`（窗口层在 8100/8099 都配了）。

**2026-11-02 核实**：`selftest.STABILITY_RUNS = 1`（2026-09-24 用户裁定「先只跑基线」），
所以现在**跑不了 5 遍**；要恢复得先改这一格（那四遍的代码/判据没删）。
另有 R-84：**基线一遍过了就不再往下跑**（`rerun` / `delay` / `viewport` 记 `not_needed`），

**2026-11-02 实跑（临时放开 RUN_ONLY / R-84 / MAX_SUBMISSIONS，用户点头）**：
`job-b6de06891f9a`（8100）跑到 `repair_stuck`；自测 3 遍 **baseline / rerun / delay 全挂**
在同一格：`zip_code` 回读是 `455 N`，不是填进去的 `455 N Rexford Dr`。
根因：同一地址在不同状态各记了一次（`field` 与 `postcode` 都指 `input[name="zip_code"]`），
`value_for` 按 FILLS 顺序认第一格 ⇒ `field` 用了 `EXAMPLES["field"]`（街道样例）。
**修完后的复验**：8099 `job-81033dc64c8d` 的 baseline **passed**（1 次提交），产物已交付。
`rerun` / `delay` 没在修完后重跑；`viewport` 仍被窗口层限制（要关窗重开流程）；
`country` 按现行裁定不跑。所以 P0-4 只到「基线在修完后过了」，4 类扰动仍未验全。
已修：`Journey.fills()` 把 `field` / `field-N` 排到语义名后面；回归
`test_a_named_fill_is_tried_before_an_unnamed_duplicate`。
第 4 遍 viewport **没跑成**：Bit 的窗口尺寸启动时生效，写配置后活窗口仍是 984x673；
要真跑得「关窗 → 改尺寸 → 重开 → 换 ws_url」，现在五遍共用一个窗口，接口不支持。
第 5 遍 country 照旧没跑。临时改的三个常量已恢复（`RUN_ONLY=("baseline",)`、
`MAX_SUBMISSIONS=3`、R-84 的「过了就不再跑」）。

### P1-1 自修复改不了骨架（架构缺口）—— ✅ 2026-11-02 落了「跨 job 指纹」那一半

`diagnose → draft` 只改旅程表（`graph._draft` 的 `deps.write`），改不到
`template_loop.py` 的交互判据 ⇒ 这次三趟全停在 `repair_noop`。

建议（要用户拍板）：
- 同一失败指纹跨 job 攒到 3 份 ⇒ 开「工具层缺陷单」（`docs/根因库.md` 已有这条规矩），
  不要再往模型头上加轮次；
- 值班自动修（`runtime/auto/flags.json`）当前 `{"on": false}`，心跳停在 2026-10-30。
  这一族缺陷它救不了，开不开要先说清。

**已做**：`_handoff` 会在交接包里记这份失败指纹（`<out_dir>/.repair-fingerprints.jsonl`）；
跨 job 第 2 次点名次数，第 3 次直接写「该开**工具层缺陷单**，别再往模型头上加轮次」。
回归：`test_the_same_failure_fingerprint_three_times_asks_for_a_tool_defect`
（`tests/test_graph.py` ⇒ 165 passed）。
**没动**：值班自动修开关（仍 `{"on": false}`）、心跳 —— 那是运维决定，不跟这刀一起改。

### P1-2 8099 的产物 —— ✅ 2026-11-02 已交付

二选一（问用户）：
- 让同事在 8099 新开一趟（推荐：PROVENANCE 对得上 8099 的运行环境）；
- 或把 8100 验过那份（sha `e8e3824a…`）拷到 `forms/sites/educationdirectory.py`，
  并清掉那边的旧 `handoff.md`（旧失败单会误导人）。

**2026-11-02 结果**：8099 新开一趟 `job-81033dc64c8d`，`end_reason=delivered`、
`selftest.passed=true`、`runs=1`、`submissions=1`；产物 sha256
`0e5ceb38a6086a946119a0af7215704435963817b5d6761f6f26322854d860d7`。
这份同时同步到 8100 的 `forms-222/sites/`（同一 sha）；两边的旧 `handoff.md` 已删。
过程中又逮到并修了两个真缺口：`field` 抢在 `postcode` 前面（FILLS 顺序）与
无 `PROVENANCE.env` 时电话退成英国号（用 phone placeholder / 账本号形状判国家）。

### P1-3 地址联想没有建模 —— ✅ 2026-11-02 已补

提交前站点弹了 `Use ZIP 10118`，这趟是通用「选项点击」碰巧点中的。
风险：联想列表可能被随机挑、或点了别的建议。真要治，给 `DEFAULT_READ_JS` 加
autocomplete 建议识别（`[role=listbox]` / `.pac-item` 一类），选与已填 ZIP 一致的那一条。

**已做**：`pick_option` 收页面当轮 `inputs`，先把**已填的五位 ZIP** 与建议文字对一遍
（`Use ZIP 10118` 这种）；对得上先选它，对不上照旧掷骰子。回归：
`test_an_autocomplete_suggestion_matching_the_filled_zip_wins`。

### P2 已知风险 / 纪律

- **不要拿旧 `ws_url` 直接 cdp 查页面**：窗口服务会回收。实测一次旧 ws_url 已经切到另一个站
  （法语问卷），探针在那页点了 2 个选项。要查就现读 `/job/{id}` 里的 `ws_url`，先核对 URL。
- 同意层提示现在是「宁漏不误标」：`clear_popups` 每次动手前仍会跑；但**静态的内联真同意条**
  不再由这一格提示。真遇到漏清的站，改 `clear_popups` 的形状判据，别把读取器那格放宽。
- 产物里嵌了 `sample/examples` 的值（含 `3125550142`）。生产任务没带 phone 时会走池子（见 P0-2）。

---

## 四、跑法与判据（复制用）

```bash
# 单测
cd /company/siteforge && python3 -m pytest tests/test_loop_template.py -q     # 74 passed

# 重启（代码是 bind mount，uvicorn 不带 reload ⇒ 必须重启容器）
docker restart siteforge siteforge-222
python3 - <<'PY'
import urllib.request, json
for p in (8099, 8100):
    print(p, json.load(urllib.request.urlopen(f'http://127.0.0.1:{p}/health'))['jobs'])
PY

# 开一趟（8100；验过的那组参数）
# POST http://127.0.0.1:8100/run
# {"url":"http://educationdirectory.net/",
#  "goal":"1.浏览到Find the program that fits your schedule 2.点Find Your Program 3.回答问题及填表单",
#  "mode":"build","site":"educationdirectory",
#  "success_text":"Thank you for your interest in furthering  your education.",
#  "allow_skips":["rerun","delay","viewport","country"],"auto":true,
#  "form_data":"{\"phone\":\"3125550142\"}"}
```

判据（trace 里逐条）：

1. 答 6 题，且 `rn_license` / `teaching_certificate` 各出现一次；
2. school 三格 `zip_code` / `education_level` / `high_school_graduation_year` 都 `form ok=True`；
3. contact 五格填上，`phone` 来源应是「任务资料」；
4. 点 `Agree & See My Schools` → 成功文案/URL。

trace 在 `runtime-222/selftest/<site>-*/baseline/*.trace.jsonl`（8099 是 `runtime/selftest/…`）。

---

## 五、能直接接手的文件索引

| 位置 | 作用 |
|---|---|
| `agent/template_loop.py` | 循环型骨架：`DEFAULT_READ_JS`、`BY_FIELD`、`SELECT_ANY`、`value_for`、`_is_select`、`_any_select_option`、`fill`、`_fill_and_check`、`pick_option`、`click` |
| `agent/browser_agent.py` | 探路/起名/`_find_element`、`_selector_tail`（P0-1） |
| `agent/graph.py` | `_same_as_the_tested_one` / `repair_noop` / `_what_the_artifact_reads`（P1-1） |
| `agent/auto.py` + `tools/auto-repair.py` | 值班自动修（开关、账本、心跳） |
| `tests/test_loop_template.py` | 本刀 6 条新回归 + 既有 68 条 |
| `docs/缺陷单-2026-10-12-取值名退成field-N-尾段对不上.md` | P0-1 的历史 |
| `docs/根因库.md` | 「同一根因第 3 份就治工具层」 |
| `runtime-222/selftest/educationdirectory-20261102-063713/` | 交付那趟的全程 trace |
