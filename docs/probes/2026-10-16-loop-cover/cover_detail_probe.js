(function(){
  var t0=(window.performance&&performance.now)?performance.now():0;
  function deepAll(sel){var out=[],roots=[document],i=0;
    while(i<roots.length){var r=roots[i++];
      var els=(r.querySelectorAll?r.querySelectorAll(sel):[]);
      for(var j=0;j<els.length;j++)out.push({el:els[j],inShadow:r!==document});
      var all=(r.getElementsByTagName?r.getElementsByTagName('*'):[]);
      for(var k=0;k<all.length;k++)if(all[k].shadowRoot)roots.push(all[k].shadowRoot);}
    return out;}
  function vis(e){var r=e.getBoundingClientRect();
    return r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight;}
  function desc(e){var r=e.getBoundingClientRect();var cs=null;
    try{cs=getComputedStyle(e);}catch(err){}
    return {tag:(e.tagName||'').toLowerCase(),
            id:e.id||'', cls:String(e.className||'').split(' ').slice(0,3).join(' '),
            rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],
            pos:cs?cs.position:'', z:cs?cs.zIndex:'',
            txt:String(e.innerText||e.value||'').replace(/\s+/g,' ').trim().slice(0,40)};}
  var CLICK='button,[role=button],a,input,select,textarea';
  var es=deepAll(CLICK);
  var light=0,shadow=0,hosts=[];
  for(var i=0;i<es.length;i++){if(es[i].inShadow)shadow++;else light++;}
  var allEls=document.querySelectorAll('*');
  for(var i=0;i<allEls.length;i++){if(allEls[i].shadowRoot){
    var n=allEls[i].shadowRoot.querySelectorAll(CLICK).length;
    hosts.push({host:desc(allEls[i]).tag+(allEls[i].id?('#'+allEls[i].id):''),clickables_in_shadow:n,
                mode:allEls[i].shadowRoot.mode});}}
  var ifr=[];
  var frames=document.querySelectorAll('iframe,frame');
  for(var i=0;i<frames.length;i++){var f=frames[i];var o=desc(f);
    var co=null;try{co=(f.contentWindow&&f.contentWindow.document)?false:true;}catch(err){co=true;}
    ifr.push({src:String(f.src||'').slice(0,90),rect:o.rect,cross_origin_guess:co});}
  var covered=[];
  for(var i=0;i<es.length&&covered.length<12;i++){var e=es[i].el;
    if(!vis(e))continue;
    var r=e.getBoundingClientRect();var x=r.left+r.width/2,y=r.top+r.height/2;
    if(x<0||y<0||x>window.innerWidth||y>window.innerHeight)continue;
    var stack=document.elementsFromPoint(x,y)||[];
    var top=stack.length?stack[0]:null;
    var who='',k;
    for(k=0;k<stack.length;k++){var h=stack[k];var pe='';
      try{pe=getComputedStyle(h).pointerEvents||'';}catch(err2){}
      if(pe==='none')continue;
      if(h===e||e.contains(h)||h.contains(e)){who='';break;}
      who=h;break;}
    if(who)covered.push({target:desc(e),point:[Math.round(x),Math.round(y)],
                         top_of_stack:top?desc(top):null, covered_by:desc(who),
                         in_shadow:es[i].inShadow});}
  var t1=(window.performance&&performance.now)?performance.now():0;
  return JSON.stringify({url:location.href,title:document.title,
    frames_len:window.frames.length, iframe_count:frames.length, iframes:ifr,
    clickables_total:es.length, clickables_light:light, clickables_shadow:shadow,
    shadow_hosts:hosts, covered:covered, probe_ms:Math.round((t1-t0)*100)/100});})()
