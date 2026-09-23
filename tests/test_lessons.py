"""经验库（`agent/lessons.py`）的守门测试。

**这一份量的是纪律，不是文案**：库的内容会一直改，但「每条都有出处」「进的是稳定层」
这两条不能靠自觉 —— 它们一松，这个文件就退化成一份**教模型编**的文档。
"""
from agent import browser_agent, lessons

#: 「出处」里不许出现的词：它们**没有指认能力**（读者照着它查不到任何一趟真事）。
NO_EVIDENCE_WORDS = ("经验", "常见", "一般来说", "大家都知道", "通常", "碰巧", "可能")


def test_every_lesson_has_a_scene_a_do_and_an_evidence():
    """一条经验三样缺一不可：**怎么认出这种情况** / **怎么做** / **出处**。

    ⚠️ 出处那一样是这条测试存在的全部理由：写不出出处的「经验」是猜测，
    而猜测写进提示词就是在教模型编 —— 这个项目从头到尾治的就是「看起来像有据」。
    """
    assert lessons.LESSONS, "库是空的 —— 那这条测试什么也没量"
    for one in lessons.LESSONS:
        for key in ("scene", "do", "from"):
            said = str(one.get(key) or "").strip()
            assert said, "这一条少了 %s：%r" % (key, one)
        #: 出处要**能查**：至少得有一个日期（2026-…）或一个站点/单号样子的词。
        src = str(one["from"])
        assert any(ch.isdigit() for ch in src), "出处里没有任何可查的东西：%r" % src
        for vague in NO_EVIDENCE_WORDS:
            assert vague not in src, "出处里出现了没有指认能力的词（%s）：%r" % (vague, src)


def test_the_library_is_short():
    """**短**这一条也得有人守：它每一趟探路都要进上下文（§13：重跑要便宜）。

    上限是**故意宽松**的（四千字符 ≈ 一条经验四百字）：它不拦正常改，只拦
    「把 `docs/` 里那篇长文整段搬进来」那种改法 —— 那会让每一趟探路都白烧一笔。
    """
    assert len(lessons.block()) <= 4000, len(lessons.block())


def test_an_empty_library_adds_nothing_at_all():
    """空库 ⇒ **空串**（一个字节都不多）—— 与 `_hints_block` 同一条规矩。

    ⚠️ 为什么单钉这一条：它是「以后想把库清空」时唯一的安全网
    （清空却还留一句「下面是经验」的话，模型会去找那些不存在的东西）。
    """
    saved = lessons.LESSONS
    try:
        lessons.LESSONS = ()
        assert lessons.block() == ""
    finally:
        lessons.LESSONS = saved


def test_the_lessons_ride_into_the_system_prompt():
    """★ 接的是**稳定层**（`_SYSTEM`），不是简报。

    为什么单钉这一条（2026-09-23 量过）：简报那条**没计划**的路被 B4 那条钉子逐字节
    钉死（`tests/test_browser_agent.py:1778`）—— 经验库要是接在那儿，要么撞钉子、
    要么就得偷偷把自由模式的行为改了。接 `_SYSTEM` 两个问题都没有，
    所以这一条钉的是**接在哪里**，不是接没接。
    """
    block = lessons.block()
    assert block, "库是空的，这条钉不住接线"
    assert block in browser_agent._SYSTEM, "经验库没有进 _SYSTEM"
    #: 每条都在（逐条比：只比整块的话，「库改了但拼装漏了一条」量不出来）。
    for one in lessons.LESSONS:
        assert str(one["scene"]) in browser_agent._SYSTEM, one["scene"]
