"""Task B2：**照一份失败证据，看那份 JSON 配置差在哪一格**。

## 这条路的形状（`plan.md` §4 Task B2）

```
读一份配置（B1 的 form_config）
      ↓
真页面复跑一遍（生产那个 JSON 执行器，逐字同一份代码）
      ↓
配置 vs 复跑量到的页面事实 —— 逐格对照
      ↓
一份 diff（原配置 vs 建议配置）+ 证据
```

## ★★ 两条承重纪律（控制者拍板，实现者别改）

**D1 诊断靠真执行器复跑，不靠模型猜。** 执行器在真页面上跑一遍，
拿回「哪一步失败、失败动作、失败时页面长什么样」——**事实来自执行，不来自想象**。

**D2 建议必须来自「页面上量到的」，不许凭空造。**
建议里出现的每一个值，都必须能在复跑量到的页面事实里找到出处；
找不到出处 ⇒ **不许提这条建议**，如实说「这一格我认不出，没动它」。

## ⚠️ 「复跑」这一层**一次模型都不许调**（2026-09-21 控制者定的）

生产跑那份配置时模型是**活**的（`json_pipeline.py:1245` 真建了一个 `OpenAI(...)`，
`json_executor.py:155` 的 `AI_RECOVERY_BUDGET = 10` 就是它的预算）。
诊断跑**不许**继承这个 —— 复跑必须用 `llm_client=None` 构造，
而且它是一条**钉死的不变量**（见 `rerun` 的 `llm` 参数），不是「记得别传」。
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Optional

__all__ = ["RESULT_MARK", "RerunUnmeasured", "diff_config", "parse_rerun"]

#: 子进程把复跑结果打在**这一行**上（它自己的日志照旧随便打，这一层只捞这一行）。
#: 单开一个标记而不是「stdout 最后一行」：日志尾巴会跟着结果跑，
#: 而「取最后一行」在子进程被中途杀掉时会取到半句日志。
RESULT_MARK = "JSONDIAG_RESULT "


class RerunUnmeasured(Exception):
    """**复跑没量着** —— 没吐出结果行，或那一行读不成 JSON。

    ★ 它**不是**「跑了、结果为空」—— 两者处置完全不同：
    这个去查子进程 / 浏览器 / 执行器 import；那个才是「这一趟页面上什么都没有」。
    本仓有案底：把「查不到」显示成「没有失败 ✓」（`fix-script` 那个无效 key）。
    """


def _norm(s: Any) -> str:
    """**比较用**的归一：折叠空白 + 大小写不敏感。

    ⚠️ 归一**只用来判断「这是同一个东西吗」** —— 建议交出去的永远是
    **页面上量到的原样写法**（`Refresh My Resume`，不是 `refresh my resume`）。
    把归一后的值当建议写回去，就是把站点自己那句话改成了我的规范化习惯。
    """
    return " ".join(str(s or "").split()).casefold()


def _texts(rows: Iterable, key: str) -> list:
    """从一串对象里取某一格的非空文字（原样，不归一）。"""
    out = []
    for row in rows or []:
        val = str((row or {}).get(key) or "").strip()
        if val:
            out.append(val)
    return out


def _found(cands: Iterable[str], want: Any) -> bool:
    """页面上量到的候选里，有没有**含**着配置那句的。

    ★ **匹配语义照抄执行器，不是我定的** —— 【我量的·2026-09-21】
    `element_finder.py:51`：

        elText.trim().toLowerCase().indexOf('<配置里那句>.toLowerCase()') !== -1

    两条都在里面：**子串**（`indexOf`）+ **大小写不敏感**（两边都 `toLowerCase`）。
    ⇒ 配置写 `Refresh my resume`、页面上是 `Refresh My Resume` **照样找得到**；
    配置写 `Send magic link`、页面上是 `Send magic link now` **也照样找得到**。

    ⚠️ **比执行器严 = 喊狼来了**（把没坏的格报成问题，人被引去改一份好配置）。
    ⚠️ **比执行器松 = 漏报**。两边都不能漂，所以这一条要有一条自己的用例钉着。
    """
    w = _norm(want)
    return any(w in _norm(c) for c in cands)


def _judge(changes: list, unresolved: list, *, path: str, was: Any, cands: list,
           where: str, also_in: str = "", pick=None, apply=None):
    """判一个格：对得上就放过；有**确定性**候选就给建议；否则**如实说提不出**。

    - `cands`：**摆给人看的**候选（`saw` 就是它）。它该是「人能一眼扫过去」的东西，
      所以只放可点的文字，不放整页正文。
    - `also_in`：**判「找得到吗」时额外要搜的一段文字**（对点击目标来说就是正文）。
      ⚠️ 它与 `cands` **不是一回事**，别合并 ——【我量的·2026-09-21】
      执行器是在 `querySelectorAll('*')` 的**所有元素**里找、找到再往上爬到可点的爹
      （`element_finder.py:44-56`）⇒ 那段文字可能出现在一个**不在可点列表里**的元素上。
      只看 `cands` 就会**喊狼来了**；而把正文塞进 `cands` 又会让 `saw` 变成一堵墙。
    - `pick` 是「从候选里确定性挑出一个替代值」那一手。**默认没有** ——
      因为「挑最像的」不是这一层的活（D2：模型只做那件事，而这里**不调模型**）。
      只有那种「目标已经靠别的格认出来了、剩下的值是**读数**不是**猜**」的场合，
      才该传一个 `pick` 进来，而且它必须能说清凭什么。
    - `apply` 是「把建议写进 `suggested`」那一手（只有真要改时才调）。
    - `where` 是**出处**的人话（D2：`why` 里必须说清这个值从页面事实的哪一格来）。
    """
    if _found(cands, was):
        return
    needle = _norm(was)
    if needle and also_in and needle in _norm(also_in):
        return
    hit = pick(cands, was) if pick is not None else None
    if hit is None:
        # ★ D2 的另一半：**提不出建议就别说**。把量到的候选原样摆出来给人看，
        #   但绝不从里面硬挑一个塞进 `changes`。
        unresolved.append({
            "path": path, "was": was, "saw": list(cands),
            "why": "页面上量到的%s里，没有含「%s」的" % (where, was),
        })
        return
    apply(hit)
    changes.append({
        "path": path, "was": was, "now": hit,
        "why": "页面上量到的%s是「%s」" % (where, hit),
    })


def diff_config(config: Any, facts: Any) -> dict:
    """配置 vs 复跑量到的页面事实 → 哪几格对不上、页面上实际是什么。

    返回 `{"changes": [...], "unresolved": [...], "original": {...}, "suggested": {...}}`。

    - `changes`：**能确定性给出建议**的格（`path` / `was` / `now` / `why`），
      `now` 这个值一定**出自 `facts`**，`why` 说清出自哪一格。
    - `unresolved`：对不上、**但提不出建议**的格（`path` / `was` / `saw` / `why`）——
      把量到的候选原样摆出来，人自己看。**这一格绝不进 `changes`。**

    ⚠️ **只读**：`original` / `suggested` 都是**副本**（改它们动不到调用方那份）。
    ⚠️ **不调模型、不开浏览器、不起子进程** —— 纯函数。
    """
    original = copy.deepcopy(config)
    suggested = copy.deepcopy(config)
    changes: list = []
    unresolved: list = []

    facts = facts or {}
    action_texts = _texts(facts.get("actions"), "text")
    labels = _texts(facts.get("fields"), "label")
    placeholders = _texts(facts.get("fields"), "placeholder")
    kinds = _texts(facts.get("fields"), "type")
    body = str(facts.get("body_text") or "")

    for i, step in enumerate(suggested.get("steps") or []):
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "")

        if action in ("click", "select"):
            find = step.get("find")
            if not isinstance(find, dict) or not find.get("text"):
                continue
            _judge(changes, unresolved,
                   path="steps[%d].find.text" % i, was=find["text"],
                   cands=action_texts, where="可点的文字", also_in=body,
                   apply=lambda hit, f=find: f.__setitem__("text", hit))

        elif action == "form":
            field = step.get("field")
            if not isinstance(field, dict):
                continue
            for key, cands, where in (("label", labels, "字段名"),
                                      ("placeholder", placeholders, "占位文字"),
                                      ("type", kinds, "字段类型")):
                if not field.get(key):
                    continue
                _judge(changes, unresolved,
                       path="steps[%d].field.%s" % (i, key), was=field[key],
                       cands=cands, where=where,
                       apply=lambda hit, f=field, k=key: f.__setitem__(k, hit))

    # 成功判据：这几段文字**要出现在正文里**（子串，不是相等）。
    succ = suggested.get("success")
    for j, branch in enumerate((succ or {}).get("any") or []):
        if not isinstance(branch, dict):
            continue
        texts = branch.get("body_contains")
        if not isinstance(texts, list):
            continue
        for k, want in enumerate(list(texts)):
            if not want:
                continue
            path = "success.any[%d].body_contains[%d]" % (j, k)
            if _norm(want) in _norm(body):
                continue
            unresolved.append({
                "path": path, "was": want, "saw": [],
                "why": "复跑那一趟的正文里没有出现「%s」（这段文字是**成功判据**："
                       "它不出现，这一趟就判不成成功）" % want,
            })

    return {"changes": changes, "unresolved": unresolved,
            "original": original, "suggested": suggested}


def parse_rerun(stdout: str) -> dict:
    """从复跑子进程的 stdout 里取回结果。

    **取最后那一行**：执行器可能被跑了两趟（重试），后一次才是这趟的结局。

    取不到 ⇒ **抛 `RerunUnmeasured`**，绝不回一个空结果 —— 本仓老纪律：
    「量不到」不许读成「量到了、是空的」。回 `{"summary": {}, "facts": {}}`
    会让下游把「子进程崩了」读成「跑了、页面上什么都没有」。
    """
    found = None
    for line in str(stdout or "").splitlines():
        if line.startswith(RESULT_MARK):
            found = line[len(RESULT_MARK):]
    if found is None:
        raise RerunUnmeasured(
            "复跑没有吐出结果行（stdout 里没有以 `%s` 开头的那一行）—— 这是"
            "「**没量着**」，不是「跑了、结果为空」。多半是子进程崩了 / 被超时杀了 / "
            "执行器 import 失败：先看它的 stderr 与退出码。" % RESULT_MARK.strip())
    try:
        got = json.loads(found)
    except Exception as exc:
        raise RerunUnmeasured(
            "复跑的结果行读不成 JSON（很可能是进程被**杀在半路**、只写了半行）：%s。"
            "这与「没有那一行」是同一件事 —— **没量着**。" % exc)
    if not isinstance(got, dict):
        raise RerunUnmeasured(
            "复跑的结果行不是一份对象（是 %s）。" % type(got).__name__)
    return got


#: 复跑子进程最多跑多久（秒）。真站上一步可能等很久，但复跑是**有人等着看的**，
#: 不是无人工厂 —— 超时就当「没量着」，让人去看，别让它挂着。
RERUN_TIMEOUT = 300.0

#: 这些前缀开头的环境变量**不许**跟着复跑的子进程跑。
#: ★ 这是「0 模型调用」的**第三道闸**（前两道在 `jsonrerun.build_executor`）：
#: 构造那两道挡的是「执行器主动去调」，这一道挡的是「它从环境里捡到一把钥匙」。
#: ⚠️ 三道闸的失效方式互不相同 —— 所以三道都要，不能拿一道顶三道。
SCRUB_ENV_PREFIXES = ("OPENAI_", "ANTHROPIC_", "AZURE_OPENAI_", "DEEPSEEK_",
                      "MOONSHOT_", "DASHSCOPE_", "SILICONFLOW_", "GEMINI_",
                      "GOOGLE_API", "CLAUDE_")


def _scrubbed_env(env: Optional[dict] = None) -> dict:
    """子进程的环境：把模型钥匙摘掉，别的照旧（**全摘了它就跑不起来**）。"""
    src = os.environ if env is None else env
    out = {}
    for key, val in src.items():
        if any(str(key).upper().startswith(p) for p in SCRUB_ENV_PREFIXES):
            continue
        out[key] = val
    return out


def rerun(config: Any, *, ws_url: str, entry_url: Optional[str] = None,
          runner: Optional[Any] = None, env: Optional[dict] = None,
          timeout: float = RERUN_TIMEOUT) -> dict:
    """**在真页面上复跑一趟**那份配置（D1）→ `{"summary": …, "facts": …}`。

    - `runner` 是**注入点**（测试用）：默认 `subprocess.run`。
      它收 `(cmd, **kw)`，回的要有 `.returncode` / `.stdout` / `.stderr`。
    - `env` 也是注入点；真跑时默认取当前环境，**摘掉模型钥匙**（见 `SCRUB_ENV_PREFIXES`）。

    ⚠️ 这一层**不判断成败** —— 它只管把事实取回来。判断是 `diff_config` 的活。
    ⚠️ 出了任何岔子一律抛 `RerunUnmeasured`，**绝不回一个空结果**。
    """
    key = str(ws_url or "").strip()
    if not key:
        raise RerunUnmeasured(
            "复跑不了：没给**浏览器的地址**（`ws_url`）—— 没有页面就没得复跑。"
            "这是免费的检查，一个进程都没起。")

    with tempfile.TemporaryDirectory(prefix="jsondiag-") as tmp:
        cfg_path = os.path.join(tmp, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False)

        cmd = [sys.executable, str(pathlib.Path(__file__).with_name("jsonrerun.py")),
               "--config", cfg_path, "--ws-url", key]
        if entry_url:
            cmd += ["--entry-url", str(entry_url)]

        run = runner or subprocess.run
        try:
            proc = run(cmd, env=_scrubbed_env(env), timeout=timeout,
                       capture_output=True, text=True)
        except subprocess.TimeoutExpired as exc:
            raise RerunUnmeasured(
                "复跑超时（%.0f 秒）—— 这是「**没量着**」，不是「页面上什么都没有」。"
                "多半是页面卡住了或窗口没了。" % timeout) from exc

        if proc.returncode != 0:
            raise RerunUnmeasured(
                "复跑的子进程退出码 %s（不是 0）—— **没量着**。它的 stderr 末尾：%s"
                % (proc.returncode, (proc.stderr or "").strip()[-400:] or "（空）"))

        return parse_rerun(proc.stdout)
