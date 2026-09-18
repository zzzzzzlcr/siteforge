# gowizard 上线清单（放到 auto-farm 测试环境）

**产物**：`/company/siteforge/forms/sites/gowizard.py`（125,076 字节）
**md5**：`a2ebe3924dac2ed8e2aa3c78ff8fdedc`
**目标**：`auto-farm` 的**测试环境**

---

## 一句话结论

**接口对得上、不会崩、缺东西会出声。** 但有三处要动，其中一处是你 9-16 决定「先不铺」的那件事。

---

## 要动的三处

### ① 路由表 —— 加条目（`scripts/ad-task.py`）

**路由行**（跟旁边那条格式一样，58 条同款）：

```python
(["gowizard.com", "www.gowizard.com"], "forms/sites/gowizard.py", "gowizard"),
```

**类别映射 —— 要加两处**（表里同一条映射写了两遍，约在 2695 行与 3107 行附近）：

```python
"gowizard": "car_insurance",
```

> 依据：同为延保站的 `endurancewarranty` 也是 `car_insurance`。
> 可用的类别：`car_insurance / casino / dating / health_insurance / hearing_aid /
> home_improvement / life_insurance / mortgage / newsletter / senior_survey /
> site_specific / timeshare_cancellation / user`

### ② `cdp` 二进制 —— ✅ **2026-09-17 14:30 已换新，这一条已了结**

**换之前**：老二进制缺 `observe` / `diff` / `screenshot`。

**⚠️ 我先前把 `observe` 的缺失定性成「回退链少一层保险」，那是错的。**
用户真机实测（R-86）：**缺 `observe` → 活帧学不到 → `_urls()` 只剩主帧 →
11 个 `gowizard-*` 状态的判据全部不成立 → 0 步执行 → 「连着 3 步没做成，收摊」。**
**它不是保险，是判据的输入。**

**已做**（`/opt/skills/auto-farm-skill/cdp`，宿主与容器同一个 bind mount）：

```
老 11 条命令 → 新 14 条
丢失：无（comm -23 为空）
新增：diff  observe  screenshot
生产 common.py 用到的 click eval form screenshot scroll snapshot —— 全在
md5 两边一致：328f0db9c4165d40898dd487342f0de6
备份：cdp.bak-20260917-preobserve
```

**回滚**（一条命令）：

```bash
mv /opt/skills/auto-farm-skill/cdp.bak-20260917-preobserve /opt/skills/auto-farm-skill/cdp
```

**自检**：

```bash
docker exec auto-farm /opt/skills/auto-farm-skill/cdp observe --help
```

> ⚠️ **其他 cdp 副本没动**（`/company/lanuage`、`/company/newTaskTest`、`/company/cdpcli`、`/click/...`）
> —— 只换了 auto-farm。

### ③ `common.py` —— 版本不齐，**它自己兜住了**

测试环境那份若不支持 `strict` / `expect_label`，产物会：**退回老路 + 大声警告**，**不会把整趟炸死**。

**代价**：那个「歧义选择器不许静默填错」的闸是**关着的**。
→ 建议把 siteforge 那份 `forms/common.py` 一起同步过去（md5 对齐即可）。

---

## 产物需要什么

### 命令行契约（与生产脚本逐字一致 ✓）

```
--ws-url          （必需）
--form-file       （必需）
--correlation-id  （必需）
--log-level       默认 INFO
--task-id         默认 ""

（可选，调试用）--trace  --stop-at N  --shots failed|all  --delay S  --no-report
```

### 运营那份 form-file 需要这几个键

| 键 | 填到哪 |
|---|---|
| `state` | 州（`What state do you live in?`） |
| `postcode`（或 `zip`） | 邮编 |
| `full_name`（或 `first_name` + `last_name`） | 姓名 |
| `email` | 邮箱 |
| `phone` | 电话 |

> 取不到就走**随机兜底**（跟生产那两套执行器同一条规矩：**资料优先，没有才随机**）。

### 成功条件

```
Good news - We've matched you! Your quote is on the way!
```

进程退出码 **0 = 跑通**，非 0 = 没跑通。

---

## 判「这趟算不算成」

1. **退出码 0**，且
2. 页面上真的出现了上面那句成功文案

**只有这两条同时成立才算成。** 缺一不可。

---

## ⚠️ 诚实的边界（上线前请知情）

1. ✅ **已在真机上跑通**（2026-09-17 15:22，用户在测试环境实测；用的是 R-87 铺上去的新 cdp）。
   此前那次失败（R-86）**不是产物的病** —— 是老 cdp 没有 `observe`，
   而产物的 `when` 判据**要子帧自己的 URL**，活帧号只能从 `observe` 学。**铺上就有了。**
2. **验收判据「扰动自测三遍全过」没有达成。** 三次完整自测里每次都只有一遍见到成功文案；
   接受依据是**用户在现场亲眼看到了成功页**。
   （这是**自测判据**层面的事，与上面那条**功能层面**的验证是两件，别混。）
3. **它会往 `/tmp` 漏临时文件**（`siteforge-before-*.json`）—— 正常路径会删，**被硬杀时会漏**。
   同一个写法在骨架模板里，**以后每个产物都会有**（待修）。

---

## 上线前自检

```bash
# 1. 二进制新不新
<测试环境>/cdp observe --help

# 2. common.py 版本
grep -c "expect_label" <测试环境>/forms/common.py     # 0 = 老版本（产物会退回老路）

# 3. 语法
python3 -m py_compile <测试环境>/forms/sites/gowizard.py

# 4. 干跑一步，看它自己说什么（-–stop-at 3 会让浏览器停在那一页，不关）
python3 <测试环境>/forms/sites/gowizard.py \
  --ws-url <WS> --form-file <FORM.JSON> --correlation-id test-1 \
  --trace /tmp/t.json --stop-at 3
```

**看 `/tmp/t.json` 里每一行有没有 `ok` / `note`。**
缺命令时它会写 `progress_why` / `shots_why` —— **那两句就是「它知道自己弱在哪」**。
