(() => {
  'use strict';
  const assetVersion = new URL(document.currentScript.src).searchParams.get('v');
  if (document.getElementById('guarded-workbench-background')) return;
  document.documentElement.classList.add('guarded-animated-workbench');
  const layer = document.createElement('div');
  layer.id = 'guarded-workbench-background';
  layer.setAttribute('aria-hidden', 'true');
  const frame = document.createElement('iframe');
  frame.title = '工作台实时演算背景';
  frame.tabIndex = -1;
  frame.setAttribute('aria-hidden', 'true');
  layer.append(frame);
  document.body.prepend(layer);
  let loaded = false, previous = '', lastFrames = 0, lastTime = performance.now();
  let liveTask = window.__guardedLiveTask || null, updateTimer;
  window.addEventListener('guarded-workbench-task', event => {
    liveTask = event.detail;
    if (!updateTimer) updateTimer = setTimeout(() => {updateTimer=null;updateContent();}, 250);
  });
  function collectContent() {
    const task = liveTask && Date.now()-liveTask.updatedAt < 15000 ? liveTask : null;
    return task ? {title:task.title.slice(0,100),source:`当前会话实时执行状态 · ${task.state}`,
      lines:task.lines.filter(line=>typeof line==='string').slice(-48).map(line=>line.slice(0,240)),
      task:{title:task.title,state:task.state,current:task.current,turn:task.turn,step:task.step,
        running:task.running,icon:task.icon,statusIcon:task.statusIcon,items:task.items || []}}
      : {title:'完整工作台',source:'等待当前会话同步',lines:['正在同步当前任务'],
        task:{title:'正在同步当前任务',state:'连接中',current:'等待会话执行事件',icon:'task',statusIcon:'offline',items:[]}};
  }
  function updateContent() {
    if (!loaded) return;
    const payload = collectContent(), signature = JSON.stringify(payload);
    if (signature === previous) return;
    previous = signature;
    frame.contentWindow.postMessage({type:'guarded-workbench-content',...payload},location.origin);
  }
  function report() {
    const root = document.getElementById('root');
    if (root) {
      const probe = document.createElement('span');
      probe.style.cssText = 'display:none;color:var(--dsw-alias-label-primary,#edf4ff)';
      root.append(probe);
      const color = getComputedStyle(probe).color.match(/[\d.]+/g)?.map(Number) || [240,240,240];
      probe.remove();
      document.documentElement.dataset.guardedTheme = color[0]*.2126+color[1]*.7152+color[2]*.0722 > 150 ? 'dark' : 'light';
    }
    const now = performance.now();
    let state = {ready:false,frames:0,fps:0,width:0,height:0,lines:0,error:''};
    try {
      const stats = frame.contentWindow.__guardedAnimationStats || {};
      const canvas = frame.contentDocument?.getElementById('stage');
      state.frames = stats.frames || 0;
      state.fps = Math.round((state.frames-lastFrames)*10000/Math.max(1,now-lastTime))/10;
      state.width = canvas?.width || 0; state.height = canvas?.height || 0;
      state.lines = stats.displayed_lines || 0; state.error = stats.error || '';
      state.integratedTask = stats.integrated_task || '';
      state.taskIconDraws = stats.task_icon_draws || 0;
      state.ready = state.frames > lastFrames && state.width > 0 && state.height > 0 && !state.error;
      lastFrames = state.frames; lastTime = now;
    } catch (error) { state.error = String(error.message || error); }
    window.__guardedWorkbenchBackground = state;
    Object.assign(state,{taskState:liveTask?.state || '等待同步',taskTitle:liveTask?.title || '',
      sessionId:liveTask?.sessionId || '',taskUpdatedAt:liveTask?.updatedAt || null,source:'session-events'});
    document.documentElement.dataset.guardedBackground = state.ready ? 'running' : 'loading';
    if (window.parent !== window) window.parent.postMessage({type:'guarded-workbench-background-status',...state},'http://127.0.0.1:8765');
  }
  frame.addEventListener('load', () => {loaded=true;previous='';updateContent();});
  const animationUrl = new URL('./assets/guarded-workbench-animation.html?background=1',location.href);
  if (assetVersion) animationUrl.searchParams.set('v',assetVersion);
  frame.src = animationUrl.href;
  setInterval(updateContent,750);
  setInterval(report,1000);
})();
