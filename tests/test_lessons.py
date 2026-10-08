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


def test_a_uk_phone_field_is_told_the_0207_shape_and_not_a_mobile():
    """★ 2026-10-08（用户点名「加上0207规则」）：英国那格电话要填 **0207** 的座机形状。

    为什么这条非得进**探路**这一层（只进产物不够）：`template_loop._pool()` 与
    `template.COUNTRY_POOLS` 那两个池子**都只在重放时生效** —— 探路那一趟模型手上
    一个池子都没有，它是自己现编的。`job-ada35bcc8dd4`（laterlifechoices.uk，
    2026-10-08）编出 `+4479111234567911123456`（国家码与号码拼在一起）⇒ 站方顶回
    ⇒ 一路绕到 86 轮预算烧光，结局 `explore_unfinished`，一个 py 都没有。
    同一站换成 0207 形状那一趟一次过（`job-cce9d4b8e5fd` 交付）。

    ⚠️ 这条钉的是「**有没有**这一条、**有没有写清哪一国**」——**不钉措辞**（措辞会改，
    而钉措辞的用例会在每次改文案时红一次，然后就被人顺手删掉）。
    """
    hit = [one for one in lessons.LESSONS if "0207" in str(one.get("do") or "")]
    assert hit, ("经验库里没有一条讲 0207 的 —— 探路时模型只会自己现编一个号"
                 "（2026-10-08 job-ada35bcc8dd4 就是这么烧掉 86 轮预算的）")
    #: **得写清是哪一国的规矩**：无条件的「电话格就填 0207」会把加拿大站也填上英国号
    #: —— 那条路已经有真站代价（mapleporchdaily，见 `template.COUNTRY_POOLS` 上面
    #: 那段注释里 `The phone number is not valid.` 那一次）。
    no_country = [one for one in hit if "英" not in str(one.get("scene") or "")]
    assert not no_country, ("这条 0207 没写清是哪一国的规矩 —— 加拿大站会被一起填错：%r"
                            % (no_country,))


def test_a_phone_number_that_changes_per_load_is_not_used_as_a_criterion():
    """★ 2026-10-08（真事 `job-0f511e5f5e0b`，alansfactoryoutlet.com）：**页头那个电话号
    不许当判据** —— 它每次开页都不一样。

    形状（同一个商品页，三次开、三个号）：探路看到 `1-888-537-9043`，自测第 1 遍看到
    `1-844-620-7858`；改稿把判据改成那个号之后再跑，页面又成了 `1-888-531-0927`。
    而 HTML 里**恒为** `1-800-488-6903`（挂在 `afo-impact-attribution.js` 上 ——
    「attribution」= 通话归因，脚本在客户端按访客来源换号）。
    代价：6 步里 4 步判据不成立、被跳过 ⇒ 没走到成功。

    ⚠️ 为什么这条**非得进探路这一层**：改稿救不了它 —— 每一轮改稿都把「这次看到的那个号」
    写进判据，下一趟开页又是新号，追不上（`max_diagnoses` 烧完也过不了）。只有探路那一趟
    **不选它**，这条路才断得掉。

    ⚠️ 与上面那条 0207 **方向相反、两条都必须有**：那条讲「电话格该填什么形状」，
    这条讲「**别拿电话号当判据**」。别把它们合成一条。
    """
    hit = [one for one in lessons.LESSONS
           if "判据" in str(one.get("do") or "")
           and "电话" in (str(one.get("scene") or "") + str(one.get("do") or ""))]
    assert hit, ("经验库里没有一条讲「会变的电话号不许当判据」的 —— 挂归因脚本的站"
                 "（2026-10-08 alansfactoryoutlet）会一路跳步到自测挂，而改稿追不上")
    #: 得说清**为什么会变**（不说清，下一个人会当成「这一站碰巧」而不当规矩）
    said = " ".join(str(one.get(k) or "") for one in hit for k in ("scene", "do"))
    assert any(w in said for w in ("归因", "每次开页", "每趟都", "A/B")), (
        "这条只说「会变」，没说清**为什么**会变（归因 / 每次开页）—— 那样它读起来像"
        "「这一站运气不好」，而不是一条规矩：%r" % said)
    #: 出处要指得到那一趟（站名或单号），否则读者查不到
    src = " ".join(str(one.get("from") or "") for one in hit)
    assert "alansfactoryoutlet" in src or "job-0f511e5f5e0b" in src, (
        "出处里既没有站名也没有单号 —— 这条经验查不到：%r" % src)
