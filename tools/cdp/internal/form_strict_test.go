package internal

import (
	"encoding/json"
	"strings"
	"testing"
)

// `cdp form --strict` 的消歧闸：**纯函数**那一半的判据（脱开浏览器就能穷举）。
//
// 为什么要有这一闸：宽松路径遇到「命中多个」时静默取文档序第一个 —— `click` 那条路
// 已经修了（`ClickElementStrict`），而 `form` 一直没修：值照样填进去了、回执照样 ok，
// **只是填进了另一个框**（真站实测：一个 class 选择器被 zip / full_name / email
// 三个字段组共用，ZIP 框里最后躺着手机号、页面红字拒收）。

func strictProbeOf(items ...[2]string) string {
	type item struct {
		Sel  string `json:"sel"`
		Blob string `json:"blob"`
	}
	var out []item
	for _, it := range items {
		out = append(out, item{Sel: it[0], Blob: it[1]})
	}
	b, _ := json.Marshal(map[string]any{"count": len(out), "items": out})
	return string(b)
}

func TestPickUnique_OneMatchIsUntouched(t *testing.T) {
	got, err := pickUnique(strictProbeOf([2]string{"#only", "zipcode"}), "")
	if err != nil || got != "#only" {
		t.Fatalf("只命中一个时不该改任何东西：got=%q err=%v", got, err)
	}
}

func TestPickUnique_NoMatchDefersToTheNormalPath(t *testing.T) {
	got, err := pickUnique(strictProbeOf(), "")
	if err != nil || got != "" {
		t.Fatalf("一个都没命中时该交回原路（让它报「找不到」）：got=%q err=%v", got, err)
	}
}

func TestPickUnique_AmbiguousWithoutIdentityIsARefusal(t *testing.T) {
	_, err := pickUnique(strictProbeOf([2]string{"#a", "fullName"}, [2]string{"#b", "zipCode"}), "")
	if err == nil {
		t.Fatal("命中多个又没给 --expect-label，必须**拒绝**，不许挑第一个")
	}
	for _, want := range []string{"命中 2 个元素", "--expect-label", "fullName", "zipCode"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("拒绝的话里要有 %q（人得看得出撞上了谁）：%s", want, err)
		}
	}
}

func TestPickUnique_IdentityPicksExactlyOne(t *testing.T) {
	probe := strictProbeOf(
		[2]string{"html>div:nth-of-type(1)>input:nth-of-type(1)", "input mui1 fullName Full Name: text"},
		[2]string{"html>div:nth-of-type(2)>input:nth-of-type(1)", "input mui2 zipCode ZIP code tel"},
	)
	got, err := pickUnique(probe, "ZIP code")
	if err != nil {
		t.Fatalf("按身份该认得出唯一一个：%v", err)
	}
	if got != "html>div:nth-of-type(2)>input:nth-of-type(1)" {
		t.Fatalf("认错了元素：%q", got)
	}
	// 大小写与多余空白不该影响认人
	if got2, err2 := pickUnique(probe, "  zip   CODE "); err2 != nil || got2 != got {
		t.Fatalf("归一化没生效：got=%q err=%v", got2, err2)
	}
}

func TestPickUnique_IdentityThatMatchesNothingIsARefusal(t *testing.T) {
	_, err := pickUnique(strictProbeOf([2]string{"#a", "fullName"}, [2]string{"#b", "zipCode"}), "phone")
	if err == nil {
		t.Fatal("身份对不上时**不许**退回去挑第一个 —— 那正是要治的洞")
	}
	if !strings.Contains(err.Error(), "一个都对不上") {
		t.Errorf("要说清是「对不上」而不是别的：%s", err)
	}
}

func TestPickUnique_IdentityThatMatchesTwoIsStillARefusal(t *testing.T) {
	_, err := pickUnique(strictProbeOf([2]string{"#a", "zipCode one"}, [2]string{"#b", "zipCode two"}), "zipCode")
	if err == nil || !strings.Contains(err.Error(), "不止一个") {
		t.Fatalf("认出不止一个也必须拒：err=%v", err)
	}
}

