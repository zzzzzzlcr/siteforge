"""复跑一趟 JSON 配置的**子进程入口** —— 全仓**唯一** import 生产执行器的地方。

## 为什么要单开一个文件（而不是在 `jsondiag.py` 里 import）

`jsondiag.py` 的 `diff_config` 是**纯函数**（不开浏览器、不 import 生产代码）——
那条性质要保住：面板上「看 diff」不该顺带把生产执行器以及它背后那一串
依赖（selenium 系、`openai`……）拉进 agent 进程。

⇒ 真跑那一半被隔在**这个文件**里，而且它只在**子进程**里被跑到。

## ⚠️ 路径：`/opt/skills/auto-farm-skill` 是**只读**的（`plan.md` §5）

B 线**不修改它的任何文件**，只把它的 `form_executor/` 挂到 `sys.path` 上 import。
`sys.path` 的改动只发生在 `main()` 里 —— **import 这个模块不产生任何副作用**
（否则测试一 import 就把生产目录挂进自己的进程了）。
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Optional

__all__ = ["EXECUTOR_DIR", "FACTS_JS", "FactsUnmeasured", "build_executor",
           "facts_from"]

#: 生产执行器所在目录（**只读**，挂到 `sys.path` 上 import 用）。
EXECUTOR_DIR = os.environ.get("SITEFORGE_JSON_EXECUTOR_DIR") or (
    "/opt/skills/auto-farm-skill/form_executor")


class FactsUnmeasured(Exception):
    """**页面事实上没量着** —— `cdp.eval` 回了空串 / 半句 JSON。

    ★ 它不是「页面上什么都没有」：那是**量着了、事实是空的**（合法的、
    有信息量的结果）。这个是执行上下文没了 / 页面正在导航 —— 去查浏览器。
    """


#: 在页面里量「现在长什么样」的那段 JS。
#:
#: ★ 它量的是**执行器找元素时看到的那几样**（`element_finder.py:44-56`），不是别的东西：
#: 那个 finder 扫 `querySelectorAll('*')`、拿 `textContent **+ value**`、
#: 只收 `offsetWidth > 0` 的 —— 所以这里也收 `value`
#: （<input type=submit value="Send"> 的字在 value 里，不在 textContent 里）。
#:
#: ⚠️ `body_text` 收的是 `innerText`（**可见**文字的拼合），它与
#: 「逐个元素看 textContent」不是一回事，但对「这段话页面上还有没有」这个问题，
#: 两者的差别只在隐藏元素上 —— 而 finder 自己也只收可见的。
FACTS_JS = r"""
(function(){
  function vis(el){ return !!(el && el.offsetWidth > 0); }
  function txt(el){ return ((el.textContent||'') + ' ' + (el.value||'')).trim(); }
  var actions = [], seen = {};
  var all = document.querySelectorAll('*');
  for (var i = 0; i < all.length; i++) {
    var el = all[i], tag = el.tagName;
    if (tag === 'HTML' || tag === 'BODY' || tag === 'SCRIPT' || tag === 'STYLE') continue;
    if (!vis(el)) continue;
    var interactive = (tag === 'A' || tag === 'BUTTON' || tag === 'LABEL'
                       || tag === 'INPUT' || tag === 'SELECT'
                       || el.getAttribute('role') === 'button'
                       || el.hasAttribute('onclick'));
    if (!interactive) continue;
    var t = txt(el);
    if (!t || t.length > 200 || seen[t]) continue;
    seen[t] = 1;
    actions.push({text: t});
  }
  var fields = [];
  var ins = document.querySelectorAll('input, textarea, select');
  for (var j = 0; j < ins.length; j++) {
    var f = ins[j];
    if (!vis(f)) continue;
    var label = '';
    if (f.labels && f.labels.length) label = (f.labels[0].innerText || '').trim();
    if (!label) label = (f.getAttribute('aria-label') || '').trim();
    if (!label) label = (f.getAttribute('name') || '').trim();
    fields.push({label: label,
                 placeholder: (f.getAttribute('placeholder') || '').trim(),
                 type: (f.getAttribute('type') || f.tagName.toLowerCase()).trim()});
  }
  return JSON.stringify({
    url: location.href,
    body_text: (document.body ? document.body.innerText : '').slice(0, 20000),
    actions: actions,
    fields: fields
  });
})()
"""


def _clean_rows(rows: Any, keys: tuple) -> list:
    """把量到的行归一：**全是空白的丢掉**、缺的格补成空串。

    ⚠️ 全是空白的那些**不摆进来**：它们不是「页面上有一个空按钮」，
    是「量到了个寂寞」—— 摆进去会让 `diff_config` 的 `saw` 里多出假候选，
    而人对着假候选是看不出真问题的。
    """
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        clean = {k: str(row.get(k) or "").strip() for k in keys}
        if not any(clean.values()):
            continue
        out.append(clean)
    return out


def facts_from(raw: str) -> dict:
    """浏览器里量回来的那条字符串 → `diff_config` 要的那份**页面事实**。

    取不到 ⇒ 抛 `FactsUnmeasured`（**不是**回一份空事实）。
    """
    text = str(raw or "").strip()
    if not text:
        raise FactsUnmeasured(
            "页面事实施量不到：`cdp.eval` 回的是空串 —— 多半是页面正在导航、"
            "执行上下文被销毁了。这是「**没量着**」，不是「页面上什么都没有」。")
    try:
        got = json.loads(text)
    except Exception as exc:
        raise FactsUnmeasured(
            "页面事实读不成 JSON（很可能是执行上下文中途没了、只写了半句）：%s。"
            "这与「空串」是同一件事 —— **没量着**。" % exc)
    if not isinstance(got, dict):
        raise FactsUnmeasured(
            "页面事实不是一份对象（是 %s）。" % type(got).__name__)
    return {
        "url": str(got.get("url") or ""),
        "body_text": str(got.get("body_text") or "").strip(),
        "actions": _clean_rows(got.get("actions"), ("text",)),
        "fields": _clean_rows(got.get("fields"), ("label", "placeholder", "type")),
    }


def build_executor(config: Any, *, cdp: Any, profile: Optional[dict] = None,
                   log: Any = None) -> Any:
    """构造生产那个 JSON 配置执行器 —— ★ **模型在这里被钉死为关**。

    ★★ 控制者 2026-09-21 定的：**复跑这一层一次模型都不许调。**

    生产那条路模型是活的（【我量的·2026-09-21】`json_pipeline.py:1245` 真建
    `openai.OpenAI(...)`；`json_executor.py:155` 的 `AI_RECOVERY_BUDGET = 10`
    就是它的预算）。诊断跑**不许继承这个** —— 否则「修一次配置」的成本
    与「重新探索一个站」是一个量级，B2 存在的理由就没了。

    **两道闸一起上**（`tests/test_jsondiag.py` 有一条用例钉着）：

    | 闸 | 在哪 | 挡什么 |
    |---|---|---|
    | `llm_client=None` | 构造函数默认值 | 根本没有客户端可调 |
    | `config["no_llm"] = True` | `json_executor.py:445` / `:1753` | 将来有人**新加**一条 LLM 路径时，它也得认这道闸 |

    只上一道的话：前者挡不住「有人以后从别处拿到客户端」，后者挡不住
    「新路径忘了读 `no_llm`」。两道的失效方式不同，所以两道都要。

    ⚠️ **不改调用方那份配置** —— `no_llm` 写在**副本**上。
    """
    from json_executor import JSONExecutor  # ← 生产那份，只读 import

    cfg = copy.deepcopy(config)
    cfg["no_llm"] = True
    return JSONExecutor(cfg, dict(profile or {}), cdp, log=log, llm_client=None)


def main(argv: Optional[list] = None) -> int:
    """子进程入口：复跑一份配置，把 `摘要 + 页面事实` 打在**一行**上。

    退出码：0 = 量着了（那一行在 stdout 上）；非 0 = **没量着**（原因在 stderr）。

    ⚠️ 这是**唯一**碰生产代码的地方，而且它只读：
    `sys.path` 挂上 `/opt/skills/auto-farm-skill` 的两个目录就 import，
    **一个文件都不改**（`plan.md` §5）。
    """
    import argparse
    import logging
    import traceback

    ap = argparse.ArgumentParser(description="复跑一份 JSON 配置并量下页面事实")
    ap.add_argument("--config", required=True, help="那份配置的 JSON 文件")
    ap.add_argument("--ws-url", required=True, help="浏览器的 WebSocket 地址")
    ap.add_argument("--entry-url", default="", help="开跑前先导航到这个地址")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    # 只读地挂 path：执行器在 form_executor/，CDPHelper 在 forms/。
    for path in (EXECUTOR_DIR, os.path.join(os.path.dirname(EXECUTOR_DIR), "forms")):
        if path not in sys.path:
            sys.path.insert(0, path)

    from common import CDPHelper  # noqa: E402  ← 生产那份，只读 import

    cdp = CDPHelper(args.ws_url)
    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)

    if args.entry_url:
        cdp.navigate(args.entry_url)

    executor = build_executor(config, cdp=cdp)
    try:
        executor.run()
    except Exception:
        # 执行器自己炸了也要把**它已经记下的**摘要带出去 —— 「跑挂了」与
        # 「跑完了、失败在第 3 步」是两份不同的证据，不能因为前者就什么都不说。
        print("执行器抛了异常（摘要仍然带出去）：\n" + traceback.format_exc(),
              file=sys.stderr)

    try:
        summary = executor.get_summary()
    except Exception as exc:
        print("取不到执行摘要，这次复跑**没量着**：%s" % exc, file=sys.stderr)
        return 2

    try:
        facts = facts_from(cdp.eval(FACTS_JS))
    except FactsUnmeasured as exc:
        print("量不到页面事实，这次复跑**没量着**：%s" % exc, file=sys.stderr)
        return 3

    sys.stdout.write(RESULT_MARK + json.dumps(
        {"summary": summary, "facts": facts}, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
