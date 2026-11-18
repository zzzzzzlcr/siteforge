(function(){
  var out={url:location.href,hash:location.hash,title:document.title,ready:document.readyState,
    innerText:(document.body?document.body.innerText:'').slice(0,900),
    testids:[],buttons:[],inputs:[],iframes:document.querySelectorAll('iframe').length};
  var ts=document.querySelectorAll('[data-testid]');
  for(var i=0;i<ts.length;i++){out.testids.push(ts[i].getAttribute('data-testid'));}
  var bs=document.querySelectorAll('button,a[role=button],input[type=submit],div[role=button],a');
  for(var j=0;j<bs.length && out.buttons.length<50;j++){
    var b=bs[j],r=b.getBoundingClientRect(),cs;
    try{cs=getComputedStyle(b);}catch(e){continue;}
    if(r.width<4||r.height<4||cs.display==='none'||cs.visibility==='hidden')continue;
    out.buttons.push({tag:b.tagName.toLowerCase(),t:(b.innerText||b.value||'').trim().slice(0,50),
      id:b.id||'',cls:String(b.className||'').slice(0,60),tid:b.getAttribute('data-testid')||'',
      x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)});
  }
  var is=document.querySelectorAll('input');
  for(var k=0;k<is.length && out.inputs.length<40;k++){
    var e=is[k],r2=e.getBoundingClientRect();
    out.inputs.push({type:e.type,name:e.name||'',tid:e.getAttribute('data-testid')||'',
      ph:e.placeholder||'',w:Math.round(r2.width),h:Math.round(r2.height)});
  }
  return JSON.stringify(out);
})()
