(function(){
  var deepAll=function(sel){var out=[],roots=[document],i=0;
    while(i<roots.length){var r=roots[i++];
      var els=(r.querySelectorAll?r.querySelectorAll(sel):[]);
      for(var j=0;j<els.length;j++)out.push(els[j]);
      var all=(r.getElementsByTagName?r.getElementsByTagName('*'):[]);
      for(var k=0;k<all.length;k++)if(all[k].shadowRoot)roots.push(all[k].shadowRoot);}
    return out;};
  var vis=function(e){var r=e.getBoundingClientRect();
    return r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight;};
  var es=deepAll('button,[role=button],a,input,select,textarea');
  var rows=[];
  for(var i=0;i<es.length&&rows.length<8;i++){var e=es[i];
    if(!vis(e))continue;
    var r=e.getBoundingClientRect();var x=r.left+r.width/2,y=r.top+r.height/2;
    if(x<0||y<0||x>window.innerWidth||y>window.innerHeight)continue;
    var stack=document.elementsFromPoint(x,y)||[];
    var who='';
    for(var k=0;k<stack.length;k++){var h=stack[k];var pe='';
      try{pe=getComputedStyle(h).pointerEvents||'';}catch(err){}
      if(pe==='none')continue;
      if(h===e||e.contains(h)||h.contains(e)){who='';break;}
      who=(h.tagName||'').toLowerCase()+(h.id?('#'+h.id):'')+'.'+String(h.className||'').split(' ')[0];
      break;}
    if(who)rows.push({tag:(e.tagName||'').toLowerCase(),txt:String(e.innerText||e.value||'').slice(0,20),covered_by:who});}
  return JSON.stringify({url:location.href,frames:window.frames.length,covered:rows});})()
