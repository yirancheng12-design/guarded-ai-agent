/* Original CSS pop-up study inspired by April Zhu's Open House. No remote runtime. */
(() => {
  if (window.GuardedOpenHouse) return;
  const root = document.createElement('div');root.className='goh-overlay';root.setAttribute('aria-hidden','true');
  root.innerHTML='<div class="goh-card"><div class="goh-eyebrow">GUARDED / OPEN HOUSE</div><div class="goh-stage"><div class="goh-book"><div class="goh-page"><div class="goh-page-copy"><b>任务与需求</b><p class="goh-page-goal"></p></div></div><div class="goh-page right"><div class="goh-page-copy"><b>步骤与文件</b><p class="goh-page-steps"></p></div></div><div class="goh-tree"></div><div class="goh-house"><div class="goh-wall"></div><div class="goh-roof"></div><div class="goh-door"></div><div class="goh-window one"></div><div class="goh-window two"></div></div></div></div><p class="goh-caption"></p><p class="goh-sub"></p><div class="goh-content"><article><h3>① 屋顶 · 任务目标</h3><p data-content="goal"></p></article><article><h3>② 墙体 · 步骤记录（会话摘录）</h3><p data-content="steps"></p></article><article><h3>③ 地基 · 结果与文件</h3><p data-content="result"></p></article></div><div class="goh-controls"><button data-action="pause">暂停阅读</button><button data-action="previous">上一步</button><button data-action="next">下一步</button><button data-action="close">返回任务 ×</button></div><div class="goh-progress"><span></span></div></div>';
  document.body.append(root);let timers=[];let serial=0;let current=0,pinned=false,active=false,currentTitle='',refreshTimer;root.hidden=true;root.setAttribute('aria-label','当前任务立体书');
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const excerpt=(s,n)=>s.length>n?s.slice(0,n)+'…':s;
  function collect(){
    const flow=document.querySelector('[data-slot="conversation.chat.flow"]');
    const users=flow?[...flow.querySelectorAll('[data-slot="conversation.user.text.node"]')]:[];
    const firstRequest=clean(users[0]?.textContent),latestRequest=clean(users.at(-1)?.textContent);const request=firstRequest+(latestRequest&&latestRequest!==firstRequest?'\n最近补充：'+latestRequest:'');
    const nodes=flow?[...flow.querySelectorAll('[data-slot="conversation.chat.node"]')].filter(n=>!n.querySelector('[data-slot="conversation.user.text.node"]')):[];
    const last=flow;let record='';
    if(last){const copy=last.cloneNode(true);copy.querySelectorAll('[data-slot="conversation.user.text.node"]').forEach(e=>(e.closest('[data-slot="conversation.chat.node"]')||e).remove());copy.querySelectorAll('button,svg,input,textarea,details').forEach(e=>e.remove());record=clean(copy.textContent).slice(-600);}
    const steps=last?[...last.querySelectorAll('ol>li,ul>li')].map(n=>clean(n.textContent)).filter(Boolean).slice(-5):[];
    const files=flow?[...flow.querySelectorAll('code,a[href]')].map(n=>clean(n.textContent)).filter(t=>/\.(txt|pdf|docx|xlsx|csv|py|js|html|mp4|png|json|md)\b/i.test(t)).slice(-5):[];
    return {goal:request?excerpt(request,600):'当前会话尚未加载需求原文。',steps:steps.length?steps.map((t,i)=>(i+1)+'. '+excerpt(t,180)).join('\n'):'当前已加载记录中没有独立步骤列表。',result:(record?'最近会话记录：'+excerpt(record,600):'当前尚无已加载的结果记录。')+(files.length?'\n文件线索（来自会话，不代表已验证）：\n'+[...new Set(files)].join('\n'):''),files};
  }
  function refresh(){if(!active)return;const d=collect();root.querySelector('.goh-page-goal').textContent=currentTitle+'\n'+excerpt(d.goal,140);root.querySelector('.goh-page-steps').textContent=excerpt(d.steps,140)+(d.files.length?'\n'+excerpt(d.files.join(' / '),80):'');for(const k of ['goal','steps','result'])root.querySelector('[data-content="'+k+'"]').textContent=d[k];}
  function cancelTimers(){timers.forEach(clearTimeout);timers=[];}
  function stop(){serial++;cancelTimers();active=false;pinned=false;root.hidden=true;root.className='goh-overlay';}
  function stage(n,title){if(title)currentTitle=title;active=true;current=Math.max(0,Math.min(4,n));root.hidden=false;root.setAttribute('aria-hidden','false');root.className='goh-overlay goh-visible'+(current>=1?' goh-open':'')+(current>=2?' goh-rise':'')+(current>=3?' goh-explode':'')+(pinned?' goh-pinned':'');root.dataset.step=String(current);root.querySelector('.goh-caption').textContent=currentTitle;root.querySelector('.goh-sub').textContent=['准备打开任务','书页内容 · 任务与需求','屋顶抬起 · 查看目标','墙体展开 · 查看步骤','地基展开 · 查看结果与文件'][current];root.querySelectorAll('.goh-content article').forEach((e,i)=>e.classList.toggle('goh-active',current===i+2));refresh();}
  function pin(){cancelTimers();pinned=true;stage(Math.max(2,current));root.querySelector('[data-action="pause"]').textContent='已暂停 · 可逐步阅读';}
  function play(title,slow=false){stop();currentTitle=title||document.title.replace(/\s*[—–]\s*Guarded$/,'');const run=serial;stage(0);root.querySelector('[data-action="pause"]').textContent='暂停阅读';const times=slow?[450,1800,4200,6600,10000]:[40,420,1100,1800,2900];[1,2,3,4].forEach((n,i)=>timers.push(setTimeout(()=>{if(run===serial)stage(n)},times[i])));timers.push(setTimeout(()=>{if(run===serial&&!pinned)stop()},times[4]));}
  root.querySelector('.goh-controls').addEventListener('click',e=>{const action=e.target.closest('button')?.dataset.action;if(action==='close')stop();else if(action==='pause')pin();else if(action){pin();stage(current+(action==='next'?1:-1));}});
  const contentObserver=new MutationObserver(records=>{if(!active||records.every(r=>root.contains(r.target)))return;clearTimeout(refreshTimer);refreshTimer=setTimeout(refresh,100);});contentObserver.observe(document.body,{subtree:true,childList:true,characterData:true});
  const onChange=e=>play(e.detail?.title||'正在切换任务');window.addEventListener('guarded:task-change',onChange);window.addEventListener('pagehide',stop);document.addEventListener('keydown',e=>{if(e.key==='Escape')stop()});
  window.GuardedOpenHouse={play,stage,stop,destroy(){stop();contentObserver.disconnect();clearTimeout(refreshTimer);window.removeEventListener('guarded:task-change',onChange);root.remove();delete window.GuardedOpenHouse;}};
})();
