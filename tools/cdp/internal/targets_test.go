package internal

import (
	"strings"
	"testing"
)

func TestFilterPageTargets(t *testing.T) {
	tests := []struct {
		name     string
		entries  []listEntry
		expected []PageTarget
	}{
		{
			name:     "empty",
			entries:  []listEntry{},
			expected: []PageTarget{},
		},
		{
			name: "single page",
			entries: []listEntry{
				{ID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
			},
		},
		{
			name: "filters non-page types",
			entries: []listEntry{
				{ID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
				{ID: "BBB", Type: "service_worker", Title: "SW", URL: "https://example.com/sw.js"},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
			},
		},
		{
			name: "filters devtools URLs",
			entries: []listEntry{
				{ID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
				{ID: "BBB", Type: "page", Title: "DevTools", URL: "devtools://devtools/bundled/inspector.html"},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
			},
		},
		{
			name: "filters empty URL",
			entries: []listEntry{
				{ID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
				{ID: "BBB", Type: "page", Title: "", URL: ""},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
			},
		},
		{
			name: "multiple pages",
			entries: []listEntry{
				{ID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
				{ID: "BBB", Type: "page", Title: "Example", URL: "https://example.com"},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
				{ID: "BBB", Title: "Example", URL: "https://example.com"},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := filterPageTargets(tt.entries)
			if len(result) != len(tt.expected) {
				t.Fatalf("len = %d, want %d", len(result), len(tt.expected))
			}
			for i := range result {
				if result[i] != tt.expected[i] {
					t.Errorf("[%d] = %+v, want %+v", i, result[i], tt.expected[i])
				}
			}
		})
	}
}

// ───────────────────── 挑活动页 + 目标歧义诊断（C 单元档） ─────────────────────
//
// 这一档打的是**纯逻辑**：pickActivePage 不碰浏览器，targetDiagsFor 只把一个结果
// 翻成诊断。两条都不需要真浏览器，所以「退回」这个分支能被稳定地钉住 ——
// 而真浏览器上要造出这个状态得让页面的主线程堵死（见 cmd/observe_e2e_test.go）。

func boolPtr(b bool) *bool { return &b }

// targetPage 造一个候选：active == nil 表示 checkPageActive **求值失败**（本仓真站实测
// 就是这个状态：两个候选都 nil），不是「不是前台页」（那是明确的 false）。
//
// ⚠️ 名字不能叫 page —— 同包里 page 是 cdproto 的 page 包（observe_frames.go 在用）。
func targetPage(id, url string, active *bool) PageTarget {
	return PageTarget{ID: id, Title: "t-" + id, URL: url, Active: active}
}

func TestPickActivePage(t *testing.T) {
	tests := []struct {
		name         string
		pages        []PageTarget
		wantErr      string
		wantID       string
		wantURL      string
		wantCount    int
		wantFallback bool
	}{
		{
			name:    "没有页面目标 → 报错（不是退回）",
			pages:   nil,
			wantErr: "no page target found",
		},
		{
			name:      "一个候选且报了 visible → 挑它，不算退回",
			pages:     []PageTarget{targetPage("AAA", "https://a.example/real", boolPtr(true))},
			wantID:    "AAA",
			wantURL:   "https://a.example/real",
			wantCount: 1,
		},
		{
			// 真站实测的那一态：候选都报了 visible=false（都在后台）
			name: "全是明确的 false → 退回第一个，并标出来",
			pages: []PageTarget{
				targetPage("AAA", "https://a.example/first", boolPtr(false)),
				targetPage("BBB", "https://b.example/second", boolPtr(false)),
			},
			wantID:       "AAA",
			wantURL:      "https://a.example/first",
			wantCount:    2,
			wantFallback: true,
		},
		{
			// **这才是真站那次**：Active == nil（checkPageActive 超时/报错），
			// 于是「没有任何一个报了 visible」→ 退回第一个。缺陷就长在这里：
			// 退回的那个可能是夹具页/旧页，而调用方拿到裸 ID 时看不出来。
			name: "候选全是 nil（可见性检查失败）→ 退回第一个，并标出来",
			pages: []PageTarget{
				targetPage("MOCK", "https://mock.example/fixture", nil),
				targetPage("REAL", "https://real.example/under-test", nil),
			},
			wantID:       "MOCK",
			wantURL:      "https://mock.example/fixture",
			wantCount:    2,
			wantFallback: true,
		},
		{
			name: "混着 nil 与 false → 同样退回（两者都不算 visible）",
			pages: []PageTarget{
				targetPage("AAA", "https://a.example/one", nil),
				targetPage("BBB", "https://b.example/two", boolPtr(false)),
			},
			wantID:       "AAA",
			wantURL:      "https://a.example/one",
			wantCount:    2,
			wantFallback: true,
		},
		{
			name: "第一个是 nil、第二个报 visible → 挑第二个，**不算**退回",
			pages: []PageTarget{
				targetPage("OLD", "https://old.example", nil),
				targetPage("NEW", "https://new.example", boolPtr(true)),
				targetPage("BG", "https://bg.example", boolPtr(false)),
			},
			wantID:    "NEW",
			wantURL:   "https://new.example",
			wantCount: 3,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := pickActivePage(tt.pages)
			if tt.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
					t.Fatalf("err = %v, want 含 %q", err, tt.wantErr)
				}
				return
			}
			if err != nil {
				t.Fatalf("不该报错: %v", err)
			}
			if got.ID != tt.wantID || got.URL != tt.wantURL {
				t.Errorf("挑中 %s (%s), want %s (%s)", got.ID, got.URL, tt.wantID, tt.wantURL)
			}
			if got.Candidates != tt.wantCount {
				t.Errorf("Candidates = %d, want %d", got.Candidates, tt.wantCount)
			}
			if got.Fallback != tt.wantFallback {
				t.Errorf("Fallback = %v, want %v —— 这个标志就是说「这次是猜的」，错了等于整条修正失效",
					got.Fallback, tt.wantFallback)
			}
		})
	}
}

// TestTargetDiagsForIsSilentWhenPickedForReal 钉住**不说话**那半边：
// 有页面真报了 visible 时，模型里一条目标诊断都不许有。
//
// 为什么同样要测：常驻的诊断等于没有诊断 —— 如果实现退化成「无条件发一条」，
// 消费侧会学会无视 diagnostics，那 C 这一整条就白做了。
func TestTargetDiagsForIsSilentWhenPickedForReal(t *testing.T) {
	diags := targetDiagsFor(TargetChoice{ID: "REAL", URL: "https://real.example/x", Candidates: 2})
	if len(diags) != 0 {
		t.Fatalf("挑中的页是真报了 visible 的那个，不该有任何诊断，实际 %d 条: %+v", len(diags), diags)
	}
}

// TestTargetDiagsForFallbackIsActionable 钉住**说话**那半边的内容：
// detail 必须能让人（和 agent）判断出「挑错了没有」，所以三个要件一个都不能少 ——
// 几个候选、挑中的是哪个 target、它的 URL。
func TestTargetDiagsForFallbackIsActionable(t *testing.T) {
	choice := TargetChoice{
		ID:         "MOCK7A1B2C3D4E5F",
		URL:        "https://mock.example/fixture-page",
		Candidates: 7, // 挑个不像 1/2 的数：避免断言被别的数字蒙对
		Fallback:   true,
	}
	diags := targetDiagsFor(choice)
	if len(diags) != 1 {
		t.Fatalf("退回时该有且只有 1 条诊断，实际 %d 条: %+v", len(diags), diags)
	}
	d := diags[0]
	if d.Kind != DiagKindTargetAmbiguous {
		t.Errorf("kind = %q, want %q", d.Kind, DiagKindTargetAmbiguous)
	}
	for _, want := range []string{choice.ID, choice.URL, "7"} {
		if !strings.Contains(d.Detail, want) {
			t.Errorf("detail 里没有 %q —— 人/agent 没法判断挑错没有: %q", want, d.Detail)
		}
	}
	// 格式串写错（占位符与参数对不上）会留下 %!v(MISSING) 这类痕迹，
	// 而 detail 是给人读的，混进这种东西等于没写。
	if strings.Contains(d.Detail, "%!") {
		t.Errorf("detail 里有 fmt 的报错痕迹（格式串与参数对不上）: %q", d.Detail)
	}
	// frame_path 必须是主帧（这是「整个 tab 选错了」，不是「某一帧没取到」），
	// 且不能是 nil —— nil 会编成 JSON null，而这一格契约上是数组。
	if len(d.FramePath) != 1 || d.FramePath[0] != mainFramePath {
		t.Errorf("frame_path = %q, want [%q]", d.FramePath, mainFramePath)
	}
	t.Logf("诊断: kind=%s frame_path=%q detail=%q", d.Kind, d.FramePath, d.Detail)
}
