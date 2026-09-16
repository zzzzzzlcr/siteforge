# 验证报告（第八轮）：`page_signature()` 里到底有没有问卷正文 —— **没有**（第一件判据没过）

**状态**：DONE_WITH_CONCERNS —— 前提（闸口/窗口）先确认过、跑了一趟干净的、**里程碑没过就按指令停了**；
**自测仍未过**（本来就不该往下跑三遍）
**commit**：见文末（只本地 commit，没 push）
**一行测试结论**：`pytest tests/ -q` → **402 passed / 12 skipped**（这一轮没改代码，只做测量）

## 0. 先答第二件（这一轮的判据）：**`page_signature()` 里没有问卷正文** ✗

跑法：一趟干净的（见 §1），三遍都读过 trace 里每一步的 `page_sig`（它就是
`page_signature()` 的产物）：**三遍里没有一步的 `page_sig` 含问卷文本**
（判据词：`Roughly, how many miles` / `your ZIP code` / `What state do you live in` / `Almost done Fill in`）。

最远走到 `/auto/?text=Sedan…`（问卷页的地址 ✓）、**执行过 5 步**，
最后一条执行过的步的 `page_sig` 原样（前 200 字）：

```
The listings featured are compensated and this influences their order on this site. Advertising
disclosure Best Extended Auto Warranty in September 2026 Take our short quiz to find an Auto
Warranty su
```

**全是主帧那页落地页的推广文案 —— 问卷正文一个字都没有** ✗

按指令「没有就立刻停，把那一刻的三个列表原样打出来」——**停了**（没往下跑三遍）。
三个列表我能拿到的部分如下（`why` 现在只把地址列全，**帧本身那两个表这一支没打**，
这是我的诊断还没覆盖到的一格，如实说）：

```
第一条跳过（原样，全文）：
地址对不上：要含「https://chameleon-na.www.gowizard.com/forms/7878/default/gowizard」；
手里这些地址都不含它：
  https://www.gowizard.com/auto/?text=Sedan&touchpointId=1fbb0d45-…&instance=1#chameleon
  ｜ https://id-msp.newsbreak.com/sync-nbu?source=2&host=www.gowizard.com

账本里的 frame_id（`self.frames` 的来源，从候选产物 STATS 抄的）：
  {'', '730E606E4EBD6925B89AC4A2E1514B71'}      ← 非空（所以「刷新活帧」那道门是能开的）
读页面用的帧列表（`_read_frames()` 的结果）：**没打**（见上，诊断只覆盖了正文那一支）
self.live_frames：**没打**（同上）
```

## 1. 第一件：跑之前先掐掉「打扰」——两条都确认过，其中一条是我自己制造的

- **闸口及时回话** ✓：驱动脚本仍然是每 4 秒轮询、`waiting` 就回 `continue`（这一趟全程没在闸口停）；
- **中途别丢窗** ✗→✓：我先加了一个「窗口死了就重开并 `reopen`」的看门狗 ——
  **结果它自己就是那条打扰**：`POST /run` 那一刻服务会给探路**换一个干净窗口**（R-F1），
  看门狗把「旧窗没了」读成「窗口死了」→ 又去开一个 → 与服务的窗口管理打架，
  上一趟就是这么被搅掉的（`reopen 失败: timed out`）。**这一趟把看门狗撤了**，干净。

## 2. 第三件（若里程碑过了再看第一条跳过的 why）—— 里程碑没过，但 `why` 还是贴了

见 §0：第一条跳过的原文已经贴出。它说明：`_urls()` 手里只有
**主帧地址 + 主帧里那个广告 iframe 的 `src`**（`id-msp.newsbreak.com`），
**没有**问卷帧的任何地址。

## 3. 这一轮量出来的「根上不够」在哪（控制器要的那一格）

对照实验（第七轮）已经证明**从外面读得到**（`cdp eval --frame-id <问卷帧>` 原样读出
`Progress: 80% … See My Match`）。这一轮又量到：**主帧的 DOM 里没有那个问卷 `<iframe>`** ——
`_iframe_srcs()`（主帧 `getElementsByTagName('iframe')` 的 `src`）拿到的是**广告帧**，
问卷帧**不在其中**。也就是说：

- 「主帧里读 iframe 的 `src`」这条路 —— **够不着问卷帧**（它不在主帧的 DOM 里）；
- 「声明/活帧的 `location.href`」这条路 —— 这一趟手里也没有它（帧表本身没打出来，见 §0）；
- **唯一被证明行得通的是 CDP 那一侧**：`observe` 的模型里带着每条元素的 `frame_path`
  （`["main", "<frameID>"]`）—— 外面对照实验正是这么找到帧的。

→ 所以下一轮该做的**不是再加一条读地址的路**，而是：**读页面用的帧表必须来自
「当场那次 `observe` 的模型」**（`_note_live_frames` 那条路），并且
**「刷新活帧」的门槛不能是「某个帧还能读」**（广告帧永远满足它）——
要么每次判据前都看一眼模型，要么把「哪些帧里有本流程的元素」当门槛。
⚠️ 这一格**我这一轮没有实现**（按指令：里程碑没过就停，不往下跑）。

## 4. 顾虑

1. **`self.live_frames` / `_read_frames()` 这两个表这一支打不出来** —— 我的自证诊断只加在
   正文那一支上；下一条跳过要是在**地址**那一支，就还是看不见帧表。**这是诊断的缺口**，
   下一轮第一件事应该是把它补到两支都有（两行的事）。
2. 三遍都没见成功文案（也没走远：执行 5 步就全跳过了）→ `delivered=false`，
   `forms/sites/` 里仍然只有 `.gitkeep`。**这一轮没有交付，也没有把三遍跑完**（按指令停的）。
3. 环境那一趟（`chrome-error://chromewebdata/`）这一趟没出现；按控制器口径，
   真出现时要单独标出来，且**不许**当「这遍过了」。
