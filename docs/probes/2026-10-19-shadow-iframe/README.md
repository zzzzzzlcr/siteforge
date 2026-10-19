# 跨域子帧 / 影子宿主 —— 2026-10-19 合成页验证

**结论**：两格都在**真浏览器**（Bit `192.168.1.222` / 8100）用**合成页**验了；真站没有靶子，
spec 对跨域帧接受**静默放行**。

| 格 | 合成页 | 读数 |
|---|---|---|
| **跨域子帧** | `data:` 页 + `sandbox="allow-scripts"` 的 `srcdoc` iframe | `frames=1`、`contentDocument` **不可访问**；四条钩子全返空（不炸、不点） |
| **影子宿主** | `data:` 页 + `#host.attachShadow({mode:'open'})` 里一颗 `[data-click="close"]` | 修复前 ad 钩子返空；修复后答 `#shadow-close|Close` |

## 影子宿主：为什么修复前返空

落点那一摞（真浏览器）：

```json
{"rect":[100,200,88,54],
 "stack":["div#host","html"],
 "hostContainsE":false,
 "shadowContainsE":true,
 "eContainsHost":false}
```

`document.elementsFromPoint` 在影子边界上只返回**宿主**；`host.contains(影子里的按钮)` 是 **false**。
`_AD_CREATIVE_JS` 原来的落点判据是 `h===e||e.contains(h)||h.contains(e)` ⇒ 把它误判成「被盖着」。
修复：**加上 `h.shadowRoot && h.shadowRoot.contains(e)`**（`agent/template.py` 的 `_AD_CREATIVE_JS`）。

⚠️ `_COVER_JS` **不遍历 shadow**：它在 `document.getElementsByTagName('*')` 里找不到影子里的目标
⇒ 返回空串 = **静默放行**（不是「被盖着」）。这条边界照实记着，不在本刀改。

## 跨域子帧：为什么是静默放行

合成页的 iframe 用 `sandbox` + `srcdoc`（opaque origin），父页 `contentDocument` 访问返回 false。
`_CONSENT_ROOTS_JS` 只走 `document` + open shadow root，**不走 iframe** ⇒ 四条钩子全返空。
这正是 spec 接受的口径：够不着就**不猜、不点**；账上不会记成做成。

## 复现

```python
# 页面 HTML（源；真跑时 base64 成 data: URL）
SHADOW = '''<!doctype html><body>
<div id="host" style="position:fixed;left:100px;top:200px;width:88px;height:54px"></div>
<script>
const host=document.getElementById('host');
const root=host.attachShadow({mode:'open'});
root.innerHTML='<button id="shadow-close" data-click="close" '
 + 'style="width:88px;height:54px">Close</button>';
</script></body>'''

IFRAME = '''<!doctype html><body>
<iframe sandbox="allow-scripts" style="width:120px;height:60px"
 srcdoc="<button id='frame-close' data-click='close' style='width:110px;height:55px'>Close</button>">
</iframe></body>'''
```

```bash
# 每页一个 data: URL；Bit 窗口里 navi 过去，再跑只读钩子表
/opt/skills/auto-farm-skill/cdp navi "data:text/html;base64,..." \
    --host 192.168.1.222 --port 61129
python3 -I hook_by_hook.py /tmp/ci_synth.py "$WS"
```

## 测试

- `tests/popup_probe_driver.js` 加到 **21 格**：新增「广告 creative：落点在 open shadow 里
  （宿主是落点）」；正控失败数 11→12。
- 宿主 / 8100 容器：`test_popup_hooks_js.py` **5 passed**。

⚠️ 这两格是**合成页**证据，不是真站：真站至今没有跨域帧 / 影子宿主的可点靶子（实测 §6-6/§6-7）。
