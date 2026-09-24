# 自修复（检测到就开修）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 后端给一份「同一个站连续失败 5 次」的榜单，值班脚本检测到就**自己开修**（不用人按），
两遍之内通了就自动上传；两遍不通或做不成 ⇒ 停，并在人用的失败榜上**打标 + 说清为什么**。

**Architecture:** 两条腿。**值班脚本**（宿主上，只做「看榜 / 对账 / 开修 / 记账 / 报人」）
通过 HTTP 驱动服务；**服务**里的 `auto` 那一趟**不改图的闸**（图仍在每道闸 `interrupt()`），
由服务**替人按「继续」**（时间线留痕）。修/判/传**全在服务里** ⇒ 脚本挂了也写不坏生产。

**Tech Stack:** Python 3.10 / FastAPI + langgraph（既有）/ pytest / 宿主上的循环脚本 /
面板是单文件 `agent/console.html`（原生 JS，夹具是 node + 假 DOM）。

**Spec:** `docs/superpowers/specs/2026-09-24-auto-repair-design.md`

## Global Constraints

- **必须走 Bit**：窗口仍由服务自己开（`BIT_WORKER_IP` / `BIT_ID`，`agent/service.py:1355`），
  开窗口那一段**一个字不改**；跑完关窗（`service.py:150` 那条硬规矩）。
- **不改图的那道闸**：`graph._enter` 的 `interrupt()` 契约**一个字不动**；自动那半全在服务侧。
- **两遍**：`AUTO_MAX_ATTEMPTS = 2`；**一遍** = 出一版稿 + 自测跑一次（稿没出成/过不了闸也算）；
  自测按 2026-09-24 的裁定**只跑基线**（`selftest.RUN_ONLY = ("baseline",)`）⇒ 一趟最多 **2 次真提交**。
- **每天最多自动开修 10 个站**；**一次只修一个**（串行）；**总开关默认关**。
- **停用行不许自动上传**（上传会**重新启用**它 ⇒ 遇到就报人）；**别碰名单**命中 ⇒ 不开修、报人。
- **国家三种一律不猜、直接报人**（不开修）：代号不认识 / 那一单没记国家 / 换链**核不到**。
- ★ **写口用的键一律「带目录」**（2026-09-24 用户点名）：**自动那条路**用榜单上那个键**原样**
  （如 `a.com/aaa`）；**JSON 写回**也从失败记录/榜单**带出来**，**页面不许人手工打键**。
  理由：同一个站两个键 = 后端建出**两条行** ⇒ 生产按其中一个键下载，拿到的可能不是这一份。
- 时间线**词表是封闭的**（`agent/events.py:149` 的 `KINDS`）—— 新词要加进去，并改
  `tests/test_service_narration.py::test_the_service_narration_kinds_stay_inside_the_closed_vocabulary`。
- **页面一个判断都不许有**：标/原因都由服务算好，页面只搬字（`tests/test_console_page.py` 那族钉着）。
- **没有静默的路径**：少修了、没验到、脚本挂了、核不到 —— 一律说出来。
- 测试命令一律 `./.venv/bin/python -m pytest -q <路径>`；**不提交** `test.sh`（含真 key）。

## Review Focus

1. **榜上同时冒出很多站**（一天 >10）：第 11 个**不修**，且要说「今天到顶了，还剩 X 个没修」+ 清单
   —— 绝不静默少修（Task 5）。
2. **国家代号不认识 / 那一单没记国家 / 换链核不到**：**不开修**、报人（绝不猜一个国家去跑真流量）（Task 3）。
3. **同一个站反复连挂**（第二天新的一串）：每站每天最多 1 趟，但**新的一串可以再修**（Task 5 的对账）。
4. **值班脚本挂了 / 卡住**：15 分钟（3 轮）没有心跳 ⇒ 面板上报警（Task 6）。
5. **上传那两个口**：后端那份被别人改过（防覆盖）/ 回读不一致 ⇒ **自动回滚 + 报人**；
   停用行 ⇒ **一次都不上传**；**写口用的键必须带目录**（Task 4 + Task 7）。

---

### Task 1: 服务认 `auto`：到闸**自己按**（图一个字不改）

**Files:**
- Modify: `agent/service.py`（`RunRequest.auto`、`AUTO_REPLIED_SAY`、`Service._auto_reply`、接进 `_advance`）
- Modify: `agent/events.py:149`（`KINDS` 加 `"auto_replied"`）
- Test: `tests/test_service_auto.py`（新建）、`tests/test_service_narration.py`（封闭词表那条 +1 项）

**Interfaces:**
- Consumes: `Service.reply(job_id, ReplyRequest(action="continue", note=""))`（既有）
- Produces: `RunRequest.auto: bool = False`、`service.AUTO_REPLIED_SAY`、
  `Service._auto_reply(job, values) -> None`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_service_auto.py
"""auto：到闸**自己按**（服务替人按），图那半边一个字不改。"""
from agent import service


def test_auto_replies_at_the_gate_without_a_human(auto_app, tmp_path):
    """带 auto 的那一趟：过闸**不需要人按**，而且时间线上留一条痕（谁按的要说清）。"""
    app = auto_app()                      # 桩 graph + 桩 selftest（见下面的 fixture）
    r = app.post("/run", json={"url": URL, "goal": "走通", "success_text": "Thank you",
                               "auto": True, "out_dir": str(tmp_path / "sites")})
    assert r.status_code == 202, r.text
    events = app.get("/job/%s/live" % r.json()["job_id"]).json()["events"]
    assert [e["say"] for e in events if e["kind"] == "auto_replied"] == [service.AUTO_REPLIED_SAY]
    #: 自动 ≠ 人：时间线上**不许**冒出「人说的」那一条
    assert not [e for e in events if e["kind"] == "human_said"], events
```

- [ ] **Step 2: 跑一下，确认它红**

Run: `./.venv/bin/python -m pytest -q tests/test_service_auto.py -x`
Expected: FAIL（`RunRequest` 不认 `auto` / 没有 `auto_replied` 这条事件）

- [ ] **Step 3: 实现（最小）**

```python
# agent/events.py —— KINDS 里加一行（挨着 selftest_run）
    #: **自动模式替人按了「继续」**（2026-09-24）。与 `human_said` 不是同一件事：
    #: 那一句是**人**说的，这一句是**服务**替他按的 —— 两张嘴、两个时刻
    #: （与 `stop_landed` / `steer_landed` 同族：合起来说就得让一张嘴同时替人和替服务签名）。
    "auto_replied",
```

```python
# agent/service.py
AUTO_REPLIED_SAY = "自动模式：这一步自己走了（没人按）。"

class RunRequest(BaseModel):
    #: ★ 2026-09-24：**这一趟是自动那条路开的**（值班脚本开的）⇒ 服务到闸替人按「继续」。
    #: ⚠️ 它**不改图**：闸照旧 `interrupt()`，是**服务**去按（图的契约一个字不动）。
    auto: bool = False

    def _auto_reply(self, job: "Job", values: dict) -> None:
        """`auto` 那一趟到闸了 ⇒ 替人按一次「继续」（并留一条痕）。**只在这一处按。**"""
        if not (job.brief or {}).get("auto"):
            return
        if not values.get("__interrupt__"):          # 不在闸上（还在跑 / 到头了）⇒ 不按
            return
        self.narrate(job, "auto_replied", AUTO_REPLIED_SAY, step=self._where_it_stopped(job.job_id)[0])
        self.reply(job.job_id, ReplyRequest(action="continue", note=""))
```

接进 `_advance`：它跑完一步之后调一次 `self._auto_reply(job, values)`（`values` 就是手上那份快照的，不再读一次）。

- [ ] **Step 4: 跑，确认绿**

Run: `./.venv/bin/python -m pytest -q tests/test_service_auto.py tests/test_service_narration.py`
Expected: PASS（词表那条要把 `"auto_replied"` 加进它认的那串里）

- [ ] **Step 5: 提交**

```bash
git add agent/service.py agent/events.py tests/test_service_auto.py tests/test_service_narration.py
git commit -m "auto 那一趟：服务到闸替人按继续（图一个字不改）"
```

---

### Task 2: 两遍为限 + 「做不成」立刻停

**Files:**
- Modify: `agent/service.py`（`AUTO_MAX_ATTEMPTS`、`AUTO_GIVE_UP_SAY`、`_auto_attempts`、`_auto_give_up`）
- Test: `tests/test_service_auto.py`

**Interfaces:**
- Consumes: Task 1 的 `_auto_reply`
- Produces: `service.AUTO_MAX_ATTEMPTS = 2`、`service.AUTO_GIVE_UP_SAY`、
  `Service._auto_attempts(values) -> int`、`Service._auto_give_up(job, values, why) -> None`

- [ ] **Step 1: 写失败的测试**

```python
def test_auto_stops_after_two_attempts_and_names_why(auto_app):
    """自测连着两遍不过 ⇒ **不再按**，并说清「两遍都没过、需要人」。"""
    app = auto_app(selftest_fails=True)
    job = app.post("/run", json=BRIEF_AUTO).json()["job_id"]
    live = app.get("/job/%s/live" % job).json()
    assert live["status"] == "waiting", live                     # 停在闸上等人（不再自动按）
    said = " ".join(e["say"] for e in live["events"])
    assert "两遍" in said and "需要人" in said, said
    assert len([e for e in live["events"] if e["kind"] == "auto_replied"]) == 2, live["events"]


def test_auto_gives_up_immediately_when_it_cannot_be_done(auto_app):
    """窗口开不出来 ⇒ **不等两遍**，立刻报人（再试也不会变）。"""
    app = auto_app(window_open_fails="外面那句原话")
    job = app.post("/run", json=BRIEF_AUTO).json()["job_id"]
    said = " ".join(e["say"] for e in app.get("/job/%s/live" % job).json()["events"])
    assert "需要人" in said and "窗口" in said, said
```

- [ ] **Step 2: 跑，确认它红**

Run: `./.venv/bin/python -m pytest -q tests/test_service_auto.py -k "two_attempts or cannot_be_done" -x`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# agent/service.py
AUTO_MAX_ATTEMPTS = 2
AUTO_GIVE_UP_SAY = ("自动模式：**两遍都没过**（一遍 = 出一版稿 + 自测一次），再试下去只是在猜 ⇒ "
                    "停下等人。为什么：%s")

    @staticmethod
    def _auto_attempts(values: dict) -> int:
        """这一趟**已经到第几遍** —— 只认账上有的东西（`visits` 里 draft / selftest 的次数）。"""
        visits = [str(v) for v in (values.get("visits") or [])]
        return max(visits.count("draft"), visits.count("selftest"))

    def _auto_give_up(self, job: "Job", values: dict, why: str) -> None:
        """不再替人按，并把「为什么需要人」记进 job（Task 6 那一栏照它说话）。"""
        with job.lock:
            job.auto_state = "needs_human"
            job.auto_why = why
        self.narrate(job, "cap_hit", AUTO_GIVE_UP_SAY % why, end_reason="auto_give_up", end_note=why)
```

`_auto_reply` 前面加两道：① `self._auto_attempts(values) >= AUTO_MAX_ATTEMPTS` ⇒ `_auto_give_up`
（原因写「两遍都没过」）并 return；② 快照里 `end_reason` ∈ {`no_window`, `missing_knob`,
`no_success_text`, `window_gone`} ⇒ `_auto_give_up`（原因照抄那句人话）并 return。
`Job` 上加两格：`auto_state: str = ""`、`auto_why: str = ""`。

- [ ] **Step 4: 跑，确认绿**

Run: `./.venv/bin/python -m pytest -q tests/test_service_auto.py tests/test_service.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/service.py tests/test_service_auto.py
git commit -m "自动那条路：两遍为限，做不成立刻报人"
```

---

### Task 3: 开修前切国家（核到了才开；三种不猜）

**Files:**
- Modify: `agent/fmr.py`（`COUNTRY_CODES` + `code_for`，贴着「把国家写成人话」那一处放）
- Modify: `agent/service.py`（`auto` + `mode=fix` 时：切国家 → **核到了** → 才往下走）
- Test: `tests/test_fmr_country.py`（新建）、`tests/test_service_auto.py`

**Interfaces:**
- Consumes: 既有的换链路（`POST /country` 背后那个 `country_runner`，判据是「**核到了**」）
- Produces: `fmr.COUNTRY_CODES: dict[str, str]`、`fmr.code_for(said) -> Optional[str]`、
  `service.AUTO_COUNTRY_UNKNOWN_SAY`、`service.AUTO_COUNTRY_UNVERIFIED_SAY`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_fmr_country.py
from agent import fmr


def test_the_country_table_is_the_reverse_of_the_one_we_print():
    """人话里那个国家 → 代理国家码；**只认表里的**，不认识的一律 `None`（绝不猜）。"""
    assert fmr.code_for("美国") == "US"
    assert fmr.code_for("加拿大") == "CA"
    assert fmr.code_for("AU") is None, "「AU」这种我们表里没有 ⇒ 不许猜成别的"
    assert fmr.code_for("") is None and fmr.code_for(None) is None
```

```python
# tests/test_service_auto.py —— 三条路各一条
def test_auto_refuses_to_start_when_the_country_is_not_known(auto_app, tmp_path):
    """代号不认识 ⇒ **400 + 不开 job**（猜一个国家 = 拿错地区的出口去跑真流量）。"""
    app = auto_app()
    r = app.post("/run", json=dict(BRIEF_AUTO_FIX, auto_country="AU"))
    assert r.status_code == 400 and "认不出来" in r.text, r.text
    assert "/run" not in [x["url"] for x in app.state_calls], app.state_calls


def test_auto_switches_the_country_and_waits_for_the_receipt(auto_app):
    """认得 ⇒ 切（拿国家码），**核到了**才继续；核不到 ⇒ 400、不开修。"""
    app = auto_app(country_verified=True)
    assert app.post("/run", json=dict(BRIEF_AUTO_FIX, auto_country="美国")).status_code == 202
    assert app.country.calls == ["US"], app.country.calls
    app2 = auto_app(country_verified=False)
    assert app2.post("/run", json=dict(BRIEF_AUTO_FIX, auto_country="美国")).status_code == 400
```

- [ ] **Step 2: 跑，确认它红**

Run: `./.venv/bin/python -m pytest -q tests/test_fmr_country.py tests/test_service_auto.py -k country -x`
Expected: FAIL（`code_for` 不存在 / `auto_country` 没人管）

- [ ] **Step 3: 实现**

```python
# agent/fmr.py —— ⚠️ 先读「把国家写成人话」那一处，别另起一套口径
COUNTRY_CODES = {"美国": "US", "加拿大": "CA", "英国": "GB", "澳大利亚": "AU", "德国": "DE"}


def code_for(said) -> Optional[str]:
    """一句人话里的国家 → 国家码；表里没有 ⇒ `None`（**不猜**）。"""
    return COUNTRY_CODES.get(str(said or "").strip())
```

```python
# agent/service.py —— start() 里，auto + fix 时先过这一道
AUTO_COUNTRY_UNKNOWN_SAY = ("自动模式开不了这一趟：这一单要的国家我认不出来（%r）—— **不猜**"
                            "（猜一个国家 = 拿错地区的出口去跑真流量）。要么补那一格，要么人工开。")
AUTO_COUNTRY_UNVERIFIED_SAY = ("自动模式开不了这一趟：国家切到 `%s` 了，可它**核不出来**是它"
                               "（%s）—— 按那条路的规矩：核不到就不算换成了。")

    def _auto_country_gate(self, body: RunRequest) -> None:
        code = fmr.code_for(getattr(body, "auto_country", ""))
        if code is None:
            raise HTTPException(status_code=400, detail=AUTO_COUNTRY_UNKNOWN_SAY % getattr(body, "auto_country", ""))
        got = self._country.switch(code)              # 既有那条路（与 POST /country 同一个 runner）
        if not got.ok:
            raise HTTPException(status_code=400, detail=AUTO_COUNTRY_UNVERIFIED_SAY % (code, got.say))
```

- [ ] **Step 4: 跑，确认绿**

Run: `./.venv/bin/python -m pytest -q tests/test_fmr_country.py tests/test_service_auto.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/fmr.py agent/service.py tests/test_fmr_country.py tests/test_service_auto.py
git commit -m "自动开修前先切国家：核到了才开，三种不猜"
```

---

### Task 4: 两遍之内过了 ⇒ 自动上传（键**带目录** + 停用行不许传）

**Files:**
- Modify: `agent/service.py`（`_auto_upload`；到头那一支接上）
- Test: `tests/test_service_auto.py`、`tests/test_service_pyupload.py`（键那条）

**Interfaces:**
- Consumes: `_what_to_upload`（既有）—— 它给的 `key` 已经是 `_backend_key(...)`（**带目录**）
- Produces: `service.AUTO_UPLOAD_OK_SAY`、`service.AUTO_UPLOAD_REFUSED_SAY`、
  `service.AUTO_UPLOAD_ROLLED_BACK_SAY`、`Service._auto_upload(job, values) -> None`

- [ ] **Step 1: 写失败的测试**

```python
def test_the_upload_key_keeps_the_directory(auto_app, tmp_path):
    """★ 用户点名（2026-09-24）：写口用的键**带目录**（`a.com/aaa`），**不许**削成 `a.com`。"""
    app = auto_app(passes=True)
    app.post("/run", json=dict(BRIEF_AUTO, fix_site="a.com/aaa", fix_site_url=URL, url=URL))
    assert app.uploader.keys == ["a.com/aaa"], app.uploader.keys


def test_auto_uploads_when_the_run_passed(auto_app):
    """两遍之内过了 ⇒ 自动跑「预检 → 确认」；**回读一致**才算成。"""
    app = auto_app(passes=True)
    job = app.post("/run", json=BRIEF_AUTO).json()["job_id"]
    assert app.uploader.calls == ["prepare", "commit"], app.uploader.calls
    assert app.uploader.rolled_back is False
    said = " ".join(e["say"] for e in app.get("/job/%s/live" % job).json()["events"])
    assert "回读" in said and "一致" in said, said


def test_auto_never_uploads_to_a_disabled_row(auto_app):
    """后端那一行是**停用**的 ⇒ 一次都不上传，报人（上传会把它重新启用）。"""
    app = auto_app(passes=True, backend_disabled=True)
    job = app.post("/run", json=BRIEF_AUTO).json()["job_id"]
    assert app.uploader.calls == [], app.uploader.calls
    said = " ".join(e["say"] for e in app.get("/job/%s/live" % job).json()["events"])
    assert "停用" in said and "需要人" in said, said
```

- [ ] **Step 2: 跑，确认它红**

Run: `./.venv/bin/python -m pytest -q tests/test_service_auto.py -k upload -x`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
    def _auto_upload(self, job: "Job", values: dict) -> None:
        """到头了、且这一版过了 ⇒ 自动跑那三下（预检 → 确认 → **回读**）。"""
        if not (job.brief or {}).get("auto"):
            return
        if not self._auto_can_write(job, values):      # 停用行 / 没产物 / 没写 token ⇒ 报人
            return self._auto_give_up(job, values, AUTO_UPLOAD_REFUSED_SAY)
        got = self._upload_and_verify(job, values)     # 既有那三下的薄封装（一处实现）
        if not got["ok"]:
            return self._auto_give_up(job, values, AUTO_UPLOAD_ROLLED_BACK_SAY % got["say"])
        self.narrate(job, "done", AUTO_UPLOAD_OK_SAY % got["sha256"][:12], end_reason="auto_uploaded")
```

`_upload_and_verify` 直接调既有的 `py_upload_prepare` / `py_upload_commit`（同一个 `operator="auto-repair"`），
**不另写一套写口**；`prepare` 里那条 409「已经是这一份了，不用传」当作**成功**处置（并说清）。

- [ ] **Step 4: 跑，确认绿**

Run: `./.venv/bin/python -m pytest -q tests/test_service_auto.py tests/test_service_pyupload.py`
Expected: PASS（含：不一致 ⇒ `rollback` 被调 + 报人）

- [ ] **Step 5: 提交**

```bash
git add agent/service.py tests/test_service_auto.py tests/test_service_pyupload.py
git commit -m "自动上传：键带目录；回读一致才算成；停用行不传、不一致回滚"
```

---

### Task 5: 值班脚本的逻辑（`agent/auto.py`）+ 薄 CLI

**Files:**
- Create: `agent/auto.py`（看榜 / 对账 / 开修 / 记账 / 心跳 / 开关；HTTP 只走一个注入的口子）
- Create: `tools/auto-repair.py`（薄 CLI：一轮一轮跑，`--once` 只跑一轮）
- Create: `tests/test_auto.py`

**Interfaces:**
- Produces:
  - `auto.parse_list(payload: dict) -> list[dict]`（形状不对**抛**；不静默空）
  - `auto.Ledger(path)`：`.load()` / `.should_open(station, streak, now) -> bool` /
    `.open_entry(station, streak, job_id, country, now)` / `.settle(job_id, outcome, why, now)`
  - `auto.heartbeat(path, now)` / `auto.heartbeat_stale(path, now, minutes=15) -> bool`
  - `auto.read_flags(path) -> dict`（`{"on": False, "stop": False, "never": []}`，**文件不在 = 全默认**）
  - `auto.run_once(deps, now) -> dict`（一轮：读榜 → 决定 → `POST /run` → 记账；`deps` 注入 HTTP 与时钟）

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_auto.py
from agent import auto

LIST_OK = {"status": 200, "data": [{"site": "a.com/aaa", "streak": 5,
                                    "country": "美国", "task_id": "26131980"}]}


def test_the_list_shape_is_closed():
    """认的形状之外**当场抛**（静默空 = 把「没读完」读成「今天没有失败」）。"""
    assert [r["site"] for r in auto.parse_list(LIST_OK)] == ["a.com/aaa"]
    for bad in ({}, {"data": {}}, {"data": [{"site": ""}]}, {"data": [{"streak": "5"}]},
                {"data": [{"site": "a.com/aaa", "streak": 4}]}):
        try:
            auto.parse_list(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("这个形状竟然收下了：%r" % (bad,))


def test_the_same_streak_is_opened_only_once(tmp_path):
    """同一站同一串：只开一次；新的一串（次数变大）可以再开。"""
    led = auto.Ledger(tmp_path / "led.json")
    assert led.should_open("a.com/aaa", {"count": 5}, now="2026-09-24T10:00:00+08:00") is True
    led.open_entry("a.com/aaa", {"count": 5}, job_id="job-1", country="US",
                   now="2026-09-24T10:00:00+08:00")
    assert led.should_open("a.com/aaa", {"count": 5}, now="2026-09-24T10:05:00+08:00") is False
    assert led.should_open("a.com/aaa", {"count": 6}, now="2026-09-24T10:05:00+08:00") is True


def test_the_daily_cap_speaks_up(tmp_path):
    """到顶 ⇒ 不修，并说「今天到顶了、还剩几个」（**不静默少修**）。"""
    led = auto.Ledger(tmp_path / "led.json")
    for i in range(10):
        led.open_entry("s%d.com/x" % i, {"count": 5}, job_id="job-%d" % i, country="US",
                       now="2026-09-24T09:00:00+08:00")
    out = auto.run_once(_deps(tmp_path, list_payload=LIST_OK), now="2026-09-24T10:00:00+08:00")
    assert out["opened"] == [] and "到顶" in out["say"] and "1" in out["say"], out
```

- [ ] **Step 2: 跑，确认它红**

Run: `./.venv/bin/python -m pytest -q tests/test_auto.py -x`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**（下面这几个函数的**契约**就是这一层的全部面；`run_once` 的每一步都单独可测）

- [ ] **Step 3a（前置）：拿到那个口之后先量它的形状**

用户给了口的名字与字段之后：拿**真后端**打一次 ⇒ 把响应**原样**存成
`tests/fixtures/auto_list.json`（连字段名与类型一起）⇒ 按量到的改 `parse_list` 与测试夹具。
⚠️ **形状对不上就抛**（`parse_list` 的规矩）—— 不许猜着跑（猜错 = 「今天没有该修的站」这种假话）。

```python
# agent/auto.py
def parse_list(payload: dict) -> list[dict]:
    """后端那份「连续失败榜」→ 一串行。形状不对**抛**（不静默空）。"""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("这份榜的形状我不认（要 `data` 是一串行）：拿到的是 %r" % (payload,)[:200])
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("榜里有一行不是对象：%r" % (row,))
        site, streak = str(row.get("site") or "").strip(), row.get("streak")
        if not site or not isinstance(streak, int):
            raise ValueError("这一行缺 `site` 或 `streak` 不是整数：%r" % (row,))
        if streak < 5:                      # 榜单口径（连续 5 次）由后端定；不够格的**不收**
            continue
        out.append({"site": site, "count": streak, "country": str(row.get("country") or ""),
                    "task_id": str(row.get("task_id") or "")})
    return out


def _streak_changed(old: dict, new: dict) -> bool:
    """同一站的「这一串」是不是新的一串：`count` 变大 ⇒ 新的一串（可以再修）。"""
    return new["count"] > int(old.get("count") or 0)
```

`run_once` 的顺序（每一步都要能单独断言）：① `read_flags`（`on` 为假 ⇒ 直接返回「总开关关着」）；
② 心跳；③ 拉榜（`deps.get_list()`）→ `parse_list`；④ 逐站 `should_open`（去掉别碰名单、去掉当天已开过的）
→ 到顶就停下并**把剩下的报出来**；⑤ 对第一个该修的站 `deps.open_run(site)`（**键原样**、`auto=True`、
`auto_country=<那一行的国家>`）→ `open_entry`；⑥ 返回 `{"opened": [...], "say": ...}`。

- [ ] **Step 4: 跑，确认绿**

Run: `./.venv/bin/python -m pytest -q tests/test_auto.py`
Expected: PASS

- [ ] **Step 5: 薄 CLI + 提交**

```python
# tools/auto-repair.py —— 只做循环与退出码；逻辑全在 agent/auto.py
while True:
    out = auto.run_once(auto.http_deps(args.service), now=auto.now())
    print(auto.round_say(out))
    if args.once:
        break
    time.sleep(args.interval)
```

```bash
git add agent/auto.py tools/auto-repair.py tests/test_auto.py
git commit -m "值班脚本：看榜/对账/开修/记账/心跳（逻辑在 agent/auto.py）"
```

---

### Task 6: 面板那一栏 + 打标 + 心跳报警 + 开关

**Files:**
- Modify: `agent/service.py`（`/auto` 那一跳：账本 + 每站的结局 → 一栏数据；失败行上加 `auto_say` 标）
- Modify: `agent/console.html`（新一栏「自动修的活」+ 失败行上的标 + 总开关/一键停那两个按钮）
- Test: `tests/test_service_auto.py`、`tests/console_js_driver.js`、`tests/test_console_js.py`

**Interfaces:**
- Consumes: Task 5 的 `auto.Ledger` / `auto.read_flags` / `heartbeat_stale`
- Produces: `service.AUTO_PATH = "/auto"`；`/auto` 回 `{rows, say, alive}`；
  `/rank` 与 `/failures` 的每一行多一格 `auto_say`（**服务算的**，没修过就是空串）

- [ ] **Step 1: 写失败的测试**

```python
def test_the_auto_row_says_why_a_human_is_needed(auto_app):
    """「自动修的活」那一栏：一站一行，**为什么需要人**必须写着（两类分得开）。"""
    app = auto_app(ledger=[{"site": "a.com/aaa", "state": "needs_human",
                            "why": "两遍都没过（第 2 步点不到 Go）", "country": "US"}])
    row = app.get("/auto").json()["rows"][0]
    assert row["site"] == "a.com/aaa" and "两遍" in row["why"], row
    assert row["country"] == "US", row


def test_the_failure_row_is_marked_and_never_hidden(auto_app):
    """人用的失败榜那一行**打标**（AI 修过两遍没成），但那一行**照旧在**（不藏）。"""
    app = auto_app(rank_rows=[{"site": "a.com/aaa", "fail_count": 5, "say": "失败 5 次"}],
                   ledger=[{"site": "a.com/aaa", "state": "needs_human", "why": "两遍都没过"}])
    rows = app.get("/rank").json()["rank"]
    assert rows and "AI 自己修过两遍没成" in rows[0]["auto_say"], rows
```

```javascript
// tests/console_js_driver.js —— 新那一节（与既有那几节同一个写法）
// ① 开页：「自动修的活」那一栏摆出服务给的行；② 三拍重画之后**还在**（Task 7 那一族的老病）。
```

- [ ] **Step 2: 跑，确认它红**

Run: `./.venv/bin/python -m pytest -q tests/test_service_auto.py -k "auto_row or marked" -x`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# agent/service.py
AUTO_PATH = "/auto"
AUTO_NEEDS_HUMAN_MARK = "**AI 自己修过两遍没成，需要人**"
AUTO_STALE_SAY = ("值班脚本**没在跑**（心跳停了 %d 分钟）—— 自动那条路现在是断的；"
                  "人得自己开修。")

    @api.get(AUTO_PATH)
    def auto_view() -> dict:
        return svc.auto_view()      # 账本 → rows + say；心跳过期 ⇒ alive=False + AUTO_STALE_SAY
```

打标那一半：`/rank` 与 `/failures` 的投影处，用账本对一下站键 ⇒ 有「两遍没成」的记录就填
`auto_say = AUTO_NEEDS_HUMAN_MARK + "（" + why + "）"`（没修过 ⇒ 空串，**不编**）。

- [ ] **Step 4: 跑，确认绿**

Run: `./.venv/bin/python -m pytest -q tests/test_service_auto.py tests/test_console_js.py`
Expected: PASS（含「重画之后还在」）

- [ ] **Step 5: 提交**

```bash
git add agent/service.py agent/console.html tests/test_service_auto.py tests/console_js_driver.js tests/test_console_js.py
git commit -m "面板：自动修的活一栏 + 失败行打标 + 心跳报警 + 开关"
```

---

### Task 7: JSON 写回那一格也收紧（键**带目录**、不许手打）

**Files:**
- Modify: `agent/console.html`（`#jsonSite` 那一格：改成**挑一条失败单/从榜单带键**，不再手打）
- Modify: `agent/service.py`（`/jsondiff`、`/jsonwrite/*` 收 `task_id`；键由**服务**从记录里取）
- Test: `tests/test_service_jsonwrite.py`、`tests/console_js_driver.js`、`tests/test_console_js.py`

**Interfaces:**
- Consumes: 既有的 `fmr.failure_evidence` / `_backend_key`（`service.py:482`，**带目录**）
- Produces: `service.JSON_KEY_FROM_RECORD_SAY`；`/jsondiff` 与写回那两下都收 `task_id`（**键由服务取**）

- [ ] **Step 1: 写失败的测试**

```python
def test_the_json_write_key_comes_from_the_record_not_from_typing(json_app):
    """★ 用户点名（2026-09-24）：写回用的键从**失败记录**带出来（带目录），不靠人手打。"""
    body = json_app.post("/jsondiff", json={"task_id": "26131980"}).json()
    assert body["site"] == "a.com/aaa", body          # 记录里那个键**原样**（带目录）
    #: 人硬填一个浅键 ⇒ **拒**（不说清就写，等于可能写到另一行）
    r = json_app.post("/jsonwrite/prepare",
                      json={"site": "a.com", "config": {}, "operator": "值班员 A"})
    assert r.status_code == 400 and "记录" in r.text, r.text
```

- [ ] **Step 2: 跑，确认它红**

Run: `./.venv/bin/python -m pytest -q tests/test_service_jsonwrite.py -k from_the_record -x`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# agent/service.py —— 写那两个口只认 task_id；键在服务这一侧用 _backend_key 取（带目录）
JSON_KEY_FROM_RECORD_SAY = ("写回**只认失败记录里那个键**（键带目录，如 `a.com/aaa`）—— "
                            "人手打一个浅键（`a.com`）可能写到**后端另一行**上，所以这儿不受理手填的键。")
```

页面那一格改成：挑一条失败单（复用上面「要修哪一条」那条链的作法）⇒ 那一跳把 `task_id` 交给服务
⇒ 服务回 `site`（**服务算的**）⇒ 页面**只摆**、不改、不发自己拼的键。

- [ ] **Step 4: 跑，确认绿**

Run: `./.venv/bin/python -m pytest -q tests/test_service_jsonwrite.py tests/test_console_js.py`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/service.py agent/console.html tests/test_service_jsonwrite.py tests/console_js_driver.js tests/test_console_js.py
git commit -m "JSON 写回的键也从记录里来（带目录、不许手打）"
```

---

### 最后一步（**要你点头才做**，不是自动化的一部分）

- [ ] **真站验收那一趟**：挑一个你能接受被自动动的站 ⇒ 打开值班脚本（`flags.on = true`、`--once`）
  ⇒ 看它：切国家（核到了）→ 开修 → 两遍 → 自动上传 → 回读一致 ⇒ 然后**关掉开关**（`on = false`）✓
  ⇒ 把那一趟的账（`runtime/auto/…`）与时间线贴回来给我对一遍。

- [ ] **视觉模型（要「AI 看截图修」才有用）**：今天没配 ⇒ 这一版**不用截图**（照拍照存、留证据）；
  你给了模型名之后**另开一个小任务**接上（`_final_success_check` 那条看图的路已经在，只是没人能看）。
- [ ] **一键停 / 总开关**：面板上那两个（`flags.on` 默认 **false**；「停」只停循环与不新开活，
  正在跑的那一趟走既有的「停」）—— Task 6 那一步里一起摆出来。
