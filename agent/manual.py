"""把 `docs/*.md` 渲染成一个**能直接看的网页**（`GET /manual`）。

为什么不是「再写一份 HTML」：那会变成两个来源，改一份忘一份，运营看到的就是旧那份。
为什么不是「引一个 markdown 库」：这一份文档只用到**六种写法**（标题 / 表格 / 代码块 /
**粗体** / `行内码` / 列表 / 引用），为它装一个库不划算（计划 §「不添依赖」）。

⚠️ **这是给运营看的东西，不是通用 markdown**：认不出来的写法**原样吐出来**
（宁可屏幕上出现 `| --- |` 也不许吃掉一句话）。`render()` 是纯函数，自检在
`tests/test_manual.py`（同一份文档真跑一遍）。
"""
from __future__ import annotations

import html
import pathlib
import re

#: 运营手册在盘上的位置（相对仓库根）。⚠️ 只有一份来源 —— 页面渲染的就是这个文件。
DOCS_DIR = pathlib.Path(__file__).resolve().parent.parent / "docs"
OPERATOR_MANUAL = DOCS_DIR / "运营手册-面板修站.md"

#: 路由（与 `CONFIGCHECK_PATH` 那些同一个风格）。
MANUAL_PATH = "/manual"

_INLINE = ((re.compile(r"\*\*(.+?)\*\*"), r"<b>\1</b>"),
           (re.compile(r"`([^`]+)`"), r"<code>\1</code>"))


def _inline(text: str) -> str:
    """**先转义、再上标记**（顺序反了的话文档里写的 `<b>` 就成了真标签）。"""
    out = html.escape(text, quote=False)
    for pat, rep in _INLINE:
        out = pat.sub(rep, out)
    return out


def _table(rows: list) -> str:
    """一张表：第一行表头、第二行 `|---|` 分隔、其余是数据。列数不齐也照摆。"""
    head, body = rows[0], rows[2:]
    cells = lambda line: [c.strip() for c in line.strip().strip("|").split("|")]  # noqa: E731
    out = ["<table><thead><tr>"]
    out += ["<th>%s</th>" % _inline(c) for c in cells(head)]
    out += ["</tr></thead><tbody>"]
    for line in body:
        out += ["<tr>"] + ["<td>%s</td>" % _inline(c) for c in cells(line)] + ["</tr>"]
    out += ["</tbody></table>"]
    return "".join(out)


def render(text: str) -> str:
    """这份文档 → 一段 HTML（认不出来的行**原样**摆进 `<p>`，不吞）。**纯函数**。"""
    lines = str(text or "").splitlines()
    out: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):                       # 代码块：里面一个字都不动
            i += 1
            block = []
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            out.append("<pre>%s</pre>" % html.escape("\n".join(block), quote=False))
        elif line.startswith("|") and i + 1 < len(lines) and lines[i + 1].startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows) if len(rows) > 1 else "<p>%s</p>" % _inline(rows[0]))
            continue
        elif re.match(r"^#{1,6} ", line):
            lvl = len(line) - len(line.lstrip("#"))
            out.append("<h%d>%s</h%d>" % (lvl, _inline(line[lvl + 1:]), lvl))
        elif line.strip() in ("---", "***"):
            out.append("<hr>")
        elif line.startswith(">"):
            out.append("<blockquote>%s</blockquote>" % _inline(line.lstrip("> ")))
        elif re.match(r"^\s*[-*] ", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*] ", lines[i]):
                items.append("<li>%s</li>" % _inline(re.sub(r"^\s*[-*] ", "", lines[i])))
                i += 1
            out.append("<ul>%s</ul>" % "".join(items))
            continue
        elif re.match(r"^\s*\d+\. ", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\. ", lines[i]):
                items.append("<li>%s</li>" % _inline(re.sub(r"^\s*\d+\. ", "", lines[i])))
                i += 1
            out.append("<ol>%s</ol>" % "".join(items))
            continue
        elif line.strip():
            out.append("<p>%s</p>" % _inline(line))
        i += 1
    return "\n".join(out)


#: 那一页自己那点样式（**不复用面板那套**：这一页只给运营看文档，越简单越好）。
_CSS = """
:root{color-scheme:light dark}
body{margin:0 auto; padding:28px 20px 80px; max-width:52rem;
     font:15px/1.75 -apple-system,"Segoe UI","Noto Sans CJK SC",sans-serif}
h1{font-size:26px; margin:0 0 6px} h2{font-size:20px; margin:34px 0 8px; padding-top:14px;
     border-top:1px solid #8884}
h3{font-size:16.5px; margin:22px 0 6px} p{margin:8px 0}
code{background:#8882; border-radius:4px; padding:1px 5px; font-size:13.5px}
pre{background:#8882; border-radius:6px; padding:10px 12px; overflow:auto; font-size:13.5px}
table{border-collapse:collapse; margin:10px 0; width:100%; font-size:14px; display:block; overflow-x:auto}
th,td{border:1px solid #8884; padding:6px 9px; text-align:left; vertical-align:top}
th{background:#8882} blockquote{margin:10px 0; padding:6px 12px; border-left:3px solid #8886; color:#7776}
hr{border:0; border-top:1px solid #8884; margin:26px 0}
.head{margin:0 0 18px; padding-bottom:12px; border-bottom:2px solid #8886}
.back{font-size:13px}
"""


def page(body_html: str, *, title: str = "运营手册") -> str:
    """那份 HTML 套成一个能独立打开的页面。"""
    return ("<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body>"
            "<p class=\"back\"><a href=\"/console\">← 回到控制台</a></p>%s</body></html>"
            % (html.escape(title), _CSS, body_html))
