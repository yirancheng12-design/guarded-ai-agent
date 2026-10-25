'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="guarded-token"]').content;
let current = localStorage.getItem('guarded-conversation') || '';
let active = null, profiles = [], windows = [], polling = false, sending = false;
let messageSignature = '', conversationSignature = '', profileSignature = '', toastTimer;
let workspaceDirty = false;
let attachments = [];
let chosenPreview = null;
let previewReturnFocus = null;
let settingsReturnFocus = null;
let hostSettings = {};
const compactLayout = window.matchMedia('(max-width:820px)');
const modeNames = {assistant:'综合助手', work:'综合助手', chat:'纯聊天', app:'打开应用', demo:'计算器演示', desktop:'桌面任务'};
const statuses = {success:'已验证完成', completed:'本轮已结束', incomplete:'还有未完成步骤', unknown:'需要核对', failed:'未完成', error:'请求失败', cancelled:'已停止', interrupted:'请求已中断', unsupported:'超出当前范围'};

async function api(path, data) {
  const response = await fetch(path, {method:data === undefined ? 'GET' : 'POST',
    headers:{'X-Guarded-Token':token, ...(data === undefined ? {} : {'Content-Type':'application/json'})},
    ...(data === undefined ? {} : {body:JSON.stringify(data)})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}
function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6500);
}
function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
function syncPanels() {
  const settingsOpen = !$('settings').hidden;
  const sidebarOpen = compactLayout.matches && $('sidebar').classList.contains('open');
  const previewOpen = !$('fileDialog').hidden;
  $('panelBackdrop').hidden = !settingsOpen && !sidebarOpen;
  document.querySelector('main').inert = settingsOpen || sidebarOpen || previewOpen;
  $('sidebar').inert = settingsOpen || previewOpen;
  $('settings').inert = previewOpen;
  $('menuButton').setAttribute('aria-expanded', String(sidebarOpen));
  $('menuButton').setAttribute('aria-label', sidebarOpen ? '收起侧栏' : '展开侧栏');
}
function showSettings(show = true) {
  if (show && $('settings').hidden) settingsReturnFocus = document.activeElement;
  $('settings').hidden = !show;
  if (show) { $('sidebar').classList.remove('open'); loadProjects(); }
  syncPanels();
  if (show) $('closeSettings').focus();
  else if (settingsReturnFocus?.isConnected && settingsReturnFocus.getClientRects().length) settingsReturnFocus.focus();
}
function closeSidebar() { $('sidebar').classList.remove('open'); syncPanels(); }
function setMode(mode) {
  $('mode').value = mode; $('modeBadge').textContent = modeNames[mode];
  const hints = {assistant:assistantHint(), chat:'纯聊天 · 回复不会触发桌面操作', demo:'计算器模拟演示 · 目标为输入 12，不操作真实桌面', desktop:'桌面任务 · 发送后有 5 秒准备时间，请切换到目标窗口'};
  $('modeHint').textContent = hints[mode]; $('modeHint').classList.toggle('task', mode !== 'chat');
  $('prompt').placeholder = mode === 'assistant' ? '给我一个想法，或交给我一件事…' : mode === 'chat' ? '向 Guarded 发送消息…' : mode === 'demo' ? '例如：先按 1，再按 2，让计算器显示 12' : '描述所选配置范围内要完成的任务…';
  if (mode === 'desktop') { $('settings').querySelector('.advanced-settings').open = true; showSettings(); }
}
function assistantHint() {
  return (hostSettings.auto_context ? '自动读取本地项目 · ' : '') + (hostSettings.auto_execute ? '普通步骤自动执行' : '操作前核对具体内容');
}
function eventText(event) {
  if (event.kind === 'model_retry') return '模型暂未生成有效答复，正在自动恢复（' + event.attempt + '/2）';
  if (event.tool === 'read_files') return event.kind === 'tool_started' ? '正在批量读取相关文件' : '批量读取文件：' + (event.outcome === 'success' ? '已完成' : '部分文件读取失败');
  if (event.kind === 'auto_approved') return (event.title || '普通步骤') + ' · 自动执行';
  if (event.tool === 'discover_projects') return event.kind === 'tool_started' ? '正在读取本地项目与任务记录' : '本地项目背景已读取';
  const toolNames = {update_plan:'更新任务计划', task_status:'读取任务记录', search_files:'搜索项目', edit_file:'修改代码或文本', read_document:'读取文档', create_document:'创建文档', describe_image:'分析图片', inspect_screen:'分析窗口截图', browser_open:'打开浏览器页面', browser_tabs:'查看浏览器标签', browser_read:'读取浏览器页面', browser_action:'操作网页', list_files:'查看文件', read_file:'读取文件', write_file:'保存文件', move_file:'移动文件', run_command:'运行命令', search_web:'搜索网页', fetch_url:'读取网页', list_apps:'查看应用', open_app:'打开应用', list_windows:'查看窗口', inspect_window:'读取控件', ui_action:'操作软件'};
  if (event.kind === 'plan_updated') return '任务计划已更新';
  if (event.kind === 'work_planning') return `正在规划第 ${event.step} 轮`;
  if (event.kind === 'tool_started') return (toolNames[event.tool] || event.tool) + (event.input ? '：' + event.input : '');
  if (event.kind === 'tool_result') return `${toolNames[event.tool] || event.tool} · ${event.outcome === 'success' ? '已完成' : statuses[event.outcome] || event.outcome}`;
  if (event.kind === 'approval_decision') return `${event.title} · ${event.allowed ? '已允许' : '已取消'}`;
  if (event.kind === 'opening_app') return `正在打开${event.app}，等待窗口验证`;
  if (event.kind === 'system_permission') return `${event.app}需要 Windows 权限确认，请在系统对话框中处理；不想启动时请选择取消。`;
  if (event.kind === 'chat_started') return '正在连接本地模型';
  if (event.kind === 'countdown') return '5 秒准备时间：请切换到目标窗口';
  if (event.kind === 'activated') return '已连接任务环境';
  if (event.kind === 'planning') return `正在规划第 ${event.step} 步`;
  if (event.kind === 'decision') return event.decision === 'act' ? `选择动作：${event.action_id}` : '正在核对任务状态';
  if (event.kind === 'executing') return `执行并验证：${event.action_id}`;
  if (event.kind === 'action_result') return `${event.action_id} · ${statuses[event.result.outcome] || event.result.outcome}`;
  if (event.kind === 'goal_verified') return '最终目标已经独立验证';
  return event.kind;
}
function renderContent(container, text) {
  // Model output is always text. Never insert model HTML or execute links/code.
  const parts = text.split(/```/g);
  parts.forEach((part, i) => {
    if (i % 2) container.append(node('pre', '', part.replace(/^[\w#+.-]*\n/, '')));
    else if (part) {
      const span = node('span');
      for (const piece of part.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g)) {
        if (piece.startsWith('`') && piece.endsWith('`')) span.append(node('code', '', piece.slice(1,-1)));
        else if (piece.startsWith('**') && piece.endsWith('**')) span.append(node('strong', '', piece.slice(2,-2)));
        else {
          const match = piece.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
          if (match) {
            try { const url = new URL(match[2]);
              if (url.username || url.password) throw new Error('credential URL');
              const link = node('a', '', match[1]); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; span.append(link);
            } catch { span.append(document.createTextNode(piece)); }
          } else span.append(document.createTextNode(piece));
        }
      }
      container.append(span);
    }
  });
}
function renderMessages(messages) {
  const ownActive = active && active.conversation === current ? active : null;
  const signature = JSON.stringify([messages, ownActive]);
  if (signature === messageSignature) return;
  messageSignature = signature;
  const scroll = $('scrollArea');
  const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 100;
  $('welcome').hidden = messages.length > 0;
  $('messages').replaceChildren();
  for (const message of messages) {
    const live = ownActive && ownActive.id === message.id ? ownActive : null;
    const row = node('article', `message ${message.role}`);
    if (message.role === 'assistant') row.append(node('div', 'message-avatar', 'g'));
    const body = node('div', 'message-body');
    body.append(node('div', 'message-label', message.role === 'user' ? '你' : `Guarded  /  ${modeNames[message.mode]}`));
    if (statuses[message.status]) body.append(node('div', `result-badge ${message.status}`, message.mode === 'chat' && message.status === 'success' ? '已回复' : statuses[message.status]));
    const content = node('div', 'message-content');
    const text = live ? live.text : message.content;
    if (text) renderContent(content, text);
    else if (live) content.append(node('span', 'thinking', live.status === 'stopping' ? '正在停止，等待当前调用结束' : live.mode === 'chat' ? '正在思考' : '正在处理任务'));
    else if (message.status === 'running') content.append(node('span', 'thinking', '正在恢复请求状态'));
    body.append(content);
    const events = live ? live.events : message.detail?.events || [];
    const plan = [...events].reverse().find(event => event.kind === 'plan_updated')?.steps || message.detail?.result?.plan;
    if (plan?.length) {
      const planBox = node('div','task-plan'); planBox.append(node('strong','','任务计划'));
      const marks = {pending:'○', in_progress:'◉', completed:'✓'};
      for (const step of plan) planBox.append(node('div', 'plan-step ' + step.status, `${marks[step.status] || '○'}  ${step.title}`));
      body.append(planBox);
    }
    if (message.mode !== 'chat' && events.length) {
      const eventBox = node('details', 'task-events');
      eventBox.open = !!live;
      eventBox.append(node('summary', '', live ? '正在执行 · 操作过程' : '查看操作过程'));
      const eventList = node('div', 'task-event-list');
      for (const event of events.slice(-6)) eventList.append(node('div', 'task-event', eventText(event)));
      eventBox.append(eventList);
      body.append(eventBox);
    }
    if (live?.approval) {
      const approval = live.approval;
      const card = node('div', 'approval-card');
      card.append(node('strong', '', '等待你核对：' + approval.title));
      card.append(node('pre', '', JSON.stringify(approval.details, null, 2)));
      const buttons = node('div', 'approval-buttons');
      for (const [label, allowed] of [['允许这次操作', true], ['取消', false]]) {
        const button = node('button', allowed ? 'approve-button' : 'secondary-button', label);
        button.addEventListener('click', async () => {
          buttons.querySelectorAll('button').forEach(item => item.disabled = true);
          try { await api('/api/approval', {id:live.id, approval_id:approval.id, allowed}); await refresh(); }
          catch(error) { toast(error.message); await refresh(); }
        });
        buttons.append(button);
      }
      card.append(buttons); body.append(card);
    }
    if (message.detail?.result?.artifacts?.length) {
      const files = node('div', 'artifact-list'); files.append(node('strong', '', '已保存的文件'));
      for (const item of message.detail.result.artifacts) {
        const line = node('div','artifact-row'); line.append(node('span','',item.absolute_path || item.path));
        const view = node('button','text-button','查看内容');
        view.addEventListener('click', () => previewFile(item.path)); line.append(view); files.append(line);
      }
      body.append(files);
    }
    if (!live && message.role === 'assistant' && message.detail?.result && message.mode !== 'chat') {
      const perf = message.detail.result.performance;
      if (perf && Number.isFinite(perf.total_seconds) && Number.isInteger(perf.model_calls)) {
        body.append(node('div', 'mode-hint task', '耗时 ' + perf.total_seconds.toFixed(1) + ' 秒 · ' + perf.model_calls + ' 轮模型调用'));
      }
      const detail = node('details'); detail.append(node('summary', '', '查看验证记录'));
      detail.append(node('pre', '', JSON.stringify(message.detail.result, null, 2))); body.append(detail);
      if (['incomplete','cancelled','interrupted'].includes(message.status) ||
          message.detail.result.text === '模型没有返回回复或工具请求。') {
        const resume = node('button','secondary-button','继续未完成部分');
        resume.addEventListener('click', () => { setMode('assistant'); $('prompt').value = '先读取本对话的任务记录，从尚未完成的步骤继续。重新检查当前状态；不要重发结果未知的动作。'; $('prompt').focus(); });
        body.append(resume);
      }
    }
    row.append(body); $('messages').append(row);
  }
  if (!messages.length) scroll.scrollTop = 0;
  else if (nearBottom || messages.length <= 2) requestAnimationFrame(() => scroll.scrollTop = scroll.scrollHeight);
}
function renderState(state) {
  active = state.active; profiles = state.profiles;
  hostSettings = state.settings || {};
  if ($('mode').value === 'assistant') $('modeHint').textContent = assistantHint();
  window.dispatchEvent(new CustomEvent('guarded-activity', {detail:!!active}));
  if (state.settings && !workspaceDirty && document.activeElement !== $('workspace')) $('workspace').value = state.settings.workspace;
  $('workspaceBadge').textContent = '项目：' + (state.settings?.workspace?.split(/[\\/]/).pop() || '工作区');
  $('workspaceBadge').title = state.settings?.workspace || '';
  const cSignature = JSON.stringify([state.conversations, current]);
  if (cSignature !== conversationSignature) {
    conversationSignature = cSignature; $('conversations').replaceChildren();
    if (!state.conversations.length) $('conversations').append(node('div','empty-history','你的对话会显示在这里'));
    for (const conversation of state.conversations) {
      const button = node('button', `conversation-item${conversation.id === current ? ' active' : ''}`, conversation.title);
      button.title = conversation.title; button.addEventListener('click', () => selectConversation(conversation.id));
      $('conversations').append(button);
    }
  }
  $('conversationTitle').textContent = state.conversations.find(c => c.id === current)?.title || '新的对话';
  const pSignature = JSON.stringify(profiles);
  if (pSignature !== profileSignature) {
    profileSignature = pSignature;
    const previous = $('profile').value || localStorage.getItem('guarded-profile');
    $('profile').replaceChildren(new Option(profiles.length ? '选择任务配置' : '尚未导入任务', ''));
    for (const profile of profiles) $('profile').add(new Option(profile.name, profile.id));
    if (profiles.some(p => p.id === previous)) $('profile').value = previous;
    updateProfile();
  }
  $('send').disabled = !!active || sending || !$('model').value;
  $('stop').hidden = !active;
  $('stop').disabled = active?.status === 'stopping';
  $('stop').textContent = active?.status === 'stopping' ? '正在停止…' : active?.mode === 'chat' ? '■ 停止生成' : '■ 停止后续动作';
  for (const id of ['mode','model','profile','importProfile','refreshWindows','bindWindow','inspectWindow','saveWorkspace','attachFile','useProject']) $(id).disabled = !!active;
  $('restoreChange').disabled = !!active || !chosenPreview?.can_restore;
  $('bottomHint').textContent = active ? (active.status === 'needs_approval' ? '请在对话中核对本次操作，可以允许或取消' : active.mode === 'chat' ? '首次加载模型可能需要稍等 · 可以继续输入草稿' : active.mode === 'demo' ? '正在运行模拟任务，无需切换窗口' : active.mode === 'work' ? '工具进度显示在对话中 · 可以继续输入草稿' : active.mode === 'app' ? '正在检查应用窗口是否显示' : '执行期间请保持目标窗口在前台') : 'Enter 发送 · Shift + Enter 换行';
  renderMessages(state.messages);
}
async function refresh() {
  if (polling) return; polling = true;
  const requestedConversation = current;
  try {
    const state = await api('/api/state' + (requestedConversation ? '?conversation=' + encodeURIComponent(requestedConversation) : ''));
    if (current === requestedConversation) renderState(state);
  } catch (error) {
    if (error.message === '对话不存在') { current = ''; localStorage.removeItem('guarded-conversation'); }
    else { $('connectionText').textContent = '服务未连接'; $('connectionDot').className = 'status-dot offline'; }
  } finally { polling = false; }
}
async function selectConversation(id) {
  attachments = []; $('attachments').replaceChildren(); workspaceDirty = false; closePreview();
  current = id; localStorage.setItem('guarded-conversation', id); messageSignature = '';
  closeSidebar(); await refresh();
  $('scrollArea').scrollTop = $('messages').children.length ? $('scrollArea').scrollHeight : 0;
}
async function newConversation(workspace) {
  try { const result = await api('/api/conversations', typeof workspace === 'string' ? {workspace} : {}); await selectConversation(result.id); if (!active) setMode('assistant'); $('prompt').focus(); return true; }
  catch(error) { toast(error.message); return false; }
}
async function loadModels() {
  $('connectionDot').className = 'status-dot loading'; $('connectionText').textContent = '连接 Ollama…';
  try {
    const result = await api('/api/models');
    const chosen = $('model').value || localStorage.getItem('guarded-model') || 'gemma4:26b';
    $('model').replaceChildren();
    for (const name of result.models) $('model').add(new Option(name, name));
    if (result.models.includes(chosen)) $('model').value = chosen;
    if (!result.models.length) { $('model').add(new Option('没有可用模型', '')); throw new Error('Ollama 中没有模型，请先安装模型'); }
    $('connectionDot').className = 'status-dot'; $('connectionText').textContent = 'Ollama 已连接';
    $('send').disabled = !!active;
  } catch(error) { $('connectionDot').className = 'status-dot offline'; $('connectionText').textContent = '重新连接 Ollama'; toast('无法连接模型：' + error.message); }
}
function updateProfile() {
  const profile = profiles.find(p => p.id === $('profile').value);
  if (!profile) { $('profileSummary').textContent = '聊天和计算器演示无需配置。'; return; }
  const data = profile.data;
  $('profileSummary').textContent = `允许动作：${Object.keys(data.actions).join('、')}\n目标：${data.goal.property} = ${JSON.stringify(data.goal.expected)}\n窗口：${data.window.hwnd || '尚未绑定'}`;
  localStorage.setItem('guarded-profile', profile.id);
}
$('composer').addEventListener('submit', async event => {
  event.preventDefault(); if (active || sending || !$('prompt').value.trim()) return;
  if (!$('model').value) return toast('请先连接 Ollama 并选择模型');
  if ($('mode').value === 'desktop' && !$('profile').value) { showSettings(); return toast('请先导入并选择任务配置'); }
  sending = true; $('send').disabled = true;
  try {
    if (!current) { const result = await api('/api/conversations', {}); current = result.id; localStorage.setItem('guarded-conversation', current); }
    const attachmentText = attachments.length ? '\n\n我添加的工作区文件（请按任务需要读取）：\n' + attachments.map(file => file.path).join('\n') : '';
    await api('/api/send', {conversation:current, text:$('prompt').value + attachmentText, model:$('model').value, mode:$('mode').value, profile_id:$('profile').value});
    attachments = []; $('attachments').replaceChildren();
    $('prompt').value = ''; $('prompt').style.height = '';
    if ($('mode').value === 'desktop') showSettings(false);
    await refresh(); $('scrollArea').scrollTop = $('scrollArea').scrollHeight;
  } catch(error) { toast(error.message); }
  finally { sending = false; $('send').disabled = !!active || !$('model').value; }
});
$('prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); $('composer').requestSubmit(); }
});
$('prompt').addEventListener('input', () => {
  $('prompt').style.height = 'auto'; $('prompt').style.height = Math.min(160, $('prompt').scrollHeight) + 'px';
  if (!active && $('mode').value === 'demo' && /^(?:请\s*)?(?:(?:帮我|替我|给我)\s*)?(?:打开|启动|运行|open\s|launch\s|start\s)/i.test($('prompt').value.trim())) {
    setMode('assistant'); toast('已切换到助手：明确的打开应用请求会启动本机程序。');
  }
});
$('newChat').addEventListener('click', newConversation);
$('attachFile').addEventListener('click', () => $('attachmentFile').click());
$('attachmentFile').addEventListener('change', async () => {
  const files = [...$('attachmentFile').files];
  if (!files.length) return;
  $('attachFile').disabled = true;
  try {
    if (files.length + attachments.length > 5) throw new Error('每条消息最多添加 5 个文件');
    if (!current) await newConversation();
    if (!current) throw new Error('请先创建对话');
    const uploadConversation = current;
    for (const file of files) {
      if (file.size > 20000000) throw new Error('每个文件最多 20 MB');
      const encoded = await new Promise((resolve,reject) => { const reader = new FileReader(); reader.onerror = reject; reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.readAsDataURL(file); });
      const saved = await api('/api/import-file', {conversation:uploadConversation, name:file.name, data:encoded});
      if (current !== uploadConversation) throw new Error('已切换对话；文件保留在原项目，请在原对话使用。');
      attachments.push(saved);
      $('attachments').append(node('span','attachment-chip',saved.name));
    }
    setMode('assistant'); toast('文件已加入工作区，请输入要完成的任务');
  } catch(error) { toast(error.message); }
  finally { $('attachmentFile').value = ''; $('attachFile').disabled = !!active; }
});
$('showSettings').addEventListener('click', () => showSettings($('settings').hidden));
$('saveWorkspace').addEventListener('click', async () => {
  try { if (await newConversation($('workspace').value)) { workspaceDirty = false; showSettings(false); } }
  catch(error) { toast(error.message); }
});
$('workspace').addEventListener('input', () => workspaceDirty = true);
$('openWorkspace').addEventListener('click', async () => {
  try { await api('/api/open-workspace', {conversation:current || null}); } catch(error) { toast(error.message); }
});
async function loadProjects() {
  try {
    const result = await api('/api/projects');
    const selected = $('projectLibrary').value;
    $('projectLibrary').replaceChildren(new Option('选择已整理的项目…',''));
    for (const project of result.projects) $('projectLibrary').add(new Option(project.category.replace(/^\d+_/,'') + ' / ' + project.name, project.workspace));
    $('projectLibrary').value = selected;
    $('projectPath').textContent = $('projectLibrary').value;
  } catch(error) { toast(error.message); }
}
$('projectLibrary').addEventListener('change', () => $('projectPath').textContent = $('projectLibrary').value);
$('useProject').addEventListener('click', async () => {
  if (!$('projectLibrary').value) return toast('先选择一个项目');
  if (await newConversation($('projectLibrary').value)) showSettings(false);
});
$('workspaceBadge').addEventListener('click', () => showSettings());
function closePreview() { const wasOpen = !$('fileDialog').hidden; $('fileDialog').hidden = true; chosenPreview = null; syncPanels(); if (wasOpen && previewReturnFocus?.isConnected) previewReturnFocus.focus(); }
function openPreview(title) {
  if ($('fileDialog').hidden) previewReturnFocus = document.activeElement;
  chosenPreview = null; $('fileDialog').hidden = false; $('fileTitle').textContent = title;
  $('fileContent').textContent = ''; $('fileNotice').textContent = ''; $('changeList').replaceChildren();
  $('restoreChange').hidden = true; $('restoreConfirm').hidden = true; $('fileContent').hidden = false;
  syncPanels();
  $('closeFileDialog').focus();
}
async function previewFile(path) {
  const conversation = current; openPreview(path);
  try {
    const result = await api('/api/file-preview', {conversation, path});
    if (current !== conversation) return;
    $('fileContent').textContent = result.text;
    $('fileNotice').textContent = result.absolute_path + (result.truncated ? ' · 这里只显示部分内容' : '');
  } catch(error) { $('fileNotice').textContent = error.message; }
}
async function showChanges() {
  if (!current) return toast('先开始一个对话');
  const conversation = current; openPreview('本对话的文件修改'); $('fileContent').hidden = true;
  try {
    const result = await api('/api/changes?conversation=' + encodeURIComponent(conversation));
    if (current !== conversation) return;
    $('fileNotice').textContent = result.changes.length ? '只列出文件工具记录的文本修改；命令和外部软件的改动不包含在此列表。' : '本对话暂无新版文件修改记录。';
    for (const change of result.changes) {
      const button = node('button','change-item', `${change.kind === 'created' ? '新建' : '修改'} · ${change.path}  ·  ${new Date(change.created*1000).toLocaleString()}`);
      button.addEventListener('click', () => previewChange(change.id)); $('changeList').append(button);
    }
  } catch(error) { $('fileNotice').textContent = error.message; }
}
async function previewChange(id) {
  const conversation = current; openPreview('修改差异');
  try {
    const result = await api('/api/change-preview', {conversation, change_id:id});
    if (current !== conversation) return;
    chosenPreview = {...result, conversation}; $('fileTitle').textContent = result.path;
    $('fileContent').textContent = result.diff; $('fileNotice').textContent = result.notice;
    $('restoreChange').hidden = !result.can_restore; $('restoreChange').disabled = !!active;
  } catch(error) { $('fileNotice').textContent = error.message; }
}
$('showChanges').addEventListener('click', showChanges);
$('backToChanges').addEventListener('click', showChanges);
$('closeFileDialog').addEventListener('click', closePreview);
$('restoreChange').addEventListener('click', () => { $('restoreConfirm').hidden = false; });
$('cancelRestore').addEventListener('click', () => $('restoreConfirm').hidden = true);
$('confirmRestore').addEventListener('click', async () => {
  if (!chosenPreview || chosenPreview.conversation !== current || active) return;
  $('confirmRestore').disabled = true;
  try {
    const result = await api('/api/restore-change', {conversation:current, change_id:chosenPreview.id, expected_sha256:chosenPreview.current_sha256});
    closePreview(); messageSignature = ''; await refresh(); toast(result.text);
  } catch(error) { $('restoreConfirm').hidden = true; toast(error.message); }
  finally { $('confirmRestore').disabled = false; }
});
document.addEventListener('keydown', event => {
  const panel = !$('fileDialog').hidden ? $('fileDialog') : !$('settings').hidden ? $('settings') : compactLayout.matches && $('sidebar').classList.contains('open') ? $('sidebar') : null;
  if (!panel) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    if (panel === $('fileDialog')) closePreview();
    else if (panel === $('settings')) showSettings(false);
    else { closeSidebar(); $('menuButton').focus(); }
    return;
  }
  if (event.key === 'Tab') {
    const focusable = [...panel.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled):not([hidden]),select:not(:disabled),summary,[tabindex="0"]')].filter(element => element.getClientRects().length && !element.closest('details:not([open]) > :not(summary)'));
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length-1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
$('closeSettings').addEventListener('click', () => showSettings(false));
$('menuButton').addEventListener('click', () => { $('sidebar').classList.toggle('open'); syncPanels(); if ($('sidebar').classList.contains('open')) $('newChat').focus(); });
$('panelBackdrop').addEventListener('click', () => { if (!$('settings').hidden) showSettings(false); else { closeSidebar(); $('menuButton').focus(); } });
compactLayout.addEventListener('change', closeSidebar);
$('mode').addEventListener('change', () => setMode($('mode').value));
$('model').addEventListener('change', () => localStorage.setItem('guarded-model', $('model').value));
$('connectionButton').addEventListener('click', loadModels);
$('profile').addEventListener('change', updateProfile);
$('stop').addEventListener('click', async () => { if (active) try { await api('/api/cancel', {id:active.id}); await refresh(); } catch(error) { toast(error.message); } });
document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => {
  setMode(button.dataset.mode); $('prompt').value = button.dataset.prompt; $('prompt').focus();
}));
$('importProfile').addEventListener('click', () => $('profileFile').click());
$('profileFile').addEventListener('change', async () => {
  const file = $('profileFile').files[0]; if (!file) return;
  try {
    if (file.size > 200000) throw new Error('配置文件不能超过 200 KB');
    const data = JSON.parse((await file.text()).replace(/^\uFEFF/, ''));
    const result = await api('/api/profiles', {name:file.name.replace(/\.json$/i,''), data});
    localStorage.setItem('guarded-profile', result.id); $('profile').value = ''; await refresh(); toast('任务配置已导入');
  } catch(error) { toast(error.message); }
  finally { $('profileFile').value = ''; }
});
$('refreshWindows').addEventListener('click', async () => {
  $('refreshWindows').disabled = true;
  try {
    windows = (await api('/api/windows', {})).windows;
    $('windowSelect').replaceChildren(new Option('选择目标窗口', ''));
    windows.forEach((window, i) => $('windowSelect').add(new Option(window.title || `窗口 ${window.window.hwnd}`, String(i))));
  } catch(error) { toast(error.message); }
  finally { $('refreshWindows').disabled = !!active; }
});
function selectedWindow() {
  const value = $('windowSelect').value;
  if (value === '' || !windows[Number(value)]) throw new Error('请先刷新并选择目标窗口');
  return windows[Number(value)].window;
}
$('bindWindow').addEventListener('click', async () => {
  try { if (!$('profile').value) throw new Error('请先选择任务配置');
    await api('/api/bind', {profile_id:$('profile').value, window:selectedWindow()}); await refresh(); toast('已绑定所选窗口');
  } catch(error) { toast(error.message); }
});
$('inspectWindow').addEventListener('click', async () => {
  try { const result = await api('/api/inspect', {hwnd:selectedWindow().hwnd});
    $('inspectOutput').textContent = JSON.stringify(result, null, 2); $('inspectDetails').hidden = false; $('inspectDetails').open = true;
  } catch(error) { toast(error.message); }
});
setMode('assistant'); refresh(); loadModels(); setInterval(refresh, 850);


// A procedural backdrop, sampled live by CSS backdrop-filter. No screenshot blur.
(() => {
  const canvas = document.getElementById('glassScene');
  const toggle = document.getElementById('glassMotion');
  const note = document.getElementById('glassMotionNote');
  if (!canvas || !toggle) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const solid = matchMedia('(prefers-reduced-transparency: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  let enabled = true;
  try { enabled = localStorage.getItem('guarded-glass-motion') !== 'off'; } catch {}
  let gl, program, buffer, uniforms, frameRequest = 0, lastPaint = 0, lastClock = 0;
  let sceneTime = 0, frames = 0, lastReport = 0, disposed = false, contextLost = false;
  let taskRunning = false, width = 0, height = 0;
  const pointer = {x:.57, y:.42, targetX:.57, targetY:.42, element:null, lastElement:null};
  const surfaces = '.composer,.suggestions>button,.settings-panel,.file-panel,.sidebar';
  const vertex = `attribute vec2 a_position;
    void main(){gl_Position=vec4(a_position,0.0,1.0);}`;
  const fragment = `precision mediump float;
    uniform vec2 u_resolution;
    uniform vec2 u_pointer;
    uniform float u_time;
    float glow(vec2 uv,vec2 center,vec2 spread){vec2 d=(uv-center)/spread;return exp(-dot(d,d)*2.0);}
    void main(){
      vec2 uv=gl_FragCoord.xy/u_resolution;
      uv.y=1.0-uv.y;
      float t=u_time;
      vec2 drift=(u_pointer-vec2(.5))*.055;
      vec2 blue=vec2(.22+.10*sin(t*.21),.58+.13*cos(t*.17))+drift;
      vec2 lilac=vec2(.75+.12*cos(t*.16),.24+.13*sin(t*.19))-drift;
      vec2 aqua=vec2(.67+.14*sin(t*.13),.86+.09*cos(t*.23))+drift;
      vec3 color=vec3(.946,.963,.986);
      color=mix(color,vec3(.59,.76,.99),glow(uv,blue,vec2(.53,.57))*.73);
      color=mix(color,vec3(.82,.70,.95),glow(uv,lilac,vec2(.45,.49))*.68);
      color=mix(color,vec3(.61,.88,.88),glow(uv,aqua,vec2(.51,.44))*.61);
      float wave=.53+.17*sin(uv.x*4.1+t*.28)+.05*sin(uv.x*8.0-t*.17);
      float ridge=exp(-pow((uv.y-wave)/.026,2.0));
      float shade=exp(-pow((uv.y-wave-.043)/.064,2.0));
      color=mix(color,vec3(.49,.68,.94),shade*.16);
      color=mix(color,vec3(.99,1.0,1.0),ridge*.49);
      float second=.82+.11*sin(uv.x*5.0-t*.22);
      color=mix(color,vec3(.97,1.0,1.0),exp(-pow((uv.y-second)/.017,2.0))*.23);
      float grain=fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5453)-.5;
      gl_FragColor=vec4(clamp(color+grain*.004,0.0,1.0),1.0);
    }`;
  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source); gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const reason = gl.getShaderInfoLog(shader); gl.deleteShader(shader); throw new Error(reason);
    }
    return shader;
  }
  function release() {
    if (!gl || contextLost) return;
    if (program) gl.deleteProgram(program);
    if (buffer) gl.deleteBuffer(buffer);
    program = buffer = null;
  }
  function setup() {
    try {
      gl = canvas.getContext('webgl', {alpha:false, antialias:false, depth:false, stencil:false, preserveDrawingBuffer:false, powerPreference:'low-power'});
      if (!gl) throw new Error('WebGL unavailable');
      let vs, fs;
      try {
        vs = compile(gl.VERTEX_SHADER, vertex); fs = compile(gl.FRAGMENT_SHADER, fragment);
        program = gl.createProgram(); gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
      } finally {
        if (vs) gl.deleteShader(vs);
        if (fs) gl.deleteShader(fs);
      }
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Shader linking failed');
      gl.useProgram(program);
      buffer=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
      gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
      const position=gl.getAttribLocation(program,'a_position');
      gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
      uniforms={resolution:gl.getUniformLocation(program,'u_resolution'),pointer:gl.getUniformLocation(program,'u_pointer'),time:gl.getUniformLocation(program,'u_time')};
      canvas.hidden=false; canvas.dataset.renderer='webgl'; resize(); return true;
    } catch {
      release(); canvas.hidden=true; canvas.dataset.renderer='css-fallback'; return false;
    }
  }
  function resize() {
    // Bound the entire canvas to 900k pixels, even on 4K/high-DPI displays.
    const scale=Math.min(devicePixelRatio || 1,1.25,Math.sqrt(900000/Math.max(1,innerWidth*innerHeight)));
    width=Math.max(1,Math.floor(innerWidth*scale)); height=Math.max(1,Math.floor(innerHeight*scale));
    if (canvas.width!==width || canvas.height!==height) { canvas.width=width; canvas.height=height; }
    canvas.dataset.resolution=width+'×'+height;
    if (gl && program && !contextLost) gl.viewport(0,0,width,height);
  }
  function shine() {
    const element=pointer.element;
    if (pointer.lastElement!==element) {
      pointer.lastElement?.style.removeProperty('--glass-hover');
      pointer.lastElement=element;
    }
    if (element && !reduced.matches && enabled) {
      const rect=element.getBoundingClientRect();
      element.style.setProperty('--glass-x',Math.round(pointer.targetX*innerWidth-rect.left)+'px');
      element.style.setProperty('--glass-y',Math.round(pointer.targetY*innerHeight-rect.top)+'px');
      element.style.setProperty('--glass-hover','.7');
    } else element?.style.removeProperty('--glass-hover');
  }
  function draw() {
    if (!gl || !program || contextLost || disposed) return;
    pointer.x+=(pointer.targetX-pointer.x)*.08; pointer.y+=(pointer.targetY-pointer.y)*.08;
    gl.uniform2f(uniforms.resolution,width,height); gl.uniform2f(uniforms.pointer,pointer.x,pointer.y);
    gl.uniform1f(uniforms.time,sceneTime); gl.drawArrays(gl.TRIANGLES,0,6);
    frames++; shine();
    // Observable render evidence, updated once per second rather than per frame.
    const now=performance.now();
    if (now-lastReport>1000 || frames===1) {
      canvas.dataset.frames=String(frames); canvas.dataset.sceneTime=sceneTime.toFixed(3);
      lastReport=now;
    }
  }
  function running() { return enabled && !reduced.matches && !solid.matches && !contrast.matches && !document.hidden && !taskRunning && !contextLost && !disposed && !!program; }
  function tick(now) {
    frameRequest=0;
    if (!running()) return;
    // At most 30 draws/second. Freeze while a model task runs or the page is hidden.
    if (now-lastPaint>=1000/30) {
      const elapsed=lastClock ? Math.min((now-lastClock)/1000,.1) : 0;
      sceneTime+=elapsed; lastClock=now; lastPaint=now; draw();
    }
    frameRequest=requestAnimationFrame(tick);
  }
  function update() {
    cancelAnimationFrame(frameRequest); frameRequest=0; lastClock=0; lastPaint=0;
    toggle.disabled=reduced.matches || solid.matches || contrast.matches || !program || contextLost;
    toggle.setAttribute('aria-checked',String(enabled && !toggle.disabled));
    note.textContent=reduced.matches || solid.matches || contrast.matches ? '已遵循系统的减少动态或透明效果设置' : !program || contextLost ? '当前环境使用静态光影，保留磨砂效果' : '任务运行时自动暂停动态光影';
    const state=running()?'running':document.hidden?'hidden':taskRunning?'task-paused':contextLost?'context-lost':!program?'fallback':'paused';
    canvas.dataset.state=state;
    document.documentElement.classList.toggle('glass-solid',solid.matches || contrast.matches);
    if (!document.hidden && !contextLost && !solid.matches && !contrast.matches) draw();
    if (running()) frameRequest=requestAnimationFrame(tick);
  }
  toggle.addEventListener('click',()=>{
    enabled=!enabled;
    try { localStorage.setItem('guarded-glass-motion',enabled?'on':'off'); } catch {}
    update();
  });
  document.addEventListener('pointermove',event=>{
    pointer.targetX=event.clientX/Math.max(innerWidth,1); pointer.targetY=event.clientY/Math.max(innerHeight,1);
    pointer.element=event.target instanceof Element ? event.target.closest(surfaces) : null;
  },{passive:true});
  document.addEventListener('pointerleave',()=>{pointer.element=null;pointer.lastElement?.style.removeProperty('--glass-hover');},{passive:true});
  document.addEventListener('visibilitychange',update);
  window.addEventListener('guarded-activity',event=>{if (taskRunning!==Boolean(event.detail)) {taskRunning=Boolean(event.detail);update();}});
  window.addEventListener('resize',()=>{resize();update();},{passive:true});
  reduced.addEventListener('change',update); solid.addEventListener('change',update); contrast.addEventListener('change',update);
  canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();contextLost=true;canvas.hidden=true;update();});
  canvas.addEventListener('webglcontextrestored',()=>{contextLost=false;program=buffer=null;setup();update();});
  window.addEventListener('pagehide',event=>{cancelAnimationFrame(frameRequest);frameRequest=0;if (!event.persisted) {disposed=true;release();}});
  window.addEventListener('pageshow',event=>{if(event.persisted) update();});
  setup(); update();
})();
