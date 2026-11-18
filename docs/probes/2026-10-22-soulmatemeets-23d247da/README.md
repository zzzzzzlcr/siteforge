# soulmatemeets.com/land/sp/23d247da 属于哪一份脚本 —— 2026-10-22

用户：「soulmatemeets.com 是因为我脚本配错地方了，你帮我看看他是属于哪种下面的脚本我再去配置。」

## 结论

**它属于 `japansdates` 那一份**（后端 config **101** 用的就是 `forms/sites/japansdates.py`，
线上 sha `41c74af2…`、37536 B —— 就是 10-20 修过 cookie 弹层的那一版）。

配置时认这个键（与任务上报的完全一致，**不要**加尾斜杠/www）：

```
soulmatemeets.com/land/sp/23d247da
```

它应该和 `soulfuldate.com/land/sp/5a4f738d`、`soulmatemeets.com/land/sp/976b69dc` 在**同一组（101）**。

## 真站证据（8100 / Bit 192.168.1.222，干净窗口）

打开 `https://soulmatemeets.com/land/sp/23d247da` 后地址栏自动变成：

```
https://soulmatemeets.com/land/sp/23d247da/?id_visit_prev=…#c3RlcDE=
```

`c3RlcDE=` = base64 `step1` —— **japansdates 平台的状态签名**（soulfuldate 10-10 那次一模一样）。

### DOM 普查（`census.js`）

| 项 | 结果 |
|---|---|
| `data-testid` 总数 | **136** |
| 前缀分布 | `step-*` 64、`option-*` 37、`continue-*` 13、`legal-*` 5、`cookie-*` 4、`pref-*` 2、`gender-*` 2、`toggle-*`/`signup-*`/`firstname`/`email`/`password` 各 1 |
| 关键 id | `step-content-item-Basic/ChooseAge/ChooseGender/ChoosePrefGender/InputNameAutofill/Preferences`、`option-have-fun/…`、`pref-gender-male/female`、`gender-male/female`、`continue-button`、`legal-checkbox`、`cookie-popup-consent-accept-all` |
| **没有** | `age-18-24` 这类 `age-*`、`image-button-*` ⇒ **不是 goldenagesouls 那套** |
| input | 全是 0×0（`landing[gender]` / `firstname` …），与 japansdates 记的坑一致 |

### 产物本体 `japansdates.py` 的 `read()`（`read_probe.py`，只读）

```
step    = step-content-item-Basic+step-content-item-AgeLegalText+step-content-item-ChooseAge
labels  = [['Under 18 – Not eligible',''], ['18-24',''], ['25-34',''], … ['65+','']]
inputs  = []   nextVisible = false
```

### 产物本体驱动 4 步（`drive_probe.py`，从不调用 `run()`，**未提交任何东西**）

```
前1  hash=step1  年龄屏  -> click_text('45-54')
前2  hash=step1  （第一下没推进，重试）-> click_text('45-54')
前3  hash=step2  option-have-fun/… -> click_option('option-where-goes')
前4  hash=step3  pref-gender-male/female -> click_option('pref-gender-male')
```

即：**同一份 japansdates.py 在这个站上读得对、点得动、状态真往前走**
（年龄屏走 labels 文本兜底，其余屏走 `option-*` testid —— 与 10-20 soulfuldate 真跑一致）。

## 后端现状（为什么它现在没脚本）

`formConfig/sites` 里 soulmatemeets 只有两条，**都不是这个 landing id**：

| 站点键 | config | 跑的是什么 |
|---|---|---|
| `soulmatemeets.com/land/sp/976b69dc` | **101** | `japansdates.py`（`41c74af2…` / 37536 B） |
| `soulmatemeets.com/land/sp/e77eb1b0` | 66 | `goldenagesouls.py`（`b7e3fc04…` / 14846 B） |
| `soulmatemeets.com/land/sp/23d247da` | **没有** | `formScript` / `formConfig` 都 404（连 `/`、`www.` 变体也 404） |

⇒ 失败单上报的正是第三个键：后端不认识 ⇒ 回落到「没有配置」。
**它不该跟着 `e77eb1b0` 进 66**（goldenagesouls 认的是 `age-*` / `image-button-*`，这页一个都没有）。

## 另：本仓 `forms/sites/soulmatemeets.py` 不是这一份

那是 10-20 由 siteforge **按人话 build** 出来的稿（`PROVENANCE.source.kind = "build"`，
`platform: null`，说明是「点击sign in，点击join soulmatemeets，填写信息注册，出现skip就点skip」），
走的是 `Sign In → /auth/login → /signup → /wizard/*` 另一条路；它和这个 landing 上的
japansdates 问卷（`#c3RlcDE=` → step1…）**不是同一条流程**。要修这个 landing 的失败单，
配的平台脚本是 `japansdates.py`。

## 文件

| 文件 | 是什么 |
|---|---|
| `census.js` | DOM 普查探针（testid 全量 / 可见按钮 / input / 正文 / hash） |
| `read_probe.py`（借用 10-10 那份） | 绑产物本体 `read()`，只读 |
| `drive_probe.py` | 真构造 japansdates 对象走 4 步（不提交） |
