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

第三件（2026-09-29，交接《骨架还缺什么》§5）：`same_generation(rows, sources)` ——
**按平台分组（同一 `config_id`）做同代体检**：同组内该同代（testid 词汇 / 判据形态 /
成功判据一致），不同代就喊出**是哪几家、差在哪几条**；分组数据与各站源码都是**注入的**，
拿不到就**如实说拿不到**（见那个函数的 docstring）。

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

# ── 跨站同代体检（《骨架还缺什么》§5）：四个**形状**读口 ─────────────────────
#:
#: 判据只读**形状**（用了哪几条通道 / 哪一族元素标记），不读站点专属的字面值 ——
#: 同代的两站域名与文案本来就不同，拿字面值比会把健康的一组喊成一锅粥。
#:
#: 元素标记：`data-testid="continue-button"` 与 `[data-testid=age-18-24]`
#: **两种写法都认**（带引号 / 不带引号；值取到第一个引号/空白/前缀数字为止）。
_TESTID_RE = re.compile(r"""data-testid\s*=\s*["']?([A-Za-z][-\w]*)""")
#: testid 值里**每站的实例号**抹掉（`age-18-24` → `age`）—— 不然同代两站会因为
#: 题目编号不同被判成两代（`image-button-1` 那种同日增删也会变）。
_TESTID_TAIL_RE = re.compile(r"(?:[-_]\d+)+$")
#: 判据/状态走的**通道**。四条各自对着被真站逼出来的一种写法：
#:   网址     —— 成功/到达判据钉在地址上（`SUCCESS_URL_MARKERS` / `SUCCESS_URLS`）；
#:   testid   —— 「走到哪一屏」靠 `data-testid` 读（2026-09 平台改版那一代）；
#:   hash状态 —— 状态写在地址 hash 的 base64 里（平台新版问卷）；
#:   文字     —— 判据靠页面正文/文案（老脚本那一代）。
_URL_MARK_RE = re.compile(r"SUCCESS_URL")
_HASH_RE = re.compile(r"location\.hash|\batob\s*\(|\bbtoa\s*\(")
_TEXT_MARK_RE = re.compile(r"text_contains|innerText|textContent")

#: 一维的名字 ↔ `_generation` 里那一格（差异句按这个顺序摆）。
_DIMENSIONS = (("形状", "family"), ("testid 词汇", "testids"),
               ("判据形态", "judges"), ("成功判据", "success"))


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


# ═══════ 跨站同代体检（《骨架还缺什么》§5）═══════════════════════════════════
#
# 为什么要有这一关（2026-09-29 交接 §5 的真事）：funchatt 与 thisromance / asiatalks /
# sparkydates 同属**一个 config 组**，而平台的新版问卷（`<button data-testid="age-*" /
# "image-button-*" / continue-button`，状态写在 hash 的 base64 里）早在 09-17/09-18 就
# 给另一个组迁过了 —— funchatt 那一组**漏在外面十来天**，失败是**稳定**的，而发现它的
# 方式是人开窗手工撞见。这一关就是「同组应当同代，不同代就喊」，把手工那一撞变成一句机器话。
#
# ⚠️ **分组数据是注入的，不是这一层去取的**（与 `Deps.patch_source` 那条「没接上就停下
# 并点名」同一规矩）：分组在 `/api/quest/formConfig/sites`（`data.sites`：
# 每条 `{site, config_id, status}`，站点清单 + config 号那两列）。**读它要走网络、
# 要读 token、要给每个站再拉一份源码** —— 那不是纯比对的活，所以这一层只吃**已经取好的**
# 输入：`rows` 是那份清单，`sources` 是 `{站点: 源码}`。**拿不到就如实说拿不到**
# （`connected: False` + 「没检查」），**不许**退化成「没检查却看着像检查过了」。
#
# 【我量的·2026-09-29，真机】`GET https://fmr.3tkj.cn/api/quest/formConfig/sites`
# （带读 token `FMR_AGENT_TOKEN`）回 `data.sites`：**196 个站 / 116 个 config 组**；
# 例：#93 组（asiatalks/sparkydates/thisromance）与 #66 组（funchatt/callyourdate/…）。
# 同一批站用 `formScript?site=` 拉源码，**同组内的确同代**（#66 那族 14 处 `data-testid`、
# #93 那族 0 处）—— 所以「按 config 分组比这一族形状」这一刀是量出来的，不是猜的。


def _testid_vocab(src: str) -> tuple:
    """这一份产物认的 testid 词汇（**按实例号归口、排好序**）—— 空 tuple = 一个都不用。"""
    out = set()
    for match in _TESTID_RE.finditer(str(src or "")):
        word = _TESTID_TAIL_RE.sub("", match.group(1)).rstrip("-_")
        if word:
            out.add(word)
    return tuple(sorted(out))


def _generation(src: str) -> dict:
    """一份产物的「这一代」指纹：**形状 + testid 词汇 + 判据形态 + 成功判据**。

    逐格都是**形状**（用了哪几条通道 / 哪一族元素标记），**不是**站点专属的字面值
    （域名、成功文案）—— 同代两站的字面值本来就不同，拿字面值比会凭空喊一锅粥。
    坏 py 读不出骨架时 `family` 是 `"unknown"`，其余格照量（那三个正则读的是**文本**，
    不吃 AST）—— 所以「源码本身坏掉了」这一件事也会在 `family` 上现形。
    """
    skel = skeleton(src)
    text = str(src or "")
    judges = []
    if skel["success_urls"] or skel["arrive"] or _URL_MARK_RE.search(text):
        judges.append("网址")
    if _TESTID_RE.search(text):
        judges.append("testid")
    if _HASH_RE.search(text):
        judges.append("hash状态")
    if (_TEXT_MARK_RE.search(text) or skel["success_texts"]
            or any(j[2] for j in skel["judges"])):
        judges.append("文字")
    success = []
    if skel["success_urls"]:
        success.append("网址")
    if skel["success_texts"]:
        success.append("文字")
    return {"family": skel["kind"], "testids": _testid_vocab(text),
            "judges": tuple(judges), "success": tuple(success)}


def _gen_key(one: dict) -> tuple:
    return (one["family"], one["testids"], one["judges"], one["success"])


def _one_value_say(value) -> str:
    """一格的值 → 人话（**空**要说成「一个都不用」，不是留白）。"""
    if isinstance(value, tuple):
        return "、".join(value) if value else "（一个都不用）"
    return str(value)


def _dim_say(name: str, per_site: list) -> str:
    """一维上**分成几拨** → 一句人话：**逐家点名**，不说分数、不凑相似度。"""
    buckets: dict = {}
    for site, value in per_site:
        buckets.setdefault(value, []).append(site)
    parts = ["%s 用 %s" % ("、".join(sites), _one_value_say(value))
             for value, sites in sorted(buckets.items(),
                                        key=lambda kv: (-len(kv[1]), str(kv[0])))]
    return "「%s」分成 %d 拨：%s" % (name, len(buckets), "；".join(parts))


def same_generation(rows, sources=None) -> dict:
    """按平台分组（同一 `config_id`）做**同代体检** —— 同组该同代，不同代就喊。

    `rows`（**注入的分组**）：`/api/quest/formConfig/sites` 的 `data.sites`，
       每条 `{"site": …, "config_id": …, "status": …}`。**整份拿不到（`None`/空）
       ⇒ 如实说「拿不到分组」**，绝不装作检查过。
    `sources`（**注入的源码**）：`{站点: 这份产物的 py/配置文本}`，按 `site` 对上上面那条。
       某个站没给 / 给的是空串 ⇒ 这一家**出声**说「读不到源码」，**不静默丢**。

    交回来：`{connected, checked, verdict, groups, say}`。
      · `connected` —— 分组数据到底接上没有（`False` = 这一关没跑）；
      · `checked`   —— 真比成了几组（`False` = 一组都没比成，**不是「都没问题」**）；
      · `verdict`   —— 一句人话结论；`say` 是给运营读的逐句（哪个组、哪几家、差在哪几条）。
    """
    if not isinstance(rows, (list, tuple)) or not rows:
        return {"connected": False, "checked": False, "verdict": "拿不到分组",
                "groups": [],
                "say": ["跨站同代体检 —— **分组数据拿不到**（一个字节都没进来）："
                        "这一次**没检查**，**不表示「没问题」**。",
                        "分组在 `/api/quest/formConfig/sites` 里（`data.sites`："
                        "每条 `{site, config_id, status}`）；把那份清单，连同每个站的源码，"
                        "一起传进来这一关才跑得起来。"]}
    srcs = sources if isinstance(sources, dict) else {}

    grouped: dict = {}
    ungrouped: list = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        site = str(row.get("site") or "").strip()
        if not site:
            continue
        cid = row.get("config_id")
        if cid is None:
            ungrouped.append(site)
        else:
            grouped.setdefault(cid, []).append(site)

    say: list = []
    if ungrouped:
        say.append("⚠️ %d 个站**没有 config 号**（分不进任何一组，这一关没管它们）：%s"
                   % (len(set(ungrouped)), "、".join(sorted(set(ungrouped)))))
    if not grouped:
        return {"connected": True, "checked": False, "verdict": "分组里没有成组的站",
                "groups": [],
                "say": say + ["清单读到了，可**没有一个站带 config 号** —— "
                              "分组这一关没有能比的对象（不是「都没问题」）。"]}

    out_groups: list = []
    compared = 0
    for cid, raw_sites in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        sites = sorted(set(raw_sites))
        gens: dict = {}
        missing: list = []
        for site in sites:
            src = srcs.get(site)
            if isinstance(src, str) and src.strip():
                gens[site] = _generation(src)
            else:
                missing.append(site)
        gsay: list = []
        head = "配置 %s 这一组 %d 家" % (cid, len(sites))
        if missing:
            gsay.append("⚠️ 有 %d 家**读不到源码**（这一关没拿它们比）：%s"
                        % (len(missing), "、".join(missing)))
        if len(gens) < 2:
            reason = ("一家都读不到源码" if not gens
                      else "只读到了 %s 一家" % "、".join(sorted(gens)))
            gsay.append("%s —— %s，**比不了**。" % (head, reason))
            out_groups.append({"config_id": cid, "sites": sites, "same": None,
                               "diffs": [], "say": gsay})
            continue
        compared += 1
        buckets: dict = {}
        for site in sorted(gens):
            buckets.setdefault(_gen_key(gens[site]), []).append(site)
        if len(buckets) == 1:
            gsay.append("%s（%s）**同一代** —— 不喊。" % (head, "、".join(sites)))
            out_groups.append({"config_id": cid, "sites": sites, "same": True,
                               "diffs": [], "say": gsay})
            continue
        gsay.append("⚠️ %s **不是同一代** —— 分成 %d 拨：%s。"
                    % (head, len(buckets),
                       "；".join("%s（%d 家）" % ("、".join(names), len(names))
                                 for names in buckets.values())))
        diffs = []
        for name, field in _DIMENSIONS:
            per_site = [(site, gens[site][field]) for site in sorted(gens)]
            if len({value for _, value in per_site}) > 1:
                diffs.append({"what": name, "say": _dim_say(name, per_site)})
        gsay.extend("  · " + one["say"] for one in diffs)
        out_groups.append({"config_id": cid, "sites": sites, "same": False,
                           "diffs": diffs, "say": gsay})

    shouted = [one for one in out_groups if one["same"] is False]
    uncompared = [one for one in out_groups if one["same"] is None]
    if shouted:
        verdict = "有 %d 组不同代" % len(shouted)
        say.append("⚠️ 比成的 %d 组里 %d 组**不同代** —— 见上面逐组的「哪几家 / 差在哪」。"
                   % (compared, len(shouted)))
    elif compared == 0:
        verdict = "没得比"
        say.append("一组都没比成（每家要么读不到源码，要么组里不足 2 家）—— "
                   "**没检查**，不是「没问题」。")
    else:
        verdict = "同代"
        say.append("比成的 %d 组都是同一代 —— 不喊。" % compared)
    if uncompared:
        say.append("⚠️ 另有 %d 组**比不了**（源码没接上 / 组里不足 2 家）—— "
                   "那几组**没检查**，不是「同一代」。" % len(uncompared))
    for one in out_groups:
        say.extend(one["say"])
    return {"connected": True, "checked": compared > 0, "verdict": verdict,
            "groups": out_groups, "say": say}
