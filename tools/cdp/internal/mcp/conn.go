package mcp

import (
	"fmt"

	"cdp/internal"
)

// 编译期钉子：这道门说的 Browser 就是内核那个 Client。
// 内核改了方法签名而这里没跟上，是**编译期**的事，不是运行时才发现的。
var _ Browser = (*internal.Client)(nil)

// Connector 打开到某个浏览器的连接。
type Connector struct {
	Target Target
}

// Dial 连上这个浏览器，返回内核客户端与释放函数。
//
// 每个工具调用**单独连一次**（不缓存长连接），和 CLI 每条命令的行为一致。
// 理由不是偷懒，是窗口的寿命：Bit 窗口活几分钟，一个长连接在窗口被重开之后
// 只会指向一个死掉的 target —— 那种“连着但什么都没反应”正是最难查的一种。
// 现连现断的代价是每条命令多一次 /json/version 往返，换来的是「窗口换了，
// 下一次调用自己就连上了」。
//
// ⚠️ 连不上必须**当场**报错，且报错里点名 <host:port>：窗口没了的时候，
// 调用方只有拿到这个串才能判断该重开哪个窗口（P6）。绝不许挂着 —— 一个挂住的
// 工具调用在 agent 那边是「模型卡住了」，而不是「窗口没了」。
func (c Connector) Dial() (Browser, func(), error) {
	client, err := internal.NewClient(c.Target.Host, c.Target.Port)
	if err != nil {
		return nil, nil, fmt.Errorf("连不上浏览器 %s：%w\n"+
			"（Bit 窗口只活几分钟，很可能已经关了 —— 重新 `bit.sh open` 拿到新的 --ws-url 再试；"+
			"别重试同一地址，它不会自己回来）", c.Target.Address(), err)
	}
	// ⚠️ 释放用 Disconnect，**不是** Close：Close 会关掉 Chrome 的页面
	// （commit 18cfd23，CLAUDE.md 里那条）。
	return client, client.Disconnect, nil
}
