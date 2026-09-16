package internal

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// 三样读法（value / selected / aria_label）里**不需要浏览器**的那几条断言。
//
// 行为那半在 cmd/observe_reads_e2e_test.go（真页面、真二进制）。这里钉的是三件
// 只有 Go 侧才看得见的事：
//
//	① 值的上限是**一个数、只有一份**（写死的第二份就是同一件事有两个真相）
//	② `selected` 的三态在 JSON 边界上真的是 true / false / **null**
//	③ 「看不出」不是靠 class 猜出来的（本项目已裁定 class 不是状态证据）

// readCapFromJS 从 observeJS 里读出那个上限。
//
// 为什么不写成 Go 常量再注入脚本：那样上限就有了两处（一处改另一处不改是静默的）。
// 这里是**从脚本里读出来**，所以断言钉的恒是脚本真正用的那个数。
func readCapFromJS(t *testing.T) int {
	t.Helper()
	re := regexp.MustCompile(`var READ_CAP = (\d+);`)
	m := re.FindStringSubmatch(observeBody(t))
	if m == nil {
		t.Fatalf("脚本里没有 `var READ_CAP = <数字>;` —— 上限找不到，下面的断言无从落地。"+
			"（value 与 aria_label 都靠这一份上限，见 observeJS 里它的注释）\n脚本: %.200s", observeBody(t))
	}
	n, err := strconv.Atoi(m[1])
	if err != nil {
		t.Fatalf("READ_CAP 解不成数字: %q", m[1])
	}
	return n
}

// TestObserveReadCapIsSane —— 上限可以调，但不许调到「没封住」或「什么都没剩下」。
//
// 两头都咬：
//
//	太小（< 40）  一个正常的地址、邮箱、车型名都装不下，回读出来的值全是半截 ——
//	              而 agent 要拿它确认「我写进去的就是这个」，半截等于确认不了
//	太大（> 200） 每条动作/字段都带一个值（观察上限 200 动作 + 100 字段），
//	              200 就能让一次观测多出几十 KB 且**没有信息增益**
//	              （确认写入是否生效，前几十个字符就够）
func TestObserveReadCapIsSane(t *testing.T) {
	cap := readCapFromJS(t)
	if cap < 40 || cap > 200 {
		t.Errorf("回读上限 = %d，落在合理区间 [40, 200] 之外 —— 太小则回读出来的是半截"+
			"（确认不了写入），太大则一次观测白涨几十 KB 上下文", cap)
	}
	t.Logf("value / aria_label 的回读上限 = %d 个字符（脚本里那一份）", cap)
}

// TestSelectedIsThreeStateAtTheJSONBoundary —— `selected` 必须是**三态**：
// true / false / null，且 null **不是** false。
//
// 为什么在 Go 侧单独钉一遍：消费侧（py）读的是 JSON。`*bool` 的零值恰好是 nil，
// 所以「字段忘了绑」与「页面真的看不出」在 struct 里长得一模一样 ——
// 这条测试把两件事都钉死：编出来是 null，解回来还是 nil。
func TestSelectedIsThreeStateAtTheJSONBoundary(t *testing.T) {
	yes, no := true, false
	for _, c := range []struct {
		name string
		in   *bool
		want string
	}{
		{"true", &yes, `"selected":true`},
		{"false", &no, `"selected":false`},
		{"看不出（null）", nil, `"selected":null`},
	} {
		b, err := json.Marshal(Action{Selected: c.in})
		if err != nil {
			t.Fatalf("%s: 编 JSON 失败: %v", c.name, err)
		}
		if !strings.Contains(string(b), c.want) {
			t.Errorf("%s: 编出来的 JSON 里没有 %s\n实际: %s", c.name, c.want, b)
		}
	}
	// 解回来：null → nil（不是 false）—— py 侧 None 与 False 也是这么分的
	for _, in := range []string{`{"selected":null}`, `{"selected":false}`, `{"selected":true}`} {
		var a Action
		if err := json.Unmarshal([]byte(in), &a); err != nil {
			t.Fatalf("解 %s 失败: %v", in, err)
		}
		switch in {
		case `{"selected":null}`:
			if a.Selected != nil {
				t.Errorf("%s 解出来是 %v，want nil（null 不是 false —— 这一格就是「看不出」）", in, *a.Selected)
			}
		case `{"selected":false}`:
			if a.Selected == nil || *a.Selected {
				t.Errorf("%s 解出来不是 false —— 「没选」与「看不出」被合并了", in)
			}
		default:
			if a.Selected == nil || !*a.Selected {
				t.Errorf("%s 解出来不是 true", in)
			}
		}
	}
}

// TestObserveReadsDoNotGuessFromClass —— 「看不出」就是看不出，**不许**从 class 猜。
//
// 本项目已经就同一条判过：`Mui-disabled` / `is-disabled` 这类 class **不算**禁用的
// 证据（class 是站点自己起的名字，认它等于把「什么样算选中」交给页面）。
// 这条测试是那条裁定的守卫：谁要是为了「让 MUI 那类控件也能看出选中」而往
// selState 里塞 class 判断，它会当场红。
//
// 判据落在**函数体**里（而不是「全脚本含不含 class」）：脚本别处合法地用着
// className（region 派生、候选选择器），断言整脚本等于空转。
func TestObserveReadsDoNotGuessFromClass(t *testing.T) {
	body := jsFuncBody(t, observeJS(), "selState")
	for _, banned := range []string{"classList", "className", "class=", "getAttribute('class')"} {
		if strings.Contains(body, banned) {
			t.Errorf("selState 里出现了 %q —— 选中态**不许**从 class 猜（class 是站点起的名，"+
				"认它等于把「什么样算选中」交给页面）。认不出来的控件就该给 null：\n%s", banned, body)
		}
	}
	// 反向对照：这个函数体真被抓到了（不然上面的禁令是在一段空串上通过的）
	if !strings.Contains(body, "aria-checked") {
		t.Errorf("selState 的函数体里没有 aria-checked —— 抓错了函数，上面的禁令空转:\n%s", body)
	}
}

// TestObserveValueMissingMeansNotAValueControl —— `value` 的 null 与 "" 语义不同，
// 并且在 Go 侧也是这么编的（null = 不是值控件；"" = 是值控件但现在是空的）。
func TestObserveValueMissingMeansNotAValueControl(t *testing.T) {
	empty := ""
	b, err := json.Marshal(Action{Value: &empty})
	if err != nil {
		t.Fatalf("编 JSON 失败: %v", err)
	}
	if !strings.Contains(string(b), `"value":""`) {
		t.Errorf("空的输入框编出来不是 \"value\":\"\" —— 「这个框是空的」是个关于页面的断言，"+
			"不能与「这不是个框」混在一起:\n%s", b)
	}
	b2, _ := json.Marshal(Action{})
	if !strings.Contains(string(b2), `"value":null`) {
		t.Errorf("没绑值的动作编出来不是 \"value\":null:\n%s", b2)
	}
}
