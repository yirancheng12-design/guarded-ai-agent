/* Original, silent startup and cover motion inspired by the user's reference. */
(() => {
  const cover = document.querySelector('.program-cover');
  const welcome = document.getElementById('welcome');
  const dialog = document.getElementById('guardedIntro');
  const title = document.getElementById('introTitle');
  const caption = document.getElementById('introCaption');
  const replay = document.getElementById('replayIntro');
  const toggle = document.getElementById('glassMotion');
  const solid = document.getElementById('glassTransparency');
  if (!cover || !dialog || !toggle) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  const transparency = matchMedia('(prefers-reduced-transparency: reduce)');
  let timers = [], busy = false, pointerFrame = 0;
  const seenKey = 'guarded-intro-seen-v1';
  const allowed = () => toggle.getAttribute('aria-checked') === 'true' &&
    solid?.getAttribute('aria-checked') !== 'true' && !reduced.matches && !contrast.matches && !transparency.matches;
  const remember = () => { try { sessionStorage.setItem(seenKey, 'yes'); } catch {} };
  function stopIntro() {
    timers.forEach(clearTimeout); timers = [];
    if (dialog.open) dialog.close();
    remember();
  }
  function update() {
    const enabled = allowed();
    document.body.classList.toggle('cover-motion-enabled', enabled);
    document.body.classList.toggle('cover-paused', !enabled || busy || document.hidden || welcome.hidden);
    document.body.classList.toggle('world-paused', !enabled || busy || document.hidden);
    // The boot-suite adapter owns replay state; cover motion may pause
    // during tasks without disabling or cancelling startup playback.
    if ((!enabled || busy || document.hidden) && dialog.open) stopIntro();
  }
  function playIntro() {
    if (!allowed() || busy || document.hidden || dialog.open) return;
    dialog.className = 'guarded-intro intro-brand';
    title.textContent = 'GUARDED'; caption.textContent = '每个想法，都值得被实现。';
    dialog.showModal();
    timers.push(setTimeout(() => {
      dialog.className = 'guarded-intro intro-orbit';
      title.textContent = '展开你的工作空间'; caption.textContent = '灵感 / 探索 / 创造';
    }, 1100));
    timers.push(setTimeout(() => {
      dialog.className = 'guarded-intro intro-ready';
      title.textContent = '让想法，成为行动。'; caption.textContent = 'WELCOME TO GUARDED';
    }, 2600));
    timers.push(setTimeout(stopIntro, 4100));
  }
  // Lightweight CSS particles; no second WebGL scene or continuous JS loop.
  for (let i = 0; i < 18; i++) {
    const particle = document.createElement('i');
    particle.className = 'core-particle';
    particle.style.setProperty('--px', `${42 + (i * 17 % 57)}%`);
    particle.style.setProperty('--py', `${8 + (i * 23 % 85)}%`);
    particle.style.setProperty('--delay', `${-i * .73}s`);
    particle.style.setProperty('--duration', `${6 + i % 5}s`);
    cover.querySelector('.core-particles').append(particle);
  }
  const rain = document.querySelector('.world-rain');
  const stars = document.querySelector('.world-stars');
  if (rain && stars) {
    for (let i = 0; i < 22; i++) {
      const streak = document.createElement('i');
      streak.style.setProperty('--lane', `${(i * 41 + 7) % 100}%`);
      streak.style.setProperty('--delay', `${-i * 1.37}s`);
      streak.style.setProperty('--speed', `${7 + i % 7}s`);
      streak.style.setProperty('--length', `${25 + i * 11 % 90}px`);
      rain.append(streak);
      const star = document.createElement('i');
      star.style.setProperty('--px', `${(i * 29 + 5) % 100}%`);
      star.style.setProperty('--py', `${(i * 43 + 9) % 100}%`);
      star.style.setProperty('--delay', `${-i * .71}s`);
      star.style.setProperty('--duration', `${8 + i % 5}s`);
      star.className = 'core-particle'; stars.append(star);
    }
  }
  cover.addEventListener('pointermove', event => {
    if (!allowed() || busy || welcome.hidden || document.hidden || event.pointerType === 'touch') return;
    const bounds = cover.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width - .5;
    const y = (event.clientY - bounds.top) / bounds.height - .5;
    cancelAnimationFrame(pointerFrame);
    pointerFrame = requestAnimationFrame(() => {
      cover.style.setProperty('--core-x', `${x * 8}px`);
      cover.style.setProperty('--core-y', `${y * 6}px`);
      cover.style.setProperty('--core-light', `${(x + .5) * 100}%`);
    });
  }, {passive:true});
  cover.addEventListener('pointerleave', () => {
    cancelAnimationFrame(pointerFrame);
    cover.style.setProperty('--core-x', '0px'); cover.style.setProperty('--core-y', '0px');
  });
  document.getElementById('skipIntro').addEventListener('click', stopIntro);
  dialog.addEventListener('cancel', event => { event.preventDefault(); stopIntro(); });
  // Startup playback is owned by boot-suite-adapter.js.
  new MutationObserver(update).observe(toggle, {attributes:true, attributeFilter:['aria-checked']});
  if (solid) new MutationObserver(update).observe(solid, {attributes:true, attributeFilter:['aria-checked']});
  new MutationObserver(update).observe(welcome, {attributes:true, attributeFilter:['hidden']});
  window.addEventListener('guarded-activity', event => { busy = Boolean(event.detail); update(); });
  document.addEventListener('visibilitychange', update);
  [reduced, contrast, transparency].forEach(query => query.addEventListener('change', update));
  window.addEventListener('pagehide', () => { stopIntro(); cancelAnimationFrame(pointerFrame); });
  update();
  let seen = false;
  try { seen = sessionStorage.getItem(seenKey) === 'yes'; } catch {}
  // Keep cover particles; the suite owns startup and replay.
})();
/* Smooth UI interactions inspired by React Bits Magnet / Tilted Card and
   VibeHub magnet, tilt, ripple, stagger, drawer and disclosure patterns. */
(() => {
  'use strict';
  const root = document.documentElement;
  const toggle = document.getElementById('uiMotion');
  if (!toggle) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  const fine = matchMedia('(hover: hover) and (pointer: fine)');
  const states = new Map(), animations = new Set();
  let enabled = true, busy = false, frame = 0, lastTime = 0;
  try { enabled = localStorage.getItem('guarded-ui-motion-v1') !== 'off'; } catch {}
  const allowed = () => enabled && !reduced.matches && !contrast.matches && !document.hidden;
  function reset() {
    cancelAnimationFrame(frame); frame = 0; lastTime = 0;
    for (const [element] of states) {
      for (const property of ['--tilt-x','--tilt-y','--magnet-x','--magnet-y','--spot-x','--spot-y']) element.style.removeProperty(property);
      element.classList.remove('spot-active', 'motion-tracking');
    }
    states.clear();
    for (const animation of animations) animation.cancel();
    animations.clear();
  }
  function update() {
    root.classList.toggle('ui-motion-off', !enabled || reduced.matches || contrast.matches);
    toggle.disabled = reduced.matches || contrast.matches;
    toggle.setAttribute('aria-checked', String(enabled && !toggle.disabled));
    document.getElementById('uiMotionNote').textContent = toggle.disabled ? '已遵循系统的减少动态或高对比度设置' : '平滑过渡、卡片倾斜与按钮反馈';
    if (!allowed()) reset();
  }
  function tick(now) {
    frame = 0;
    if (!allowed() || busy || !fine.matches) { reset(); return; }
    const factor = 1 - Math.exp(-Math.min(now - (lastTime || now - 16), 40) / 65);
    lastTime = now;
    let pending = false;
    for (const [element, state] of states) {
      state.x += (state.tx - state.x) * factor;
      state.y += (state.ty - state.y) * factor;
      const dx = state.x - .5, dy = state.y - .5;
      if (state.card) {
        element.style.setProperty('--tilt-x', (-dy * 5).toFixed(3) + 'deg');
        element.style.setProperty('--tilt-y', (dx * 5).toFixed(3) + 'deg');
        element.style.setProperty('--spot-x', (state.x * state.rect.width).toFixed(1) + 'px');
        element.style.setProperty('--spot-y', (state.y * state.rect.height).toFixed(1) + 'px');
      } else {
        element.style.setProperty('--magnet-x', (dx * 6).toFixed(2) + 'px');
        element.style.setProperty('--magnet-y', (dy * 6).toFixed(2) + 'px');
      }
      const settled = Math.abs(state.tx - state.x) + Math.abs(state.ty - state.y) < .001;
      if (!settled) pending = true;
      else if (!state.hovered) {
        element.style.removeProperty('--tilt-x'); element.style.removeProperty('--tilt-y');
        element.style.removeProperty('--magnet-x'); element.style.removeProperty('--magnet-y');
        element.classList.remove('motion-tracking'); states.delete(element);
      }
    }
    if (pending) frame = requestAnimationFrame(tick);
    else lastTime = 0;
  }
  function start() { if (!frame) frame = requestAnimationFrame(tick); }
  const targets = document.querySelectorAll('.suggestions > button, #send, #newChat');
  for (const element of targets) {
    const card = element.matches('.suggestions > button');
    element.classList.add(card ? 'motion-card' : 'motion-magnet');
    element.addEventListener('pointermove', event => {
      if (!allowed() || busy || !fine.matches || element.disabled) return;
      let state = states.get(element);
      if (!state) {
        state = {card, rect:element.getBoundingClientRect(), x:.5, y:.5};
        states.set(element, state);
      }
      state.hovered = true;
      state.tx = Math.max(0, Math.min(1, (event.clientX - state.rect.left) / state.rect.width));
      state.ty = Math.max(0, Math.min(1, (event.clientY - state.rect.top) / state.rect.height));
      element.classList.add('motion-tracking');
      if (card) element.classList.add('spot-active');
      start();
    }, {passive:true});
    element.addEventListener('pointerleave', () => {
      const state = states.get(element);
      element.classList.remove('spot-active');
      if (state) { state.tx = state.ty = .5; state.hovered = false; start(); }
    }, {passive:true});
    function ripple(x, y) {
      if (!allowed() || busy || element.disabled || !element.animate) return;
      // One transient element per target; rapid repeated clicks remain bounded.
      element.querySelector('.motion-ripple')?.remove();
      const rect = element.getBoundingClientRect(), size = Math.hypot(rect.width, rect.height) * 2;
      const ring = document.createElement('span');
      ring.className = 'motion-ripple'; ring.setAttribute('aria-hidden', 'true');
      Object.assign(ring.style, {width:size+'px',height:size+'px',left:x-size/2+'px',top:y-size/2+'px'});
      element.append(ring);
      const animation = ring.animate([{transform:'scale(0)',opacity:.32},{transform:'scale(1)',opacity:0}],{duration:520,easing:'cubic-bezier(.16,1,.3,1)'});
      animations.add(animation);
      animation.finished.catch(() => {}).finally(() => { ring.remove(); animations.delete(animation); });
    }
    element.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      const rect = element.getBoundingClientRect(); ripple(event.clientX-rect.left,event.clientY-rect.top);
    }, {passive:true});
    element.addEventListener('keydown', event => {
      if (!event.repeat && (event.key === 'Enter' || event.key === ' ')) {
        const rect = element.getBoundingClientRect(); ripple(rect.width/2,rect.height/2);
      }
    });
  }
  // Animate user disclosure gestures, not details restored by streamed updates.
  const disclosures = new WeakSet();
  document.addEventListener('click', event => {
    const summary = event.target instanceof Element ? event.target.closest('summary') : null;
    if (summary?.parentElement instanceof HTMLDetailsElement) disclosures.add(summary.parentElement);
  }, true);
  document.addEventListener('toggle', event => {
    const item = event.target;
    const requested = disclosures.has(item);
    disclosures.delete(item);
    if (!requested || !allowed() || !(item instanceof HTMLDetailsElement) || !item.open || !item.closest('.app')) return;
    for (const child of item.children) {
      if (child.tagName === 'SUMMARY' || !child.animate) continue;
      const animation = child.animate([{opacity:0,transform:'translateY(-5px)'},{opacity:1,transform:'translateY(0)'}],{duration:200,easing:'cubic-bezier(.2,.8,.2,1)'});
      animations.add(animation);
      animation.finished.catch(() => {}).finally(() => animations.delete(animation));
    }
  },true);
  toggle.addEventListener('click', () => {
    enabled = !enabled;
    try { localStorage.setItem('guarded-ui-motion-v1',enabled?'on':'off'); } catch {}
    update();
  });
  window.addEventListener('guarded-activity', event => { busy = !!event.detail; if (busy) reset(); });
  document.addEventListener('visibilitychange', () => { reset(); update(); });
  window.addEventListener('resize', reset, {passive:true});
  document.getElementById('scrollArea')?.addEventListener('scroll', reset, {passive:true});
  window.addEventListener('pagehide', reset);
  reduced.addEventListener('change', update); contrast.addEventListener('change', update);
  fine.addEventListener('change', reset);
  update();
})();
