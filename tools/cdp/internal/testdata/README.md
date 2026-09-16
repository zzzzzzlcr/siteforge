# internal/testdata —— observe 集成测试的 fixture

这 6 个 html 是 R3 探针（`docs/probes/2026-09-16-observe-r3/`）用过的那批难页面，
由 Task 2 搬进来，供 `observe_integration_test.go` 用 `httptest` 起服务观测。

| 文件 | 谁在用 | 档 |
|---|---|---|
| `base.html` | `TestObserveLightDOM` | light DOM：hero 主按钮 + 页脚同名按钮 + cookie 横幅 |
| `shadow.html` | 三条 shadow 测试（陷阱 ①②③） | 两层嵌套 open shadow root |
| `outer.html` / `outer_same.html` / `inner.html` | **Task 4**（跨源 iframe 合并，未启用） | 跨源 / 同源 iframe |
| `form.html` | 表单片段（备用） | — |

## ⚠️ `shadow.html` 与探针副本**不同**（有意为之，别"修回去"）

```diff
-<div id="host1"></div>
+<main><div id="host1"></div></main>
```

（外面还加了一段说明注释。其余 5 个文件与 `docs/probes/2026-09-16-observe-r3/fixtures/`
逐字节相同，`md5sum` 已核对。）

**为什么必须加**：陷阱③那条断言（`TestObserveShadowRegionNotAllBody`，`region` 不得全
退化成 `body`）需要页面里**有 landmark**，而且 landmark 必须在 shadow **外面**。
2026-09-16 实测：没有 landmark 时，`region` 的逻辑（沿合成树找 `header/nav/footer/
aside/main/hero`）对**两种**实现给同一个答案 ——

| 实现 | `#submit` 的合成树链 | 结果 |
|---|---|---|
| 正确（走 `getRootNode().host`） | `button#submit → div#host2 → div#host1 → body → html` | `body` |
| 陷阱③（只走 `parentElement`，到 shadow root 顶就断） | `button#submit` | `body` |

两边都 `body` → 断言恒红、**对任何实现都红**，也就测不出陷阱③了。加 `<main>` 后
（链变成 `… → div#host1 → main → body → html`）两者才分道扬镳：正确实现 `main`、
错误实现 `body`。这是**加强**断言，不是放宽 —— 断言一个字没改，改的是被观测的页面。

注意：landmark 加在 shadow **里面**是没用的 —— 错误实现的 `parentElement` 遍历照样
找得到它，仍然分辨不出来。必须在 shadow 外面（真实站点就是这个形态：Salesforce
Lightning 那种把表单塞进 shadow 的页，外面有 header/nav/main）。

## 🚫 不要用探针目录覆盖这里的 fixture

```bash
# 别干这个：
cp docs/probes/2026-09-16-observe-r3/fixtures/*.html tools/cdp/internal/testdata/
```

`docs/probes/.../fixtures/` 是**历史记录**（探针当时真跑过的东西，README 里的数字都基于它），
**不许改**；`testdata/` 是**活的测试输入**。两者对 `shadow.html` 的差异是有意的。
覆盖回去的后果是陷阱③退回「永久红且空转」，而下一个人看到一条怎么修都红的断言，
第一反应多半是去削弱它 —— 那是本项目反复踩过的坑。

原委与实测数据：`.superpowers/sdd/2026-09-16-tool-layer-observe/task-3-report.md`（第 3 节）。
