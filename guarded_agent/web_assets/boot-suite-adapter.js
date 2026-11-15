/* Local Guarded adapter. Original animation: 沈宏宏 / LeeRolling, dsh-boot-suite 2.13.2. */
(() => {
  'use strict';
  // Host-owned Guarded shield + G; upstream animation keeps its own fallback.
  window.__GUARDED_BRAND_PATH__ = "M25 2 L45 10 L43 29 L36 39 L25 48 L14 39 L7 29 L5 10 Z M25 7 L10 13 L12 27 L18 35 L25 42 L32 35 L38 27 L40 13 Z M35 17 L31 21 L22 21 L19 24 L19 28 L22 31 L29 31 L32 28 L26 28 L26 24 L36 24 L36 30 L31 35 L20 35 L14 29 L14 23 L20 17 L31 17 Z";
  const key = 'guarded-boot-suite-v1';
  let config = {enabled: true, template: 'archive-warm', wallpaper: 'suite', sound: true, voice: true,
    effects: true, transitionIn: 'scanline', transitionJoin: 'glitch', transitionOut: 'iris'};
  try { config = {...config, ...JSON.parse(localStorage.getItem(key) || '{}')}; } catch {}
  // Undo only the short-opening migration; keep other saved preferences.
  try {
    if (localStorage.getItem('guarded-opening-v2') === 'on') {
      if (config.template === 'guarded-opening') {
        config.template = 'archive-warm';
        config.transitionIn = 'scanline'; config.transitionJoin = 'glitch'; config.transitionOut = 'iris';
        localStorage.setItem(key, JSON.stringify(config));
      }
      localStorage.removeItem('guarded-opening-v2');
    }
  } catch {}
  const catalog = [{"id": "550c-homage", "name": "550C 复刻（地球仪版）"}, {"id": "archive-warm", "name": "档案暖白"}, {"id": "chars-globe", "name": "字符地球"}, {"id": "deep-sonar", "name": "深海声呐"}, {"id": "dsh-intro", "name": "Guarded 片头"}, {"id": "dsh-intro-act2", "name": "Guarded 片头 · 第二幕"}, {"id": "dsh-intro-act3", "name": "Guarded 片头 · 第三幕"}, {"id": "dsh-intro-full", "name": "Guarded 片头 · 完整版"}, {"id": "dsh-intro-two", "name": "片头 + 扫描登录（两幕连播）"}, {"id": "dsh-intro-welcome", "name": "只播片头（WELCOME 收束）"}, {"id": "earth-globe", "name": "可旋转地球"}, {"id": "effects-gallery", "name": "效果总览"}, {"id": "pos-test", "name": "定位测试"}, {"id": "terminal-phosphor", "name": "磷光终端"}, {"id": "timeline-demo", "name": "时间线演示"}, {"id": "wipe-demo", "name": "擦除演示"}, {"id": "wipe-tutorial", "name": "擦除·教程"}, {"id": "world-line", "name": "世界地图线描"}];
  const templates = new Set(catalog.map(item => item.id));
  if (!templates.has(config.template)) config.template = 'archive-warm';
  const enabled = document.getElementById('bootEnabled');
  const template = document.getElementById('bootTemplate');
  template.replaceChildren(...catalog.map(item => {
    const option = document.createElement('option'); option.value = item.id;
    option.textContent = item.name;
    return option;
  }));
  const wallpaper = document.getElementById('bootWallpaper');
  const preview = document.getElementById('bootPreview');
  const replay = document.getElementById('replayIntro');
  const status = document.getElementById('bootStatus');
  const app = document.getElementById('app') || document.querySelector('.app');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const forced = matchMedia('(forced-colors: active)');
  let playing = false, watchdog, previousFocus, serial = 0, rootObserver, startupPending = false;
  const note = text => { status.textContent = text; };
  const save = () => { try { localStorage.setItem(key, JSON.stringify(config)); } catch {} };
  // Apply the requested Guarded-branded final background once per browser.
  try {
    if (localStorage.getItem('guarded-wallpaper-brand-v1') !== 'on') {
      config.wallpaper = 'suite'; save();
      localStorage.setItem('guarded-wallpaper-brand-v1', 'on');
    }
  } catch { config.wallpaper = 'suite'; }
  const sound = document.getElementById('bootSound');
  const voice = document.getElementById('bootVoice');
  const effects = document.getElementById('bootEffects');
  const transitionTypes = new Set(['dissolve', 'flash', 'glitch', 'scanline', 'slide', 'iris', 'black', 'none']);
  for (const field of ['transitionIn', 'transitionJoin', 'transitionOut']) {
    if (!transitionTypes.has(config[field])) config[field] = 'dissolve';
    const select = document.getElementById('boot' + field);
    select.value = config[field];
    select.addEventListener('change', () => { config[field] = select.value; save(); });
  }
  function fullEffects() {
    config.enabled = config.sound = config.voice = config.effects = true;
    enabled.checked = sound.checked = voice.checked = effects.checked = true;
    const solid = document.getElementById('glassTransparency');
    const motion = document.getElementById('glassMotion');
    if (solid.getAttribute('aria-checked') === 'true' && !solid.disabled) solid.click();
    if (motion.getAttribute('aria-checked') !== 'true' && !motion.disabled) motion.click();
    document.body.classList.add('suite-all-effects'); save();
    note('全部效果已开启：光影、透明、粒子、转场、音效、语音与模型切换动画。声音受浏览器自动播放规则约束，可点击预览播放。');
  }
  document.getElementById('bootAllEffects').addEventListener('click', fullEffects);
  for (const [field, input] of [['sound', sound], ['voice', voice], ['effects', effects]]) {
    input.checked = Boolean(config[field]);
    input.addEventListener('change', () => {
      config[field] = input.checked; save(); document.body.classList.toggle('suite-all-effects', config.effects);
    });
  }
  function applyWallpaper() {
    const suite = config.wallpaper === 'suite';
    document.body.classList.toggle('suite-wallpaper', suite);
    for (const image of document.querySelectorAll('.world-art, .world-wire, .cover-art')) {
      image.src = suite ? '/boot-suite/lib/assets/guarded-background.png' : '/guarded-cover.webp';
    }
  }
  function controls() {
    const disabled = playing || reduced.matches || forced.matches;
    replay.disabled = preview.disabled = disabled;
    replay.title = '播放开机套件动画 · Esc 可跳过';
  }
  function release() {
    if (!playing) return;
    playing = false; clearTimeout(watchdog); rootObserver?.disconnect();
    app.inert = false;
    document.documentElement.classList.remove('dsu-lock');
    document.documentElement.style.overflow = ''; document.body.style.overflow = '';
    document.body.classList.remove('suite-playing');
    controls();
    if (!reduced.matches && !forced.matches) {
      app.classList.remove('guarded-original-reveal');
      requestAnimationFrame(() => app.classList.add('guarded-original-reveal'));
    }
    previousFocus?.focus?.({preventScroll: true});
  }
  function stop() {
    serial++;
    try { window.__DSH_STARTUP_API__?.skip(); } catch {}
    // Adapter owns a final escape hatch even if the vendor runtime fails.
    document.getElementById('dsh-startup-root')?.remove();
    document.querySelectorAll('.dsu-trans-overlay').forEach(node => node.remove());
    release();
  }
  async function loadTemplate(id) {
    const response = await fetch(`/boot-suite/templates/${id}/template.json`, {signal: AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error('模板读取失败');
    const data = await response.json();
    if (data.id !== id) throw new Error('模板标识无效');
    return data;
  }
  async function play() {
    if (playing || reduced.matches || forced.matches || document.hidden) return;
    const run = ++serial;
    playing = true; previousFocus = document.activeElement; controls();
    try {
      const first = await loadTemplate(config.template);
      if (run !== serial) return;
      window.__DSH_STARTUP__ = {enabled: true, mode: 'always', theme: 'light', speed: 1,
        requireInteraction: false, allowSkip: true, sound: config.sound, voice: config.voice, voiceSource: 'tts',
        orgZh: 'Guarded', orgEn: 'Guarded', orgSub: 'LOCAL AI',
        identity: 'OPERATOR', identityId: 'LOCAL', accessLevel: '3',
        transitionIn: config.transitionIn, transitionJoin: config.transitionJoin, transitionOut: config.transitionOut};
      window.__DSH_STARTUP_TEMPLATE__ = first;
      window.__DSH_STARTUP_TEMPLATE2__ = null;
      window.__DSH_STARTUP_TPL_BASE__ = '/dsh-startup';
      window.__DSH_STARTUP_SCRIPT_URL__ = '/boot-suite/lib/splash.js';
      window.__DSH_STARTUP_SKIP__ = false;
      window.__DSH_STARTUP_LOADED__ = false;
      const script = document.createElement('script');
      script.src = '/boot-suite/lib/splash.js';
      script.onerror = () => { if (run === serial) { stop(); note('动画未能加载，已恢复聊天界面。'); } };
      app.inert = true; document.body.classList.add('suite-playing');
      rootObserver = new MutationObserver(() => {
        const root = document.getElementById('dsh-startup-root');
        if (root) { root.setAttribute('role', 'dialog'); root.setAttribute('aria-label', '启动动画，按 Esc 跳过'); }
      });
      rootObserver.observe(document.body, {childList: true});
      script.onload = () => {
        if (run !== serial) return;
        document.querySelector('#dsh-startup-root button')?.focus();
        script.remove();
        if (!document.getElementById('dsh-startup-root')) release();
      };
      const duration = Math.max(Number(first.duration) || 30000, 30000) + 30000;
      watchdog = setTimeout(() => { stop(); note('动画超时，已恢复聊天界面。'); }, Math.min(duration, 180000));
      document.head.append(script);
      note('正在播放 · 可按 Esc 跳过');
    } catch {
      stop(); note('模板或素材不可用，已恢复聊天界面。');
    }
  }
  enabled.checked = Boolean(config.enabled); template.value = config.template;
  wallpaper.value = config.wallpaper === 'guardian' ? 'guardian' : 'suite';
  enabled.addEventListener('change', () => { config.enabled = enabled.checked; save(); });
  template.addEventListener('change', () => { config.template = template.value; save(); note('已保存，下次播放生效。'); });
  wallpaper.addEventListener('change', () => { config.wallpaper = wallpaper.value; save(); applyWallpaper(); });
  replay.addEventListener('click', play); preview.addEventListener('click', play);
  window.addEventListener('dsh-startup:done', () => {
    // A skipped prior instance can emit its completion after a new one starts.
    if (!document.getElementById('dsh-startup-root')) { release(); note('播放结束，已返回聊天。'); }
  });
  document.addEventListener('keydown', event => {
    if (playing && event.key === 'Escape') { event.preventDefault(); stop(); note('已跳过启动动画。'); }
  }, true);
  // Server polling must not cancel the page's opening animation, including
  // when a background model task was already running before this page opened.
  for (const query of [reduced, forced]) query.addEventListener('change', () => { if (query.matches) stop(); controls(); });
  function startupWhenVisible() {
    if (!startupPending || !config.enabled || document.hidden || playing) return;
    startupPending = false;
    play();
  }
  window.addEventListener('pagehide', () => {
    stop(); startupPending = Boolean(config.enabled);
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) startupPending = Boolean(config.enabled);
    startupWhenVisible();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && playing) { stop(); startupPending = Boolean(config.enabled); }
    else startupWhenVisible();
  });
  window.addEventListener('error', event => {
    if (playing && event.filename?.includes('/boot-suite/lib/splash.js')) {
      stop(); note('动画运行异常，已恢复聊天界面。');
    }
  });
  // Selecting a model changes the next request; it does not preload its weights.
  const model = document.getElementById('model');
  const thinking = document.getElementById('deepThinking');
  const scene = document.querySelector('.guardian-world');
  const contour = document.createElement('img');
  contour.className = 'world-contour'; contour.alt = ''; contour.setAttribute('aria-hidden', 'true');
  // Derive real edges from the current wallpaper in the compositor, rather
  // than brightening the whole portrait or generating a different character.
  const edgeDefinitions = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  edgeDefinitions.setAttribute('width', '0'); edgeDefinitions.setAttribute('height', '0');
  edgeDefinitions.setAttribute('aria-hidden', 'true');
  edgeDefinitions.style.position = 'absolute';
  edgeDefinitions.innerHTML = '<defs><filter id="guarded-character-edges" x="-5%" y="-5%" width="110%" height="110%" color-interpolation-filters="sRGB"><feConvolveMatrix order="3" kernelMatrix="-1 -1 -1 -1 8 -1 -1 -1 -1" preserveAlpha="true"/><feColorMatrix type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  .2126 .7152 .0722 0 0"/><feComponentTransfer><feFuncA type="linear" slope="3"/></feComponentTransfer><feGaussianBlur stdDeviation=".35" result="edgeAlpha"/><feFlood flood-color="var(--character-glow-color, #83e7ff)"/><feComposite in2="edgeAlpha" operator="in" result="lines"/><feGaussianBlur stdDeviation="1.5"/><feMerge><feMergeNode/><feMergeNode in="lines"/></feMerge></filter></defs>';
  document.body.append(edgeDefinitions);
  scene.insertBefore(contour, scene.querySelector('.world-shade'));
  const baseArt = scene.querySelector('.world-art');
  function syncContour() { contour.src = baseArt.getAttribute('src'); }
  new MutationObserver(syncContour).observe(baseArt, {attributes:true, attributeFilter:['src']});
  syncContour();
  let outlineTimer;
  function updateThinkingOutline() {
    document.body.classList.toggle('character-thinking', thinking.checked && !document.hidden);
  }
  function flashCharacter() {
    clearTimeout(outlineTimer);
    document.body.classList.remove('character-switching');
    // Restart the pulse even when model changes arrive before it finishes.
    void contour.offsetWidth;
    document.body.classList.add('character-switching');
    outlineTimer = setTimeout(() => document.body.classList.remove('character-switching'), 2600);
  }
  thinking.addEventListener('change', updateThinkingOutline);
  document.addEventListener('visibilitychange', updateThinkingOutline);
  window.addEventListener('pageshow', updateThinkingOutline);
  window.addEventListener('pagehide', () => {
    clearTimeout(outlineTimer); document.body.classList.remove('character-switching', 'character-thinking');
  });
  updateThinkingOutline();
  const transition = document.createElement('div');
  transition.className = 'model-switch-notice';
  transition.hidden = true;
  transition.setAttribute('role', 'status');
  transition.setAttribute('aria-live', 'polite');
  const emblem = document.createElement('span');
  emblem.className = 'model-switch-emblem';
  emblem.setAttribute('aria-hidden', 'true');
  const copy = document.createElement('div');
  copy.className = 'model-switch-copy';
  const label = document.createElement('span');
  label.className = 'model-switch-label';
  label.textContent = '模型已切换';
  const name = document.createElement('strong');
  const hint = document.createElement('span');
  hint.className = 'model-switch-hint';
  hint.textContent = '下一条消息将使用此模型';
  copy.append(label, name, hint); transition.append(emblem, copy);
  document.body.append(transition);
  let switchTimer, frame, previousModel = model.value;
  function dismissSwitch() {
    clearTimeout(switchTimer); cancelAnimationFrame(frame);
    transition.hidden = true; transition.classList.remove('model-switch-enter');
  }
  model.addEventListener('focus', () => { previousModel = model.value; });
  model.addEventListener('change', () => {
    if (!model.value || model.value === previousModel) return;
    previousModel = model.value;
    flashCharacter();
    dismissSwitch();
    name.textContent = model.selectedOptions[0]?.textContent || model.value;
    transition.hidden = false;
    frame = requestAnimationFrame(() => transition.classList.add('model-switch-enter'));
    switchTimer = setTimeout(dismissSwitch, 2600);
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') dismissSwitch(); });
  window.addEventListener('pagehide', dismissSwitch);
  document.addEventListener('visibilitychange', () => { if (document.hidden) dismissSwitch(); });
  document.body.classList.toggle('suite-all-effects', Boolean(config.effects));
  let activateAll = false;
  try {
    activateAll = localStorage.getItem('guarded-all-effects-v1') !== 'on';
    if (activateAll) localStorage.setItem('guarded-all-effects-v1', 'on');
  } catch { activateAll = true; }
  if (activateAll) fullEffects();
  const fullWorkbenchButton = document.createElement('button');
  fullWorkbenchButton.type = 'button'; fullWorkbenchButton.className = 'harness-launch-button';
  fullWorkbenchButton.textContent = '✦ 完整工作台';
  document.querySelector('.sidebar-bottom').prepend(fullWorkbenchButton);
  const workbench = document.createElement('section');
  workbench.className = 'harness-workbench'; workbench.hidden = true;
  workbench.setAttribute('aria-label', 'Guarded 完整工作台');
  const toolbar = document.createElement('div'); toolbar.className = 'harness-toolbar';
  const back = document.createElement('button'); back.type = 'button'; back.textContent = '← 返回聊天';
  const titleText = document.createElement('strong'); titleText.textContent = 'Guarded · 完整工作台';
  const runtimeState = document.createElement('span'); runtimeState.className = 'harness-runtime-state';
  const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = '重新连接';
  toolbar.append(back, titleText, runtimeState, retry);
  const message = document.createElement('div'); message.className = 'harness-loading'; message.setAttribute('role','status');
  const frameView = document.createElement('iframe'); frameView.title = 'Guarded 完整工作台';
  frameView.setAttribute('allow','clipboard-write; microphone; fullscreen'); frameView.hidden = true;
  workbench.append(toolbar, message, frameView); document.body.append(workbench);
  let connecting = false;
  let lastNativeAnimationPayload = '';
  window.addEventListener('message', event => {
    if (event.origin !== 'http://127.0.0.1:8775' || event.source !== frameView.contentWindow ||
        event.data?.type !== 'guarded-native-animation-content') return;
    const value = event.data.payload;
    if (!value || typeof value.title !== 'string' || typeof value.source !== 'string' ||
        !Array.isArray(value.lines) || value.lines.length > 48) return;
    const payload = {title:value.title.slice(0,100), source:value.source.slice(0,100),
      lines:value.lines.filter(line => typeof line === 'string').slice(-48).map(line => line.slice(0,240))};
    const serialized = JSON.stringify(payload);
    if (serialized === lastNativeAnimationPayload) return;
    lastNativeAnimationPayload = serialized;
    api('/api/native-animation-content', payload).catch(() => {});
  });
  let frameTimer;
  const backgroundState = document.createElement('span');
  backgroundState.textContent = '背景加载中';
  backgroundState.style.cssText = 'font-size:12px;color:#a9c6df;white-space:nowrap';
  toolbar.insertBefore(backgroundState, retry);
  window.addEventListener('message', event => {
    if (event.origin !== 'http://127.0.0.1:8775' || event.source !== frameView.contentWindow ||
        event.data?.type !== 'guarded-workbench-background-status') return;
    const state = event.data;
    if (typeof state.ready !== 'boolean' || !Number.isFinite(state.fps)) return;
    backgroundState.textContent = state.ready ? `背景 ${Math.round(state.fps)} FPS` : '背景加载中';
    backgroundState.title = state.error || `工作台内背景 · ${state.width}×${state.height} · ${state.frames} 帧 · ${state.lines} 条实时记录`;
  });
  async function openFullWorkbench() {
    if (connecting) return;
    if (!document.getElementById('panelBackdrop').hidden) document.getElementById('panelBackdrop').click();
    workbench.hidden = false;
    if (frameView.src && !frameView.hidden) return;
    connecting = true; retry.disabled = true; workbench.classList.remove('guarded-frame-ready');
    message.hidden = false; message.textContent = '正在连接完整工作台…'; runtimeState.textContent = '连接中';
    clearTimeout(frameTimer);
    try {
      backgroundState.textContent = '背景加载中';
      const result = await api('/api/harness/open', {});
      const url = new URL(result.url);
      if (url.origin !== 'http://127.0.0.1:8775') throw new Error('工作台地址无效');
      // The original animation is rendered inside the workbench beneath its UI.
      frameView.onload = () => { clearTimeout(frameTimer); workbench.classList.add('guarded-frame-ready'); message.hidden = true; runtimeState.textContent = '已连接'; };
      frameTimer = setTimeout(() => {
        frameView.onload = null;
        frameView.removeAttribute('src'); frameView.hidden = true;
        runtimeState.textContent = '连接超时';
        message.hidden = false; message.textContent = '工作台加载超时，请点击重新连接';
      }, 30000);
      lastNativeAnimationPayload = ''; frameView.src = url.href; frameView.hidden = false;
    } catch (error) {
      runtimeState.textContent = '连接失败'; message.textContent = error.name === 'AbortError' ? '连接超时，请点击重新连接' : error.message;
    } finally { connecting = false; retry.disabled = false; }
  }
  fullWorkbenchButton.addEventListener('click', openFullWorkbench);
  back.addEventListener('click', () => { workbench.hidden = true; fullWorkbenchButton.focus(); });
  retry.addEventListener('click', () => { frameView.removeAttribute('src'); frameView.hidden = true; openFullWorkbench(); });
  let checkingRuntime = false;
  setInterval(async () => {
    if (workbench.hidden || connecting || checkingRuntime || document.hidden) return;
    checkingRuntime = true;
    try {
      const response = await fetch('/api/harness', {headers:{'X-Guarded-Token':document.querySelector('meta[name="guarded-token"]').content}});
      if (!response.ok) return;
      const state = await response.json();
      if (!state.running) {
        runtimeState.textContent = '正在恢复连接';
        frameView.removeAttribute('src'); frameView.hidden = true;
        await openFullWorkbench();
      }
    } catch (error) { runtimeState.textContent = '连接中断，请重新连接'; }
    finally { checkingRuntime = false; }
  }, 10000);
  applyWallpaper(); controls();
  startupPending = Boolean(config.enabled);
  startupWhenVisible();
})();

/* Motion study: april-zhu.com rolling labels, adapted for Guarded controls.
   Original implementation; keeps button names, focus and click listeners intact. */
(() => {
  const controls = document.querySelectorAll('.harness-launch-button, .harness-toolbar button, #replayIntro');
  for (const button of controls) {
    if (button.querySelector('.guarded-roll-window') || button.children.length) continue;
    const label = button.textContent.trim();
    if (!label) continue;
    button.setAttribute('aria-label', label);
    const window = document.createElement('span'); window.className = 'guarded-roll-window'; window.setAttribute('aria-hidden','true');
    for (let i=0;i<2;i++) {
      const line = document.createElement('span'); line.className = 'guarded-roll-line'; line.textContent = label; window.append(line);
    }
    button.replaceChildren(window); button.classList.add('guarded-roll-control');
  }
})();
