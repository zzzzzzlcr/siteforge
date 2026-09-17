# bit-window —— 开窗口、配窗口、关窗口（**动手前读这一页**）

> 规格 §4.6 是权威版本；这一页是**干活时要照着做的那份**。
> 规格 §4.6 自己写着「三个**必须写进 skill** 的陷阱」—— 这一页就是那个 skill。
> 2026-09-16 补：**接口的真实返回形状**与**孤儿窗口**两节，是踩过之后才有的。

## 什么时候用

任何要**真的操作一个浏览器**的活：探索站点、跑 py 产物、截图、observe。
不操作浏览器就不用看这一页。

## 前提：别碰生产的那套

| 东西 | 谁的 | 规矩 |
|---|---|---|
| 生产 worker + 它的 `bit_id` | 生产任务 | **绝不共用**。共用会复现「两任务抢同一窗口 → 窗口进程僵死」 |
| 宿主 `:1080` 的 gost | 生产（**单例热换**） | **绝不碰**。碰了会把正在跑的生产任务的链一起换掉 |
| 宿主 `:1081` 的 gost | 实验/agent | 用这个。`gost-watch.sh` 常驻它，按 `config/gost1081.chain` 文件变化换链 |

agent 用**自己的** worker 和**自己的** `bit_id`（由人指定，不是配置文件里那个）。

## 顺序不能反（五步）

```
① 拉链      → 写 config/gost1081.chain，等 :1081 起来
② 配窗口    → 直接 POST /browser/update（**不是** bit.sh update！）—— 必须在 open 之前，
              因为 clearCacheFilesBeforeLaunch / clearCookiesBeforeLaunch 只在启动时生效
③ 开窗口    → bit.sh open <worker_ip> <bit_id>   → 打印 ws_url（127.0.0.1 已改写成 worker_ip）
④ 干活      → cdp --ws-url <ws_url> observe / click / form / …
⑤ 关窗口+验死 → bit.sh close <worker_ip> <bit_id>；再 POST /browser/pids/alive 确认
```

**② 的权威 JSON**（照抄，别自己拼）：

```jsonc
{ "id": "<bit_id>", "proxyMethod": 1, "proxyType": "socks5",
  "host": "<宿主IP>", "port": <agent 自己的 gost 端口>,
  "syncTabs": false,
  "clearCacheFilesBeforeLaunch": true, "clearCookiesBeforeLaunch": true,
  "browserFingerPrint": {
    "coreVersion": "<random 130|132|134|136|138|140|142>",
    "ostype": "PC", "os": "Win32", "osVersion": "11,10",
    "devicePixelRatio": 1
  } }
```

⚠️ `/browser/update` 是**全量记录更新**：只给部分字段会被拒（`请选择代理方式`）——
**要改一个字段也得把整条记录发全**，否则会把代理配置一起抹掉。

## 接口的真实返回形状（2026-09-16 在真 worker 上量的）

```
POST /browser/open       {"id":bit_id,"args":[...],"queue":true}
                      → {"success":true,"data":{"ws":"ws://…"}}      取 .data.ws

POST /browser/close      {"id":bit_id}
                      → 看 .success（**旧脚本静默丢弃它** → close 失败时窗口还开着，
                        下次 open 复用同一窗口导致任务错乱）

POST /browser/pids/alive {"ids":[bit_id]}
   活着 → {"success":true,"data":{"<bit_id>": 4256}}     ← data 是 **dict**，值是 PID
   关掉 → {"success":true,"data":{}}                     ← 空 dict
```

**第一版实现只认 list/bool/str，于是恒返回「不知道」——「验死」这根线看起来接好了、
其实永远不响。**「接上了但不响」比没接更坏：它让前置检查看起来存在。

## 三个必须记住的陷阱

| 陷阱 | 后果 |
|---|---|
| **`bit.sh update` 是残缺包装，不能用来下发指纹/代理** | 收 10 个参数只下发 4 个：**UA 字符串不下发**（只拿来推 OS 类型）、**sw/sh/dpr 收了完全没用**（JSON 里连键都没有）、**代理用户名密码不下发**。要用就**直接 POST /browser/update** |
| **DPR 字段名必须是 `devicePixelRatio`** | 写 `dpr` 被**静默忽略**，回读仍是 3。Bit 默认 DPR=3 → 手势按 CSS 像素发坐标、底层按设备像素落 → **点击偏移 3 倍，点空气**。实测：写 `dpr` 回读 3；写 `devicePixelRatio` 回读 1 |
| **「换出口国家」= 换宿主 gost 的链** | 窗口连的是宿主 gost（`host=宿主IP`），出网走 gost 的链 —— **改窗口配置改不了国家** |

## 另外两条（时效 / 归属）

- **窗口比原先记的活得久**（2026-09-16 两趟实测：一趟自然死亡在 **25m14s**，
  另一趟活到 **33m32s** 还是好的、是**我们自己关的**）。
  ⚠️ 这一页早先写的「只活几分钟 / 实测约 10 分钟」**是错的**，会让人白白重开窗口 —— 已改。
  **但「会死」这件事仍然成立**：跑长活照样要把「窗口还活着吗」当成**可失败前置**，
  并准备**重开 + 从断点续跑**，而不是整轮重来。
  ⚠️ **窗口寿命 ≠ 页面里的部件寿命**：2026-09-16 实测，窗口 33 分钟后仍活着，
  但页面里那个跨源问卷 iframe（chameleon）**在 ~30 分钟时自己超时消失了** ——
  **主 frame 还在，子 frame 没了**。所以「窗口还开着」不代表「页面还在那个状态」，
  判活要连**你关心的那一帧**一起看。
- **`close` 返回成功 ≠ 窗口真关了** —— 必须查 `/browser/pids/alive`；不查就复用，
  下次任务会拿到一个**正在死掉**的窗口。

## ⚠️ 孤儿窗口 —— 这条最贵，仓库里以前一个字都没有

**2026-09-08 的事故**：单台 worker 反复开同一个窗口失败并一直领单，几小时不停。

- **什么是孤儿**：bit 服务**启动窗口到一半失败后「忘记」了它，进程却活着** ——
  停在一个空白工作台页上**吃内存**。API `close` 对服务端不记得的窗口**无效**；
  **`pids/alive` 对孤儿也是瞎的（同样返回空）**。只能到宿主机上 `taskkill`。
- **为什么致命**：那些 worker 是 ~3GB 内存的 Windows 小机器。孤儿堆积 → **内存 >95%**
  → 新 `open` 直接报「内存使用率超出 95%」→ 更多半启动残留 → 死锁。
  实测 3 周 **6,174 次 open 失败 ≈ 325 单空转**。
- **因此**：`alive` 返回「否」**分不清两种情况** ——
  **①窗口干净关掉了** 与 **②孤儿还在吃内存**。对「还能不能继续用这个 ws_url」答案一样（不能），
  但对**重开策略**不一样：②的情况下重开是在往一台快满的机器上再压一个浏览器。
  → **重开之前，先看一眼 worker 的余量**；连着失败就别硬开。
- 生产侧有**每天清一次孤儿**的例行操作（`ClearBitOrphans`）。

## 干完活的自检清单

- [ ] 用的是**自己的** worker + bit_id（不是生产那个）
- [ ] 代理走 **:1081**，全程没碰 :1080
- [ ] 指纹是**直接 POST /browser/update** 下发的，DPR 字段名是 `devicePixelRatio`
- [ ] 关窗口之后**查过 `/browser/pids/alive`**，确认返回空 `data`
- [ ] 如果中途重开过窗口：确认过 worker 的内存余量，不是盲目重开
