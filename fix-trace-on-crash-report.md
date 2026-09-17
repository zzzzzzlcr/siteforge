# 修复报告：「产物死了」不许表现成「走完了」（异常路落 trace + 那个抛 + 每遍一个目录）

**状态**：DONE_WITH_CONCERNS —— 四件都做了、都有钉子/证据；**自测仍未过**（差在哪见 §4）
**commit**：见文末（只本地 commit，没 push）
**一行测试结论**：`pytest tests/ -q` → **399 passed / 12 skipped**（新增 2 条钉子；旧断言一条没放宽）

## 0. 先认错：我上一轮把 trace 的行数当成了产物的步数

我说「产物只有 10 步」—— **看错了**。产物本体（`/tmp/gwacc4/kept/gowizard.candidate.2.py`）
是 **20 个状态 / 28 步**，整条漏斗都在里面。10 是 **trace 的行数**。
控制器把这一处扳正了，谢谢 —— 这正是「拿行数当结论」的老毛病。

## 1. 真相：第 11 步抛了，而 trace 一个字没落

三条证据（控制器给的，我复核过）：`11-before.png` 在（37,845 字节 → 第 11 步**进了**
`_run_step`）／trace 里 `"step": 11` **0 行**／没有 `11-after.png`。
→ 第 11 步死在 `self._perform(...)` 里；`run()` 的 `except Exception` 把它吞成一句日志就
`return False`，**trace 一行都没写** —— 而自测**只读 trace**，于是把「产物死了」
读成「走完了 10 步、0 跳过」。

**② 那次崩溃的原文（我照原样复现到的）** —— 用留着的产物候选 + 那份**没更新的**部署
`common.py` 在真窗口上跑到第 11 步：

```
2026-09-17 04:00:59 [ERROR] form_fill_gowizard: [crash_probe] 出错：CDPHelper.form() got an unexpected keyword argument 'strict'
```

**就是控制器猜的那条**：我这一轮给 `_do` 的 `form` 路径加了 `strict=` / `expect_label=`，
而**部署的那份 `common.py` 还是旧的签名**（`/tmp/gwacc4/forms/common.py` 是我 22:17 抄的
那一版）→ `TypeError` → 整趟死。

## 2. 四件都做了

**① 异常路必须落 trace**（`agent/template.py`）：
- `_run_step` 里 `_perform` 抛出来的东西现在**当场落一行**：
  `{"step": N, "ok": false, "error": "TypeError: …", "note": "产物自己在第 N 步出错了（…）—— 这一步没做成，后面也不做了"}`，
  然后再 `raise`；`run()` 的 `except` 也补了一行（防抛在 `_run_step` 之外）。
- 判据上分得开：`10 步做完就结束` 是 trace 里 10 行**都 ok**；
  `第 11 步炸了` 是 trace 里有一行 **`ok: false` + `error`** —— 自测的 `_verdict` 读的就是
  第一条 `ok is False`，于是它会说「卡在第 11 步：产物自己出错了（TypeError: …）」。

**③ 修那个抛**：两处一起。
- **部署那份刷新了**（`cp` 仓库的 `forms/common.py`；md5 两边一致 `26720beeacb0…`）。
  这一条是**规矩**：铺产物时必须把同版本的 `common.py` 带过去（那份文件的头里写着）。
- 产物侧**不再被它炸死**：`_do` 的 form 分支 `try: … except TypeError:` → **退回老路**
  （不带严格闸照填）+ 一条**大声**的警告（「部署的那份 common.py 不认识 --strict /
  --expect-label；这一步退回不做消歧的老路 —— 值可能落进别的框」）。
  一次版本不齐不该让整趟跑死，但**也不许静默降级**（降级的方向正是这轮要治的东西）。

**④ 每一遍一个子目录**（`agent/selftest.py`）：`run_dir/<名字>/` —— 三遍共用目录时
`11-before.png` 只有一份、分不清是哪一遍的（这轮排查正是被它绊了一下）。
trace 落进子目录，截图跟着 trace 走。

**顺手**：trace 的 `url` 字段里混着 cdp 的 stderr 噪声
（`ERROR: could not unmarshal event: … IPAddressSpace`）—— `_url()` 现在**只取第一行**
（`CDPHelper.eval` 交出来的是 stdout+stderr 串在一起，地址本来就是一行）。

## 3. 钉子（2 条，都在这轮新加的那一族旁边）

- `test_a_crash_inside_a_step_leaves_a_failed_line_in_the_trace`：步骤里抛 → trace 里
  **必须**有一行 `ok: false`，带 `step` / `error` / 人话 note。
- `test_the_artifact_survives_a_stale_common_py_but_says_so`：`common.py` 不认识新参数 →
  **照填**（退回老路）+ 日志里大声说；不许抛、也不许静默。

## 4. 真站复跑：跳过大减，但**成功文案还是没出现**

最后一趟（`job-dd048b007327`）的三遍 trace（每遍一个子目录 ✓）：

```
baseline | 执行 24 步 | 跳过 7 步 | 没做成 0 步     ← 历史最深的一次（此前最好 17）
rerun    | 执行  8 步 | 跳过 1 步 | 没做成 1 步
```

- **24 步真做了、0 步没做成** = 「等这一页加载完再判 `when`」那条修得对
  （修之前同一趟是「执行 3 / 跳过 31」）。
- **第一条跳过是第 25 步**（baseline），`why` 原文：
  「地址对不上：要含 `https://chameleon-na.www.gowizard.com/forms/7878/default/gowizard`，
  现在是 `https://www.gowizard.com/…`」—— 又是「子帧地址」那一族，
  只是这次 `iframe.src` 那条路也没救到（**为什么没救到，这一轮没钉死**：
  可能是那一刻主帧里那个 `<iframe>` 的 `src` 为空/被换过，也可能是件被摘掉了）。
- **成功文案 `Good news - We've matched you! Your quote is on the way!` 没有出现**
  → `delivered=false`、`py_path=null`，`forms/sites/` 里仍然只有 `.gitkeep`。
  **「走深 24 步」不算成功** —— 按控制器的口径，只有那句文案出现才算。

## 5. 顾虑

1. **第 25 步那条跳过没钉死**（同上）：它是这一轮唯一剩下的、看得见的拦路石，
   而它的形状与上一轮修掉的那条**同族但不同路**（上一轮修的是「主帧 `iframe.src` 读得到」，
   这一条发生在那条路也读不到的时候）。下一轮该量的是**那一刻主帧里到底有没有那个
   `<iframe>`、它的 `src` 是什么**（探针现成：`/tmp/gwacc4/zipwatch.py` 那套改一下就能用）。
2. `_wait_ready` 的等待上限是 10 秒、判 `when` 失败时**才**等（不在每一步都等）；
   重站点上「加载完」不等于「那句话有了」（SPA 的异步渲染），这条边界没量。
3. 「`goto` 之后等加载」那一格**没有钉子**：试过两版替身（readyState 与正文耦合），
   都会随调用次序翻面 —— 与其留一条会骗人的绿，不如明说没有（代码在 `_wait_ready`，
   真站上验过：那一趟 31 步跳过 → 修完 7 步）。
4. 交付目录仍是空的：**这一轮没有交付任何东西**。
