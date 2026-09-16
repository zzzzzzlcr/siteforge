package internal

import (
	"strings"
	"testing"
)

// EnvPort 是 CDP_PORT 那道裁决**唯一**的家（cmd 与 cmd/mcp 两道门都调它）。
// 这几条规矩原先在两处各写一遍、各自只被「含有 CDP_PORT 字样」那种断言看着，
// 所以现在钉在定义处：值怎么读、坏值怎么拒绝、拒绝时那句话里有什么。
func TestEnvPortReadsTheThreeCases(t *testing.T) {
	cases := []struct {
		name    string
		raw     string
		want    int
		wantSet bool
	}{
		{name: "空串 = 没设", raw: "", wantSet: false},
		{name: "普通数字", raw: "9999", want: 9999, wantSet: true},
		{
			// 前后空白才是最常见的成因（脚本里多一个空格、从文件里读来的一个换行）：
			// 读得懂就必须读，拒绝它等于把这道门的规矩搬到另一道门上。
			name: "前后空白不算数", raw: " 9999 ", want: 9999, wantSet: true,
		},
		{name: "0 是用户给的值，不是「没设」", raw: "0", want: 0, wantSet: true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, set, err := EnvPort(tc.raw)
			if err != nil {
				t.Fatalf("EnvPort(%q) 报错了: %v", tc.raw, err)
			}
			if set != tc.wantSet {
				t.Errorf("EnvPort(%q) 的 set = %v，期望 %v", tc.raw, set, tc.wantSet)
			}
			if set && got != tc.want {
				t.Errorf("EnvPort(%q) = %d，期望 %d", tc.raw, got, tc.want)
			}
		})
	}
}

func TestEnvPortRefusesWhatItCannotParse(t *testing.T) {
	// 注意这里没有 "-1" / "99999"：那是「读得出来但不像端口」的值，两道门原先都收，
	// 这次搬家不顺手改规矩（范围校验是另一件事，不在这条 finding 里）。
	for _, raw := range []string{"abc", "12ab", "   ", "9222.5"} {
		t.Run(raw, func(t *testing.T) {
			_, set, err := EnvPort(raw)
			if err == nil {
				t.Fatalf("EnvPort(%q) 收下了 —— 静默回落 = 「我设了 CDP_PORT，但它没生效」，"+
					"命令于是连上**另一个浏览器**且不报错", raw)
			}
			if set {
				t.Errorf("EnvPort(%q) 既报错又说 set=true", raw)
			}
			// 报的是原值：操作者得能在自己那行环境里一眼认出它。
			if !strings.Contains(err.Error(), "CDP_PORT") {
				t.Errorf("报错没点名 CDP_PORT: %v", err)
			}
			if !strings.Contains(err.Error(), raw) {
				t.Errorf("报错里没有原值 %q —— 前后有空白正是最常见的成因，修剪掉就认不出来了: %v", raw, err)
			}
		})
	}
}
