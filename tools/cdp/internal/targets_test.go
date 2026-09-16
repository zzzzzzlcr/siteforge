package internal

import (
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
