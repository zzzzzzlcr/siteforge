#!/usr/bin/env bash
# 给 cdp 的 cdproto 补一个浏览器已经会用、上游还没跟上的枚举值：
#   clientSecurityState.initiatorIPAddressSpace = "Private"
# 不补的后果（2026-09-23 真站实证）：事件解不出来，cdp 打 stderr 报错、**偶尔整条命令
# 什么都不返回** ⇒ 读页面读到空 ⇒ 判据全判「这页不像」⇒ 产物一步不做，且日志不提原因。
#
# 做法：vendor + 打补丁（唯一可靠的一刀）。将来 cdproto 上游补上了，把 vendor/ 删掉、
# 把 go.mod 那一行升上去、重新 `go mod vendor` 即可（脚本是幂等的，pattern 找不到就报错停）。
set -euo pipefail
cd "$(dirname "$0")"
export PATH=/usr/local/go/bin:$PATH
export GOPROXY=https://goproxy.cn,direct

echo "[1/4] go mod vendor"
go mod vendor

F=vendor/github.com/chromedp/cdproto/network/types.go
python3 - "$F" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
src = p.read_text(encoding="utf-8")
old_c = "IPAddressSpaceUnknown  IPAddressSpace = \"Unknown\"\n"
new_c = ("IPAddressSpaceUnknown  IPAddressSpace = \"Unknown\"\n"
         "\t// ← 本地补的一行（siteforge）：Chrome 已经在发这个值，上游 cdproto 还没有它。\n"
         "\t//   不加 = UnmarshalJSON 直接报错 ⇒ cdp 整条命令可能什么都不返回。\n"
         "\tIPAddressSpacePrivate  IPAddressSpace = \"Private\"\n")
old_s = "\tcase IPAddressSpaceUnknown:\n\t\t*t = IPAddressSpaceUnknown\n"
new_s = old_s + "\tcase IPAddressSpacePrivate:\n\t\t*t = IPAddressSpacePrivate\n"

if "IPAddressSpacePrivate" in src:
    print("    已经补过了 ✓")
    raise SystemExit(0)
for old, new, label in ((old_c, new_c, "常量"), (old_s, new_s, "case")):
    if src.count(old) != 1:
        raise SystemExit("✗ %s：命中 %d 处，停下（别猜着改）" % (label, src.count(old)))
    src = src.replace(old, new, 1)
p.write_text(src, encoding="utf-8")
print("    常量 + case 都补上了 ✓")
PY

echo "[2/4] 它还认不认 Private（拿真 json 解一遍）"
cat > /tmp/cdproto_check_test.go <<'GO'
package network

import (
	"encoding/json"
	"testing"
)

func TestSiteforgePrivateIPAddressSpace(t *testing.T) {
	var v IPAddressSpace
	for _, raw := range []string{`"Private"`, `"Loopback"`, `"Public"`} {
		if err := json.Unmarshal([]byte(raw), &v); err != nil {
			t.Fatalf("%s 解不了：%v", raw, err)
		}
	}
	if v != IPAddressSpacePublic {
		t.Fatalf("最后一个应该是 Public，得到 %q", v)
	}
}
GO
cp /tmp/cdproto_check_test.go vendor/github.com/chromedp/cdproto/network/siteforge_private_test.go
go test -run TestSiteforgePrivateIPAddressSpace ./vendor/github.com/chromedp/cdproto/network/ 2>&1 | tail -3
rm -f vendor/github.com/chromedp/cdproto/network/siteforge_private_test.go

echo "[3/4] 两个二进制重新建"
go build -ldflags="-s -w" -o cdp main.go
go build -ldflags="-s -w" -o cdp-mcp ./cmd/mcp
ls -l cdp cdp-mcp | sed 's/  */ /g'

echo "[4/4] 自己的用例（mcp + 内核）"
go test ./internal/... 2>&1 | tail -8
echo "== 完 =="
