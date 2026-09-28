"""「在跑的时候说的那句话，要能**改产物**」—— 账本那一族的改稿那双手（★ 2026-09-28）。

用户报的形状（原话）：「在跑的时候说的那句话，为什么传不到改稿那一步」。查下来是**两截断的**：

  ① **服务没接线**：`Deps.write` 这根缝一直在（`graph._draft` 把 `spec` + `feedback` 递给它，
     `feedback` 里装着人在闸上说的话），但生产里跑的是默认那个恒等翻译器
     （`graph._writer_from_journey`：`return spec`）⇒ 那句话一路进了提示词，**产物一个字节不变**。
     ⇒ 钉在这儿：服务真的把手接上（`test_the_service_hands_the_model_writer_to_the_graph`）。
  ② **就算接上，循环型的产物也不读 `states`**（怎么走由 `render_loop` 现算）——
     模型只改 `states` 的话会**静默丢**（闸上看着像改过了）。
     ⇒ 钉在这儿：那一族能改的几格（`fills` / `loop`）**真的落到产物上**，
     而且模型**看得见现值**（看不见就只能猜一个数）。

⚠️ 这一份里「改一格」改的是 `fallback`（**值从哪来**：先看 `--form-file` 的 `source`，
没有就用 `fallback` 里的一个）—— 不是 `value`：渲染器**不读** `value` 那个键，
写它等于没改（那种「假装改过」正是这一片要防的形状）。

这一份只量「话 → 表 → 产物字节」这条链；不量模型的判断力（那是提示词的事，钉不住）。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, fix, graph, service  # noqa: E402
from test_graph import SITE, SUCCESS, URL, _brief, _build, _deps, _drive  # noqa: E402
from test_service_artifact import _client, _factory, _out_dir  # noqa: E402

SAID = "年龄有时候会提前能修复自适应吗"


def _decl(value="SW1A 1AA"):
    """一份**真能用**的 `fills` 声明（`target.selectors` 非空 —— 渲染时的硬要求）。"""
    return {"source": "postcode", "kind": "postcode", "label": "Postcode",
            "target": {"selectors": ["input#postcode"]}, "fallback": [value]}


# ─────────────────────── 模型那一边：提示词与回话的形状 ───────────────────────


def test_the_human_words_and_the_whole_table_go_to_the_model():
    """人说的话**逐字**进提示词，整张表也逐字摆出来（概括一次，细节就没了）。"""
    spec = {"site": SITE, "success_text": SUCCESS, "success_urls": "Thank you",
            "states": [{"name": "q1"}], "fills": {"postcode": _decl()}}
    user = fix.states_user(spec, hints=[SAID], diagnosis="第 2 遍卡在第 6 步",
                           violations=["第 12 行 —— 手拼 JS"])

    assert SAID in user
    assert "第 2 遍卡在第 6 步" in user
    assert "第 12 行 —— 手拼 JS" in user
    #: 表在提示词里（**逐字**：现值看得见，才谈得上「改成多少」）
    assert "SW1A 1AA" in user and "input#postcode" in user
    #: 系统提示点明了「改它就是改产物」这件事，以及那一格只认哪几个键
    assert "只交一个 JSON 对象" in fix.STATES_SYSTEM
    for key in fix.LOOP_KEYS:
        assert key in fix.STATES_SYSTEM, "`loop` 里那一格没写进提示词：%s" % key
    #: ★ 值走 `source` / `fallback`，`value` 那个键渲染器不读（写了等于没改）——
    #: 提示词里要点名，否则模型最自然的改法就是往里塞一个 `"value"`（下一遍自测原样挂）。
    assert "`fallback`" in fix.STATES_SYSTEM
    assert '"value"' in fix.STATES_SYSTEM and "不读" in fix.STATES_SYSTEM


def test_the_model_only_changes_the_keys_it_mentions():
    """只写要改的那几格 ⇒ 其余原样（省略 = 不改，不是清空）。"""
    spec = {"site": SITE, "success_text": SUCCESS, "states": [{"name": "q1"}],
            "fills": {"postcode": _decl()}}
    reply = '```json\n%s\n```' % json.dumps(
        {"fills": {"postcode": _decl("SW1A 9AA")}, "note": "按人说的改了邮编那格"})
    new, notes = fix.states_from_reply(spec, reply)

    assert new["fills"]["postcode"]["fallback"] == ["SW1A 9AA"]
    assert new["states"] == spec["states"], "没提的那一格被动了"
    assert new["success_text"] == SUCCESS
    assert notes == ["按人说的改了邮编那格"]


@pytest.mark.parametrize("reply", [
    "这件事我做不到（一段散文，不是 JSON）",
    '```json\n[1, 2, 3]\n```',
    '{"fills": ["age"], "note": "x"}',
    '{"loop": {"谁都不知道的键": 1}, "note": "x"}',
    '{"fills": {"age": "35"}}',                    # 少了 `note`
])
def test_a_reply_that_cannot_be_read_changes_nothing_at_all(reply):
    """读不动 / 有一样不合法 ⇒ **整份原样退回 + 原因**（不许「能认的那几格先收下」）。

    半份收下的后果是**闸上看到「改过了」而其中一半根本没进去** —— 那种静默分岔是这套工具
    反复付过代价的坏法（`graph._patch_draft` 的「逐条人话」也是为它写的）。
    """
    spec = {"site": SITE, "success_text": SUCCESS, "states": [{"name": "q1"}],
            "fills": {"postcode": _decl()}}
    new, notes = fix.states_from_reply(spec, reply)

    assert new == spec, "退回来的不是原样：%r" % (new,)
    assert notes and all(str(x).strip() for x in notes), "退回来却没说为什么"
    assert any(("JSON" in x or "note" in x or "只认" in x or "得是" in x) for x in notes), notes


def test_a_fenced_json_reply_is_read_even_without_the_language_tag():
    """```` ```json ```` 与 ```` ``` ```` 两种围栏都认（⚠️ 不复用 `_CODE_FENCE`：它只认 py）。"""
    spec = {"site": SITE, "fills": {}, "states": []}
    body = json.dumps({"fills": {"postcode": _decl("SW1A 9AA")}, "note": "改了"})
    for text in ("```json\n%s\n```" % body, "```\n%s\n```" % body, body):
        new, notes = fix.states_from_reply(spec, text)
        assert new["fills"]["postcode"]["fallback"] == ["SW1A 9AA"], text
        assert notes == ["改了"], text


# ──────────────── 图那一边：接上之后，产物**真的变了** ────────────────


def test_what_the_human_said_at_the_gate_changes_the_artifact(tmp_path):
    """★ 用户报的那件事本身：闸上说的那句话，**交付出去的那份 py 字节真的变了**。

    桩照真的那双手做（`fix.states_from_reply`）——但**只在人说了之后**才改：
    第一版是账本里的值，人说完之后那一版才换成他说的。于是「改的是那句话，不是随便谁」也钉住了。
    """
    def write(spec, feedback, n):
        reply = '```json\n{"note": "照原样：这张表里没有能改它的格子"}\n```'
        if "SW1A 9AA" in " ".join(feedback["hints"]):
            reply = '```json\n%s\n```' % json.dumps(
                {"fills": {"postcode": _decl("SW1A 9AA")}, "note": "按人说的改了邮编那格"})
        return fix.states_from_reply(spec, reply)[0]

    deps, rec = _deps(write=write)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path),
                    reply=lambda p: ({"action": "revise", "note": "邮编那一格填 SW1A 9AA"}
                                     if p["step"] == "draft" else "continue"))

    assert out["end_reason"] == "delivered", out.get("end_note")
    assert "邮编那一格填 SW1A 9AA" in rec.write[0]["feedback"]["hints"], "人说的话没进 draft"
    assert rec.write[0]["spec"]["fills"]["postcode"]["fallback"] == [{"random": "postcode"}], \
        "第一版就该是账本里那个值（不是人说的那个）"
    #: 交出去的是**改过的那一版**（拿产物里的字面量量，不看眼看）
    src = pathlib.Path(out["py_path"]).read_text(encoding="utf-8")
    _, fills = fix._literals(src)
    #: 自测那一遍跑的就是这一版（交付时重渲只多一段出身/自测记录，见 `graph._deliver`）
    _, tested = fix._literals(rec.tested_src)
    assert tested["postcode"]["fallback"] == ["SW1A 9AA"], "自测跑的还不是那一版"


def _funnel_journey():
    """一份**单页漏斗**的账本（3 个状态共用一个地址 ⇒ 循环型那条分派会命中）。"""
    steps = [{"state": "q1", "action": "form", "note": "填好了「Postcode」",
              "target": {"text": None, "label": "Postcode", "role": None, "near": None,
                         "selectors": ["input#postcode"], "above_fold_only": False},
              "result": {"ok": True, "selector": "input#postcode",
                         "fill": {"name": "postcode", "source": "postcode", "kind": "value",
                                  "label": "Postcode", "value": "SW1A 1AA",
                                  "fallback": [{"random": "postcode"}]}}}]
    pages = [{"name": "q%d" % i, "when": {"url_contains": "quiz"}, "url": URL,
              "title": "t%d" % i} for i in (1, 2, 3)]
    return browser_agent.Journey(steps=steps, notes=["走完了：填了一个邮编"],
                                 stop_reason="model_done",
                                 final_answer="成功时页面上会出现「Thank you」",
                                 pages=pages)


def test_the_loop_shape_lets_the_writer_move_its_own_knobs(tmp_path):
    """循环型那一族：模型能改的是 `fills` 与 `loop` 那几格，**而且它看得见现值**。

    ⚠️ 为什么非要有这一条：循环型的产物**不读 `states`**（怎么走由 `render_loop` 现算）。
    看不见现值时，「把 max_steps 调大」这种话它只能**猜一个数**（编值）——
    所以图那一边是**先把形态定下来，再把带现值的表交给它**。
    """
    seen: list = []

    def write(spec, feedback, n):
        seen.append(spec["loop"]["max_steps"])
        return fix.states_from_reply(
            spec, '{"loop": {"max_steps": 90}, "note": "按人说的把步数调大"}')[0]

    deps, rec = _deps(journey=_funnel_journey(), write=write)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path, success_urls="Thank you"))

    assert out["end_reason"] == "delivered", out.get("end_note")
    assert out["shape"] == "loop", out.get("shape_why")
    assert seen and seen[0] >= 60, "模型没看到现值（它只能猜一个数）"
    assert out["loop_spec"]["max_steps"] == 90, "它改的那一格没落到这一份旋钮上"
    src = pathlib.Path(out["py_path"]).read_text(encoding="utf-8")
    assert "MAX_STEPS = 90" in src, "旋钮改了，产物却没变"


def test_a_loop_key_that_the_renderer_does_not_know_is_dropped(tmp_path):
    """`loop` 那一格是**白名单**：渲染器不认的键一个都不许进那一份旋钮（第二道闸）。"""
    def write(spec, feedback, n):
        return {**spec, "loop": {"max_steps": 90, "谁都不知道的键": 1}}

    deps, rec = _deps(journey=_funnel_journey(), write=write)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, _brief(tmp_path, success_urls="Thank you"))

    assert out["end_reason"] == "delivered", out.get("end_note")
    assert out["loop_spec"]["max_steps"] == 90
    assert "谁都不知道的键" not in out["loop_spec"], out["loop_spec"]


# ──────────────── 服务那一边：这根线**真的接上了**（用户那件事的根） ────────────────


def _deps_captured(box: list):
    """把「这一趟服务拼出来的 `Deps`」记下来的假图（图本身还是 `_factory` 那张）。"""
    inner = _factory()

    def factory(brief, deps):
        box.append(deps)
        return inner(brief, deps)

    return factory


def _run_once(app, tmp_path):
    return app.post("/run", json={"url": URL, "goal": "走到 Thank you", "success_text": SUCCESS,
                                  "site": SITE, "out_dir": str(_out_dir(tmp_path)),
                                  "ws_url": "ws://127.0.0.1:9222/devtools/page/ABC",
                                  "form_file": str(tmp_path / "form.json")})


def test_the_service_hands_the_model_writer_to_the_graph(tmp_path, monkeypatch):
    """★ 有模型 key 时，服务必须把**真的那双**手交给图 —— 而且要问得对人说的话。

    这条一红就等于回到了那一天：人在闸上说话、面板回「记下了」，而产物一个字节不变。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-用例里不真打模型")
    seen: dict = {}
    said = '```json\n%s\n```' % json.dumps(
        {"fills": {"postcode": _decl("SW1A 9AA")}, "note": "按人说的改了邮编那格"})

    def fake_loop(system, user, specs, exec_tool, **kw):
        seen["system"], seen["user"] = system, user
        return [{"round": 1, "tool_calls": [], "usage": {}, "elapsed_ms": 1, "content": said}]

    monkeypatch.setattr(service.llm, "run_tool_loop", fake_loop)
    box: list = []
    r = _run_once(_client(tmp_path, _deps_captured(box)), tmp_path)
    assert r.status_code == 202, r.text

    deps = box[-1]
    assert deps.write is not graph._writer_from_journey, "服务没把改稿那双手接上"
    new = deps.write({"site": SITE, "fills": {"postcode": _decl()}, "states": []},
                     {"hints": [SAID], "violations": [], "diagnosis": None})
    assert new["fills"]["postcode"]["fallback"] == ["SW1A 9AA"]
    assert SAID in seen["user"], "人说的话没进提示词"
    assert "现在的表" in seen["user"], "整张表没摆给模型"
    assert seen["system"] == fix.STATES_SYSTEM


def test_without_a_model_key_the_service_does_not_pretend(tmp_path, monkeypatch):
    """没有模型 key ⇒ **连这一格都不传**（默认那个恒等翻译器）—— 不许编一个假的改稿器。

    编一个的后果：打回时闸上看着像「改过了」，而产物一个字节不变 ——
    那正是用户问的那件事的形状。
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    box: list = []
    r = _run_once(_client(tmp_path, _deps_captured(box)), tmp_path)
    assert r.status_code == 202, r.text

    deps = box[-1]
    assert deps.write is graph._writer_from_journey, \
        "没 key 时接了一双假的手（那会让「改不了」看起来像「改过了」）"
