# 生成 py 时的「规则勾选」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 运营在「开一趟新的」面板勾选手机 02/07、生日随机、密码强随机；勾了的字段在生成期被确定性地写成「规则现编」，产物运行时完全不看任务资料。

**Architecture:** 规则以对象随 `/run` 进入 `SiteState`；图在 `deps.write` 之后把规则套到 `FILLS`（`rule`/`prefixes`/`date_format`）；表型与循环型两套骨架在运行期先看 `rule`、再看资料。规则是**生成期烤进产物**的，FMR/生产不需要额外参数。

**Tech Stack:** Python 3 / FastAPI / LangGraph / pytest；console 是 `agent/console.html` 里的原生 JS。

**Spec:** `docs/superpowers/specs/2026-10-20-rules-checkbox-design.md`

## Global Constraints

- `rules` 是对象：`{"phone_prefixes": [...], "dob": {"random": true, "format": "..."}, "password": "strong"}`；三块都可省略。
- 手机号第一批只允许 `"02"`/`"07"`；生成 **10 位**（前缀 + 8 位随机）。
- 勾了规则 = **强制现编，完全不读 `form_data`**；同一规则组所有字段共用同一个值。
- 生日规则：18–68 岁；格式解析顺序 **手填 > placeholder > label > hint**；都识别不出 ⇒ `end_reason=rule_needs_format`，不产 py。
- 密码规则必须匹配 `^(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])(?=.*[!@#$%^&*]).{8,}$`。
- **无 `rules` 时行为逐字节不变**；现有 `phone_area_code` 保留。
- 未知规则/非法值在 `/run` 门口 **400**；规则勾了但无命中在闸上警告，不静默。
- 改 `agent/template.py` / `agent/template_loop.py` 后要重启 8100/8099 才对新生成脚本生效。

## Review Focus

- 未知规则 key/值必须 400（不是静默丢掉）—— Task 1/2 的校验用例。
- 规则勾了但产物里没有对应字段 ⇒ 闸上必须有 `unmatched` 警告 —— Task 1/5 的用例。
- 生日格式识别不出且没手填 ⇒ 必须停在生成期，不能默认 DD/MM —— Task 1/5 的用例。
- 同一趟 phone/phone-confirm 必须同值；多字段同规则组不能各编各的 —— Task 3/4 的用例。
- 不勾规则 / 只有旧 `phone_area_code` 时行为不变 —— Task 3/4/5 的回归用例。

## File Structure

- **Create** `agent/rules.py` —— 规则契约、校验、日期格式解析、把规则套到 `FILLS` 的纯函数。
- Modify `agent/service.py` —— `RunRequest.rules`、`start()` 门口校验、`_payload` 白名单、恢复白名单。
- Modify `agent/state.py` —— `SiteState.rules` / `rules_effective`、`END_RULE_NEEDS_FORMAT`。
- Modify `agent/graph.py` —— `_apply_rules()`、`_draft` 接线、`_provenance`/闸上 facts。
- Modify `agent/template.py` —— 表型运行时的 `_rule_value` / `_format_date` / `_strong_password`、`_fill_value` 规则分支。
- Modify `agent/template_loop.py` —— 循环型 `value_for` 的规则分支与同一套生成器。
- Modify `agent/console.html` —— 规则区、生日格式输入、`runRulesPayload()`。
- Tests：`tests/test_rules.py`（新）、`tests/test_service.py`、`tests/test_graph.py`、`tests/test_template.py`、`tests/test_loop_template.py`、`tests/test_console_page.py`、`tests/test_console_js.py`、`tests/console_js_driver.js`。

---

### Task 1: 规则契约模块 `agent/rules.py`

**Files:**
- Create: `agent/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Produces: `RuleError`、`normalize_rules(raw)`、`parse_date_format(text)`、`apply_to_fills(fills, rules)`。
- `apply_to_fills` 返回 `{"fills", "applied", "unmatched", "needs_format", "effective"}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rules.py
import pytest
from agent import rules


def test_normalize_accepts_the_first_batch():
    got = rules.normalize_rules({
        "phone_prefixes": ["02", "07", "02"],
        "dob": {"random": True, "format": "DD/MM/YYYY"},
        "password": "strong",
    })
    assert got["phone_prefixes"] == ["02", "07"]
    assert got["dob"] == {"random": True, "format": "DD/MM/YYYY"}
    assert got["password"] == "strong"


def test_normalize_rejects_unknown_rules_and_bad_formats():
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"phone_prefixes": ["04"]})
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"dob": {"random": True, "format": "03/10/1970"}})
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"webhook": "x"})


def test_parse_date_format_reads_page_text_but_not_a_date_value():
    assert rules.parse_date_format("Date of birth (DD/MM/YYYY)") == "DD/MM/YYYY"
    assert rules.parse_date_format("MM-DD-YYYY") == "MM-DD-YYYY"
    assert rules.parse_date_format("3/10/1970") is None


def _fill(name, label, selector):
    return {"name": name, "source": name, "kind": "value", "label": label,
            "target": {"label": label, "placeholder": None,
                       "hint": None, "selectors": [selector]}}


def test_apply_phone_dob_password_ignores_nothing_and_sets_rules():
    fills = {
        "phone": _fill("phone", "PhoneNumber", "#phone"),
        "phone_confirm": _fill("phone_confirm", "Confirm phone", "#phone2"),
        "dob": _fill("dob", "Date of birth (DD/MM/YYYY)", "#dob"),
        "password": _fill("password", "Password", "#password"),
    }
    got = rules.apply_to_fills(fills, {
        "phone_prefixes": ["02", "07"],
        "dob": {"random": True},
        "password": "strong",
    })
    assert got["fills"]["phone"]["rule"] == "phone_prefix"
    assert got["fills"]["phone"]["prefixes"] == ["02", "07"]
    assert got["fills"]["phone_confirm"]["rule"] == "phone_prefix"
    assert got["fills"]["dob"]["rule"] == "dob_random"
    assert got["fills"]["dob"]["date_format"] == "DD/MM/YYYY"
    assert got["fills"]["password"]["rule"] == "password_strong"
    assert got["needs_format"] == []
    assert got["unmatched"] == []


def test_apply_reports_unmatched_and_needs_format():
    got = rules.apply_to_fills(
        {"email": _fill("email", "Email", "#email")},
        {"phone_prefixes": ["02"], "dob": {"random": True}},
    )
    assert got["unmatched"] == ["phone:02", "dob_random"]
    assert got["needs_format"] == []

    only_dob = rules.apply_to_fills(
        {"dob": _fill("dob", "Date of birth", "#dob")},
        {"dob": {"random": True}},
    )
    assert only_dob["needs_format"] == ["dob"]
    assert only_dob["fills"]["dob"].get("rule") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_rules.py -q`
Expected: FAIL（`agent.rules` 不存在）

- [ ] **Step 3: 实现 `agent/rules.py`**

```python
"""生成期规则：手机号 02/07、生日随机、密码强随机。

这一层只做**纯函数**：校验载荷、解析日期格式、把规则写进 FILLS。
运行时的生成器在 template.py / template_loop.py 里（产物必须自包含，不能 import 本仓）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

PHONE_PREFIXES = ("02", "07")
_DOB_TOKENS = {"D", "DD", "M", "MM", "YYYY", "YY"}
_DATE_RE = re.compile(
    r"(?i)\b(D{1,2}|M{1,2}|Y{2,4})[\/\-\.](D{1,2}|M{1,2}|Y{2,4})[\/\-\.](D{1,2}|M{1,2}|Y{2,4})\b"
)


class RuleError(ValueError):
    """载荷里的规则不合法（`/run` 门口 400 用）。"""


def _fail(msg: str) -> "RuleError":
    return RuleError("规则不合法：%s" % msg)


def normalize_rules(raw: Any) -> Optional[dict]:
    """把 `/run` 的 rules 对象规范化；None/空对象 ⇒ None。非法 ⇒ RuleError。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _fail("`rules` 得是一个对象")
    unknown = set(raw) - {"phone_prefixes", "dob", "password"}
    if unknown:
        raise _fail("不认识的规则：%s（只认 phone_prefixes / dob / password）"
                    % "、".join(sorted(unknown)))
    out: dict = {}
    phones = raw.get("phone_prefixes")
    if phones is not None:
        if not isinstance(phones, list) or any(not isinstance(x, str) for x in phones):
            raise _fail("`phone_prefixes` 得是一串字符串")
        cleaned = []
        for x in phones:
            x = x.strip()
            if x and x not in cleaned:
                cleaned.append(x)
        bad = [x for x in cleaned if x not in PHONE_PREFIXES]
        if bad:
            raise _fail("手机号只认 %s；给的是 %s"
                        % ("/".join(PHONE_PREFIXES), "、".join(bad)))
        if cleaned:
            out["phone_prefixes"] = cleaned
    dob = raw.get("dob")
    if dob is not None:
        if not isinstance(dob, dict):
            raise _fail("`dob` 得是对象：{\"random\": true, \"format\": \"DD/MM/YYYY\"}")
        unknown_dob = set(dob) - {"random", "format"}
        if unknown_dob:
            raise _fail("`dob` 里不认识的键：%s" % "、".join(sorted(unknown_dob)))
        if dob.get("random") is not True:
            raise _fail("`dob.random` 必须是 true")
        fmt = dob.get("format")
        if fmt is not None:
            if not isinstance(fmt, str):
                raise _fail("生日格式得是字符串")
            fmt = parse_date_format(fmt) or ""
            if not fmt:
                raise _fail("生日格式认不出：%r（例：DD/MM/YYYY）" % dob.get("format"))
        out["dob"] = {"random": True, **({"format": fmt} if fmt else {})}
    password = raw.get("password")
    if password is not None:
        if password != "strong":
            raise _fail("`password` 只认 \"strong\"")
        out["password"] = "strong"
    return out or None


def parse_date_format(text: Any) -> Optional[str]:
    """从一段页面文字/格式串里提取日期格式；提取不到 ⇒ None。

    只认 D/DD、M/MM、YYYY/YY 三个 token，分隔符 `/`、`-`、`.`。
    这是**格式**，不是**值**：`3/10/1970` 这种数字开头的不算。
    """
    if not isinstance(text, str):
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    parts = [p.upper() for p in (m.group(1), m.group(2), m.group(3))]
    if any(p not in _DOB_TOKENS for p in parts):
        return None
    if sum(p in ("D", "DD") for p in parts) != 1:
        return None
    if sum(p in ("M", "MM") for p in parts) != 1:
        return None
    if sum(p in ("Y", "YY", "YYYY") for p in parts) != 1:
        return None
    return text[m.start():m.end()].upper()


def _fill_text(fill: dict) -> str:
    target = fill.get("target") if isinstance(fill.get("target"), dict) else {}
    hay = " ".join(str(x or "") for x in (
        fill.get("name"), fill.get("kind"), fill.get("source"), fill.get("label"),
        target.get("label"), target.get("hint"),
    ))
    return hay.lower()


def is_phone_fill(fill: dict) -> bool:
    text = _fill_text(fill)
    return any(s in text for s in ("phone", "tel", "mobile"))


def is_dob_fill(fill: dict) -> bool:
    text = _fill_text(fill)
    return any(s in text for s in ("dob", "birth", "birthday", "date of birth"))


def is_password_fill(fill: dict) -> bool:
    return "password" in _fill_text(fill)


def _detect_date_format(fill: dict) -> tuple[Optional[str], Optional[str]]:
    target = fill.get("target") if isinstance(fill.get("target"), dict) else {}
    for source, value in (
        ("placeholder", target.get("placeholder")),
        ("label", target.get("label")),
        ("label", fill.get("label")),
        ("hint", target.get("hint")),
    ):
        fmt = parse_date_format(value)
        if fmt:
            return fmt, source
    return None, None


def apply_to_fills(fills: dict, rules: Optional[dict]) -> dict:
    """把规则写成 FILLS 的 `rule`/`prefixes`/`date_format`；不修改入参。"""
    out = {name: dict(fill or {}) for name, fill in (fills or {}).items()}
    result = {"fills": out, "applied": [], "unmatched": [],
              "needs_format": [], "effective": None}
    if not rules:
        return result

    phones = list(rules.get("phone_prefixes") or [])
    if phones:
        hits = [n for n, f in out.items() if is_phone_fill(f)]
        if hits:
            for n in hits:
                out[n]["rule"] = "phone_prefix"
                out[n]["prefixes"] = phones
            result["applied"].append("phone:" + "/".join(phones))
        else:
            result["unmatched"].append("phone:" + "/".join(phones))

    dob = rules.get("dob")
    if dob:
        hits = [n for n, f in out.items() if is_dob_fill(f)]
        if hits:
            resolved = {}
            for n in hits:
                fmt = dob.get("format")
                source = "operator"
                if not fmt:
                    fmt, source = _detect_date_format(out[n])
                if not fmt:
                    result["needs_format"].append(n)
                    continue
                out[n]["rule"] = "dob_random"
                out[n]["date_format"] = fmt
                out[n]["date_format_source"] = source
                resolved.setdefault("format", fmt)
                resolved.setdefault("format_source", source)
            if len(result["needs_format"]) < len(hits):
                result["applied"].append("dob_random")
            if resolved:
                result["effective"] = dict(dob, **resolved)
        else:
            result["unmatched"].append("dob_random")

    password = rules.get("password")
    if password:
        hits = [n for n, f in out.items() if is_password_fill(f)]
        if hits:
            for n in hits:
                out[n]["rule"] = "password_strong"
            result["applied"].append("password_strong")
        else:
            result["unmatched"].append("password_strong")

    effective = dict(rules)
    if result["effective"] and "dob" in effective:
        effective["dob"] = result["effective"]
    effective["applied"] = list(result["applied"])
    effective["unmatched"] = list(result["unmatched"])
    result["effective"] = effective
    return result
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_rules.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add agent/rules.py tests/test_rules.py
git commit -m "feat: 规则契约与日期格式解析" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: 服务/状态接线

**Files:**
- Modify: `agent/service.py`（`RunRequest.rules`、`start()`、`_payload`、恢复白名单）
- Modify: `agent/state.py`（`SiteState.rules` / `rules_effective`、`END_RULE_NEEDS_FORMAT`）
- Test: `tests/test_service.py`（或 `tests/test_rules.py` 加服务级用例）

**Interfaces:**
- Consumes: `agent.rules.normalize_rules`（Task 1）。
- Produces: `/run` 收 `rules`；非法 400；brief 里带规范化后的 `rules`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_rules.py` 顶部补服务级用例（若 `Service` 夹具太重，改用 `RunRequest` 直接构造 + 一个小的 `start` 桩；见本步骤代码）：

```python
from fastapi import HTTPException
from agent.service import RunRequest


def test_run_request_keeps_rules_and_normalize_rejects_unknown():
    body = RunRequest(url="https://example.test", mode="build",
                      success_text="Thank you",
                      rules={"phone_prefixes": ["02", "07"]})
    assert body.rules == {"phone_prefixes": ["02", "07"]}
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"phone_prefixes": ["04"]})
```

另外在 `tests/test_service.py` 加一条（用文件里现有的 `Service` 夹具）：

```python
def test_start_rejects_bad_rules(service):
    from fastapi import HTTPException
    from agent.service import RunRequest
    body = RunRequest(url="https://example.test", mode="build",
                      success_text="Thank you", rules={"phone_prefixes": ["04"]})
    with pytest.raises(HTTPException) as ei:
        service.start(body)
    assert ei.value.status_code == 400
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_rules.py::test_run_request_keeps_rules_and_normalize_rejects_unknown -q`
Expected: FAIL（`RunRequest` 没有 `rules`）

- [ ] **Step 3: 实现**

`agent/service.py` 顶部加 `from agent import rules as rules_mod`（与现有 `agent` 导入同一段）。

`RunRequest` 在 `phone_area_code` 附近加：

```python
    rules: Optional[dict] = Field(
        None, description="生成期规则：{phone_prefixes:['02','07'], dob:{random:true,format:'DD/MM/YYYY'}, password:'strong'}")
```

`Service.start()` 在 `problems` 检查之后、`brief = body.model_dump(...)` 之前加：

```python
        try:
            normalized = rules_mod.normalize_rules(getattr(body, "rules", None))
        except rules_mod.RuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
```

`brief = body.model_dump(exclude_none=True)` 之后：

```python
        if normalized is None:
            brief.pop("rules", None)
        else:
            brief["rules"] = normalized
```

`Service._payload` 的 `keep` 元组加 `"rules"`；`_recover` 的 `keep` 元组也加 `"rules"`（两处都改，见 spec §4.2）。

`agent/state.py`：

```python
END_RULE_NEEDS_FORMAT = "rule_needs_format"
```

加进 `__all__`；`SiteState` 加：

```python
    rules: Optional[dict]
    rules_effective: Optional[dict]
```

`_brief_facts`（`agent/graph.py`）加一行：

```python
            #: ★ 2026-10-20：这一趟勾了什么规则（原样摆出来；生效结果在 draft 闸上）
            "规则": state.get("rules") or None,
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_rules.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/service.py agent/state.py agent/graph.py tests/test_rules.py tests/test_service.py
git commit -m "feat: /run 接收规则并校验" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 表型运行时 `agent/template.py`

**Files:**
- Modify: `agent/template.py`（`Filler.__init__`、`_fill_value`、新增 `_rule_value`/`_format_date`/`_strong_password`）
- Test: `tests/test_template.py`

**Interfaces:**
- Consumes: `FILLS[*]` 的 `rule` / `prefixes` / `date_format`（Task 1/5 写入）。
- Produces: `_fill_value` 在 `rule` 存在时**完全不读** `self.form_data`；同规则组同值。

- [ ] **Step 1: 写失败测试**

在 `tests/test_template.py` 加（复用现有 `sandbox` / `_load` / `_stub` 夹具；`WS`/`SAMPLE_PROVENANCE` 用文件内已有的）：

```python
def test_rule_fills_ignore_form_data_and_share_values(sandbox, tmp_path):
    ff = tmp_path / "form.json"
    ff.write_text(json.dumps({"phone": "0000000000",
                              "phone_confirm": "0000000000",
                              "dob": "01/01/1900",
                              "password": "weak"}), encoding="utf-8")

    def _target(label, sel):
        return {"text": None, "label": label, "role": None, "near": None,
                "selectors": [sel]}

    fills = [
        {"name": "phone", "source": "phone", "kind": "value", "label": "PhoneNumber",
         "target": _target("PhoneNumber", "input#phone"),
         "rule": "phone_prefix", "prefixes": ["02", "07"]},
        {"name": "phone_confirm", "source": "phone_confirm", "kind": "value",
         "label": "Confirm phone", "target": _target("Confirm phone", "input#phone2"),
         "rule": "phone_prefix", "prefixes": ["02", "07"]},
        {"name": "dob", "source": "dob", "kind": "value", "label": "Date of birth",
         "target": _target("Date of birth", "input#dob"),
         "rule": "dob_random", "date_format": "DD/MM/YYYY"},
        {"name": "password", "source": "password", "kind": "value", "label": "Password",
         "target": _target("Password", "input#password"),
         "rule": "password_strong"},
    ]
    steps = [{"action": "form", "fill": f["name"], "note": "填 %s" % f["label"]}
             for f in fills]
    prod = template.render("example-rule", "Thank you",
                           [{"name": "walk", "when": None, "steps": steps}], fills,
                           SAMPLE_PROVENANCE)
    module, _ = _load("run_rule_fills", prod, sandbox)
    common = _stub(sandbox, diff={"actionable": True},
                   observe={"url": "https://example.test/", "actions": [], "fields": []})
    common.STATE.texts = ["Walk"] * 4 + ["Thank you"]

    assert module.Filler(WS, str(ff), "cid_1", "task_1", delay=(0, 0)).run() is True
    filled = {a[1]: a[2] for a in common.STATE.actions if a[0] == "form"}

    assert filled["input#phone"] == filled["input#phone2"]
    assert re.fullmatch(r"0[27]\d{8}", filled["input#phone"]), filled["input#phone"]
    assert filled["input#phone"] != "0000000000"
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", filled["input#dob"]), filled["input#dob"]
    day, month, year = (int(x) for x in filled["input#dob"].split("/"))
    today = datetime.date.today()
    age = today.year - year - ((today.month, today.day) < (month, day))
    assert 18 <= age <= 68, (filled["input#dob"], age)
    assert re.fullmatch(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])(?=.*[!@#$%^&*]).{8,}$",
                        filled["input#password"]), filled["input#password"]
```

（若文件里没有 `datetime`，在测试文件顶部 `import datetime`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_template.py::test_rule_fills_ignore_form_data_and_share_values -q`
Expected: FAIL（填的是 form_data 的 `0000000000` / `01/01/1900` / `weak`）

- [ ] **Step 3: 实现**

`agent/template.py` 在 `_shaped_like` 附近加模块级函数：

```python
def _format_date(day, month, year, fmt):
    out = []
    for part in re.split(r"([/\-\.])", str(fmt or "")):
        if not part:
            continue
        if part in ("/", "-", "."):
            out.append(part)
            continue
        token = part.upper()
        if token == "D":
            out.append(str(day))
        elif token == "DD":
            out.append("%02d" % day)
        elif token == "M":
            out.append(str(month))
        elif token == "MM":
            out.append("%02d" % month)
        elif token == "YYYY":
            out.append(str(year))
        elif token == "YY":
            out.append(str(year)[-2:])
        else:
            raise ValueError("产物写错了：生日格式里有不认识的 token「%s」" % part)
    return "".join(out)


def _strong_password():
    upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lower = "abcdefghijklmnopqrstuvwxyz"
    digits = "0123456789"
    specials = "!@#$%^&*"
    chars = [random.choice(upper), random.choice(lower),
             random.choice(digits), random.choice(specials)]
    alphabet = upper + lower + digits + specials
    chars += [random.choice(alphabet) for _ in range(8)]
    random.shuffle(chars)
    return "".join(chars)
```

`Filler.__init__` 加 `self._rule_values = {}`（放在 `self.form_data = ...` 之后）。

`_fill_value` 开头（`self._value_from = ""` 之后）加：

```python
        rule = str(fill.get("rule") or "").strip()
        if rule:
            return self._rule_value(rule, fill)
```

在 `_fill_value` 之前加：

```python
    def _rule_value(self, rule, fill):
        key = (rule, tuple(fill.get("prefixes") or ()),
               str(fill.get("date_format") or ""))
        if key in self._rule_values:
            self._value_from = "规则「%s」现编（同一趟同值）" % rule
            return self._rule_values[key]
        if rule == "phone_prefix":
            prefixes = [str(x).strip() for x in (fill.get("prefixes") or [])
                        if str(x).strip()]
            prefix = random.choice(prefixes) if prefixes else "04"
            value = prefix + "".join(str(random.randint(0, 9))
                                     for _ in range(max(0, 10 - len(prefix))))
            self._value_from = "规则「手机号 %s」现编" % ("/".join(prefixes) or prefix)
        elif rule == "dob_random":
            fmt = str(fill.get("date_format") or "DD/MM/YYYY")
            year = time.localtime().tm_year - random.randint(19, 68)
            value = _format_date(random.randint(1, 28), random.randint(1, 12), year, fmt)
            self._value_from = "规则「生日 %s，18-68」现编" % fmt
        elif rule == "password_strong":
            value = _strong_password()
            self._value_from = "规则「强密码」现编"
        else:
            raise ValueError("产物写错了：不认识的规则「%s」" % rule)
        self._rule_values[key] = value
        return value
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_template.py::test_rule_fills_ignore_form_data_and_share_values -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/template.py tests/test_template.py
git commit -m "feat: 表型产物支持规则现编" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 循环型运行时 `agent/template_loop.py`

**Files:**
- Modify: `agent/template_loop.py`（`value_for` 规则分支、`__init__` 缓存、同一套生成器）
- Test: `tests/test_loop_template.py`

**Interfaces:**
- Consumes: 与 Task 3 相同的 `rule`/`prefixes`/`date_format`。
- Produces: `value_for` 命中 `spec["rule"]` 时返回规则值，**不读 `form_data`/`EXAMPLES`**。

- [ ] **Step 1: 写失败测试**

在 `tests/test_loop_template.py` 加（复用文件里现有的 loop Filler 构造夹具；若没有，按 `test_a_site_area_code...` 那一段的方式构造）：

```python
def test_value_for_rule_ignores_form_data_and_is_stable(tmp_path, monkeypatch):
    calls = {"click": [], "form": [], "nav": [], "scroll": [], "report": []}
    page = {"url": "https://funnel.test/quiz", "head": "Q", "progress": "",
            "inputs": [], "options": [], "nextVisible": True, "nextTxt": "Continue"}
    _fake_common(monkeypatch, page, calls)
    src = template_loop.render_loop(
        "phonesite", "Thank you", "thank-you",
        {"phone": {"kind": "value", "label": "PhoneNumber",
                   "rule": "phone_prefix", "prefixes": ["02"]}},
        dict(PROV))
    path = tmp_path / "rule_phone.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("rule_phone_product", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    obj = module.Filler.__new__(module.Filler)
    obj.form_data = {"phone": "0000000000"}
    obj._rule_values = {}
    got = [obj.value_for("\u0002PhoneNumber", {}, 0) for _ in range(3)]
    assert len(set(got)) == 1, got          # 同一趟同值
    assert re.fullmatch(r"02\d{8}", got[0]), got
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_loop_template.py::test_value_for_rule_ignores_form_data_and_is_stable -q`
Expected: FAIL（返回 `0000000000` 或不是 02 号段）

- [ ] **Step 3: 实现**

`agent/template_loop.py` 的 `FILLS` 遍历处（`value_for` 里 `for name, spec in (FILLS or {}).items():` 之后）加：

```python
            if spec.get("rule"):
                return self._rule_value(spec)
```

在类里加与 Task 3 相同的 `_rule_value`/`_format_date`/`_strong_password`（两份实现、同一套判据；
不能 import 表型骨架）。`__init__` 加 `self._rule_values = {}`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_loop_template.py::test_value_for_rule_ignores_form_data_and_is_stable -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/template_loop.py tests/test_loop_template.py
git commit -m "feat: 循环型产物支持规则现编" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: 图：套规则 + 生日格式 + 闸上可见

**Files:**
- Modify: `agent/graph.py`（`_apply_rules`、`_draft` 接线、`_provenance`、facts）
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: Task 1 `rules.apply_to_fills`；Task 2 `SiteState.rules`、`END_RULE_NEEDS_FORMAT`。
- Produces: `_apply_rules(spec, state) -> dict`；`_draft` 在 `needs_format` 时直接结束。

- [ ] **Step 1: 写失败测试**

在 `tests/test_graph.py` 加（纯函数级；不启图）：

```python
def test_apply_rules_sets_fills_and_returns_effective():
    from agent import graph
    spec = {"fills": {
        "phone": {"name": "phone", "kind": "value", "label": "PhoneNumber",
                  "target": {"selectors": ["#phone"]}},
        "dob": {"name": "dob", "kind": "value",
                "label": "Date of birth (DD/MM/YYYY)",
                "target": {"placeholder": "DD/MM/YYYY", "selectors": ["#dob"]}},
    }}
    got = graph._apply_rules(spec, {"rules": {"phone_prefixes": ["02"],
                                              "dob": {"random": True}}})
    assert got["fills"]["phone"]["rule"] == "phone_prefix"
    assert got["fills"]["dob"]["date_format"] == "DD/MM/YYYY"
    assert got["rules_effective"]["dob"]["format"] == "DD/MM/YYYY"


def test_apply_rules_stops_without_a_dob_format():
    from agent import graph
    spec = {"fills": {
        "dob": {"name": "dob", "kind": "value", "label": "Date of birth",
                "target": {"selectors": ["#dob"]}},
    }}
    got = graph._apply_rules(spec, {"rules": {"dob": {"random": True}}})
    assert got["end_reason"] == "rule_needs_format"
    assert "生日格式" in got["end_note"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_graph.py::test_apply_rules_sets_fills_and_returns_effective tests/test_graph.py::test_apply_rules_stops_without_a_dob_format -q`
Expected: FAIL（`graph._apply_rules` 不存在）

- [ ] **Step 3: 实现**

`agent/graph.py` 加：

```python
def _apply_rules(spec: dict, state: dict) -> dict:
    """把 `state["rules"]` 套到 `spec["fills"]`（模型写完之后、render 之前）。"""
    rr = rules_mod.apply_to_fills(spec.get("fills") or {}, state.get("rules"))
    out = {"fills": rr["fills"], "rules_effective": rr["effective"],
           "rules_applied": rr["applied"], "rules_unmatched": rr["unmatched"]}
    if rr["needs_format"]:
        out["end_reason"] = END_RULE_NEEDS_FORMAT
        out["end_note"] = (
            "生日格式没识别出来：%s。请在面板「生日格式」里填一个，比如 `DD/MM/YYYY`"
            "（或 `MM/DD/YYYY` / `YYYY-MM-DD`）；识别不出的这一趟**不写 py**。"
            % "、".join(rr["needs_format"]))
    return out
```

在 `_draft` 的 `_area` 块之后（render 之前）加：

```python
    _rr = _apply_rules(spec, state)
    spec["fills"] = _rr["fills"]
    if _rr.get("end_reason"):
        out.update({"end_reason": _rr["end_reason"], "end_note": _rr["end_note"]})
        return out
    if _rr.get("rules_effective"):
        out["rules_effective"] = _rr["rules_effective"]
        state["rules_effective"] = _rr["rules_effective"]
    out["rules_applied"] = _rr.get("rules_applied") or []
    out["rules_unmatched"] = _rr.get("rules_unmatched") or []
```

`_provenance` 加：

```python
        "rules": state.get("rules_effective") or state.get("rules") or None,
```

lint 闸的 `_enter(..., "lint", ..., facts={...})` 里加：

```python
                 facts={"检查的是": "刚写好的那一版（%d 行）" % len((state.get("src") or "").splitlines()),
                        "规则": state.get("rules_applied") or [],
                        "规则没命中": state.get("rules_unmatched") or []})
```

（`rules_applied`/`rules_unmatched` 由 `_draft` 的 `out` 合并进 state；
`rules_mod` 的 import 与现有 `agent` 模块导入同一段；`END_RULE_NEEDS_FORMAT` 从 `agent.state` 导入。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_graph.py::test_apply_rules_sets_fills_and_returns_effective tests/test_graph.py::test_apply_rules_stops_without_a_dob_format -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/graph.py tests/test_graph.py
git commit -m "feat: 图在生成期套规则并处理生日格式缺失" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: 面板规则区 + `/run` 载荷

**Files:**
- Modify: `agent/console.html`
- Test: `tests/test_console_page.py`、`tests/test_console_js.py`、`tests/console_js_driver.js`

**Interfaces:**
- Consumes: Task 2 的 `rules` 载荷。
- Produces: 勾选才发 `rules`；不勾时正文没有这个键。

- [ ] **Step 1: 写失败测试（静态 + JS 驱动）**

`tests/test_console_page.py` 加：

```python
def test_the_run_form_has_rule_checkboxes_and_dob_format():
    page = CONSOLE.read_text(encoding="utf-8")   # 用文件里已有的读取方式
    for element_id in ("rulePhone02", "rulePhone07", "ruleDob",
                       "runDobFormat", "rulePassword"):
        assert 'id="%s"' % element_id in page, element_id
    assert "function runRulesPayload" in page
    body = re.search(r"function runPayload\(\).*?\n  \}", page, re.S).group(0)
    assert "runRulesPayload()" in body
    assert '"rules"' in body
```

`tests/console_js_driver.js` 的 `runScenario` 里，在设置 `runForm` 之后加：

```js
  const rr = payload.run.rules || {};
  el("rulePhone02").checked = (rr.phone_prefixes || []).indexOf("02") >= 0;
  el("rulePhone07").checked = (rr.phone_prefixes || []).indexOf("07") >= 0;
  el("ruleDob").checked = !!rr.dob;
  el("runDobFormat").value = (rr.dob && rr.dob.format) || "";
  el("rulePassword").checked = rr.password === "strong";
```

`tests/test_console_js.py` 加一个 `run-rules` 场景（复用现有 `RUN_FORM` / `NEW_JOB` / `SUBMITTED_SAY`）：

```python
def _run_rules_payloads():
    return {"scenario": "run", "search": "",
            "run": dict(RUN_FORM, rules={
                "phone_prefixes": ["02", "07"],
                "dob": {"random": True, "format": "DD/MM/YYYY"},
                "password": "strong"}),
            "responses": {"/run": [{"body": {"job_id": NEW_JOB,
                                             "say": SUBMITTED_SAY}}]}}


def test_run_payload_carries_rules(tmp_path):
    out = _drive(tmp_path, scenario="run-rules")
    body = json.loads(out["runBody"])
    assert body["rules"] == {"phone_prefixes": ["02", "07"],
                             "dob": {"random": True, "format": "DD/MM/YYYY"},
                             "password": "strong"}
```

并在 `_drive` 的 scenario 分派里加：

```python
    elif scenario == "run-rules":
        payload = _run_rules_payloads()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_console_page.py::test_the_run_form_has_rule_checkboxes_and_dob_format -q`
Expected: FAIL（找不到 `rulePhone02`）

- [ ] **Step 3: 实现**

`agent/console.html` 在 `runMode` 那个 `.field` 之后加：

```html
              <div class="field">
                <label>规则（勾了就按我们的规则现编，不取任务资料）</label>
                <label><input type="checkbox" id="rulePhone02"> 手机号 02 开头</label>
                <label><input type="checkbox" id="rulePhone07"> 手机号 07 开头</label>
                <label><input type="checkbox" id="ruleDob"> 生日随机（18–68 岁）</label>
                <label><input type="checkbox" id="rulePassword"> 密码强随机（8+ 大小写数字特殊字符）</label>
                <div id="runDobFormatField" hidden>
                  <input type="text" id="runDobFormat" autocomplete="off"
                         spellcheck="false" placeholder="DD/MM/YYYY（可空 = 按页面识别）">
                </div>
                <p class="why">勾了就<b>完全不看任务资料</b>；02/07 同勾 = 每趟随机一个号段，
                  同一趟所有 phone 字段用同一个号。生日格式空着就按探路记下的页面格式。</p>
              </div>
```

`console.html` 的 JS 里加：

```js
  function runRulesPayload() {
    var out = {};
    var phones = [];
    if (document.getElementById("rulePhone02").checked) { phones.push("02"); }
    if (document.getElementById("rulePhone07").checked) { phones.push("07"); }
    if (phones.length) { out.phone_prefixes = phones; }
    if (document.getElementById("ruleDob").checked) {
      out.dob = { "random": true };
      var fmt = document.getElementById("runDobFormat").value.trim();
      if (fmt) { out.dob.format = fmt; }
    }
    if (document.getElementById("rulePassword").checked) { out.password = "strong"; }
    return Object.keys(out).length ? out : undefined;
  }

  function syncDobFormat() {
    document.getElementById("runDobFormatField").hidden =
      !document.getElementById("ruleDob").checked;
  }
```

`runPayload` 的对象里加一行：

```js
      "rules": runRulesPayload(),
```

并在初始化段加：

```js
  document.getElementById("ruleDob").addEventListener("change", syncDobFormat);
  syncDobFormat();
```

（`JSON.stringify` 会丢掉 `undefined` ⇒ 不勾时正文没有 `rules`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_console_page.py::test_the_run_form_has_rule_checkboxes_and_dob_format tests/test_console_js.py -q`
Expected: PASS（JS 驱动若没有 node 就按 `siteforge-tests-not-mounted` 的规矩先拷 node 再跑）

- [ ] **Step 5: 提交**

```bash
git add agent/console.html tests/test_console_page.py tests/test_console_js.py tests/console_js_driver.js
git commit -m "feat: 面板规则勾选与生日格式" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: 端到端 + 回归

**Files:**
- Test: `tests/test_template.py`（或独立 `tests/test_rules_e2e.py`）
- Verify: 现有 `test_template.py` / `test_loop_template.py` / `test_graph.py` / `test_service.py` / `test_console_*`

- [ ] **Step 1: 加端到端用例**

在 Task 3 的 `test_rule_fills_ignore_form_data_and_share_values` 之上，再补一个**两条对照**：

```python
def test_rules_off_leaves_the_old_behavior(sandbox, tmp_path):
    ff = tmp_path / "form.json"
    ff.write_text(json.dumps({"phone": "0000000000", "password": "weak"}),
                  encoding="utf-8")

    def _target(label, sel):
        return {"text": None, "label": label, "role": None, "near": None,
                "selectors": [sel]}

    fills = [
        {"name": "phone", "source": "phone", "kind": "value", "label": "PhoneNumber",
         "target": _target("PhoneNumber", "input#phone")},
        {"name": "password", "source": "password", "kind": "value", "label": "Password",
         "target": _target("Password", "input#password")},
    ]
    steps = [{"action": "form", "fill": f["name"], "note": "填 %s" % f["label"]}
             for f in fills]
    prod = template.render("example-rule-off", "Thank you",
                           [{"name": "walk", "when": None, "steps": steps}], fills,
                           SAMPLE_PROVENANCE)
    module, _ = _load("run_rule_off", prod, sandbox)
    common = _stub(sandbox, diff={"actionable": True},
                   observe={"url": "https://example.test/", "actions": [], "fields": []})
    common.STATE.texts = ["Walk"] * 2 + ["Thank you"]

    assert module.Filler(WS, str(ff), "cid_1", "task_1", delay=(0, 0)).run() is True
    filled = {a[1]: a[2] for a in common.STATE.actions if a[0] == "form"}
    assert filled["input#phone"] == "0000000000"
    assert filled["input#password"] == "weak"
```

- [ ] **Step 2: 跑定向回归**

Run:
```bash
pytest tests/test_rules.py tests/test_service.py tests/test_graph.py \
       tests/test_template.py tests/test_loop_template.py \
       tests/test_console_page.py tests/test_console_js.py -q
```
Expected: 新用例通过；已知环境红（宿主缺 `langgraph`、本地 cdp `/json/list`）与本次改动无关。
在容器里跑 `tests/test_template.py` / `test_graph.py` / `test_graph.py` 相关用例时，
按 `siteforge-tests-not-mounted` 先 `docker cp tests/.`。

- [ ] **Step 3: 更新产物说明**

在 `docs/probes/2026-10-20-belong/README.md` 或新 probe 目录记一条：
规则勾选实现后，belong 的 DOB/密码改法由「手改产物」升级为「面板勾选」；
旧产物不受影响。

- [ ] **Step 4: 提交**

```bash
git add tests/test_template.py docs/probes
git commit -m "test: 规则勾选端到端与不勾回归" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## 执行后的验收

- 勾 `手机号 02`/`07`：产物 `FILLS[*].rule=phone_prefix`，10 位、同趟同值、不读 form_data。
- 勾生日：`date_format` 有值（手填/页面识别），18–68；识别不出 ⇒ `rule_needs_format`，不产 py。
- 勾密码：满足 `8+ 大写 小写 数字 !@#$%^&*`。
- 不勾：`/run` 正文、产物、运行行为与今天一致。
- 闸上/`PROVENANCE.rules` 能看到 `applied` 与 `unmatched`。
- `/run` 对未知规则 400。
