# eval 子命令实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 cdp 添加 eval 子命令，支持在指定 frame 或主帧执行 JavaScript 代码

**Architecture:** 新建 `cmd/eval.go` 实现 eval 子命令，复用 `internal/client.go` 的 `EvalInFrame` 方法。`--frame-id` 和 `--file` flags 通过 cobra 定义。

**Tech Stack:** Go, cobra, chromedp

---

### Task 1: 创建 cmd/eval.go

**Files:**
- Create: `cmd/eval.go`

- [ ] **Step 1: 创建 cmd/eval.go 文件**

```go
package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/spf13/cobra"
	"cdp/internal"
)

var evalCmd = &cobra.Command{
	Use:   "eval",
	Short: "Execute JavaScript in a frame",
	RunE:  runEval,
}

func init() {
	rootCmd.AddCommand(evalCmd)
	evalCmd.Flags().String("frame-id", "", "Frame ID to execute in (empty for main frame)")
	evalCmd.Flags().String("file", "", "Read JS from file instead of argument")
}

func runEval(cmd *cobra.Command, args []string) error {
	frameID, _ := cmd.Flags().GetString("frame-id")
	filePath, _ := cmd.Flags().GetString("file")

	var js string
	var err error

	if filePath != "" {
		js, err = readFile(filePath)
		if err != nil {
			return fmt.Errorf("failed to read file: %w", err)
		}
	} else {
		if len(args) == 0 {
			return fmt.Errorf("no JS code provided")
		}
		js = args[0]
	}

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Close()

	var result json.RawMessage
	err = client.EvalInFrame(frameID, js, &result)
	if err != nil {
		return fmt.Errorf("JS exception: %s", err)
	}

	if len(result) > 0 {
		fmt.Fprintln(os.Stdout, string(result))
	}
	return nil
}

func readFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	data, err := io.ReadAll(f)
	return string(data), err
}
```

- [ ] **Step 2: 验证文件创建**

Run: `go build -o cdp main.go`
Expected: 编译成功，无错误

- [ ] **Step 3: 提交代码**

```bash
git add cmd/eval.go
git commit -m "feat: add eval subcommand"
```

---

### Task 2: 测试 eval 命令

**Files:**
- Test: 运行 `./cdp eval --help` 验证帮助信息

- [ ] **Step 1: 验证帮助信息**

Run: `go run . eval --help`
Expected:
```
Execute JavaScript in a frame

Usage:
  cdp eval [flags]

Flags:
      --file string   Read JS from file instead of argument
      --frame-id string   Frame ID to execute in (empty for main frame)
  -h, --help          help for eval
```

- [ ] **Step 2: 提交变更**

```bash
git add -A
git commit -m "test: verify eval command works"
```

---

**Plan complete.**

两个执行选项：

**1. Subagent-Driven (recommended)** - 每个 task 由独立 subagent 执行，任务间有检查点

**2. Inline Execution** - 在当前 session 执行，带检查点的批量执行

选择哪种方式？