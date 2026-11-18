# southernliving.com「页面是好的却没成功」—— 2026-10-24

用户：「www.southernliving.com 需要修复下」，站点键
`www.southernliving.com/featured/original-plus/where-design-meets-real-life-ge`
（FMR 清单里那条；失败记录里它是 `www.southernliving.com_featured_original-plus_where-design-m`）。

任务：`26789988`（2026-10-24 05:38 CST，US，`type=site_specific`，`exit=no_success`）。

## 结论（8100 真站复现坐实）

**脚本比页面先到。** 广告落地页在**冷缓存**下先给一个空壳（`document.title` 是
`Simple Page`、0 个可见链接），随后可能再过一道 Cloudflare 的「Just a moment...」，
真页面 **15~20 秒**后才出来。生产重跑**不 observe**，回退链救不了「点得太早」：
第 1 步的选择器在空壳上必然找不到 ⇒ 整趟 `no_success`。

而 30 秒后 ad-task 抓的现场截图里，页面是**好的** —— 照片里 SUBSCRIBE 就在页头
（`prod_fail.png`，来自 FMR `failDiag.fail_img`：
`https://fmr.3tkj.cn/oss/farmer/screenshot/fail/2026/10/24/6adbd3f74e684.png`）。

生产日志原话（`failDiag` 那一行 `lines`）：

```
21:38:27 第 1 个选择器没成：#utility-nav_1-0 > ul:nth-of-type(1) > li:nth-of-type(5) > a:nth-of-type(1)
21:38:29 第 1 步：页面上没找到「Subscribe」，这一步没做成
21:38:29 第 1 次重试「security_for_your_style_adt」这一组（上限 1 次）…
21:38:30 第 1 个选择器没成：…
21:38:31 第 2 步：重试…；页面上没找到「Subscribe」，这一步没做成
21:38:32 还没见到成功文案 —— 再等最多 30 秒…
21:39:02 **没走到成功**：…
```

**不是** Cloudflare 把人拦在外面（用户看线上截图也是这个判断）。8100 上直接打这条
广告 URL 有时会先撞到 Cloudflare 的「Verify you are human」（那是 8100 出口 IP 脏，
与这一单无关）；用户手动点过之后，同一份脚本原样跑就是成功的。

## 复现（8100 的 Bit，192.168.1.222:61129）

`cold_nav.py`：清浏览器缓存 → 开 `about:blank` → 导航到失败记录里那条完整广告 URL
（带 `tblci` / `response.session`），**立刻返回**。然后以**生产模式**（不给 `--trace`）
跑 `forms-222/sites/southernliving.py`：

```
01:52:35 开始：1 步；生产重跑（不截图、不 observe、不落 trace）
01:52:37 第 1 个选择器没成：…
01:52:39 第 1 步：页面上没找到「Subscribe」，这一步没做成
01:53:05 还没见到成功文案 —— 再等最多 30 秒…
01:53:36 **没走到成功**…
```

与生产那单逐行同形。冷缓存下页头的出现时间（`timeline_probe.py`，从新文档提交起算）：

```
0ms      readyState=loading  title="Simple Page"               可见链接 0
4251ms   readyState=complete title="Just a moment..."（CF）     可见链接 0
18254ms  readyState=loading  title="Where Design Meets Real Life"
18387ms  readyState=loading  选择器命中                          可见链接 32
```

## 修法（骨架 + 产物）

判据：**开跑前先等这一页真的出来** —— 三条同时成立才算：

1. `readyState === 'complete'`；
2. 标题不是验证/加载页那一族（`Just a moment...` / `Simple Page` / `Attention Required`…）；
3. 页面上有真东西（可见链接 / 表单控件 / 正文 ≥ 20 字）。

- `agent/template.py`：新增常量 `LANDING_WAIT_SECONDS = 60.0` / `LANDING_POLL_SECONDS = 0.5`、
  模块函数 `_landing_state_say()`、`Filler._LANDING_STATE_JS` + `_wait_landing_ready()`；
  `run()` 开跑前调用一次，`goto` 之后也调用一次。
- `forms-222/sites/southernliving.py`：同一处补丁（产物是 09-28 那版骨架渲的，没有整体重渲）。
- 读不出来（cdp 老 / eval 炸 / 答复不是 JSON）⇒ **不等**、照旧往下走：量不到不许变成新的
  失败点，也不许白等一分钟；等超了也只说一句再往下走，这一笔不把整趟判死。

⚠️ 第一版探针踩的坑（已修，留作教训）：`_ev()` 会把 JS 包进 `(function(){…})()`，
探针必须是**函数体**、带 `return`；写成自执行的 `(function sfLanding(){…})()` 时
外层函数没有 return ⇒ 回 `undefined` ⇒ 这一笔**静默不生效**（真站上量到的原话：
空白页上照样立刻开点）。现在 JS 开头留 `/* sfLanding */` 给测试替身认人。

## 验过

`forms-222` 里的产物，生产模式（不给 `--trace`），冷缓存 + 立刻开跑：

```
02:03:42 开始：1 步；生产重跑（不截图、不 observe、不落 trace）
02:03:45 落地页等了 2 秒才真的出来 —— 继续
02:03:46 第 1 步：点了「Subscribe」
02:03:49 等到了：成功判据成立了
EXIT=0
```

空白页上（`about:blank`）等的那一笔也验过：

```
02:03:17 落地页还没出来（页面上还没有能点的东西（可见链接 0 / 控件 0 / 正文 0 字））—— 等它，最多 60 秒
```

测试：

- `tests/test_template.py::test_a_landing_shell_is_waited_out_before_the_first_click`（新，先红后绿）
- `tests/test_template.py -k landing`：2 passed
- 整套 `tests/test_template.py` + `tests/test_lint.py` + `tests/test_loop_template.py`：
  **333 passed / 3 failed**（36 分 22 秒）。
  - `test_reference_fixture_is_current` —— 改过骨架就该重渲，已按用例提示
    `python3 tests/test_template.py` 重生 `fixtures/reference_site.py`，单跑已绿；
  - `test_actions_go_through_cdp`、`test_legacy_cdp_gaps_are_said_in_human_words` ——
    `git archive HEAD` 摊出来的**净树**上同样两条红（1.02 秒跑完），与这次改动无关。
- 落地页那一刀的真站端到端：见上面「验过」。

## FMR（2026-10-24 10:08 已推 + 已启用）

用户 10:06：「两个都推」。

`push_fmr.py --apply`（本目录），`where-design` 那一条：

```
LOCAL forms-222/sites/southernliving.py bytes 166203 sha256 2d981b0a394c1128…
BEFORE site=…/where-design-meets-real-life-ge type=py sha256=1c53c291… status=0
WRITE {"ok": true, "verdict": "ok", "status": 200, "msg": "success", …}
AFTER  site=…/where-design-meets-real-life-ge type=py sha256=2d981b0a… status=0
AFTER_SHA_LOCAL_NORM 2d981b0a… equals_local: True
```

⚠️ 第二条 `--key …/security-for-your-style-adt` **被写前闸挡下**（`matches_expect: False`）——
一读才发现**两个键共用同一条脚本行**（写第一个时已经把第二个也换掉了）：回读两边都是
`sha256 2d981b0a…`、`norm == local`。所以「两个都推」实际是**一次写入覆盖两条键**。

⚠️ **上传 ≠ 启用**（`status` 仍是 0）。这次查了后端路由：
`formScript/update` 那一支**不认** `status` 字段（本地 farmer 的
`QuestDiagnosisController@formScriptUpdate` 里没有它），启停是另一个口
`POST /api/quest/formConfig/status`（写 token，`site` + `status`）——
它对 `form_configs.status` 落值，一次改的是整条 config（这里 `config_id 120`）。

```
POST formConfig/status {"site": …/where-design…, "status": 1, "operator": "claude (用户 2026-10-24：两个都推)", …}
→ {"status":200,"msg":"success","data":[]}
```

回读（两边都走**正常读**，不再用 `allow_disabled`）：

```
NORMAL_READ OK …/where-design-meets-real-life-ge  sha 2d981b0a…  norm==local True
NORMAL_READ OK …/security-for-your-style-adt      sha 2d981b0a…  norm==local True
form_config_sites: 两条 status=1（config_id 120）
```
