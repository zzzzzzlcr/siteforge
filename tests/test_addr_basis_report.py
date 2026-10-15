"""`tools/addr_basis_report.py` 的钉子 —— 那两条子线怎么分，以及「不许**假装**绿」。

## 为什么这一条要单独钉

这是「误阻断」那条**硬线**的**唯一执行处**（spec §5）：老产物本来就缺证据
（click/scroll 带锚 09-28～10-12 全 0），所以新判据**有理由**在它们身上报
`judge-fragile-no-basis` —— 那既不是回归，也不能靠放宽新规则去消掉。分法只有一句话：
**看冻结那份里到底有没有那几样**。有 ⇒ ①（回归，硬线 = 0）；没有 ⇒ ②（历史欠账，交人裁）。

## 两处「静默变绿」要堵住

1. **读不到账本 ⇒ 一个 ① 都不报** —— 账本那一趟要是筛错了（表名/列名/编码变了），
   打印出来的是「①错误新增 0」，与人眼期待的验收数字**长得一模一样**。
   所以这里拿一份**合成账本**真跑一遍 `main`，要求它**确实**分出了两族、也确实数到了 ③。
2. **没有冻结那份 ⇒ 不许报回归** —— 连证据都没有的时候，唯一诚实的输出是「什么都没说」
   （`classify` 给空串）；要是那时候也报一条 ①，这条硬线就永远为 0 了（它成了恒真式）。
"""
from __future__ import annotations

import sqlite3


def _packb(value):
    """msgpack **字节**（不写死 packer）：宿主上是 `msgpack`、容器里是 `ormsgpack`。

    ⚠️ 这里**故意与工具各写一份**：两边共用同一个 helper 的话，helper 本身写错了
    也照样「自洽通过」，而账本那一趟是**在宿主上**跑的 —— 那份自洽在下面这次量里没有证人。
    """
    try:
        import ormsgpack
        return ormsgpack.packb(value)
    except ImportError:
        import msgpack
        return msgpack.packb(value, use_bin_type=True)


def test_report_splits_the_two_sublines(tmp_path):
    """① 错误新增（冻结那份里**有**锚却还报）= 回归；② 历史证据缺失 = 单列交人裁。

    ⚠️ 这一条是**硬线**的守门人：子线 ② 再长也不许把规则放宽（spec §5）。
    ⚠️ **2026-10-15 更正：夹具按「现在的」判据重写** —— 原版是早期版本写的，按今天的行为
    **产不出 no-basis**（产物那根地址与卡逐字相同 ⇒ 逐字相等 ⇒ `OBSERVED` ⇒ 不报）。两族现在长这样：
      · **①**：卡上**有锚**（`label`），而产物的地址是**编的**（`header a`，卡里没有）⇒ 候选全被剔掉 ⇒ 第二格；
      · **②**：卡上**没锚**、产物用的**就是卡上那根**脆链（逐字相同）⇒ 第一格。
    """
    from tools.addr_basis_report import classify

    # ① 错误新增：卡上有料、而报的是「无据可依」
    src_一 = ("STATES = [ {'name': 'a', 'steps': [ {'action': 'click',\n"
              "  'target': {'selectors': ['header a']}} ]} ]\nFILLS = {}\n")
    card_有锚 = {"states": [{"name": "a", "steps": [
        {"action": "click", "target": {"label": "Menu", "role": "a",
                                       "selectors": ["a[href='/comp']"]}}]}], "fills": {}}
    assert classify(src_一, card_有锚) == "①错误新增"

    # ② 历史证据缺失：卡上真没料（那根脆链就是探路量到的全部）
    _FRAG = "body:nth-of-type(1) > header:nth-of-type(1) > div:nth-of-type(1) > a:nth-of-type(1)"
    src_二 = ("STATES = [ {'name': 'a', 'steps': [ {'action': 'click',\n"
              "  'target': {'selectors': ['%s']}} ]} ]\nFILLS = {}\n" % _FRAG)
    card_没锚 = {"states": [{"name": "a", "steps": [
        {"action": "click", "target": {"role": "a", "near": "header",
                                       "selectors": [_FRAG]}}]}], "fills": {}}
    assert classify(src_二, card_没锚) == "②历史证据缺失"


def test_a_card_with_a_non_fragile_selector_is_a_regression_too(tmp_path):
    """★ 评审 A5：卡上**没有锚**、但有一根**验过唯一的不脆候选**，而产物那根是**编的** ⇒ **①**。

    为什么它是硬线那一侧（而不是「交人裁」）：判据这一格本来**有路可走**
    （把卡上那根拿来用），报「无据可依」就是**误阻断**。spec §5 的表写的是两样：
    「有**那几样锚** / **有 Observed 候选**」—— 只看第一样的话，这种形状的真回归会被
    记进「交人裁」那一节，而那正是这条硬线唯一存在的理由。
    ⚠️ 两句都要算（`_has_evidence`），各自与 `lint._card_has_nothing` 的**卡那一侧**逐句对齐；
    这一条钉的是**第二句**（上一版只写了第一句，这条用例当时是红的）。
    """
    from tools.addr_basis_report import classify

    src = ("STATES = [ {'name': 'a', 'steps': [ {'action': 'click',\n"
           "  'target': {'selectors': ['header a']}} ]} ]\nFILLS = {}\n")
    card = {"states": [{"name": "a", "steps": [
        {"action": "click", "target": {"role": "link", "selectors": ["a[href='/comp']"]}}]}],
        "fills": {}}
    assert classify(src, card) == "①错误新增"


def test_no_frozen_evidence_means_no_claim_at_all():
    """**没有冻结那份 ⇒ 一个 ① 都不许报**（报了就说明这条硬线是恒真的，等于没量）。"""
    from tools.addr_basis_report import classify

    src = ("STATES = [ {'name': 'a', 'steps': [ {'action': 'click',\n"
           "  'target': {'selectors': ['header a']}} ]} ]\nFILLS = {}\n")
    assert classify(src, None) == ""
    #: 认不出是哪一步（卡取不到）也算「没证据」—— 与 `fix.find_observed` 同一条纪律：
    #: fail-closed 的方向是**不报**，不是「当它有、然后报一条回归」。
    assert classify(src, {}) == "②历史证据缺失"


def test_the_tool_takes_the_card_the_way_the_judge_does(tmp_path):
    """★ 评审 M6：取卡必须**与判据同一把尺子**（`fix.find_observed`，**指纹优先**）。

    真形状：**表被重排**（模型把一步挪到前面 / 删掉了前面那一步）。
    冻结那份里：第 0 步**有锚**、第 1 步只有那根脆链；产物把「脆链那一步」挪到了第 0 位。
      · 判据按**指纹**找「这一步是谁」⇒ 取到**原件第 1 步**那张卡（只有脆链、没锚）⇒ **②**（交人裁）；
      · 照 `(状态名, 步序)` 取 ⇒ 取到**原件第 0 步**那张卡（有锚）⇒ **①** —— 一条**假回归**。
    这一处是「① 必须是 0」那条硬线**唯一执行处**：两边分家时，屏幕上那个 ① 与判据的实际
    判决对不上，而硬线的意义就是这一条数字。
    """
    from tools.addr_basis_report import classify

    _FRAG = ("body:nth-of-type(1) > header:nth-of-type(1) > div:nth-of-type(1) "
             "> a:nth-of-type(1)")
    frozen = {"states": [{"name": "a", "steps": [
        {"action": "click", "target": {"label": "Menu", "role": "a",
                                       "selectors": ["a[href='/comp']"]}},
        {"action": "click", "target": {"role": "a", "near": "header",
                                       "selectors": [_FRAG]}}]}], "fills": {}}
    #: 产物：那根脆链那一步挪到了第 0 位（原件里它在第 1 位）——判据按指纹认得出它
    src = ("STATES = [ {'name': 'a', 'steps': [ {'action': 'click',\n"
           "  'target': {'selectors': ['%s']}} ]} ]\nFILLS = {}\n" % _FRAG)
    assert classify(src, frozen) == "②历史证据缺失"


#: 合成账本用的两族源码 / 卡（与上面那条用例同源，只是走**账本**那条路）。
#: ⚠️ 变量名一律 ASCII：`①` 那一类字符**不是**合法标识符（`SyntaxError`，实测）。
_SRC_A = ("STATES = [ {'name': 'a', 'steps': [ {'action': 'click',\n"
          "  'target': {'selectors': ['header a']}} ]} ]\nFILLS = {}\n")
_FRAG = "body:nth-of-type(1) > header:nth-of-type(1) > div:nth-of-type(1) > a:nth-of-type(1)"
_SRC_B = ("STATES = [ {'name': 'a', 'steps': [ {'action': 'click',\n"
          "  'target': {'selectors': ['%s']}} ]} ]\nFILLS = {}\n" % _FRAG)
_STATES_A = [{"name": "a", "steps": [
    {"action": "click", "target": {"label": "Menu", "role": "a",
                                   "selectors": ["a[href='/comp']"]}}]}]
_STATES_B = [{"name": "a", "steps": [
    {"action": "click", "target": {"role": "a", "near": "header",
                                   "selectors": [_FRAG]}}]}]


def _ledger(path, threads):
    """按 `writes` 那张表的形状造一份**合成账本**（值与生产同形：msgpack）。"""
    con = sqlite3.connect(str(path))
    con.execute("create table writes (thread_id text, checkpoint_ns text, checkpoint_id text,"
                " task_id text, idx integer, channel text, type text, value blob)")
    for tid, rows in threads:
        for i, (channel, value) in enumerate(rows):
            con.execute("insert into writes values (?, '', '', '', ?, ?, 'msgpack', ?)",
                        (tid, i, channel, _packb(value)))
    con.commit()
    con.close()
    return str(path)


def test_the_ledger_run_really_reads_the_ledger(tmp_path, capsys):
    """★ 堵「读不到账本 ⇒ 假装 ① 是 0」：合成本两族 + 一趟没账的，真跑一遍 `main`。

    ⚠️ 前两族走的是**老 job 那条路**（没有 `observed` 那一格，从最早的 `states`/`fills` 重建）
    —— 存量账本**全都是**这个形状，所以这条路才是这份报告真正走的那条。
    ⚠️ 第三族走**新 job 那条路**（`observed` 那一格在），两条都要有人钉。
    """
    from tools.addr_basis_report import main

    p = _ledger(tmp_path / "ckpt.sqlite", [
        ("t-old-a1", [("src", _SRC_A), ("site", "site-a"),
                      ("states", _STATES_A), ("fills", {})]),
        #: ⚠️ `site` 那一格**先写一条空的、再写真名**（存量账本实测就是 `['', 'ic']` 这个形状）
        #: ⇒ 列出来的每一趟要显示**真名**；照 `[0]` 取的话，「逐条交人裁」那一节全是空白。
        ("t-old-b2", [("src", _SRC_B), ("site", ""), ("site", "site-b"),
                      ("states", _STATES_B), ("fills", {})]),
        ("t-new-b3", [("src", _SRC_B), ("site", "site-c"),
                      ("observed", {"states": _STATES_B, "fills": {}})]),
        ("t-none-4", [("src", _SRC_B), ("site", "site-d")]),
    ])
    assert main(["--ledger", p]) == 0
    out = capsys.readouterr().out

    assert "'①错误新增': 1" in out, out
    assert "'②历史证据缺失': 2" in out, out
    assert "'③没冻过也没账本': 1" in out, out
    #: 人裁那一节要能一眼看出**是哪一趟**（不然「逐条交人裁」无从裁起）。
    assert "t-old-b2" in out and "site-b" in out, out


def test_a_null_observed_cell_does_not_fall_back_silently(tmp_path, capsys):
    """★ 评审 ④：`observed` 那一格**先写一条 `None`、再写真那份** ⇒ 要用真那份。

    ⚠️ 照 `[0]` 取的话，后果是**无声降级**：冻结那份被跳过去、退回「从最早的
    `states`/`fills` 重建」那条路 —— 两条路给出的卡**不一定一样**，而屏幕上什么都看不出来。
    这里量的就是那个差：这一趟真那份里有锚（⇒ **①**），照 `[0]` 取会落到 ③（连重建都没有）。
    """
    from tools.addr_basis_report import main

    p = _ledger(tmp_path / "ckpt.sqlite", [
        ("t-null-then-real", [("src", _SRC_A), ("site", ""), ("site", "site-e"),
                              ("observed", None),
                              ("observed", {"states": _STATES_A, "fills": {}})]),
    ])
    assert main(["--ledger", p]) == 0
    out = capsys.readouterr().out

    assert "'①错误新增': 1" in out, out
    assert "③" not in out, out
