# lauradate a34293f4：japansdates DOM 却被挂在 config 75 —— 2026-10-24

## 结论

- 真站 DOM 是 **japansdates 那套**：`step-content-item-*` + `option-*` + `legal-checkbox` +
  `continue-button`，hash `#c3RlcDE=`（step1）→ 分类器 `japansdates / high`，建议 config 101。
- 后端原本只有**主机键** `lauradate.com` → **config 75**，脚本 `LauraDateFiller`
  （sha `80b5c9bf…`，version `20260918`，9981 字节）：**一个 `data-testid` 都没有**，
  靠正文文字认步骤、按文字点按钮（注释写「Same platform as placetochat」）。
- 线上 10-01 起 3 条 failed，最新 10-23 04:55，**全部 `max_steps` 停在 hash `step1`**。
- 用 config 101 的线上脚本（sha `4c8b9fbe…`）在 8100 真跑**成功**：
  `RUN_DONE ok=True`，57 秒，最终 `https://lauradate.com/main-page?referrer=…`；
  假注册 `alexprobe558827@outlook.com`（`_rpt` 换成空函数，没写生产记录）。
  中途 `#terms-agree` 有一次「点了还在」的警告，随后照常过了。
- 已把**路径键** `lauradate.com/land/sp/a34293f4` link 到 **config 101**；
  **主机键 `lauradate.com` → 75 保持不动**（别的路径可能还在用那份 placetochat 脚本）。
  最长前缀匹配 ⇒ 这个 landing 走 101，其余 lauradate 路径仍走 75。

## 映射回读

| 键 | link 前 | link 后 |
|---|---|---|
| `lauradate.com/land/sp/a34293f4` | 无行；前缀解析到 75 的脚本 `80b5c9bf…` | **config 101, status 1**；脚本 `4c8b9fbe…` |
| `lauradate.com` | config 75, status 1；脚本 `80b5c9bf…` | **不变**（同一行、同一脚本） |

底稿：`fmr_before_link.json` / `fmr_after_link.json` / `fmr_before_config75.py` /
`fmr_link_result.json`。

## 现场

- `japansdates_from_fmr.py`：config 101 线上脚本原文（sha `4c8b9fbe…`，54388 字节）
- `run_101.py`：在 8100 真跑这个 URL（真提交，`_rpt` no-op）
- `link_101.py`：`--apply` 把路径键挂到 101，并回读主机键不变

```bash
docker cp docs/probes/2026-10-24-lauradate-a34293f4/japansdates_from_fmr.py \
  siteforge-222:/tmp/japansdates_from_fmr.py
docker cp docs/probes/2026-10-24-lauradate-a34293f4/run_101.py siteforge-222:/tmp/run_101.py
docker exec -w /opt/siteforge -e PYTHONPATH=/opt/siteforge/forms:/opt/siteforge \
  -e SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  -e BIT_WORKER_IP=192.168.1.222 -e BIT_ID=982d158b41b14fbb87754667859b4719 \
  -e BIT_API_PORT=54345 siteforge-222 python3 /tmp/run_101.py
```
