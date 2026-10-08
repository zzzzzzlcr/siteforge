"""**IR 层**：把「一次成功的探路」变成一份**能编译的东西**，并在编译之前**校验它完整**。

为什么要单独有这一层（用户 2026-09-30 的诊断，原话）：

> 探路能走通，但「把一次成功探路压缩成可重复运行的 py」这一步不稳定 …… 核心瓶颈已经不在
> CDP，也不在 Agent 会不会操作页面，而是在 **Explore → Py 编译层** …… 我建议你立刻把链路
> 拆成 3 个明确阶段：Explore（只负责走通）→ IR / Trace Normalize（变成稳定中间表示）→
> Py Emit（只负责从 IR 生成 py）…… 只要你把「生成不出来」都塞进这 5 类，问题会瞬间清晰很多。

这一层在仓库里**原本是隐形的**：`Journey.states()` / `Journey.fills()` 算出来的东西**直接**
交给 `graph._draft` 去渲（`template.render`）。于是失败全叫「生成脚本失败」——而它们其实分别
卡在**不同的层**：

| 层 | 判据（这一层能不能过） | 谁算的 |
|---|---|---|
| `explore` | 这一趟**走到成功**了吗（`reached_success`）—— 没走到就没有 IR 可谈 | `graph._explore_reached_success` |
| `ir` | 账本→IR 这一步能不能**表达**（每组有判据、每组有可重放的步、没有「只有地址」的空门、`goto` 不许落在组中间） | **本模块** |
| `emit` | IR 完整的前提下渲得出来吗（契约检查 / 形态 / 成功判据在不在） | `template.render` + `lint` |
| `selftest` | 产物在真站上按扰动序列跑得过吗（**跑不起来 ≠ 跑不过**，两种分开报） | `selftest.run` |

⚠️ **本模块只做校验，不动 IR 本身** —— 「发现问题」与「修 IR」是两件事：这一段只回答
「这份 IR 能不能编译」，修不修、怎么修由上游（`browser_agent` 那几处）负责。
⚠️ **判下来是「不完整」时要说清是哪一层的哪一条**（`layer` + `code`），不要把机器码丢给运营：
`say` 那一格才是上屏的（人话），`code` 只用来归类与统计。

## 五类归因（用户点名要的那五类）

`code` 的前缀就是那五类之一（写在这儿，免得日后各处各叫）：

- `trace_incomplete` —— ① 轨迹不完整（某一步没记下来 / 没走到成功）
- `trace_ambiguous` —— ② 轨迹有歧义（同一动作多个候选 / 成功原因不确定 / 靠临时推理过的）
- `ir_unexpressible` —— ③ IR 无法表达（循环 / 随机题 / 动态分支 / 空的判据）
- `emit_bug` —— ④ Emitter Bug（IR 完整、稿出不来 / 语法 / 参数 / 模板）
- `selftest_failed` —— ⑤ py 能生成、重放失败（**含「自测没跑成」这一档，单独一个 code**）

（五类里前四类归本模块与 `emit` 那两处；`selftest` 那两档在 `selftest.py` / `graph` 那侧报。）
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 允许出现在产物里的动作（与骨架的契约同一张表 —— `template.render` 只认这五种）。
REPLAYABLE_ACTIONS = ("click", "form", "scroll", "goto", "wait")


@dataclass
class Problem:
    """IR 不完整的一条：**哪一层、哪一类、说给人听的那句话**。"""

    layer: str          # explore / ir / emit / selftest
    code: str           # 上面那五类之一（+ 更细的后缀）
    say: str            # 人话：哪一组、为什么、人能做什么
    name: str = ""      # 哪一组（有的话）
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"layer": self.layer, "code": self.code, "say": self.say,
                "name": self.name, **({"extra": self.extra} if self.extra else {})}


def _stable(url) -> str:
    """判「是不是同一个地址」用的**稳定前缀**：去掉 fragment / query、去掉尾斜杠、小写。

    与 `browser_agent._stable_url` 的前两步同一口径（那一步还丢「像轮换码的末段」——
    这里不必：它只用来数「同一个地址上有几个状态」）。
    """
    return str(url or "").split("#")[0].split("?")[0].rstrip("/").lower()


def _norm_text(text) -> str:
    """比名字前的归一：去空白、小写（「First Name」与「first name」是同一个框）。"""
    return " ".join(str(text or "").split()).lower()


def _norm_sel(sel) -> str:
    """比选择器前的归一：只去空白（大小写与空白是同一件事，别的**不许动**）。"""
    return "".join(str(sel or "").split())


def _step_covers(field: dict, steps: list) -> bool:
    """产物里**有没有一步**是在填这一格（选择器对得上、或名字对得上就算有）。

    ⚠️ **宽松判「有」**（用户口径：本来对的产物不许被拦）：任一处对上就算覆盖 ——
    选择器（`target.selectors` 里有一个与它一模一样）、或者名字（步骤的 `name` /
    `label` / `fill` **以及选择器原文**，与它的 `label` / `hint` 互相含）。
    判不出来（格上既没选择器也没名字）也算「有」：**没有证据就不拦**。

    ★ 2026-10-07 补上「选择器原文」这一路（真事 `job-fa794783c63b`）：**没有 DOM 名字**的
    输入框，两条路各记各的选择器，于是一个都对不上 ——

      · 动作那条路（产物照它重放）：`form > section.visible input[data-label="@Email"]`
      · `observe` 那条路（这一行是判据）：`form:has(input[placeholder="Search"]) input[type=email]`

    账本给那次填起的名是 `field-3`（没有名字可起）⇒ 「一模一样」与「名字互相含」两条
    **都撞不上**，闸就把一份本来对的稿拦在编译之前。而产物那一步的选择器里明明写着
    `@Email` —— 格子的标签就藏在它的**原文**里。所以选择器原文也算一条「名字」。
    ⚠️ 代价说在明处：这是**宽松**的一路（格子的标签碰巧出现在某步选择器里就判「有」）——
    与这条判据一贯的偏向一致：**宁可不拦，不许拦对的**。
    """
    sel, names = _field_handles(field)
    if not sel and not names:
        return True
    for step in steps or []:
        if str(step.get("action") or "") != "form":
            continue
        if _covers(field, _step_handles(step)):
            return True
    return False


def _field_handles(field: dict) -> tuple:
    """这一格身上能拿来认它的两样东西：`(选择器, 名字集合)`。"""
    return (_norm_sel(field.get("selector")),
            {_norm_text(field.get(k)) for k in ("label", "hint")} - {""})


def _step_handles(step: dict) -> tuple:
    """**一步**身上能拿来认它的两样东西：`(选择器集合, 名字集合)`。

    ⚠️ 名字那一格里**含选择器原文**（★ 2026-10-07）：没有 DOM 名字的元素，它的身份只写在
    选择器里（`data-label="@Email"`）—— 理由见 `_step_covers` 的 docstring。
    """
    target = step.get("target") or {}
    raw = list(target.get("selectors") or [])
    said = {_norm_text(target.get("name")), _norm_text(target.get("label")),
            _norm_text(step.get("fill"))} - {""}
    said |= {_norm_text(s) for s in raw} - {""}
    return [_norm_sel(s) for s in raw], said


def _covers(field: dict, handles: tuple) -> bool:
    """这一格与「一步的身份」是不是同一格。**宽松判「是」**（宁可不拦、不许拦对的）。"""
    sel, names = _field_handles(field)
    if not sel and not names:
        return True                       # 没有证据就不拦
    selectors, said = handles
    if sel and sel in selectors:
        return True
    return bool(names and said and any(n in s or s in n for n in names for s in said))


def _collision_say(collisions: list) -> str:
    """判据在**别的页**上也成立时，为什么不产这一版（交接《骨架还缺什么》§1）。

    ⚠️ 这段字**从 `graph._gate_collision_note` 搬过来的**（用户 2026-09-30：「闸门充分性」
    要进 IR 层 —— 那一格以前只写 `end_reason`，`ir_layer` 是空的，面板上看不出「卡在第几层」；
    搬进 IR 后它跟别的 IR 病一样：层、类、人话三样齐全）。**一个字没改，只换住处** ——
    除了开头那句「先别写 py：」：那句由 `say()` 统一给，留着会念两遍。

    为什么不产：`_applies` 是重放时**唯一的门**。两个状态的判据互相命中 ⇒ 走到那一页时，
    前一组的步骤会**在那页上真做一遍**（它点的会是那一页上的元素），而日志里每一步照样
    写着「点上了/填好了」—— 与「判据太严 ⇒ 整组静默跳过」是同一个门的两面，都是这个项目
    最贵的那类失败（georgiapower 那趟「真做 2 步、跳过 23 步」就是这个门出的）。

    ⚠️ **不许**在这儿替它把判据改「稳」一点：判据是**观测来的**（`_when_for` 从那一页正文
    剪的原文），编一条更漂亮的出来正是那段注释里说的循环论证。两条正路都在**生成**这一侧：
      - 让判据落在**只有那一页有**的那句话上（页头/导航那种公用文案一被剪成判据就长这样）；
      - 或者承认这两页**分不开**、把它们**并成一个状态** —— 相邻同门的两页
        `_Pages.note_page` 早就是这么办的（2026-09-22），那件事要改生成器，不是改产物。

    """
    lines = "；".join(
        "「%s」这一组的判据在「%s」上也成立"
        % (one.get("name"), "、".join(str(u) for u in (one.get("pages") or [])))
        for one in collisions)
    return ("这一趟账本里的判据**分不开页** —— %s。\n"
            "重放走到那几页时，前一组的步骤会**在那页上真做一遍**，而每一步的日志都写着做成了"
            "（2026-09-28 georgiapower：真做 2 步、跳过 23 步，日志里看不出来）。"
            "**宁可没有，也不交一份「看着在跑、其实在别的页上乱点」的产物**。\n"
            "两条正路：① 让判据落在**只有那一页有**的那句话上（别用页头/导航那种公用文案）；"
            "② 承认这两页分不开，把它们**并成一个状态**（相邻同门的两页生成器早就是这么并的）。"
            % lines)


def _gate_problems(journey, shape: str | None) -> list:
    """★ 2026-09-30（用户裁定「IR 必须先证明完整」的第二半）：**闸门够不够用**。

    判据**只有一个来源**：`journey.gate_collisions`（探路期 `_gates_that_hold_elsewhere`
    算的「这条判据在**别的页**上也成立」）。这里**照它说的话判，不另算一遍** ——
    同一个事实两份算法就是两个真相源，而那正是这个仓一直在治的病。

    这一搬是为了**归类**：原来是 `graph._draft` 那一格自己拦，只写 `end_reason`，
    `ir_layer` 空着 ⇒ 面板上看不出「卡在第几层」（搬之前那 9 趟就是 `ir_layer=None`）。
    搬进 IR 之后它跟别的 IR 病一样：层 / 类 / 人话三样齐全。

    ⚠️ **试过、又砍掉的那一条**（写在这儿免得下一个人重走）：还想加一条「这一组的闸在
    **它自己看过的地址**上从没成立过（地址不截断，是硬证据）」——**误拦**，当场被
    `test_graph.py::test_a_retry_that_sees_the_success_text_wins` 逮到：合并组会把
    `when` 换成 `_best_gate` 挑的那一条（可能是**另一页**的判据），于是「地址不含」在
    **对**的产物上也成立。没有跨过那一步的证据 ⇒ 不拦（用户口径：本来对的产物出不来才是大错）。

    ⚠️ 循环型（`shape == "loop"`）全关：它靠地址判、运行期自己找，静态这一条不适用。
    """
    if shape == "loop":
        return []
    collisions = list(getattr(journey, "gate_collisions", None) or [])
    if not collisions:
        return []
    return [Problem("ir", "trace_ambiguous.gate_holds_elsewhere",
                    _collision_say(collisions),
                    str((collisions[0] or {}).get("name") or ""),
                    {"collisions": collisions[:4]})]


def uncovered_required(states: list, journey) -> list:
    """`[(组名, 格子, 能补回来的那一格的名字 or "")]` —— 账本说必填、而产物里**没有一步在填**的。

    `""` = 账本里**也没填过**这一格（补不了 ⇒ 交给闸拦；见 `repair_required`）。

    ⚠️ 与 `_field_problems` 走**同一条判据**（同一份 `fields_by_state` + 同一个 `_step_covers`）：
    两处各写一份的话，「拦不拦」与「补什么」迟早走散。
    """
    every_step = [s for st in states for s in (st.get("steps") or [])]
    try:
        fields = journey.fields_by_state()
    except Exception:                                  # noqa: BLE001 —— 读不出来不拦（没证据）
        return []
    try:
        fills = dict(journey.fills() or {})
    except Exception:                                  # noqa: BLE001
        fills = {}
    out: list = []
    for where, rows in fields.items():
        for row in rows:
            if not row.get("required") or _step_covers(row, every_step):
                continue
            name = ""
            for key, info in fills.items():
                target = dict((info or {}).get("target") or {})
                if not target.get("label"):
                    target["label"] = (info or {}).get("label")
                if _covers(row, _step_handles({"action": "form", "fill": key,
                                               "target": target})):
                    name = str(key)
                    break
            out.append((where, row, name))
    return out


def repair_required(states: list, journey) -> tuple:
    """账本里**真填过**的必填格，产物里没有一步在填 ⇒ **把它补回产物**，而不是拦。

    返回 `(新 states, 补回去的名字, notes)`。`notes` 是人话（哪一格、补到哪一组、凭什么）。

    ★ 2026-10-07（用户：「探路不是探出来了吗？不能自己修正？」）：这就是 `graph` 里
    `FILLS` 那一格的同一句话 —— **能补就补，补不了才拦**。补的是**账本里那一格**
    （选择器与值都照抄，不编一个）；补不了（账本里也没填过 —— 迪士尼那趟
    `Town/City` 认不出来、没动它）⇒ 那一格**原样留着**，交给 `_field_problems` 去拦。
    「尽量能出脚本」不许变成「缺了也照样出」。

    ⚠️ 原样那一份**一个字节都不动**（补出来的是新的一份）：调用方可能还要拿旧的对账。
    """
    fixed = [dict(st) for st in (states or [])]
    by_name = {str(st.get("name") or ""): st for st in fixed}
    try:
        fills = dict(journey.fills() or {})
    except Exception:                                  # noqa: BLE001
        fills = {}
    restored: list = []
    notes: list = []
    for where, row, name in uncovered_required(states, journey):
        st = by_name.get(str(where))
        if not name or st is None or name not in fills:
            continue                       # 补不了 / 那一组不在产物里 ⇒ 不硬塞，留给闸
        info = dict(fills.get(name) or {})
        st["steps"] = list(st.get("steps") or []) + [
            {"action": "form", "fill": name,
             "note": "从账本补回来的：这一步探路时真填过（%s）"
                     % (row.get("label") or row.get("hint") or row.get("selector") or "？"),
             "target": dict(info.get("target") or {})}]
        restored.append(name)
        notes.append("「%s」（%s 那一组）产物里没有一步在填，而账本里**真填过**（`%s`）"
                     "⇒ 照账本把那一步补回去。" % (
                         row.get("label") or row.get("hint") or row.get("selector") or "？",
                         where, name))
    return fixed, restored, notes


def _field_problems(states: list, journey) -> list:
    """★ 2026-09-30（用户裁定「**IR 必须先证明完整**」）：**这一页的必填格，产物有没有去填**。

    为什么这条门非有不可（样本是真站挂单，`job-243fd328e088` 的 `fix_notes` 原话）：
    「「Town/City」认不出是什么 —— **没动它**（认不出就不填，绝不猜）」「「I have read and
    agree to the My Disney Experience Terms and Conditions.」认不出是什么 —— 没动它」
    ⇒ 一趟下来**关键格一个没填**，而产物照样跑、照样过（自测那条尺子当时**看不见填**）
    ⇒ 交付的是废稿。这条门就是让这一步**在编译之前**停下来，而不是等到真站上才发现。

    两条（都**只用账本里已经记着的事实**，不猜）：
      ① 账本观察到的格是 `required`（DOM 自己说的必填 —— 不是我们猜的「该填」），
         而**所有组里都没有一步**在填它；
      ② 账本里**真填成功过**的那一格（`journey.fills()`），在步骤表里**没有任何一步引用**
         —— 死条目 = 那一步被改稿那双手**弄丢了**（`fills()` 的 docstring 里那条口径）。

    ⚠️ **覆盖是对「所有组」判的，不是对「归属那一组」**（`fields_by_state` 的归属只用来
    写人话里说哪一组）：归属是按判据算的、可能算不准，而**拦不拦不能建立在一个可能算不准的
    结论上** —— 只有「哪儿都没填它」这一条是不会误伤的。

    ⚠️ **只对平铺型（`shape == "table"`）跑**：循环型（`template_loop`）的填法是**运行期
    按页面挑**的，静态对不上号 —— 拿这两条去判它会全是误杀。`validate(shape=...)` 是那个口子。
    """
    out: list = []
    #: ⚠️ 与 `repair_required` **同一条判据**（`uncovered_required`）：两处各写一份的话，
    #: 「补什么」与「拦什么」迟早走散 —— 补过的那几格就不该再被拦。
    missed = [(where, row) for where, row, _name in uncovered_required(states, journey)]
    if missed:
        who = "、".join("「%s」（%s 那一页）" % (
            r.get("label") or r.get("hint") or r.get("selector"), where)
            for where, r in missed[:4])
        out.append(Problem("ir", "trace_incomplete.required_field_without_step",
                           "账本里见到过 %d 个**必填格**，而产物里**一步都没填它们**：%s —— "
                           "这些格是页面自己说的必填（`observe` 记着 `required`），"
                           "就照这个交出去，站方多半直接退回（缺必填项），"
                           "而日志看上去「每一步都做成了」。" % (len(missed), who),
                           str(missed[0][0]),
                           {"fields": [r.get("label") or r.get("hint") or r.get("selector")
                                       for _, r in missed[:8]],
                            "selectors": [r.get("selector") for _, r in missed[:8]]}))
    try:
        ledger_fills = dict(journey.fills() or {})
    except Exception:                                  # noqa: BLE001
        ledger_fills = {}
    referenced = referenced_fills(states)
    dead = sorted(n for n in ledger_fills if n not in referenced)
    if dead:
        out.append(Problem("ir", "trace_incomplete.dead_fill",
                           "账本里**真填成功过**的这几格，步骤表里一步都没引用：%s —— "
                           "等于那几步被改稿那双手弄丢了（产物里会留一个没人用的 `FILLS` 条目），"
                           "重放时那几格就**空着**。" % "、".join("「%s」" % n for n in dead[:6]),
                           extra={"fills": dead[:8]}))
    return out


#: ★ 2026-10-08（用户裁定「先把限制那道闸去掉」「让他允许交付吧，修复我们这边来」）：
#: **这几条病不再砍整趟** —— `graph._draft` 拿它分流：放行的照旧出稿，缺什么写进 `unverified`。
#:
#: 为什么只有**这一条**（而不是整个 IR 闸都拆）：
#:   · `trace_incomplete.required_field_without_step`（账本里探到过必填格、产物里没一步填它）
#:     —— 放它过去，站方多半**当面退回**（缺必填项）：看得见、拿回来改就行。
#:     真代价（改之前）：`job-a02dcd78380d`（laterlifechoices.uk，2026-10-08）—— 两个必填格
#:     在产物里名字对不上 ⇒ 整趟砍死 ⇒ **运营什么都拿不到**。
#:   · `trace_ambiguous.gate_holds_elsewhere`（两组判据分不开）**照旧砍** —— 它会让产物
#:     **静默地在错的页上做动作**、而日志每一步照样写着「做成了」（georgiapower 那趟
#:     「真做 2 步、跳过 23 步」）⇒ 用户 2026-09-30 定的口径是「**宁可没有**，也不交一份
#:     看着在跑、其实在别的页上乱点的产物」。
#: ⚠️ 这两类是两码事（一个**看得见**、一个**静默**），别图省事一起放。
SOFT_CODES = ("trace_incomplete.required_field_without_step",)


def validate(journey, values: dict | None = None, shape: str | None = None,
             states: list | None = None) -> list:
    """这份 IR 能不能编译 ⇒ `[Problem]`（空列表 = 可以）。

    ⚠️ **只在 IR 这一层判**：这里不看探路成没成（那是 `explore` 层，`graph` 有它自己的判据），
    也不看产物渲得出来渲不出来（那是 `emit` 层）。越界判会让「卡在第几层」这件事又糊掉。

    `shape`：形态（`"table"` 平铺 / `"loop"` 步进循环）。**循环型有几条判据必须关掉**
    （`_field_problems` 那两条：它运行期挑格，静态对不上号）—— 不给（`None`）就按平铺判，
    与老调用方一个字节都不差。

    `states`：**要判的那一份步骤表**。不给（`None`）= 账本自己那份（老口径，一个字节不变）。
    ★ 2026-10-07：`graph` 在**补回必填格**之后把它传进来 —— 补过的那一份才是要出稿的那一份，
    判旧的等于把刚补的那一步当没看见。
    """
    values = dict(values or {})
    out: list = []
    if journey is None:
        return [Problem("ir", "ir_unexpressible.no_ledger",
                        "这一趟没有账本（探路那一层就没留下东西）—— 没有账本就没有「怎么走」。")]

    try:
        if states is None:
            states = journey.states()
    except Exception as exc:                      # noqa: BLE001 —— 读不出来也要**归类**，不许炸给上层
        return [Problem("ir", "ir_unexpressible.states_raised",
                        "账本读不成 IR（`states()` 自己抛了：%s：%s）—— 这是 IR 这一层的代码问题，"
                        "不是站点的问题。" % (type(exc).__name__, exc))]

    if not states:
        out.append(Problem("ir", "trace_incomplete.no_steps",
                           "账本里**一步可重放的都没有**（全是「看一眼」这类感知动作）—— "
                           "没有可重放步骤就没有产物可谈。"))

    #: 每个**稳定地址**上有几个状态 —— 下面那条 `bare_gate` 要看它（★ 2026-09-30 收窄）。
    _urls: dict = {}
    for _st in states:
        _u = _stable((_st.get("when") or {}).get("url_contains"))
        if _u:
            _urls[_u] = _urls.get(_u, 0) + 1

    for st in states:
        name = str(st.get("name") or "")
        when = st.get("when") or {}
        steps = list(st.get("steps") or [])
        has_text = bool(when.get("text_contains"))
        has_url = bool(when.get("url_contains"))

        # ① 一组里一步都没有 —— `states()` 已经滤掉了空组，这里只是兜底（读账的人要能看见）。
        if not steps:
            out.append(Problem("ir", "trace_incomplete.empty_group",
                               "「%s」这一组一步都没有。" % name, name))
            continue

        # ② **只有地址**的门 —— ★ 2026-09-30 **收窄**（`zekear` 现场逮到的第二个误判）：
        #    **只在同一个地址上还有别的组时**才算病（那时它分不出是哪一屏 ✗）；
        #    而这个地址上**独一份**时，地址前缀本来就够用（`/user/signIn` 就是它自己那一页）
        #    ⇒ 报它是误判，而它会拦住一份本来对的产物。
        #    ⚠️ 根子多半还是「记下这一组的那一眼没读到正文」（页面还在加载）。
        if when and not has_text and _urls.get(_stable(when.get("url_contains")), 0) > 1:
            out.append(Problem("ir", "ir_unexpressible.bare_gate",
                               "「%s」这一组**只有地址**当门（`%s`），而这个地址上还有别的组 —— "
                               "地址前缀在**每一屏**上都成立，等于没有门、分不出是哪一屏。"
                               "多半是记下这一组的那一眼**没读到正文**（页面还在加载）。"
                               % (name, when.get("url_contains") or ""), name))
        if when and not has_url and not has_text:
            out.append(Problem("ir", "ir_unexpressible.no_criterion",
                               "「%s」这一组没有任何判据（地址与正文都没有）。" % name, name))

        # ③ 动作必须都在允许的几种里（骨架只认那五种；别的到了产物里会被当成「写错了」）。
        for i, step in enumerate(steps, 1):
            act = str(step.get("action") or "")
            if act not in REPLAYABLE_ACTIONS:
                out.append(Problem("ir", "ir_unexpressible.bad_action",
                                   "「%s」第 %d 步的动作是 `%s` —— 产物只认 %s 这几种。"
                                   % (name, i, act, "、".join(REPLAYABLE_ACTIONS)), name,
                                   {"step": i, "action": act}))
            # ④ `goto` **夹在中间**（前面有步、后面也有步）**而且真的换了页**：
            #    前面那几步是按这一组那一页记的，后面那几步按**新页**记的 ⇒ 重放时一页上做一半、
            #    另一半在别的页上做（2026-09-30 现场：`zekear` 那趟自测「页面上没找到「Buy now」」
            #    就是这个形状）。
            #    ⚠️ ★ 2026-09-30 **两次收窄**（都是现场逮到的误判，两处都很常见）：
            #      · **同址的 `goto` 是重载**（真站实测：`goto https://…/user/signUp` 之后再填表
            #        —— 页面没换）⇒ 放行；
            #      · **`goto` 在组的第一步**是合法形状（「从这一页出发，去那一页做事」：
            #        `center-2` 的 `[goto 商品页, 点 Buy now]` 就是这样，且它是对的）⇒ 放行。
            #    只剩**真的夹在中间**（前面有重放步）才报。
            if act == "goto":
                after = [s for s in steps[i:] if str(s.get("action") or "") != "scroll"]
                before = [s for s in steps[:i - 1]
                          if str(s.get("action") or "") not in ("scroll", "wait")]
                where = str(step.get("url") or "")
                own = str(when.get("url_contains") or "")
                if after and before and not (own and where.startswith(own)):
                    out.append(Problem("ir", "trace_ambiguous.goto_in_the_middle",
                                       "「%s」第 %d 步是 `goto`（去「%s」），而它**前后都有别的动作** —— "
                                       "重放时前面的步在这一页上做、后面的步在新页上做，"
                                       "而日志看不出区别。这一组该在 `goto` 那儿断开。"
                                       % (name, i, where[:60]), name,
                                       {"step": i, "goto": where}))
    #: ⑤ ★ 2026-09-30（用户裁定「IR 必须先证明完整」）：**这一页的必填格，产物有没有去填**。
    #: ⚠️ 只看平铺型：循环型的填法是运行期挑的，静态对不上号（口径写在 `_field_problems`）。
    if shape in (None, "table"):
        out.extend(_field_problems(states, journey))
    #: ⑥ ★ 2026-09-30（同一裁定的第二半）：**闸门够不够用**（判据分不开页那一条，搬进 IR 层）。
    #: 循环型由 `_gate_problems` 自己关掉（它靠地址判、运行期自己找）。
    out.extend(_gate_problems(journey, shape))
    return out


def referenced_fills(states: list) -> set:
    """产物里**被步骤引用到的**那些 fill 名（`form` 步的 `fill` 那一格）。"""
    out = set()
    for st in states or ():
        for step in (st.get("steps") or ()):
            name = str((step or {}).get("fill") or "").strip()
            if name:
                out.add(name)
    return out


def missing_fills(states: list, fills: dict) -> set:
    """被步骤引用、却**不在 `FILLS` 里**的那些名字 —— 产物自我矛盾的正身。

    ★ 2026-09-30（用户问「FILLS 没有不是有兜底的随机码」时当场量到的）：现场那一趟

        账本 `journey.fills()`      : email / password / field      ← 三格都在
        交出去那一版 `FILLS`        : field                          ← 少了两格
        而步骤里还**引用**着        : email / password / field
        ⇒ 跑起来报「这一步要填「email」，FILLS 里没有它」并停

    ⚠️ **兜底管不了这一格**：别名 → 拆名 → 随机池那条链，覆盖的是「**资料里**没有那一列」；
    而这里缺的是「**产物自己的声明表**里没有这一格」—— 那种时候产物连「填什么、什么类型」
    都没有，只能报「产物写错了」（拿一个凭空的随机值去填，正是「自信地错」）。
    能补就补（账本里有 ⇒ `graph` 那一侧补回去），补不了才拦。
    """
    return set(referenced_fills(states)) - set((fills or {}).keys())


def layer_of(problems: list) -> str:
    """这一批问题的**最靠前的那一层**（给「卡在第几层」那一格用）。空列表 ⇒ 空串。"""
    order = ("explore", "ir", "emit", "selftest")
    got = {p.layer for p in problems}
    return next((one for one in order if one in got), "")


def say(problems: list) -> str:
    """一段人话（上屏用）：先说是哪一层卡住，再逐条摆。**机器码一个都不上屏**。"""
    if not problems:
        return ""
    where = {"explore": "探路那一层", "ir": "把账本变成「可编译的步骤表」那一层",
             "emit": "按步骤表生成 py 那一层", "selftest": "在真站上跑自测那一层"}
    lines = ["先别写 py：**卡在「%s」** —— 这一趟的账本还编译不出来。"
             % where.get(layer_of(problems), "IR")]
    for p in problems:
        lines.append("  · %s" % p.say)
    return "\n".join(lines)
