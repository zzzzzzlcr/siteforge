"""Task B2 ①：**配置 vs 复跑量到的页面事实** —— 差在哪一格。

## 这一份钉的是一条纪律（D2），不是几个函数

> **建议里出现的每一个值（label / placeholder / text），都必须能在
> 「复跑时量到的那份页面事实」里找到出处。**

找不到出处的建议，**一个字都不许提** —— 那一格要进「认不出」，不是进「建议」。

这条纪律不是新发明的，它与 `fix.py` 同源（「认不出就留空并说出来，绝不猜」：
往「州」里填一个人名比留空更坏）。D2 把它抬成了 B2 的判据。

## 为什么这一份能离线跑（而 `rerun` 那份不能）

`diff_config(config, facts)` 是个**纯函数**：给它一份配置、一份「页面上量到了什么」，
它只回答「哪几格对不上、页面上实际是什么」。**它不开浏览器、不起子进程、不调模型。**

⇒ 这正是「先做逻辑」那一条的落点：B2 里唯一必须真跑的部分被隔在 `rerun()` 里，
而**判断**这一半今天就能全套钉住。
"""
from __future__ import annotations

import copy
import sys
import types

import pytest

from agent import jsondiag


def _config():
    """`cvrefresh.com` 那份配置的**逐字复制**（2026-09-21 经 B1 的读口取回，实测）。

    用真形状当底稿的理由：D2 那条纪律只有在**真配置**上才量得出来 ——
    自己编一份简单的，量到的只是「我编的那份能被判对」。
    """
    return {
        "form_type": "magic_link",
        "site": "cvrefresh.com",
        "steps": [
            {"action": "wait", "max": 10, "min": 5},
            {"action": "click", "find": {"text": "Refresh my resume"}},
            {"action": "form",
             "field": {"label": "Email", "placeholder": "your@email.com", "type": "email"},
             "value": "{{random.email}}"},
            {"action": "click", "find": {"text": "Send magic link"}},
            {"action": "wait", "max": 10, "min": 5},
        ],
        "success": {"any": [{"body_contains": ["Check your email"]}]},
    }


def _facts(*, body_text="", actions=(), fields=()):
    """复跑时量到的那份页面事实（形状与 `observe` 交出来的一致）。"""
    return {
        "url": "https://cvrefresh.com/",
        "body_text": body_text,
        "actions": [{"text": t} for t in actions],
        "fields": [dict(f) for f in fields],
    }


# ── 对得上 ────────────────────────────────────────────────────────

def test_每一格都对得上就什么都不报():
    """配置里每一个可寻址的值都在事实里 ⇒ 没有建议、也没有「认不出」。

    ⚠️ 这条是**负控**：少了它，一个「不管三七二十一全报一遍」的实现
    也能让后面每一条通过。
    """
    facts = _facts(
        body_text="Refresh my resume Send magic link Check your email",
        actions=["Refresh my resume", "Send magic link"],
        fields=[{"label": "Email", "placeholder": "your@email.com", "type": "email"}],
    )

    got = jsondiag.diff_config(_config(), facts)

    assert got["changes"] == []
    assert got["unresolved"] == []


def test_不改原配置一个字():
    """`diff_config` 只读 —— 交出去的 `original` 是副本，改它不许动到调用方那份。"""
    config = _config()
    before = copy.deepcopy(config)
    facts = _facts(
        body_text="Refresh my resume Send magic link Check your email",
        actions=["Refresh my resume", "Send magic link"],
        fields=[{"label": "Email", "placeholder": "your@email.com", "type": "email"}],
    )

    got = jsondiag.diff_config(config, facts)
    got["original"]["steps"].append({"action": "click", "find": {"text": "我塞的"}})
    got["suggested"]["steps"][1]["find"]["text"] = "我改的"

    assert config == before


# ── 什么叫「对得上」—— 照执行器**量到的**匹配语义，不是我猜的 ──────

def test_写法不同不算对不上_执行器本来就是大小写不敏感子串匹配():
    """【我量的·2026-09-21】`element_finder.py:51`：

        elText.trim().toLowerCase().indexOf('<配置里那句>.toLowerCase()') !== -1

    ⇒ 执行器找元素是**大小写不敏感 + 子串**。
    配置写 `Refresh my resume`、页面上是 `Refresh My Resume` —— **照样找得到**，
    这一格**不该**被报成「对不上」。

    ⚠️ 这条是**防喊狼来了**：把这种格报成问题，人会被引去改一个本来没坏的配置。
    （这一条原先写成「大小写变了 ⇒ 给建议」，那是**我把匹配语义猜错了** ——
    猜错的方向是**多报**，所以它必须有一条自己的用例钉着。）
    """
    facts = _facts(
        body_text="Refresh My Resume Send magic link Check your email",
        actions=["Refresh My Resume", "Send magic link"],
        fields=[{"label": "Email", "placeholder": "your@email.com", "type": "email"}],
    )

    got = jsondiag.diff_config(_config(), facts)

    assert got["changes"] == []
    assert got["unresolved"] == []


def test_配置里那句只是页面那句的一部分_也算找得到():
    """子串匹配的另一半：配置写 `Send magic link`、页面上是 `Send magic link now`
    ⇒ `indexOf` 命中 ⇒ 对得上。**这一格也不许报。**"""
    facts = _facts(
        body_text="Refresh my resume Send magic link now Check your email",
        actions=["Refresh my resume", "Send magic link now"],
        fields=[{"label": "Email", "placeholder": "your@email.com", "type": "email"}],
    )

    got = jsondiag.diff_config(_config(), facts)

    assert got["changes"] == []
    assert got["unresolved"] == []


def test_配置那句只出现在正文里_也算找得到():
    """★【我量的·2026-09-21】`element_finder.py:44-56` 的找法：

        var el = document.querySelectorAll('*');      ← **所有元素**，不只按钮
        ... if (best) { if (!bestIsInteractive) { 往上走最多 5 层找一个可点的爹 } }

    ⇒ 「不在我列出的可点文字里」**不等于**「找不到」：执行器在**任何**元素里找，
    找到之后自己往上爬到可点的那个。所以判「对得上」必须也看正文。

    ⚠️ 这条与 `test_按钮文字页面上整个没有了` 是**一对**：
    那条说「正文里都没有 ⇒ 报」，这条说「正文里有 ⇒ 别报」。
    只有前一条的话，一个「凡是没出现在可点列表里就报」的实现也能全绿 ——
    而那会把**大量好配置**报成坏的（页面上真正在找的往往是包着按钮的那层 div 的文字）。
    """
    facts = _facts(
        body_text="Refresh my resume Send magic link Check your email",
        actions=["Send magic link"],      # ← 故意**不**把那一句放进可点列表
        fields=[{"label": "Email", "placeholder": "your@email.com", "type": "email"}],
    )

    got = jsondiag.diff_config(_config(), facts)

    assert got["unresolved"] == [], "正文里明明有，却报成找不到：%s" % got["unresolved"]
    assert got["changes"] == []


# ── 真的对不上：提不出建议的那一类 ──────────────────────────────

def test_按钮文字页面上整个没有了_摆出实际看到的但不给建议():
    """配置要点的那个按钮，页面上量到的可点文字里**一个都不含**它。

    ★ D2 的另一半：**提不出建议就一个字都不许提。**
    这一格进 `unresolved`（把量到的候选原样摆出来给人看），**不进 `changes`**。
    """
    facts = _facts(
        body_text="Resume refresh Send magic link Check your email",
        actions=["Resume refresh", "Send magic link"],
        fields=[{"label": "Email", "placeholder": "your@email.com", "type": "email"}],
    )

    got = jsondiag.diff_config(_config(), facts)

    assert got["changes"] == [], "提不出建议却给了建议：%s" % got["changes"]
    assert len(got["unresolved"]) == 1, got["unresolved"]
    u = got["unresolved"][0]
    assert u["path"] == "steps[1].find.text"
    assert u["was"] == "Refresh my resume"
    # 摆出来的是**页面上真实量到的**那几条，不是配置里的、也不是编的。
    assert u["saw"] == ["Resume refresh", "Send magic link"]
    # 另一格（Send magic link）对得上，不许混进来。
    assert all(x["path"] != "steps[3].find.text" for x in got["unresolved"])


def test_成功判据那段文字没出现_如实报出来():
    """`success.any[].body_contains` 里的文字，复跑那一趟的正文里**没有**。

    这一格与前面几条**不是一回事**：它不是「配置指错了目标」，而是
    **「这一趟按它自己的判据也没算成功」** —— 复盘的人要靠它把
    「走通了但没认出来」和「根本没走通」分开。
    """
    facts = _facts(
        body_text="Refresh my resume Send magic link",
        actions=["Refresh my resume", "Send magic link"],
        fields=[{"label": "Email", "placeholder": "your@email.com", "type": "email"}],
    )

    got = jsondiag.diff_config(_config(), facts)

    assert got["changes"] == []
    paths = [u["path"] for u in got["unresolved"]]
    assert "success.any[0].body_contains[0]" in paths, paths
    u = [x for x in got["unresolved"] if x["path"].startswith("success.")][0]
    assert u["was"] == "Check your email"


# ── 复跑：**这一层一次模型都不许调** ────────────────────────────

def _fake_executor_module(seen):
    """一个假的 `json_executor` 模块 —— 只为把「构造时收到了什么」记下来。"""
    class FakeExecutor:
        def __init__(self, config, profile, cdp, **kw):
            seen["config"] = config
            seen["kw"] = kw

    mod = types.ModuleType("json_executor")
    mod.JSONExecutor = FakeExecutor
    return mod


def test_复跑构造执行器时模型是关的(monkeypatch):
    """★★ 控制者 2026-09-21 定的：**复跑这一层一次模型都不许调。**

    生产那条路模型是活的（【我量的·2026-09-21】`json_pipeline.py:1245` 真建
    `openai.OpenAI(...)`，`json_executor.py:155` 的 `AI_RECOVERY_BUDGET = 10`
    就是它的预算）—— **诊断跑不许继承这个**，否则「修一次配置」的钱
    与「重新探索一个站」是一个量级，B2 存在的理由就没了。

    这条钉的是**构造那一刻**，不是「记得别传」：
      · `llm_client` 必须是显式的 `None`；
      · 配置里必须带上 `no_llm`（执行器自己那道闸，`:445` / `:1753`）。
    两道都上 —— 只上一道的话，将来有人加一条新的 LLM 路径就会静默漏进来。
    """
    from agent import jsonrerun

    seen = {}
    monkeypatch.setitem(sys.modules, "json_executor", _fake_executor_module(seen))

    jsonrerun.build_executor({"steps": [{"action": "wait"}]}, cdp=object())

    assert seen["kw"].get("llm_client", "**没传这一格**") is None, seen["kw"]
    assert seen["config"].get("no_llm") is True, seen["config"]


def test_复跑不改调用方那份配置(monkeypatch):
    """`build_executor` 要往配置里塞 `no_llm` —— 但**不许动调用方那一份**。"""
    from agent import jsonrerun

    seen = {}
    monkeypatch.setitem(sys.modules, "json_executor", _fake_executor_module(seen))
    config = {"steps": [{"action": "wait"}]}

    jsonrerun.build_executor(config, cdp=object())

    assert "no_llm" not in config, "把调用方那份配置改了：%s" % config


# ── 复跑的结果怎么取回来：**量不到不许读成空** ──────────────────

def _line(summary=None, facts=None):
    import json as _json
    return jsondiag.RESULT_MARK + _json.dumps(
        {"summary": summary or {}, "facts": facts or {}}, ensure_ascii=False)


def test_从复跑输出里取回摘要与页面事实():
    """子进程把结果打在**一行**上（日志照旧随便打），这一层把它捞出来。"""
    out = "\n".join([
        "[JSON] Step 1/5: wait",
        "[JSON] Step 2/5: click",
        _line(summary={"status": "failed", "failed_step": 2,
                       "failed_action": "click", "error": "Step 2 click failed"},
              facts={"url": "https://cvrefresh.com/", "actions": [{"text": "Resume refresh"}]}),
    ])

    got = jsondiag.parse_rerun(out)

    assert got["summary"]["failed_step"] == 2
    assert got["summary"]["error"] == "Step 2 click failed"
    assert got["facts"]["actions"] == [{"text": "Resume refresh"}]


def test_复跑没吐出结果行_要抛而不是给个空结果():
    """★ 本仓的老纪律：**「量不到」不许读成「量到了、是空的」**。

    子进程崩了 / 被超时杀了 / 执行器 import 失败 —— 这几种情况下
    stdout 里**没有**那一行。此时**必须抛**：回一个
    `{"summary": {}, "facts": {}}` 会让下游把「没跑成」读成
    「跑了、页面上什么都没有」，而那正是这个仓库反复栽的形状
    （`fix-script` 把查不到的站显示成「近 2 天没有失败 ✓」）。
    """
    with pytest.raises(jsondiag.RerunUnmeasured) as exc:
        jsondiag.parse_rerun("[JSON] Step 1/5: wait\n[JSON] 然后它就死了\n")

    assert "没有" in str(exc.value) or "没吐" in str(exc.value), str(exc.value)


def test_结果行不是合法JSON_也要抛():
    """半行 JSON（进程被杀在半路）与「没有那一行」是同一件事：**没量着**。"""
    with pytest.raises(jsondiag.RerunUnmeasured):
        jsondiag.parse_rerun(jsondiag.RESULT_MARK + '{"summary": {"failed_st')


def test_同一行出现两次_取最后那一次():
    """执行器可能被跑了两趟（重试）—— 后一次才是这趟的结局。"""
    out = "\n".join([
        _line(summary={"failed_step": 1}),
        _line(summary={"failed_step": 4}),
    ])

    assert jsondiag.parse_rerun(out)["summary"]["failed_step"] == 4


# ── 复跑：起子进程那一层 ────────────────────────────────────────

def _fake_proc(stdout="", returncode=0, stderr=""):
    import subprocess
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_没给浏览器地址就抛_一个进程都不起():
    """没有 `ws_url` 就没有页面可复跑 —— **免费的检查**，一个进程都不该起。"""
    called = []

    def runner(cmd, **kw):
        called.append(cmd)
        return _fake_proc()

    with pytest.raises(jsondiag.RerunUnmeasured) as exc:
        jsondiag.rerun({"steps": []}, ws_url="", runner=runner)

    assert called == [], "没页面可跑却起了进程：%s" % called
    assert "没有" in str(exc.value) or "没给" in str(exc.value), str(exc.value)


def test_复跑的环境里模型钥匙被摘掉():
    """★★ 除了构造那两道闸，**环境也摘一遍**。

    子进程默认继承环境 —— 而生产那台机器上是有 `OPENAI_API_KEY` 的。
    构造那两道闸挡的是「执行器主动去调」，这一道挡的是「它从环境里捡到一把钥匙」。
    三道失效方式互不相同，所以三道都要。
    """
    seen = {}
    out = _line(summary={"status": "success"},
                facts={"url": "https://cvrefresh.com/", "actions": [], "fields": [],
                       "body_text": ""})

    def runner(cmd, **kw):
        seen["cmd"] = cmd
        seen["env"] = kw.get("env") or {}
        return _fake_proc(stdout=out)

    monkeypatch_env = {
        "OPENAI_API_KEY": "sk-should-not-travel",
        "OPENAI_BASE_URL": "https://api.example.com",
        "PATH": "/usr/bin",
    }
    got = jsondiag.rerun({"steps": []}, ws_url="ws://127.0.0.1:9222/devtools/page/X",
                         runner=runner, env=monkeypatch_env)

    assert got["summary"]["status"] == "success"
    blob = " ".join("%s=%s" % kv for kv in seen["env"].items()).upper()
    assert "OPENAI_API_KEY" not in blob, seen["env"]
    assert "OPENAI_BASE_URL" not in blob, seen["env"]
    assert "SK-SHOULD-NOT-TRAVEL" not in blob, seen["env"]
    # PATH 这类还得留着，不然子进程起不来。
    assert seen["env"].get("PATH") == "/usr/bin"


def test_复跑的子进程崩了_抛而不是给空结果():
    """退出码非 0 —— 与「没有结果行」是同一件事：**没量着**。"""
    def runner(cmd, **kw):
        return _fake_proc(stdout="", returncode=1, stderr="ModuleNotFoundError: json_executor")

    with pytest.raises(jsondiag.RerunUnmeasured) as exc:
        jsondiag.rerun({"steps": []}, ws_url="ws://127.0.0.1:9222/x", runner=runner)

    assert "json_executor" in str(exc.value), str(exc.value)


# ── 页面事实怎么从浏览器里量出来 ────────────────────────────────

def test_页面事实从原始量测里归一出来():
    """`cdp.eval` 回的是**一条字符串**（页面里 `JSON.stringify` 的结果）——
    这一层把它读成 `diff_config` 要的那份事实。

    归一的三件事：空白的丢掉、缺的格补成空、类型不对的**说出来**（不是硬塞）。
    """
    from agent import jsonrerun

    raw = '{"url":"https://cvrefresh.com/","body_text":"  Refresh my resume  ",'
    raw += '"actions":[{"text":"Refresh my resume"},{"text":"   "},{"text":"Send magic link"}],'
    raw += '"fields":[{"label":"Email","placeholder":"your@email.com","type":"email"},'
    raw += '{"label":"","placeholder":"","type":""}]}'

    got = jsonrerun.facts_from(raw)

    assert got["url"] == "https://cvrefresh.com/"
    assert got["body_text"] == "Refresh my resume"
    # ⚠️ 全是空白的那些**不摆进来**：它们不是「页面上有一个空按钮」，
    #    是「量到了个寂寞」—— 摆进去会让 diff 的 `saw` 里多出假候选。
    assert got["actions"] == [{"text": "Refresh my resume"}, {"text": "Send magic link"}]
    assert got["fields"] == [{"label": "Email", "placeholder": "your@email.com",
                              "type": "email"}]


def test_the_result_marker_is_one_thing_the_two_sides_agree_on():
    """★ 子进程**打**的记号与调用方**捞**的记号必须是同一个（一处定义）。

    【2026-09-21 实测】`jsonrerun.py` 里那个名字**根本没定义** ⇒ 它在**成功那一刻**炸
    （`NameError`）⇒ 一趟**量着了的**复跑被读成「没量着」（退出码 1、结果行压根没打出来）。
    这一条走一遍**真的**：按子进程那样拼出那一行 → 交给调用方那条解析路。
    """
    import json as _json

    from agent import jsondiag, jsonrerun

    assert jsonrerun.RESULT_MARK == jsondiag.RESULT_MARK, "记号两处定义、已经漂开了"
    line = jsonrerun.RESULT_MARK + _json.dumps(
        {"summary": {"status": "success"}, "facts": {"url": "https://x/"}}, ensure_ascii=False)
    got = jsondiag.parse_rerun(line)
    assert got["summary"]["status"] == "success" and got["facts"]["url"] == "https://x/", got


def test_the_executor_own_helper_wins_the_path_race():
    """★ 两个目录里各有一个 `common.py`，执行器要的是**它自己那个**。

    【2026-09-21 实测】顺序反了 ⇒ `AttributeError: no attribute 'wait_page_stable'` ⇒
    复跑退出码 3，而执行器记下的那几句（`click: element not found` / `deferred unfilled`）
    **全是假账**。⚠️ 在**子进程**里量：`import common` 会进 `sys.modules`，
    在本进程里量会把后面所有用例的环境弄脏。
    """
    import pathlib
    import subprocess

    code = (
        "import sys; sys.path.insert(0, '/company/siteforge')\n"
        "from agent import jsonrerun\n"
        "jsonrerun._prepare_paths()\n"
        "from common import CDPHelper\n"
        "print(sys.path[0])\n"
        "print(sys.modules['common'].__file__)\n"   #: ⚠️ 比**文件路径**（类自己不带 `__file__`，两边模块名都叫 `common`）
        "print(hasattr(CDPHelper, 'wait_page_stable'))\n")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-600:]
    first, where, has = proc.stdout.strip().splitlines()
    assert first.rstrip("/").endswith("form_executor"), "执行器目录没排在 sys.path[0]：%r" % first
    assert where.endswith("form_executor/common.py"), "from common 捡到的不是执行器那个：%r" % where
    assert has.strip() == "True", proc.stdout


def test_the_facts_payload_that_is_wrapped_twice_still_decodes():
    """`cdp.eval` 吐的被包**两层**（与 `selftest._cdp_eval` 同一个坑）。

    【2026-09-21 实测】只解一层 ⇒ 复跑退出码 3 + 「页面事实不是一份对象（是 str）」，
    而那一趟**其实量着了**（事实就在那层字符串里）。
    """
    import json as _json

    from agent import jsonrerun

    inner = {"url": "https://x/", "body_text": "hi", "actions": [], "fields": []}
    wrapped = _json.dumps(_json.dumps(inner))          # 包两层：外层是字符串、内层才是那份对象
    got = jsonrerun.facts_from(wrapped)
    assert got["url"] == "https://x/" and got["body_text"] == "hi", got
    #: ★ **生产那条路给的是对象**（`CDPHelper.eval` 自己解过一层了）——
    #: 【2026-09-21 实测】原先这里第一句 `str(raw)` 把 dict 变成 Python repr ⇒ 当场炸：
    got = jsonrerun.facts_from(inner)
    assert got["url"] == "https://x/" and got["body_text"] == "hi", got
    #: 一层（没包）也照旧要认；空串 / 半句照旧要**抛**（不许回一份空事实）
    assert jsonrerun.facts_from(_json.dumps(inner))["url"] == "https://x/"
    for bad in ("", "null", '{"url": "https://x/"'):
        try:
            jsonrerun.facts_from(bad)
        except jsonrerun.FactsUnmeasured:
            continue
        raise AssertionError("这个输入该抛没抛：%r" % bad)


def test_the_rerun_subprocess_really_starts(tmp_path):
    """★ **真起一次那个子进程**（不是 stub）—— 这类 bug 只有真跑才抓得到。

    2026-09-21 实测：`agent/jsonrerun.py` 里 `sys` 没 import ⇒ `main()` 第一句
    （`logging.basicConfig(stream=sys.stderr)`）就 NameError ⇒ 复跑子进程退出码 1 ⇒
    `/jsondiff` 与「预检写回」**全废**（502 / 409），而**用例全绿**：
    服务那一层的用例把复跑那根线 stub 掉了，从没真起过这个进程。

    这一条给一个**注定连不上**的窗口：要的是「**干净的失败**」（一句人话 + 非 0 退出码），
    不是 Traceback —— 有 Traceback 就说明它连门都没进去。
    """
    import json
    import pathlib
    import subprocess

    from agent import jsonrerun

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"form_type": "magic_link", "site": "example.test",
                               "steps": [], "success": {"any": [{"body_contains": ["x"]}]}}),
                   encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(pathlib.Path(jsonrerun.__file__)),
         "--config", str(cfg), "--ws-url", "ws://127.0.0.1:1/devtools/browser/dead"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0, "连不上的窗口居然报了成功：%r" % proc.stdout[-300:]
    assert "Traceback" not in proc.stderr, proc.stderr[-800:]
    assert "NameError" not in proc.stderr, proc.stderr[-800:]
    #: 它得**说清**是没量着（而不是一声不吭地挂掉）
    assert ("没量着" in proc.stderr) or ("量不到" in proc.stderr), proc.stderr[-500:]
    """`cdp.eval` 回了空串 / 半句 JSON（页面正在导航、执行上下文没了）——
    **这是「没量着」**，与「页面上什么都没有」是两件事。"""
    from agent import jsonrerun

    with pytest.raises(jsonrerun.FactsUnmeasured):
        jsonrerun.facts_from("")

    with pytest.raises(jsonrerun.FactsUnmeasured):
        jsonrerun.facts_from('{"url": "https://x/", "body_te')
