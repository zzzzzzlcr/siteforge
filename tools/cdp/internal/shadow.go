package internal

// pierceJS 是注入到每段脚本前的解析助手，让选择器能穿进 shadow DOM。
//
// 为什么需要它：2026-09-15 georgiapower 的 Salesforce Lightning 表单页实测，
// 页面自己报 document.querySelectorAll('button').length === 0、input === 0 ——
// 整个表单在 shadow root 里。cdp form / click / scroll 全部报 element not found。
//
// 关键点：shadow DOM **不影响** getBoundingClientRect()。元素在 shadow 里的
// 视口坐标照样正确，所以「滚动 → 算中心点 → 拟人点击」那套逻辑一行都不用改 ——
// 坏的只有解析这一步。修好这里，全部调用方（站点脚本、clickthrough、执行器）
// 一起受益，不必各自重写一套。
//
// 助手是递归的（shadow root 可以嵌套，那页实测 76 个）。
// closed 模式的 shadow root 拿不到，这是平台限制，无解。
const pierceJS = `
var __cdpRoots = function(root) {
	var out = [root];
	(function walk(r) {
		var all;
		try { all = r.querySelectorAll('*'); } catch (e) { return; }
		for (var i = 0; i < all.length; i++) {
			var sr = all[i].shadowRoot;
			if (sr) { out.push(sr); walk(sr); }
		}
	})(root);
	return out;
};
var __cdpQAIn = function(root, sel) {
	var rs = __cdpRoots(root), out = [];
	for (var i = 0; i < rs.length; i++) {
		var found;
		try { found = rs[i].querySelectorAll(sel); } catch (e) { continue; }
		for (var j = 0; j < found.length; j++) out.push(found[j]);
	}
	return out;
};
// __cdpVisible 回答「这个元素画不画得出来」：只看尺寸与 display/visibility，
// **不判遮挡**（遮挡是落点判据的事）。放在内核里而不是各自实现 ——
// 「第一个可见的匹配」这个口径一旦分家，报出来的坐标与真正点下去的那一下
// 就会静默对不上（本仓反复踩的坑，见 click.go 顶部）。
var __cdpVisible = function(el) {
	if (!el || el.nodeType !== 1 || !el.getBoundingClientRect) return false;
	var r;
	try { r = el.getBoundingClientRect(); } catch (e) { return false; }
	if (!(r.width > 0) || !(r.height > 0)) return false;
	try { if (el.getClientRects && el.getClientRects().length === 0) return false; } catch (e) {}
	try {
		var cs = getComputedStyle(el);
		if (cs && (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse')) return false;
	} catch (e) {}
	return true;
};
// __cdpQIn 取「第一个**看得见**的匹配」；一个都看不见时退回旧的「文档序第一个」。
//
// 为什么（2026-09-29 afrotech 任务 26238922 开窗实测）：页脚订阅按钮在页面上有
// 两个同名件，文档序第一个是 [hidden] 里的 0×0 重复件。老实现只看文档序，于是
// 坐标、可见性（0/0 = NaN 恒假）、滚动方向（隐藏件 centerY = 0 ⇒ 每轮都判成
// 「在视口上方」⇒ 每轮手势把页面往**远处**拖，20 轮结构上不可能收敛，实测
// cdp scroll 46.9 秒且方向反了）全被那个 0×0 带偏；最坏一档是点 (0,0) 还报
// 成功（假成功）。**退回 all[0]** 是刻意的：一个可见的都没有时，老行为后面会各自
// 报「不可见 / 找不到」，比凭空变成「找不到元素」多一层信息。
var __cdpQIn = function(root, sel) {
	var all = __cdpQAIn(root, sel);
	for (var i = 0; i < all.length; i++) {
		if (__cdpVisible(all[i])) return all[i];
	}
	return all.length ? all[0] : null;
};
var __cdpQ = function(sel) { return __cdpQIn(document, sel); };
var __cdpQA = function(sel) { return __cdpQAIn(document, sel); };

// __cdpDescribe 是「那一点上站着谁」的一行人话（<tag id="…" class="…">）——
// 落点判据说「不是它」时必须带上它，否则拒点的理由只能是一句「不知道」。
var __cdpDescribe = function(el) {
	if (!el || el.nodeType !== 1) return String(el);
	var s = '<' + String(el.tagName || '').toLowerCase();
	if (el.id) s += ' id="' + el.id + '"';
	var cls = el.getAttribute ? (el.getAttribute('class') || '') : '';
	if (cls) s += ' class="' + cls.slice(0, 60) + '"';
	return s + '>';
};
// __cdpTopAt 回答「这一点上**真的会收到点击**的最上面那个元素」。
// 两条讲究：
//   - 不用 elementFromPoint 单点：它只回最上面一个，而那一个常常是 pointer-events:none
//     的空遮罩（2026-09-28 ukg 实测：一个空遮罩被当成了「遮挡」，于是每一步都不点）——
//     所以走 elementsFromPoint 整摞、跳过 pointer-events:none 的层；
//   - **逐层下钻 shadow root**：ElementFromPoint 只看得见当前 root 里的东西，命中一个
//     shadow host 时给的是 host 本身，不是里面的节点。而 cdp 的解析（__cdpQ）是穿 shadow
//     的，两边的「那一点上是谁」必须对得上 —— 2026-09-29 本机实测：两层嵌套的 shadow
//     fixture 上只下一层会让落点判据**恒判不是目标**，一次本来正常的点击被拒
//     （TestClickPiercesShadowRoot）。
var __cdpTopAt = function(x, y) {
	var root = document, el = null;
	for (var depth = 0; depth < 32; depth++) {
		var stack = null;
		try { stack = root.elementsFromPoint(x, y); } catch (e) { return el; }
		if (!stack || !stack.length) return el;
		var pick = null;
		for (var i = 0; i < stack.length; i++) {
			var pe = '';
			try { pe = getComputedStyle(stack[i]).pointerEvents; } catch (e) {}
			if (pe === 'none') continue;
			pick = stack[i];
			break;
		}
		if (!pick) pick = stack[0];
		el = pick;
		if (!pick.shadowRoot) return el;
		root = pick.shadowRoot;
	}
	return el;
};
// __cdpSwallowed 回答「从命中点走到 label 的这一段里，有没有会**吞掉**这次点击的
// 交互件」：label 里的 <a href> 是典型 —— 浏览器遇到交互件的点击不做 label 激活行为，
// 于是「点上了」其实点开了链接（2026-09-20 warthunder 实测：EULA 勾选框的中心正压在
// 相邻的 Terms of Service 链接上，三张失败截图都停在法律条款页）。
var __cdpSwallowed = function(n, stop, el) {
	for (var x = n; x && x.nodeType === 1; x = x.parentElement) {
		if (x !== el) {
			var t = x.tagName;
			if (t === 'INPUT' || t === 'BUTTON' || t === 'SELECT' || t === 'TEXTAREA') return true;
			if (t === 'A' && x.hasAttribute('href')) return true;
			if (x.hasAttribute('onclick')) return true;
			var role = x.getAttribute('role');
			if (role === 'button' || role === 'link' || role === 'checkbox' ||
			    role === 'radio' || role === 'switch') return true;
		}
		if (x === stop) break;
	}
	return false;
};
// __cdpReachesAt 是「落点判据」的那一问：在 (x,y) 按下去，会不会落到 el 身上。
// 接受三样：el 自己 / el 的后代 / 包着 el 的 <label>（label.control === el，
// 且中间没有交互件吞点击）。返回 {reach, at} —— at 恒是那一点上实际站着谁，
// 拒点时要靠它说人话（「那一点上站着的是 <div class=…>」）。
var __cdpReachesAt = function(el, x, y) {
	var hit = __cdpTopAt(x, y);
	if (!hit) return {reach: false, at: ''};
	var at = __cdpDescribe(hit);
	if (hit === el || el.contains(hit)) return {reach: true, at: at};
	try { if (hit.shadowRoot && hit.shadowRoot.contains(el)) return {reach: true, at: at}; } catch (e) {}
	var lab = null;
	try { lab = hit.closest ? hit.closest('label') : null; } catch (e) {}
	if (lab && lab.control === el && !__cdpSwallowed(hit, lab, el)) return {reach: true, at: at};
	return {reach: false, at: at};
};
`

// withPierce 在脚本前注入解析助手，返回可直接求值的完整脚本。
//
// 助手定义在顶层，因而落在全局对象上 —— 生成的脚本里以裸名调用 __cdpQ 才解析得到。
// 重复求值只是重新定义一次，幂等。
func withPierce(js string) string {
	return pierceJS + js
}
