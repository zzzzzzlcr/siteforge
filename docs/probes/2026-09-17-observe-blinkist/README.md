# 观察探针：blinkist 漏斗上的 `observe` 实测

2026-09-17。**探针产物，不是生产代码、不改 ledger。**
用来定死 `docs/superpowers/specs/2026-09-17-capability-gap-blinkist.md` 的
**U2**（observe 能不能替代那 17 次 eval）与 **U3**（`disabled` 是不是真缺）。

两者**都按「不能」结案**，而且是跑出来的，不是读代码推的。

## 怎么跑出来的

浏览器是**自己拉的一个私有 Chrome**：headed（Xvfb `:97`）+ 独立
`--user-data-dir` + 独立端口 `9411`。**没有用 Bit 窗口、没碰 `:1080`、
没碰 `/opt/skills/auto-farm-skill/`。**

```bash
# 1) 建二进制
cd tools/cdp && PATH=/usr/local/go/bin:$PATH go build -o /tmp/cdp-probe .

# 2) 私有显示 + 私有 Chrome（headed！headless 过不了 CF，见「坑一」）
Xvfb :97 -screen 0 1280x900x24 &
DISPLAY=:97 google-chrome --remote-debugging-port=9411 \
  --user-data-dir=/tmp/probe-blinkist/profile --no-first-run about:blank &

# 3) 跑
/tmp/cdp-probe --port 9411 navi "https://www.blinkist.com/en/onboarding/matrix/certain"
/tmp/cdp-probe --port 9411 observe --json > observe-certain-initial.json
```

`sleep` 不可省：Cloudflare 过关后 React 还要 hydrate，**hydrate 前后
Continue 的 `disabled` 不一样**（见「坑二」）。本目录所有 JSON 都在
`disabled` 状态前后各验一次之后才落盘（`dom-ground-truth.txt` 记了校验值）。

### 本目录的文件

| 文件 | 是什么 |
|---|---|
| `observe-certain-initial.json` | `/matrix/certain` 首次访问、**Continue `disabled:true`** 时的模型 |
| `observe-certain-human.txt` | 同一状态的 `--json=false` 人话渲染（**最能说明问题的一张**） |
| `observe-certain-after-select.json` | 同一页、点了一个选项后 **Continue `disabled:false`** 的模型 |
| `observe-growth-areas-initial.json` | 兄弟步骤 `/matrix/growth-areas`（多选、选项**有文字**）的模型 |
| `observe-login-fields.json` / `observe-login-filled.json` | `/account/login` 填值**前 / 后**的模型（两份**逐字节相同**） |
| `dom-ground-truth.txt` | DOM 真值 —— 用 `cdp eval` 量的，**不是**从 observe 里读的 |

## ⚠️ 产地说明（别当成生产观测）

这台 Chrome **不是**生产指纹、**不在美国代理后面**（直连，出口是本地机房 IP）。
所以：

- **两个问题照样能答**：`observe` 的**字段集**是纯页内计算的，与指纹/geo 无关。
- 页面本身**没有**被 geo 拦、漏斗步骤**直达可达**（`/matrix/certain`、
  `/matrix/growth-areas` 直接 `navi` 就进得去，没有前置步骤门禁），
  所以这次没有「够不着页面」这一层损失。
- 仍然**不能**说「生产的 blinkist 就是这样」：站点 A/B 变体、真指纹下的
  渲染差异都可能有。**这里量的是模型字段的有无，不是站点的行为。**

## 坑一：headless 过不了 Cloudflare，headed 可以

headless（`--headless=new`）时 title 恒为 `Just a moment...`，bodyLen 264，
30 秒不消 —— Cloudflare full-page interstitial，且**没有 Turnstile iframe**
（`document.querySelectorAll('iframe').length === 0`），页内没有任何可交互的东西。
换成 Xvfb 上的 headed Chrome，**4 秒过关**，同一 URL 直接出真页面。

## 坑二：`disabled` 会随 hydrate 变，标量取样会取错元素

两个独立的坑，都会**静默给出错的观测**：

1. **hydrate 竞态**：刚 `navi` 完立刻读，Continue 可能还是 SSR 的
   `disabled:false`；React hydrate 完才变 `true`。
2. **标量探测取错元素**：`document.querySelector('button.cursor-pointer.items-center')`
   —— 这个选择器**匹配 5 个按钮**，`querySelector` 返回的是**文档序第一个 = Back**，
   **不是 Continue**。用它读 `disabled` 读到的是 Back 的值。
   正确的读法是全枚举后再按 `innerText === 'Continue'` 过滤（本目录就是这么量的）。

## U2 判决：不能替代 —— 缺 3 种读法，其中 1 种是「语义标签」

那 17 次 eval 的用途（规格 §1.2/§1.4）逐条对：

| eval 问了什么 | `observe` 有吗 | 证据 |
|---|---|---|
| `location.href` | ✅ `url` | 每份模型都有 |
| `document.body.innerText` | ✅ `page_text` | `'Profile Personality Patterns You always know what you want, agree? Continue We use Cookies 🍪'` |
| 枚举可点元素 | ✅ `actions`（6 条 = 5 button + 1 cookie div） | — |
| 看「页面是不是白的」 | ⚠️ 部分：`page_text` 为空/`actions` 为空可推；**无 `readyState`、无元素总数** | `diagnostics: []` |
| `errorShown`（找 `"couldn"`） | ✅ `page_text` 里有就能看到 | — |
| **`aria-label`（选项/Back 的名字）** | ❌ **没有** | 全 JSON 里 `Disagree` / `Not sure` / `Agree` / `"Back"` 各 **0 次** |
| **`disabled`** | ❌ **没有**（U3） | 全 JSON `disabled` **0 次** |
| **`inputValue`（值填进去没有）** | ❌ **没有** | 填值前后两份模型**逐字节相同** |

### 最贵的一条：Back 变成了一个**没有名字的匿名按钮**

DOM 真值（`dom-ground-truth.txt`）：Back 是 `text:""` + `aria-label:"Back"`；
三个选项是 `text:""` + `aria-label: Disagree / Not sure / Agree`。
`observe` **只给 `text`，不给 `aria-label`**，于是模型里：

- 三个选项 = 三行 `text: ""`，`nearby_text: []`，**彼此完全同形**，
  只有 `selector` 相同（`button._option_1cgnd_66.focus-ring`）能看出是一组；
- Back = 同样 `text: ""` 的一行，**没有任何字段说它叫 Back**。

`blinkist.py` 的 `_click_option()` 正是靠 `label(b)`（含 aria-label）来
**排除 Back**、**排除 Continue** 的。今天这个信息在模型里不存在，
只剩几何可猜（Back = `region:"header"` + `24x24` + `relative_size:0.1`）。
**能猜，但不是「看得见」。**

### 分组也没表达出来：`option_groups` 在两个步骤上都是 `[]`

`/matrix/certain`（3 个图标选项）和 `/matrix/growth-areas`（7 个有文字的选项）
**都是 `option_groups: []`**。兄弟步骤那份模型里 7 个选项共享
`selector: button._option_1cgnd_66.focus-ring`、`peer_count: 8`、
`stability: low` —— 组**可以推出来**（同选择器 + 同尺寸 + 相邻 bbox），
但**没有字段说「这 7 个是一组、这组叫选项」**，而且 `peer_count: 8`
把 Continue 也算进了同一组（Continue 也是 `peer_count: 8`），
**所以它不能当分组信号用**。

## U3 判决：`disabled` 确实真缺 —— 而且后果是「静默假成功」

最硬的一条证据：**同一个 URL、同一个页面，DOM 里 Continue 的 `disabled`
从 `true` 翻成 `false`，而 `observe` 给这个按钮的整条 action 逐字节相同。**

```
DOM disabled=true  ->  {"selector":"button.cursor-pointer.items-center","text":"Continue",
                        "visible":true,"occluded_by":null,"stability":"medium", ...}
DOM disabled=false ->  {"selector":"button.cursor-pointer.items-center","text":"Continue",
                        "visible":true,"occluded_by":null,"stability":"medium", ...}   ← 完全一样
```

两份模型的差异只有 `page_text`（多了一句 "We hear you! ..."）和选中项的一个 `bbox`。
**模型里没有任何字段能让消费者分辨这两种状态。**

模型给的 19 个字段是：
`above_fold alt / alternates / bbox / contrast / frame_path / nearby_text /
occluded_by / peer_count / region / relative_size / role / selector /
shadow_depth / stability / tag / text / type / visible / z_index`
—— 没有 `disabled`，`stability` 也不代替它（Continue 是 `medium`，
选项是 `low`，而 `medium` 的 Back 同样在列）。

### 后果链（每一步都实测过）

1. Continue 在 DOM 里 `disabled: true`；
2. `observe` 把它当**普通候选**递出来（`visible: true`、`occluded_by: null`）；
3. 用它的**唯一**（positional）选择器去点 —— `cdp click` **exit 0**，
   还回了一对像样的坐标 `{"x":642.87,"y":682.38}`；
4. **什么都没发生**：URL 不变、页面不变、**没有任何报错**。

这就是本项目最贵的那类失败：**一次静默的空点被报成成功**。
agent 唯一的察觉途径是再 observe 一次做 diff。

## 顺带挖到的一条（比 U3 更险）：`selector` 可能**指向 Back**

模型给 Continue 的 `selector` 是 `button.cursor-pointer.items-center`。
这个选择器在 DOM 里**匹配 5 个按钮**，文档序是：

```
[0] Back   [1] Disagree   [2] Not sure   [3] Agree   [4] Continue
```

**`querySelector` 取到的是 `[0]` = Back。** 实测：

```
$ cdp click --selector "button.cursor-pointer.items-center"
{"x":170.77,"y":81.86}          ← Back 的 bbox 是 [160,78,24,24]，中心 (172,90)
URL before: /onboarding/matrix/certain
URL after:  /onboarding/matrix/checkpoint-focus-type
```

点的是 **Back**，不是 Continue —— 正是 `blinkist.py` 设计要点里
「排除 Back(每步都有, **误点会倒退**)」要防的那一下。
而 `cdp click` **对歧义选择器不报错**，静默取第一个匹配。

模型**有**唯一候选（`alternates[0]` 是 positional 的，实测唯一），
所以正确的消费者能自救 —— 但**必须自己知道要去验唯一性**，
`stability` 帮不上忙（Continue 是 `medium`，MCP 的工具说明还写着
「优先 stability=high 的」）。

## 一句话

**U2/U3 都按「不能」结案，且 `observe` 的缺口比原分析列的多一项
（`aria-label` 也不在模型里，而它正是让 06:31 那轮卡住、06:32 那轮通了的东西）。**
最有行动价值的两条：**加 `disabled`**、**让 `selector` 的唯一性可见
（或让 `click` 对歧义选择器报错）**。
