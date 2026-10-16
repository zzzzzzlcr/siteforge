"""`tools/anchor_report.py` 的钉子 —— 三组数怎么数，以及「不许**假装**绿」。

## 为什么这一条要单独钉（照 `tests/test_addr_basis_report.py` 那份的理由）

这是本刀**三处「往紧里收」唯一的执行处**（spec §5.1）。三组数都是**要交人裁**的，
而「数字看着对、量的是错的东西」在这个仓里发生过两次（上一刀交接单 ③.1）——
所以这里拿一份**合成账本**真跑一遍 `measure`，要求它三个桶**都数得到**。
⚠️ **守门人本身也要被验**：三个口径都要能对自造输入产出非零，否则那三个 0 只是形容词。

## 另外两条钉的是 ② 的**边界**（★ 2026-10-16，写实现时量出来的）

② 是**交人裁**的一栏：多一条少一条都要说得出理由。两处边界各钉一条 ——
两处都是「拿产物的整张候选表喂给判据」会走岔的地方：

1. **全是脆链那族**（`nth` 长链）：老末句（`not [s for s in kept if not _fragile(s)]`）
   **本来就会报**它 ⇒ 记进 ② 就是**虚报**（人裁那一栏里混着早就报过的账）。
2. **编的地址压不住纯位置那根**：生产那边交给 `_card_has_nothing` 的候选是**验过出处的**
   （`_rule_i` 里 `UNSUPPORTED` 的当场剔掉）。拿产物的整张候选表去喂，一根编的地址
   就能把「纯位置那根」压下去 ⇒ 新规**真会报**的那一步**数漏** —— 漏报比虚报坏：
   人裁看不到，就放行了。
"""
from __future__ import annotations

import sqlite3

#: 4 跳的 `nth` 链（`template._HOPS_LOW` = 3 ⇒ `_fragile` 认这种），且**零断言**
#: （全是位置，`assertions_of` 给空表）—— ② 想数的正是「纯位置」这一族。
_LONG_CHAIN = ("body:nth-of-type(1) > div:nth-of-type(2) > div:nth-of-type(1) "
               "> span:nth-of-type(2)")


def _packb(value):
    """msgpack **字节**（不写死 packer）：宿主上是 `msgpack`、容器里是 `ormsgpack`。"""
    try:
        import ormsgpack
        return ormsgpack.packb(value)
    except ImportError:
        import msgpack
        return msgpack.packb(value)


def _ledger(path, rows):
    """按 `writes` 那张表的形状造一份**合成账本**。

    ⚠️ 建表语句与 `tools/addr_basis_report._rows` 实际查的那一句**逐字对齐**
    （`select thread_id, channel, type, value from writes order by rowid`）。
    ⚠️ `order by rowid` 是**承重的**：`_rows` 里「`site`/`observed` 取最后一条非空、
    `states` 取最早那一条」都按**追加顺序**读 —— 这里 insert 的顺序就是那个顺序。
    """
    con = sqlite3.connect(str(path))
    con.execute("create table writes (thread_id text, channel text, type text, value blob)")
    for row in rows:
        con.execute("insert into writes values (?,?,?,?)",
                    (row["thread_id"], row["channel"], "msgpack", _packb(row["value"])))
    con.commit()
    con.close()


def test_measure_counts_all_three_buckets(tmp_path):
    """一份合成账本，三个口径各一条 ⇒ 三个桶都得数得到（**测的是工具真会数**）。"""
    from tools.anchor_report import measure

    ledger = tmp_path / "ck.sqlite"
    _ledger(ledger, [
        # ① I1 连带：第一版那一步没有选择器、只有 text（意图）—— 且**卡也是空的**
        #    ⇒ 合并后会变成「既没地址也没锚」⇒ 会被 `_steps_must_be_findable` 新拒。
        {"thread_id": "t1", "channel": "site", "value": ""},
        {"thread_id": "t1", "channel": "site", "value": "one"},
        {"thread_id": "t1", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click", "target": {}}]}]},
        {"thread_id": "t1", "channel": "fills", "value": {}},
        {"thread_id": "t1", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click",
                                            "target": {"text": "Continue"}}]}]},
        # ② M2：空卡 + 零断言地址（今天放行、新规会报）
        {"thread_id": "t2", "channel": "site", "value": "two"},
        {"thread_id": "t2", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click", "target": {}}]}]},
        {"thread_id": "t2", "channel": "fills", "value": {}},
        {"thread_id": "t2", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click",
                                            "target": {"selectors": ["li:nth-child(2n+1)"]}}]}]},
        # ③ M3：观测 `#Postcode`、产物写 `#postcode`（只差大小写）
        {"thread_id": "t3", "channel": "site", "value": "three"},
        {"thread_id": "t3", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click",
                                            "target": {"selectors": ["#Postcode"]}}]}]},
        {"thread_id": "t3", "channel": "fills", "value": {}},
        {"thread_id": "t3", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click",
                                            "target": {"selectors": ["#postcode"]}}]}]},
    ])
    got = measure(str(ledger))
    assert got["①合并后新拒的步"], got
    assert got["②空卡上的纯位置地址"], got
    assert got["③只差大小写的标识"], got


def test_already_reported_fragile_chains_are_not_new(tmp_path):
    """**全是脆链**那族：老末句**本来就会报** ⇒ 不许记进 ②（虚报）。

    ⚠️ 一条 4 跳的 `nth` 链：`_fragile` 认它（`_HOPS_LOW` = 3）、`assertions_of` 给空表
    （位置不算断言）⇒ 它**同时**满足「纯位置」与「新末句为真」两句。
    老末句那半（`not [s for s in kept if not _fragile(s)]`）也认它 ⇒ 今天就在报
    `judge-fragile-no-basis` ⇒ 它不是本刀**新**咬到的，③② 都得空着。
    """
    from tools.anchor_report import measure

    ledger = tmp_path / "ck.sqlite"
    _ledger(ledger, [
        {"thread_id": "t4", "channel": "site", "value": "four"},
        {"thread_id": "t4", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click", "target": {}}]}]},
        {"thread_id": "t4", "channel": "fills", "value": {}},
        {"thread_id": "t4", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click",
                                            "target": {"selectors": [_LONG_CHAIN]}}]}]},
    ])
    got = measure(str(ledger))
    assert got["②空卡上的纯位置地址"] == [], got
    assert got["①合并后新拒的步"] == [], got


def test_a_step_that_is_already_unfindable_is_not_new(tmp_path):
    """**新**拒才进 ①：交付那一版本来就认不出来的步，不许记成「合并把它弄没了」。

    ⚠️ 这一族的形状：草稿那一步 `target` 是空的（没有选择器、也没有 `text`/`role`/`near`）——
    `render` 当初就把它拒了（这道闸不是本刀加的），而**拒掉的草稿照样留在账本里**
    （`states` 那一格是每一版都追加）。合并对它是**幂等**的（卡也是空的、没什么可删）⇒
    它「认不出来」这件事**与合并无关** ⇒ 记进来就是**虚红**，而虚红的下场是有人去放宽规则。
    """
    from tools.anchor_report import measure

    ledger = tmp_path / "ck.sqlite"
    _ledger(ledger, [
        {"thread_id": "t6", "channel": "site", "value": "six"},
        {"thread_id": "t6", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click", "target": {}}]}]},
        {"thread_id": "t6", "channel": "fills", "value": {}},
        {"thread_id": "t6", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click", "target": {}}]}]},
    ])
    got = measure(str(ledger))
    assert got["①合并后新拒的步"] == [], got
    assert got["②空卡上的纯位置地址"] == [], got
    assert got["③只差大小写的标识"] == [], got


def test_the_already_unfindable_guard_is_per_step_not_per_version(tmp_path):
    """① 的守卫要落在**这一步**上：按整版判一次 ⇒ 一条坏兄弟步就把整版的新账**一起漏掉**。

    ⚠️ 形状（修复轮 1 评审的复现）：交付那一版第 0 步本来就认不出来（`click {}` —— render
    当初就拒过它），第 1 步**合并前**认得出（只有 `text`）、**合并后**卡是空的 ⇒ **真·新拒**。
    整版判一次的结果是：`_findable(交付版)` 为假 ⇒ 整版跳过 ⇒ ① 报**假 0** ——
    而这个 0 恰恰是这条硬线唯一要数的东西（假 0 比虚报坏：屏幕上与「真干净」长得一模一样）。
    """
    from tools.anchor_report import measure

    ledger = tmp_path / "ck.sqlite"
    _ledger(ledger, [
        {"thread_id": "t7", "channel": "site", "value": "seven"},
        {"thread_id": "t7", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click", "target": {}},
                                           {"action": "click", "target": {}}]}]},
        {"thread_id": "t7", "channel": "fills", "value": {}},
        {"thread_id": "t7", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click", "target": {}},
                                           {"action": "click",
                                            "target": {"text": "Continue"}}]}]},
    ])
    got = measure(str(ledger))
    #: 第 0 步：交付那版本来就认不出 ⇒ 与合并无关、不算新账；第 1 步：合并把它掏空 ⇒ **必须**报
    assert [one[2] for one in got["①合并后新拒的步"]] == ["g#1"], got


def test_the_fills_branch_is_measured_too(tmp_path):
    """②③ 要把 `FILLS` 那一支一起量 —— `_rule_i` 的 FILLS 分支**同样**调 `_card_has_nothing`。

    ⚠️ M2/M3 那两处改动落在 `_card_has_nothing` 与 `address_basis` 上，而这两样是
    **states / FILLS 两支共用**的 ⇒ 只循环 `states` 的话，「②③ = 0」这句话就比它读起来**窄**：
    FILLS 那一支的槽位一个都没数，而生产那边照样新报 `judge-fragile-no-basis`。
    ⚠️ 卡要照**生产那一句**取（`_rule_i` 的 FILLS 支）：`card_of(冻结那一格.get("target"))` ——
    fill 那一格自己带着 `label`（产物写的那份名字），拿**整格**去当卡，就是把产物自己的话
    当成证据（与那边「这里**不许**用 `fix._literals(src)` 取卡」同一条规矩）。
    """
    from tools.anchor_report import measure

    ledger = tmp_path / "ck.sqlite"
    _ledger(ledger, [
        {"thread_id": "t8", "channel": "site", "value": "eight"},
        {"thread_id": "t8", "channel": "states",
         "value": [{"name": "g", "steps": []}]},
        # 冻结那一份：`postcode` 那格的 target 是空的（**量过、什么都没有**）；`email` 观测量到 `#Email`
        {"thread_id": "t8", "channel": "fills",
         "value": {"postcode": {"label": "Postcode", "target": {}},
                   "email": {"label": "Email", "target": {"selectors": ["#Email"]}}}},
        # 交付那一版：`postcode` 只剩一根纯位置地址（②）；`email` 写成了 `#email`（③，只差大小写）
        {"thread_id": "t8", "channel": "fills",
         "value": {"postcode": {"label": "Postcode",
                                "target": {"selectors": ["li:nth-child(2n+1)"]}},
                   "email": {"label": "Email", "target": {"selectors": ["#email"]}}}},
    ])
    got = measure(str(ledger))
    assert [one[2] for one in got["②空卡上的纯位置地址"]], got
    assert "FILLS:postcode" in got["②空卡上的纯位置地址"][0][2], got
    assert [one[2] for one in got["③只差大小写的标识"]], got
    assert "FILLS:email" in got["③只差大小写的标识"][0][2], got
    assert got["①合并后新拒的步"] == [], got      # 这一趟没有 click 步，① 与它无关


def test_a_made_up_address_does_not_hide_the_position_only_one(tmp_path):
    """**编的地址不算答案**：它不许把纯位置那根压下去（生产先剔 `UNSUPPORTED` 才问卡）。

    ⚠️ 这一步两根候选：`header a`（卡里没有 ⇒ `UNSUPPORTED`，今天也过不去）与
    `li:nth-child(2n+1)`（纯位置 ⇒ `OBSERVED`）。生产那边 `kept` 只剩后者 ⇒
    `_card_has_nothing` 为真 ⇒ **新规会报**（老末句不报：`not _fragile` 那半还成立）。
    拿**整张**候选表去问，`header a` 是「不脆、又断言了两根标签」⇒ 老末句也判「有答案」
    ⇒ 这一步就**数漏**了。
    """
    from tools.anchor_report import measure

    ledger = tmp_path / "ck.sqlite"
    _ledger(ledger, [
        {"thread_id": "t5", "channel": "site", "value": "five"},
        {"thread_id": "t5", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click", "target": {}}]}]},
        {"thread_id": "t5", "channel": "fills", "value": {}},
        {"thread_id": "t5", "channel": "states",
         "value": [{"name": "g", "steps": [{"action": "click",
                                            "target": {"selectors": ["header a",
                                                                     "li:nth-child(2n+1)"]}}]}]},
    ])
    got = measure(str(ledger))
    assert got["②空卡上的纯位置地址"], got
    assert got["①合并后新拒的步"] == [], got      # 有选择器 ⇒ 合完照样认得出
    assert got["③只差大小写的标识"] == [], got
