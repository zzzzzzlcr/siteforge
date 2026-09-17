# 修复报告：判据为什么还判「不像」（进度条那条线索核了 + 跳过自证 + 两处修）

**状态**：DONE_WITH_CONCERNS —— 三件都做了、量了；**自测仍未过**（差在哪见 §4，不粉饰）
**commit**：见文末（只本地 commit，没 push）
**一行测试结论**：`pytest tests/ -q` → **400 passed / 12 skipped**（新增 1 条钉子；旧断言一条没放宽）

## 1. 第一件：第 25 步 / 跳过那一族 —— 量到的（探针就是新加的 `why`）

第六轮的 `why` 现在会把**手里实际有的东西**原样摆出来。最近一趟（`gowizard-20260917-060317`）：

```
baseline 第 12 步跳过：
  正文里没有「Progress: 20% How soon do you want your new」；
  页面上**实际看到的**开头那段是：「___ The listings featured are compensated and this
  influences their order on this site. Advertising disclosure … Take our short quiz…」
rerun / delay 第 1 步跳过：
  地址对不上：要含「https://console.bitbrowser.net/」；
  手里这些地址都不含它：https://www.gowizard.com/auto-warranty/ ｜ https://id-msp.newsbreak.…
```

**三问的答案**：
1. **主帧里到底有没有那个 `<iframe>`**：**有**（`why` 里列出的地址里就带着主帧那些 iframe 的
   `src` —— 比如 `https://id-msp.newsbreak.…` 这种广告帧）。**所以「主帧里读不到 iframe」这条不成立**
   （不是「嵌得更深 / 在 shadow 里」那一族）。
2. **判成「不像」的直接原因，这次是正文那一侧**：要的那句
   （`Progress: 20% How soon do you want your new…`）**在子帧的问卷里**，
   而实际看到的正文是**主帧那页落地页的推广文案** —— 也就是说 `page_signature()` 那一步
   **没把子帧的正文拼进来**。
3. **差在哪**：`url_contains` 那一侧手里是「主帧地址 + 主帧里各 iframe 的 src」；
   要的那串是子帧自己的 `location.href`（`chameleon-na…/forms/7878/…`）——
   主帧里那个 `<iframe>` 的 `src` 与它**不是同一个串**（部件是用 JS 造帧/换 src 的，
   `src` 与帧内 `location.href` 会不一样），所以这一路仍然对不上。

## 2. 第二件：协调者的线索（`Progress: N%` 会不会动）—— **会动，但它不是这次的原因**

真站量了 14 分钟（探针 `/tmp/gwacc4/progresswatch.py`，1.5 秒一次，
原样记录在 `/tmp/gwacc4/progresswatch.jsonl`）：

```
05:41:47  Progress: 0%   What is the year, make & model of your vehicle? …
05:42:30  Progress: 10%  Roughly, how many miles are on the vehicle? …
05:43:24  Progress: 20%  How soon do you want your new auto warranty? …
05:44:24  Progress: 30%  What state do you live in? …
05:44:33  Progress: 40%  What's your ZIP code? …
05:44:39  Progress: 50%  Hold on - we're finding suppliers…
05:44:45  Progress: 60%  Almost done Fill in your last few details…
05:44:51  Progress: 70%  Only one more step left. Email Address: …
```

**结论**：进度条**确实会变**（0→10→…→70），每一次变化都是**换题**那一下。
但这一趟的跳过**不是**它引起的：判据要的是「`Progress: 20% How soon…`」，
而页面上**实际看到的正文里连 `Progress` 都没有**（是主帧那页落地页文案）——
**问题在「子帧正文没拼进来」，不在进度条慢半拍**。
所以「把 `Progress:` 从判据里去掉」这条**我没有做**：它治的不是这个病，
而在没有量出「去掉之后判据还唯不唯一」之前动它，属于猜。
（这条线索本身成立，记在这里备查：**如果**哪一天子帧正文拼进来了而仍判不像，
它就该是第一个嫌疑。）

## 3. 修的两处 + 自证

- **跳过自证**（第一件要的工具，也是这轮定位的功臣）：`_when_why` 现在把
  **要的东西**与**手里实际有的东西**一起摆出来 —— 地址那一侧列出**全部**可用地址
  （主帧 + 主帧里各 iframe 的 `src` + 各帧的 `location.href`），正文那一侧列出
  **实际看到的那一段**（240 字）。下一个同样的病不用再靠人翻 trace。
  钉子：`test_the_skip_reason_carries_what_we_actually_saw`。
- **`page_signature()` 并上最近一次 observe 的正文**（`self._last_model["page_text"]`）——
  observe 的 page_text 本来就是跨帧拼的、与这里同一口径，比「直接去读每一帧」全。
  ⚠️ **实测这一趟没有救到**：第一件量到的那一步里，实际看到的仍是主帧落地页文案 ——
  说明那一刻**手上那份观测的 page_text 也不含子帧正文**（为什么，这轮**没量**）。
  ⚠️ 这一格**没有钉子**：单跑绿、全量套件红（替身的模块级 `STATE` 与调用次序耦合），
  与其留一条会骗人的绿，不如明说没有。

## 4. 这一轮**没做到**的（如实说）

**自测仍未过。** 最近一趟（`gowizard-20260917-060317`）：
baseline **执行 23 / 跳过 14 / 没做成 4**、delay 13/22/6、rerun 21/15/5，**三遍都没见成功文案**
→ `delivered=false`、`py_path=null`，`forms/sites/` 里仍然只有 `.gitkeep`。
**「执行 24 步」「0 没做成」都不算成功**（按控制器的口径，只有那三遍都见到
`Good news - We've matched you! Your quote is on the way!` 才算）。

**差在哪（一句话）**：跳过那一族的根**不在地址**（主帧里 iframe 的 src 读得到），
而在**正文**：判据要的是**子帧里的问卷文案**，而 `page_signature()` 那一刻只能看到
**主帧的落地页文案** —— 子帧正文在「直接读帧」与「手上那份观测」两条路上**都没拿到**。
**为什么两条路都没拿到，这一轮没有量**（下一轮该量的就是它：那一刻
`_read_frames()` 返回了什么、`_last_model` 是哪一次观测的、它的 `page_text` 里有什么）。

## 5. 顾虑

1. **证据按控制器要求留着**：产物候选 `/tmp/gwacc4/kept/gowizard.candidate.{1,2,3}.py`、
   账本 `/tmp/gwacc4/runtime/explore/job-*/attempt-1.jsonl`、每遍一个子目录的
   trace + 截图（`runtime/selftest/<时刻>/<遍名>/`）、进度探针原始记录
   `/tmp/gwacc4/progresswatch.jsonl`。
2. 两趟之间窗口会自然死亡（25~33 分钟），而一趟「探路 + 三遍自测」已经接近这个上限 ——
   这一轮有一趟就是这样半路丢窗的（job 停在 `waiting`，服务正确地拒绝继续）。
   **要跑完三遍，需要要么缩短单趟、要么在中途换窗续跑**（`reopen` 那条路）。
3. 这一格（子帧正文）修完之前，**判据中的文本类 `when` 在 iframe 为主的站上都不牢** ——
   而这一站从第二题起全在 iframe 里。
