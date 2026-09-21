"""`GET /manual`：运营手册那一页（2026-09-21）。

用户口径：「我说的运营手册也给个访问地址」。两件事要一起钉住：
① 渲染的是**盘上那一份 .md**（不是另抄一份 HTML —— 两个来源必然漂开）；
② 渲染不吞话（认不出来的写法原样摆出来，宁可屏幕上出现 `| --- |`）。
"""
from __future__ import annotations

import pathlib

import pytest

from agent import manual, service


def test_the_manual_renders_the_ten_cent_document_itself():
    """那一页渲染的**就是** `docs/运营手册-面板修站.md`（改文档 = 改网页，一处来源）。"""
    assert manual.OPERATOR_MANUAL.name == "运营手册-面板修站.md"
    assert manual.OPERATOR_MANUAL.exists(), manual.OPERATOR_MANUAL
    html = manual.render(manual.OPERATOR_MANUAL.read_text(encoding="utf-8"))
    #: 文档里的要害那几句（目录结构变了、这几条没了 ⇒ 这一条红，提醒去改文档/改这里）
    for want in ("运营手册：用面板修一个坏掉的站", "第 1 步：挑靶子", "每道闸上该看什么",
                 "出岔子时对着这话找", "三条规矩"):
        assert want in html or want.replace("：", "") in html, want
    #: 表格真的被摆成了 `<table>`（这一份文档一半是表 —— 渲染不出来等于没渲染）
    assert html.count("<table>") >= 5, html.count("<table>")


def test_the_subset_that_survives_and_what_it_does_not_swallow():
    """认得出的六种写法 + **认不出的不许被吃掉**（纯函数，不用点亮服务）。"""
    got = manual.render("# 标题\n\n这是一句 **着重** 与 `代码`。\n\n"
                        "| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n\n```\necho hi\n```\n\n> 一句引用\n\n- 一\n- 二\n")
    assert "<h1>标题</h1>" in got
    assert "<b>着重</b>" in got and "<code>代码</code>" in got
    assert got.count("<th>") == 2 and got.count("<td>") == 2
    assert "<pre>echo hi</pre>" in got
    assert "<blockquote>一句引用</blockquote>" in got
    assert got.count("<li>") == 2
    #: 文档里写的尖括号与 `&` 必须被转义（否则它会变成真标签，那一页就坏了）
    assert "&lt;a href&gt;" in manual.render("文本里的 <a href> 与 & 号")
    #: 认不出来的行**原样**摆进 `<p>`（宁可难看，不许吞）
    assert "| --- |" in manual.render("| --- |")
    assert "~~划掉~~" in manual.render("~~划掉~~")


def _client():
    from fastapi.testclient import TestClient
    from langgraph.checkpoint.memory import InMemorySaver
    return TestClient(service.create_app(graph_factory=lambda brief, deps: None,
                                         checkpointer=InMemorySaver()))


def test_manual_is_a_page_a_human_can_open(tmp_path):
    """那一页要**独立能开**：200 + HTML + 有返回控制台的路（运营不可能手敲 URL）。"""
    client = _client()
    r = client.get(manual.MANUAL_PATH)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html"), r.headers
    assert "<table>" in r.text and "回到控制台" in r.text, r.text[:200]
    #: 盘上那份读不了 ⇒ 说清是「读不了」，**不许**回一个空白的 200（那一页看着就像「手册是空的」）
    from agent import service as svc_mod
    missing = pathlib.Path(tmp_path / "没有这一份.md")
    try:
        original, svc_mod.manual.OPERATOR_MANUAL = svc_mod.manual.OPERATOR_MANUAL, missing
        r2 = client.get(manual.MANUAL_PATH)
        assert r2.status_code == 500 and "读不了那份手册" in r2.text, r2.text[:200]
    finally:
        svc_mod.manual.OPERATOR_MANUAL = original


def test_the_panel_links_to_the_manual():
    """面板上要有入口 —— 手册再好，找不到就等于没有。"""
    page = (pathlib.Path(service.__file__).resolve().parent / "console.html").read_text(encoding="utf-8")
    assert 'href="/manual"' in page, "面板上没有去手册的入口"
