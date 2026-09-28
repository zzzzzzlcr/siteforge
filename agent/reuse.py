"""跨站复用：把两份产物摊成**骨架**比一比，回答「这一份能不能拿去那个站用」。

为什么要它（用户 2026-09-28 的原话：「现在有很多不同站点怀疑脚本一样，想加个验证」）：
许多站在跑**同一套漏斗**（换个域名而已），平台上已经有一份能用的脚本 —— 但「看着一样」
不能当证据。三处只要有一处钉死，换站之后就是**整组静默跳过**（本项目最贵的那类失败：
跑完不报错、其实一步没做）：

  · 判据（`when.url_contains`）钉着**旧域名**；
  · 循环型的**到达判据**（`PROVENANCE.arrive_url`）钉着旧的到达地址 —— 前置会 `give_up`；
  · 成功判据只认**旧文案 / 旧网址那一截**。

所以这里只做一件事：**把差异逐条摆出来**，并明确哪几条是「挡路的」。它不生成新脚本、
不上传、不碰面板（「验证通过我自己去后台加对应的」——那一手是人的）。

两个形状（今天产物只有这两种，混着比 = 不同族，它们连「怎么走路」都不一样）：

  · `template` —— 有 `STATES`（按状态一组组走）：骨架 = 判据 + 步骤序列 + `FILLS`；
  · `loop`     —— 循环型（读页面 → 有什么做什么）：骨架 = 前置 + 到达判据 + `FILLS`；
  · `legacy`   —— 线上那一族手写脚本（没有 `STATES`/`FILLS`）：只能比**词表**
    （选择器串 + 页面文字串的 Jaccard）—— 这条**是估的，不是判的**，`say` 里会说明。

⚠️ 读源码**不走 exec**（与 `fix._literals` 同一条规矩）：坏 py / 动态构造的值一律当
「读不出来」，不抛、不猜。
"""

from __future__ import annotations

import ast
import re

from agent import fix

#: 选择器长相的串（`#a > b.c[attr="x"]` 这种）—— 只在**词表**那一档用（legacy）。
_SELECTOR_RE = re.compile(r"[#.][A-Za-z][-\w]*(?:[ >+~][-\w#.=\"'\[\]()]+)*")
#: 词表那档收「像页面文字」的串：够长、带空格、不是 URL/模板话术。
_TEXT_MIN = 8


def _parse(src):
    try:
        return ast.parse(str(src or ""))
    except (SyntaxError, ValueError):        # 坏 py = 读不出来
        return None


def _module_literal(tree, name):
    """模块级那个名字的字面量（读不动就 `None` —— 不猜）。"""
    for node in tree.body:
        targets = None
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not targets:
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        try:
            return ast.literal_eval(node.value)
        except Exception:                     # noqa: BLE001 —— 动态构造 ⇒ 读不出来
            return None
    return None


def _fields(fills) -> list:
    """`FILLS` → 骨架里的字段表：`[(格名, kind, 第一个选择器)]`（保序）。"""
    out = []
    for name, one in (fills or {}).items():
        if not isinstance(one, dict):
            continue
        target = one.get("target") or {}
        sels = [s for s in (target.get("selectors") or []) if str(s or "").strip()]
        out.append((str(name), str(one.get("kind") or ""), str(sels[0] if sels else "")))
    return out


def _one_step(step) -> tuple:
    """一步的骨架：`(动作, 目标文字, 角色)` —— **选择器与文字分开比**（见 `compare`）。"""
    if not isinstance(step, dict):
        return ("", "", "")
    target = step.get("target") or {}
    return (str(step.get("action") or ""), str(target.get("text") or ""),
            str(target.get("role") or ""))


def _one_selector(step) -> str:
    if not isinstance(step, dict):
        return ""
    sels = [s for s in ((step.get("target") or {}).get("selectors") or [])
            if str(s or "").strip()]
    return str(sels[0] if sels else "")


def _template_skeleton(states) -> tuple:
    steps, judges = [], []
    for state in states if isinstance(states, (list, tuple)) else []:
        if not isinstance(state, dict):
            continue
        when = state.get("when") or {}
        judges.append((str(state.get("name") or ""), str(when.get("url_contains") or ""),
                       tuple(str(t) for t in (when.get("text_contains") or []))))
        for step in state.get("steps") or []:
            steps.append(_one_step(step) + (_one_selector(step),))
    return steps, judges


def _loop_skeleton(prov) -> list:
    steps = []
    for step in (prov or {}).get("pre_steps") or []:
        steps.append(_one_step(step) + (_one_selector(step),))
    return steps


def _clues(src: str) -> dict:
    """词表那一档：选择器串 + 页面文字串（只给 `legacy` 用，**是估的不是判的**）。"""
    try:
        tree = ast.parse(str(src or ""))
    except (SyntaxError, ValueError):
        return {"selectors": set(), "texts": set()}
    sels, texts = set(), set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        value = node.value.strip()
        if not value or len(value) > 400:
            continue
        sels.update(m.group(0).strip() for m in _SELECTOR_RE.finditer(value)
                    if len(m.group(0).strip()) >= 4)
        if (len(value) >= _TEXT_MIN and " " in value and "http" not in value.lower()
                and not value.startswith("[")):
            texts.add(value)
    return {"selectors": sels, "texts": texts}


#: 网址判据那两族的名字 —— **两族都要读**：模板产物写 `SUCCESS_URLS`（见
#: `template.render`），老脚本写 `SUCCESS_URL_MARKERS` / `SUCCESS_URL_MARK`
#: （`fix.success_urls_from_source` **只认后者**，所以这里补上前者）。
_TEMPLATE_URL_NAMES = ("SUCCESS_URLS", "SUCCESS_URL")


def _success_urls(tree, src: str) -> list:
    out = list(fix.success_urls_from_source(src))
    for name in _TEMPLATE_URL_NAMES:
        got = _module_literal(tree, name)
        values = [got] if isinstance(got, str) else list(got or [])
        for url in values:
            if isinstance(url, str) and url.strip() and url not in out:
                out.append(url)
    return out


def skeleton(src: str) -> dict:
    """一份 py 的骨架（人话：这份脚本**要做什么**、**认什么**、**填什么**）。

    读不出来（坏 py / 动态构造）⇒ `kind: "unknown"` + `why` 里说为什么，**不抛**。
    """
    text = str(src or "")
    out = {"kind": "unknown", "site": "", "success_texts": [], "success_urls": [],
           "fields": [], "steps": [], "judges": [], "arrive": "", "entry": "",
           "clues": {"selectors": set(), "texts": set()}, "why": []}
    tree = _parse(text)
    if tree is None:
        out["why"] = ["`ast.parse` 过不了 —— 这不是一份能跑的 py（不猜它是什么形状）"]
        return out

    out["site"] = str(_module_literal(tree, "SITE") or "")
    out["success_texts"] = fix.success_texts_from_source(text)
    out["success_urls"] = _success_urls(tree, text)
    states, fills = fix._literals(text)
    prov = _module_literal(tree, "PROVENANCE") or {}
    if not isinstance(prov, dict):
        prov = {}
    out["fields"] = _fields(fills)

    if isinstance(states, list) and isinstance(fills, dict):
        out["kind"] = "template"
        out["steps"], out["judges"] = _template_skeleton(states)
        out["entry"] = str(prov.get("entry_url") or "")
    elif isinstance(fills, dict) or _module_literal(tree, "READ_PAGE_JS"):
        out["kind"] = "loop"
        out["steps"] = _loop_skeleton(prov)
        out["arrive"] = str(prov.get("arrive_url") or "")
        out["entry"] = str(prov.get("entry_url") or "")
    else:
        out["kind"] = "legacy"
        out["why"] = ["没有 `STATES`/`FILLS`（线上那一族手写脚本）—— 只能比词表，"
                      "不是逐条判的骨架"]
    out["clues"] = _clues(text)
    return out


def _host(url: str) -> str:
    """`https://a.b/c?q` → `a.b`（没有 host 就空串）。"""
    text = str(url or "").strip()
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", text)
    return (match.group(1) if match else "").lower()


def _judged_hosts(skel: dict) -> list:
    """这份骨架里**钉住的域名**（判据 / 到达 / 成功网址）—— 换站时挡路的就是它们。"""
    found = []
    for candidate in ([skel.get("arrive") or ""] + list(skel.get("success_urls") or [])
                      + [j[1] for j in skel.get("judges") or []]):
        host = _host(candidate)
        if host and host not in found:
            found.append(host)
    return found


def portability(cand_src: str, new_url: str) -> dict:
    """这份产物拿去**新站**能不能直接用（只答「站点专属那几处挡不挡路」）。

    ⚠️ 它**不判**「这一趟跑得通」—— 那是真跑那一手的事（`--run`）。这里只把
    「肯定挡路」的那几条摆出来：判据钉着别的域名、到达判据钉着别的地址、成功判据
    只认别的网址。**没有挡路项 ≠ 能跑通**，只是「没有一眼可见的钉子」。

    ★ 2026-09-28（拿真产物试出来的一个假阳性）：同一趟流程**自己会跳到自家另一个域名**
    （实测 travelandleisure 那份：文章页 `www.travelandleisure.com` → 落地页
    `www.travelandleisurego.com`，两处都在判据里，那是**它自己的路**）。
    ⇒ 判据只能是「**新站的域名，一个都没出现在判据里**」才算挡路；只要有一个落在新站上，
    其他那些就是流程自带的跳转域名 —— 只**说明**、不算挡路。
    （反面：本该报的绝不放过 —— 一个都不匹配时全列出来。）
    """
    skel = skeleton(cand_src)
    new_host = _host(new_url)
    notes = []
    if not new_host:
        notes.append("没给新站地址（或它读不出域名）⇒ **判不了**站点专属那几处 —— "
                     "这不是「没问题」。")
        return {"host": "", "blocking": [], "notes": notes, "say": notes}
    hosts = _judged_hosts(skel)

    def _same(one: str) -> bool:
        return (one == new_host or new_host.endswith("." + one)
                or one.endswith("." + new_host))

    matched = [h for h in hosts if _same(h)]
    foreign = [h for h in hosts if not _same(h)]
    blocking = []
    if hosts and not matched:
        for host in foreign:
            blocking.append({
                "what": "判据钉着别的域名",
                "cand": host, "new": new_host,
                "say": "产物里钉着 `%s`（判据/到达/成功网址之一），而新站 `%s` "
                       "**一个判据都没落在它上面** ⇒ 换站后那一组会**静默跳过**、"
                       "或者前置直接 `give_up`。" % (host, new_host)})
    elif foreign:
        notes.append("判据里另有这些域名：%s —— 流程**自己会跳过去**（实测就是这么走的），"
                     "不算挡路。" % "、".join("`%s`" % h for h in foreign))
    if skel.get("site") and skel.get("site").lower() not in new_host.lower():
        notes.append("`SITE=%r` 与 `%s` 对不上 —— 它只进日志与产物文件名，**不挡路**，"
                     "但要改名免得上报时看着像另一个站。" % (skel["site"], new_host))
    if not blocking:
        notes.append("站点专属那几处**没有钉死别的域名**（`SITE` 除外）—— 挡路的钉子看不见，"
                     "但仍要以真跑为准。")
    return {"host": new_host, "blocking": blocking, "notes": notes,
            "say": [b["say"] for b in blocking] + notes}


def _const_diffs(a: dict, b: dict) -> list:
    """两边的**站点专属常量**差在哪（逐条摆出来，不问它挡不挡路）。"""
    out = []
    if (a.get("site") or "") != (b.get("site") or ""):
        out.append({"what": "SITE（站名）", "cand": a.get("site"), "new": b.get("site"),
                    "blocking": False, "say": "站名不同：%r vs %r（只进日志）"
                    % (a.get("site"), b.get("site"))})
    for key, label in (("success_texts", "成功文案"), ("success_urls", "成功网址")):
        if list(a.get(key) or []) != list(b.get(key) or []):
            out.append({"what": label, "cand": str(a.get(key)), "new": str(b.get(key)),
                        "blocking": True,
                        "say": "%s不同：%r vs %r —— 这一条**必须**在新站上真验一遍"
                        % (label, a.get(key), b.get(key))})
    ha, hb = _judged_hosts(a), _judged_hosts(b)
    if ha != hb:
        out.append({"what": "钉住的域名", "cand": ha, "new": hb, "blocking": True,
                    "say": "两边钉的域名不同：%s vs %s"
                    % (ha or "（一个都没有）", hb or "（一个都没有）")})
    return out


def compare(a_src: str, b_src: str) -> dict:
    """两份产物**骨架**比一比 → `{verdict, diffs, same, say}`。

    三档（`verdict`）：`能直接搬` / `同族，但要改 N 处` / `不同族`。
    不同族时 `diffs` 里那一条就是**第一处分歧**（第几步、差在哪）—— 不硬凑相似度分数。
    """
    a, b = skeleton(a_src), skeleton(b_src)
    if a["kind"] == "unknown" or b["kind"] == "unknown":
        side = "第一份" if a["kind"] == "unknown" else "第二份"
        why = (a["why"] or b["why"]) or ["读不出骨架"]
        return {"verdict": "不同族",
                "diffs": [{"what": "读不出骨架", "blocking": True,
                           "say": "%s读不出骨架：%s" % (side, "；".join(why))}],
                "same": [], "say": ["%s读不出骨架 —— 比不了。" % side]}
    if a["kind"] != b["kind"]:
        said = ("形状不同：%s vs %s —— 连「怎么走路」都不一样，不能互搬。"
                % (a["kind"], b["kind"]))
        return {"verdict": "不同族",
                "diffs": [{"what": "形状", "blocking": True, "say": said}],
                "same": [], "say": [said]}
    if a["kind"] == "legacy":
        return _compare_clues(a, b)

    diffs = _const_diffs(a, b)
    same = []
    steps_a, steps_b = a["steps"], b["steps"]
    if [s[:3] for s in steps_a] != [s[:3] for s in steps_b]:
        first = next((i for i in range(max(len(steps_a), len(steps_b)))
                      if steps_a[i:i + 1] != steps_b[i:i + 1]), 0)
        one = steps_a[first][:3] if first < len(steps_a) else ("（没有这一步）", "", "")
        two = steps_b[first][:3] if first < len(steps_b) else ("（没有这一步）", "", "")
        diffs.append({"what": "步骤序列", "blocking": True,
                      "say": "第 %d 步起不一样：%s vs %s（总步数 %d vs %d）"
                      % (first + 1, one, two, len(steps_a), len(steps_b))})
    else:
        same.append("步骤序列一致（%d 步）" % len(steps_a))
        diff_sel = [i for i in range(len(steps_a)) if steps_a[i][3] != steps_b[i][3]]
        if diff_sel:
            one, two = steps_a[diff_sel[0]][3], steps_b[diff_sel[0]][3]
            diffs.append({"what": "选择器", "blocking": True,
                          "say": "有 %d 步的首选选择器不同；第 %d 步：%r vs %r"
                          % (len(diff_sel), diff_sel[0] + 1, one, two)})
        else:
            same.append("每步的首选选择器都一致")
    if [f[:2] for f in a["fields"]] != [f[:2] for f in b["fields"]]:
        diffs.append({"what": "字段表", "blocking": True,
                      "say": "要填的格子不同：%s vs %s"
                      % ([f[0] for f in a["fields"]], [f[0] for f in b["fields"]])})
    else:
        same.append("字段表一致（%d 格：%s）" % (len(a["fields"]), [f[0] for f in a["fields"]]))
    if a["judges"] and b["judges"]:
        ja = [(j[0], _host(j[1]), j[2]) for j in a["judges"]]
        jb = [(j[0], _host(j[1]), j[2]) for j in b["judges"]]
        if ja != jb:
            first = next((i for i in range(min(len(ja), len(jb))) if ja[i] != jb[i]), 0)
            diffs.append({"what": "判据", "blocking": True,
                          "say": "判据组第 %d 条起不一样：%s vs %s（组数 %d vs %d）"
                          % (first + 1,
                             ja[first] if first < len(ja) else "（没有）",
                             jb[first] if first < len(jb) else "（没有）",
                             len(ja), len(jb))})
        else:
            same.append("判据组一致（%d 条，域名也一样）" % len(ja))
    blocking = [d for d in diffs if d.get("blocking")]
    verdict = "能直接搬" if not blocking else ("同族，但要改 %d 处" % len(blocking))
    return {"verdict": verdict, "diffs": diffs, "same": same,
            "say": [d["say"] for d in diffs]}


def _compare_clues(a: dict, b: dict) -> dict:
    """`legacy` 那一档：只能比词表 —— **说清楚这是估的**。"""
    sa, sb = a["clues"]["selectors"], b["clues"]["selectors"]
    ta, tb = a["clues"]["texts"], b["clues"]["texts"]
    share_s = len(sa & sb) / max(1, len(sa | sb))
    share_t = len(ta & tb) / max(1, len(ta | tb))
    diffs = []
    if share_s < 0.3 or share_t < 0.15:
        diffs.append({"what": "词表", "blocking": True,
                      "say": "词表差得远：选择器重合 %.2f、文字重合 %.2f —— "
                             "**看着不是同一套漏斗**" % (share_s, share_t)})
    say = ["⚠️ 这两份是 `legacy` 形状（没有 `STATES`/`FILLS`）—— 下面是**词表**比出来的，"
           "是估的不是判的；要真结论只有在新站上真跑。",
           "选择器重合 %.2f（%d vs %d 条）；文字重合 %.2f（%d vs %d 条）"
           % (share_s, len(sa), len(sb), share_t, len(ta), len(tb))]
    if sa & sb:
        say.append("共有的选择器样例：%s" % ", ".join(sorted(sa & sb)[:6]))
    return {"verdict": "不同族" if diffs else "看着同族（词表，需真跑确认）",
            "diffs": diffs, "same": [], "say": say}
