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
var __cdpQIn = function(root, sel) {
	var rs = __cdpRoots(root);
	for (var i = 0; i < rs.length; i++) {
		var found;
		try { found = rs[i].querySelector(sel); } catch (e) { continue; }
		if (found) return found;
	}
	return null;
};
var __cdpQ = function(sel) { return __cdpQIn(document, sel); };
var __cdpQA = function(sel) { return __cdpQAIn(document, sel); };
`

// withPierce 在脚本前注入解析助手，返回可直接求值的完整脚本。
//
// 助手定义在顶层，因而落在全局对象上 —— 生成的脚本里以裸名调用 __cdpQ 才解析得到。
// 重复求值只是重新定义一次，幂等。
func withPierce(js string) string {
	return pierceJS + js
}
