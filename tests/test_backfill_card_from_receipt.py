"""`tools/backfill_card_from_receipt.py` 的**一条**钉子：**对齐认不出 ⇒ 0 改动、0 写入**。

## 为什么钉这一条

那个工具会**写产物 py**（一次性补写），而它至今一条测试都没有 —— 「配不上就整组不认、
一个字都不许猜」那条纪律现在只靠运气：`_match_rows` 的**保序对齐**（`(状态, 动作, 选择器)`）
与 `_replay_step` 的**逐字段复核**是两道闸，任何一道松掉，补上去的就是**别的元素的名字**
—— 工具自己的 docstring 里记着真事：`job-3e99eb67519e` 第 20 步，观测说
`Hair, skin + nails`、回执说 `Bones, joints + muscles`。屏幕上什么都看不出来，
而下游判据会拿它当「量到的事实」用。

这一条量的是那个后果：**账本里那一行配不上 ⇒ 一处都不补**，而且**连 `--write` 都不许写**
（文件一个字节不动、`.bak-*` 一个都不建）。
"""
from __future__ import annotations

import json


def _product_py(tmp_path, states):
    """一份**合法**的产物 py：字面量用工具自己那把渲染器渲出来。

    ⚠️ 写盘前第一道门就是「当前渲染器重渲这一份字面量与文件里的逐字节相同」
    （`_checked`）—— 这里用 `_literal_of` 构造，**故意的**：这条用例要量的是**对齐**
    那道闸，不是渲染器那道（渲染器那条另有其门）。
    """
    from tools.backfill_card_from_receipt import _literal_of

    py = tmp_path / "x.py"
    py.write_text("STATES = %s\n\nFILLS = %s\n" % (_literal_of("STATES", states),
                                                   _literal_of("FILLS", {})),
                  encoding="utf-8")
    return py


def test_a_group_that_cannot_be_aligned_is_not_touched_and_not_written(tmp_path, capsys):
    """★ 对齐认不出 ⇒ 0 改动、0 写入（**含 `--write`**）。

    ⚠️ 夹具是**咬得住**的那一种：账本那一行的**回执里确实有一行可解的名字**
    （下面先断言这一点）。所以「一处都没补」这件事**只能**是对齐那道闸挡下来的 ——
    一个照组内步序硬配的实现会把它补上去（补的还是**别的元素**的名字）。
    """
    from agent import browser_agent
    from tools.backfill_card_from_receipt import backfill, main

    #: 产物那一步：卡是**空的**（`text`/`role`/`near` 全 `null`）—— 正是这个工具要补的那张。
    states = [{"name": "a", "steps": [
        {"action": "click", "note": "点了「Menu」",
         "target": {"text": None, "role": None, "near": None, "selectors": ["#keep"]}}]}]
    py = _product_py(tmp_path, states)
    before = py.read_bytes()

    #: 账本那一行：同一个状态名、同一个动作，**但地址不是那一个**（`#other`）——
    #: 组内步序一样（都是第 0 步），照步序硬配就会配到它头上。
    row = {"state": "a", "action": "click", "step_no": 7,
           "target": {"text": None, "role": None, "near": None, "selectors": ["#other"]},
           "result": {"ok": True, "match_count": 1, "selector": "#other"},
           "note": "点了「Menu」",
           "receipt": {"match_count": 1,
                       "target": '<li> "Every now and then" aria-label="Every now and then" '
                                 "[61,252,240,45]"}}
    trace = tmp_path / "attempt-1.jsonl"
    trace.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    #: 先证明夹具咬得住：那一行回执**真能**解出名字（不然「一处都没补」可能是
    #: 「回执里本来就没东西」这种平凡绿）。
    assert browser_agent.card_from_receipt(row["receipt"]["target"]) == {
        "label": "Every now and then", "text": "Every now and then"}

    got = backfill(str(trace), str(py))
    assert got["filled"] == [], got["filled"]              # 一处都不补
    assert got["unmatched"] == ["a"], got["unmatched"]     # 而且如实把「认不出」写出来
    assert got["src"] == before.decode("utf-8")            # 一个字节都没改

    # ── 干跑（默认那条路）：0 改动、0 写入 ────────────────────────────────
    assert main(["--trace", str(trace), "--py", str(py)]) == 0
    out = capsys.readouterr().out
    assert "认不出对齐的组：a" in out, out
    assert "这份 py 一个字节都不用改" in out, out
    assert py.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-*")), "干跑不该建备份"

    # ── 加了 `--write` 也不许写（没有一处要补 ⇒ 走不到写盘那一步）──────────
    assert main(["--trace", str(trace), "--py", str(py), "--write"]) == 0
    assert py.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-*")), "0 改动时连备份都不该建"
