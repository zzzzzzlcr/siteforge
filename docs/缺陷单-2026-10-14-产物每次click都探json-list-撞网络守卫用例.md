# 缺陷单（2026-10-14）：产物**每次 click** 都去探 CDP 的 `/json/list`（找新 tab），撞上「自测不许出网」那两条用例

**一句话**：产物骨架里那个「点完冒没冒新 tab」的探针（`_page_targets` → `urlopen`）**每一步 click 都跑**，
而 `tests/test_selftest.py` 那两条**最承重**的守卫用例断言「**一个 `urlopen` 都不许有**」——
守卫**不区分目标**，本地回环的 CDP 端点也记账 ⇒ 两条从 **2026-09-29** 起一直红。

**现场**（两条都红，断言处）：

```
FAILED tests/test_selftest.py::test_a_self_test_run_never_touches_the_production_api
FAILED tests/test_selftest.py::test_a_real_run_passes_and_stays_off_production
```

`net.log` 的原话（它拦下并记了账的那两笔）：

```
urlopen http://127.0.0.1:9222/json/list      ← 桩那一趟（cdp 是假二进制）
urlopen http://127.0.0.1:37533/json/list     ← 真 Chrome 那一趟（端口就是那扇窗的）
```

断言原话：「自测往生产发了请求：…」/「真跑这一遍往生产发了请求：…」——**它说的是生产，记的却是本地 CDP**。

---

## 一、根因（三处，都在产物骨架里）

| 处 | 行 | 干什么 |
|---|---|---|
| `agent/template.py` | `2417` | `_perform` 的 **click 支**：`before_ids = {t.get("id") for t in self._page_targets()}` —— **每一次 click** 都叫（无条件） |
| `agent/template.py` | `2543` / `2555` | `_page_targets()` → `_u.urlopen("http://%s:%s/json/list" % (host, port), …)`（读不到**吞掉**返回 `[]`） |
| `agent/template.py` | `2566` | 同一族还有一条 `/json/activate/<id>`（`_follow_new_tab` 里换 tab 用） |

守卫那一侧：`tests/test_selftest.py` `1262 _install_net_guard`（给子进程装 `sitecustomize`，把 `urllib`/`socket`
出口堵上、**先记一笔再抛**，写 `net.log`）；断言在 `1301` / `1466`。

**两边的口径都说得通，撞的是同一件事**：产物那个功能是「点之前记下有哪些页，点完多出来的就是新 tab」——
它**必须**问一次浏览器；而那两条用例的本意是「**网线上没有生产包**」，
它们的判据却是「**一个包都没有**」。

## 二、为什么迟至今天才显形（三条 `git log -S` 的日期）

```
50d7241 2026-09-16  自测不再往生产上报 —— 产物的 --no-report + report_url 认 SCREENSHOT_API_URL（守卫与断言）
307ecdd 2026-09-29  骨架：每一步动手之前兜一次同意弹层（…）   ← /json/list 那一跳随它进骨架
d236c80 2026-09-29  §8：判据不成立时先复核那一击（晚到的新 tab …）  ← _follow_new_tab
```

守卫立在 **09-16**、探针进骨架在 **09-29** ⇒ 中间那两周之后它们就**一直**顶着；
今天（10-14）做寻址那两刀时全量跑套件才把它翻出来。

## 三、A/B（**不是** 2026-10-14 那两刀带来的）

1. 把当日「寻址」那两刀从 `agent/template.py` 撤掉、单跑这两条 —— **照样红**；
2. 干净检出（`git worktree add … HEAD`，**零未提交改动**）上单跑第一条 —— **照样红**。

⇒ 既有缺陷，判据见上表，与工作区里在飞的改动无关。

## 四、两条出路（谁定谁定，我不动）

1. **守卫按目标放行**（改用例这一侧）：`sitecustomize` 里对**本机 CDP 端点**（`127.0.0.1:<那个 ws 的端口>`）
   不记账或不拦 —— 断言仍然只盯「生产」。改动小，语义上回到那两条用例自己写的那句话
   （「证据是**网线上没有**（生产）**包**」）。
2. **产物别走 urllib**（改产物这一侧）：那个「新 tab」探针改走 `cdp targets` 那条 CLI
   （Go 二进制，不受这层 python 守卫影响）—— 代价是给每步 click 多一次子进程；
   真站那一趟到底值不值，要先量（探针现在读不到是**静默返回 []**，功能本身就是半哑的）。

## 五、复现

```bash
cd /company/siteforge
.venv/bin/python -m pytest -q \
  tests/test_selftest.py::test_a_self_test_run_never_touches_the_production_api \
  tests/test_selftest.py::test_a_real_run_passes_and_stays_off_production
# 两条都要几秒钟；`net.log` 落在各自的 tmp_path 里（`/tmp/pytest-of-*/…/net.log`）
```

## 六、参考

- 起因那条 commit：`307ecdd`（骨架探针）、`d236c80`（`_follow_new_tab`）；守卫：`50d7241`
- 顺带一条**不是**本单的事：`_page_targets` 读不到时**吞掉返回 `[]`**（设计如此：探针失败不该让那一步挂）
  —— 所以生产上这个功能哑了也**看不出来**。要不要给它留一格证据（比如 trace 里记一笔），另说。
