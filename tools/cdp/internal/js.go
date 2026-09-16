package internal

// TouchDetectionJS returns true if user agent contains mobile/tablet indicators
const TouchDetectionJS = `navigator.maxTouchPoints > 0`

// SnapshotJS captures DOM hierarchy as JSON
const SnapshotJS = `(function(){
	function domToJson(node) {
		if ('SCRIPT' == node.tagName || 'IMG' == node.tagName) return;

		let obj = {
			tag: node.tagName,
			attr: {}
		};

		if (node.attributes) {
			for (let i = 0; i < node.attributes.length; i++) {
			let attr = node.attributes[i];
			if (attr.nodeName == 'id' || attr.nodeName == 'class')
				obj.attr[attr.nodeName] = attr.nodeValue;
			}
		}

		if (node.childNodes.length === 1 && node.childNodes[0].nodeType === 3) {
			obj.text = node.childNodes[0].nodeValue.trim();
		}

		let children = [];
		for (let i = 0; i < node.children.length; i++) {
			let ret = domToJson(node.children[i]);
			if (ret)
				children.push(ret);
		}
		if (children.length > 0) obj.children = children;
		if (Object.keys(obj.attr).length === 0) obj.attr = undefined;
		return obj;
	}

	const rootElement = document.querySelector('body');
	if (!rootElement) return JSON.stringify({error: 'no body'});
	const jsonHierarchy = domToJson(rootElement);
	return JSON.stringify(jsonHierarchy);

})();`

// TrackRenderJS renders track points from window.__trackPath variable
const TrackRenderJS = `
(function() {
    var points = window.__trackPath;
    if (!points || points.length < 2) return;
    var color = window.__trackColor || 'hsl(200, 70%, 50%)';

    var container = document.createElement('div');
    container.id = '__track-overlay';
    container.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:99999;overflow:hidden;';

    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;';

    for (var i = 1; i < points.length; i++) {
        var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', points[i-1][0]);
        line.setAttribute('y1', points[i-1][1]);
        line.setAttribute('x2', points[i][0]);
        line.setAttribute('y2', points[i][1]);
        line.setAttribute('stroke', color);
        line.setAttribute('stroke-width', '1');
        line.setAttribute('stroke-opacity', '0.7');
        svg.appendChild(line);
    }

    container.appendChild(svg);

    var lastPoint = points[points.length - 1];
    var dot = document.createElement('div');
    dot.style.cssText = 'position:absolute;left:' + (lastPoint[0] - 3) + 'px;top:' + (lastPoint[1] - 3) + 'px;width:6px;height:6px;border-radius:50%;background:' + color + ';box-shadow:0 0 8px ' + color + ';transition:opacity 2s ease-out;';
    container.appendChild(dot);

    document.body.appendChild(container);

    setTimeout(function() {
        dot.style.opacity = '0';
        svg.style.opacity = '0';
    }, 500);

    setTimeout(function() {
        if (container.parentNode) {
            container.parentNode.removeChild(container);
        }
    }, 2500);
})();
`
