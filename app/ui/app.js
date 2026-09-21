/* ==========================================================================
   文件批量重命名 · 前端逻辑
   所有重命名计算都在 Python 侧完成，这里只负责交互与渲染
   ========================================================================== */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.prototype.slice.call(document.querySelectorAll(sel));

const state = {
  meta: { rules: [] },
  items: [],
  rows: [],
  workflow: [],
  mode: 'replace',
  nav: { inside: false, crumbs: [], base: '' },
  stats: {},
  can: {},
  rowEls: new Map(),
  editing: null,
  cursor: null,
  dropDepth: 0,
  busy: false,
  hotkey: null,
  sort: '',
};

const IMG = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'heic', 'tif', 'tiff'];
const VIDEO = ['mp4', 'mov', 'avi', 'mkv', 'wmv', 'flv', 'webm', 'm4v'];
const AUDIO = ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'wma'];
const ARCHIVE = ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'];
const CODE = ['js', 'ts', 'vue', 'html', 'htm', 'css', 'json', 'py', 'go', 'rb', 'java', 'c',
  'cpp', 'h', 'rs', 'sh', 'yml', 'yaml', 'xml', 'sql'];
const DOC = ['txt', 'md', 'doc', 'docx', 'pdf', 'ppt', 'pptx', 'xls', 'xlsx', 'csv'];

const RULE_MAP = {};
function ruleMeta(id) { return RULE_MAP[id] || { name: id, fields: [], icon: '' }; }

/* 模块化模式：前四个常驻标签页，其余通过「更多」下拉进入 */
const MODES = [
  { id: 'replace', name: '查找替换', icon: '🔍', rules: ['replace', 'replace_multi'], permanent: true },
  { id: 'insert', name: '插入内容', icon: '🧩', rules: ['insert', 'affix'], permanent: true },
  { id: 'sequence', name: '自动编号', icon: '🔢', rules: ['sequence', 'template', 'timestamp'], permanent: true },
  { id: 'manual', name: '手动编辑', icon: '✏️', rules: [], permanent: true },
  { id: 'extract', name: '提取子串', icon: '⛏', rules: ['extract'] },
  { id: 'case', name: '大小写转换', icon: '🔠', rules: ['case'] },
  { id: 'clean', name: '清理文件名', icon: '🧹', rules: ['clean'] },
  { id: 'delete', name: '删除字符', icon: '✂️', rules: ['delete'] },
  { id: 'extension', name: '扩展名处理', icon: '📎', rules: ['extension'] },
  { id: 'uniqueify', name: '智能去重', icon: '🔀', rules: ['uniqueify'] },
];
const MORE_MODES = MODES.filter((m) => !m.permanent);

function activeMode() {
  return MODES.find((m) => m.id === state.mode) || MODES[0];
}

function modeStepCount(mode) {
  return state.workflow.filter((s) => (mode.rules || []).indexOf(s.id) >= 0).length;
}

const SHORTCUTS = [
  ['Ctrl + O', '选择文件'],
  ['Ctrl + Shift + O', '选择文件夹'],
  ['Ctrl + V', '导入资源管理器复制的文件；没有文件时按一列名字粘贴'],
  ['Ctrl + Enter', '开始重命名'],
  ['Ctrl + Z', '撤回已完成的改名'],
  ['Ctrl + A / Ctrl + I', '全选 / 反选'],
  ['↑ ↓ / Home / End', '在列表中移动当前行'],
  ['空格', '勾选 / 取消勾选当前行'],
  ['双击行', '手动改名；文件夹则是进入'],
  ['Alt + ↑ / 退格', '返回上一级目录'],
  ['Delete', '把选中项从列表移除'],
  ['F5', '重新读取磁盘目录'],
  ['Ctrl + T', '深色 / 浅色主题'],
  ['F1 / Esc', '使用说明 / 关闭弹层'],
];

/* ------------------------------------------------------------------ 工具 */
function esc(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function extOf(name) {
  const i = name.lastIndexOf('.');
  return i > 0 ? name.slice(i + 1).toLowerCase() : '';
}

function icon(html) { return '<svg aria-hidden="true"><use href="#' + html + '"/></svg>'; }

function tagOf(row) {
  if (row.isDir) return { cls: 'dir', text: '夹' };
  const ext = extOf(row.name);
  if (IMG.includes(ext)) return { cls: 'img', text: '图' };
  if (VIDEO.includes(ext)) return { cls: 'video', text: '影' };
  if (AUDIO.includes(ext)) return { cls: 'audio', text: '音' };
  if (ARCHIVE.includes(ext)) return { cls: 'archive', text: '压' };
  if (CODE.includes(ext)) return { cls: 'code', text: '码' };
  if (DOC.includes(ext)) return { cls: 'doc', text: '文' };
  return { cls: '', text: ext ? ext.slice(0, 2).toUpperCase() : '文' };
}

function debounce(fn, wait) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), wait);
  };
}

function store(key, value) {
  try { localStorage.setItem(key, value); } catch (err) { /* 忽略 */ }
}

/* ------------------------------------------------------------------ 通信 */
function api() { return window.pywebview && window.pywebview.api; }

async function call(name, ...args) {
  const box = api();
  if (!box || typeof box[name] !== 'function') return null;
  try {
    const payload = await box[name].apply(box, args);
    applyPayload(payload);
    return payload;
  } catch (err) {
    toast('操作失败：' + err, 'error');
    return null;
  }
}

function applyPayload(payload) {
  if (!payload) return;
  if (payload.workflow) {
    state.workflow = payload.workflow;
    renderModebar();
    renderModePanel();
  }
  if (payload.nav) {
    state.nav = payload.nav;
    renderNav();
  }
  if (payload.items) {
    state.items = payload.items;
    renderItems();
  } else if (payload.rows) {
    updateRows(payload.rows);
  }
  if (payload.stats) {
    state.stats = payload.stats;
    renderStats();
  }
  if (payload.can) {
    state.can = payload.can;
    renderButtons();
  }
  if (payload.clip) fallbackClipboard(payload.clip);
  if (payload.toast) toast(payload.toast.text, payload.toast.type);
  if (payload.dialog) showDialog(payload.dialog.title, payload.dialog.lines);
}

window.__onPush = function (payload) { applyPayload(payload); };

window.__onProgress = function (done, total) {
  const btn = $('#btnRun');
  const bar = $('#progress');
  btn.querySelector('span').textContent = '处理中 ' + done + '/' + total;
  $('#statusRight').textContent = '正在重命名 ' + done + ' / ' + total;
  bar.hidden = false;
  bar.querySelector('i').style.width = (total ? Math.round(done * 100 / total) : 100) + '%';
  if (done >= total) {
    setTimeout(() => {
      btn.querySelector('span').textContent = '开始重命名';
      bar.hidden = true;
      bar.querySelector('i').style.width = '0%';
    }, 460);
  }
};

/* ------------------------------------------------------------------ Toast */
function toast(text, type) {
  if (!text) return;
  const kind = {
    ok: 'ok', success: 'ok', warn: 'warn', warning: 'warn',
    error: 'error', err: 'error', info: 'info',
  }[type] || 'info';
  const map = { ok: '#i-check', warn: '#i-warn', error: '#i-warn', info: '#i-check' };
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = icon(map[kind]) + '<span>' + esc(text) + '</span>';
  $('#toasts').appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 240);
  }, kind === 'error' ? 5200 : 3000);
}

/* ------------------------------------------------------------------ 弹层 */
function showDialog(title, lines) {
  const box = $('#dialog');
  box.hidden = false;
  box.innerHTML =
    '<h3>' + esc(title) + '</h3>' +
    '<div class="dialog-body">' + (lines || []).map(esc).join('<br>') + '</div>' +
    '<div class="dialog-foot"><button type="button" class="btn primary" data-close>知道了</button></div>';
  $('#popmask').hidden = false;
  box.querySelector('[data-close]').onclick = hideLayers;
  box.querySelector('[data-close]').focus();
}

function showHelpDialog() {
  const box = $('#dialog');
  box.hidden = false;
  box.innerHTML =
    '<h3>使用说明</h3>' +
    '<div class="dialog-body help-body">' +
    '<p class="help-lead">导入 → 配规则 → 看预览 → 执行；规则按从上到下的顺序依次生效。</p>' +
    SHORTCUTS.map((row) =>
      '<div class="help-row"><kbd>' + esc(row[0]) + '</kbd><span>' + esc(row[1]) + '</span></div>').join('') +
    '</div>' +
    '<div class="dialog-foot"><button type="button" class="btn primary" data-close>知道了</button></div>';
  $('#popmask').hidden = false;
  box.querySelector('[data-close]').onclick = hideLayers;
  box.querySelector('[data-close]').focus();
}

/* ------------------------------------------------------------------ 设置 */
const MOD_LABELS = { ctrl: 'Ctrl', alt: 'Alt', shift: 'Shift', meta: 'Win' };
const KEY_LABELS = {
  Up: '↑', Down: '↓', Left: '←', Right: '→', Space: '空格',
  Enter: 'Enter', Home: 'Home', End: 'End', PgUp: 'PgUp', PgDn: 'PgDn',
  Ins: 'Ins', Del: 'Del', Tab: 'Tab',
};

function hkLabel(hk) {
  if (!hk) return '未设置';
  if (hk.type === 'mouse') {
    return hk.button === 'x2' ? '鼠标侧键 X2（前进键）' : '鼠标侧键 X1（后退键）';
  }
  const mods = (hk.mods || []).map((m) => MOD_LABELS[m] || m);
  const key = KEY_LABELS[hk.key] || hk.key;
  return mods.concat([key]).join(' + ');
}

/* 把按键事件规范化成 (mods, key)；只按修饰键时返回 null */
function normalizeKeyEvent(e) {
  const key = e.key;
  if (key === 'Control' || key === 'Alt' || key === 'Shift' || key === 'Meta') return null;
  let out = null;
  if (/^[a-zA-Z]$/.test(key)) out = key.toUpperCase();
  else if (/^[0-9]$/.test(key)) out = key;
  else if (/^F([1-9]|1[0-2])$/.test(key)) out = key;
  else {
    const named = {
      ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
      ' ': 'Space', Spacebar: 'Space', Enter: 'Enter', Home: 'Home', End: 'End',
      PageUp: 'PgUp', PageDown: 'PgDn', Insert: 'Ins', Delete: 'Del', Tab: 'Tab',
    };
    out = named[key] || null;
  }
  if (!out) return undefined;              // 不支持的键
  const mods = [];
  if (e.ctrlKey) mods.push('ctrl');
  if (e.altKey) mods.push('alt');
  if (e.shiftKey) mods.push('shift');
  if (e.metaKey) mods.push('meta');
  return { mods, key: out };
}

let hkRecording = false;

function stopHkRecord(save) {
  if (!hkRecording) return;
  hkRecording = false;
  window.removeEventListener('keydown', hkKeyCapture, true);
  window.removeEventListener('mousedown', hkMouseCapture, true);
  if (save) {
    state.hotkey = save;
    call('set_hotkey', save);
  }
  if ($('#dialog').hidden) return;
  showSettingsDialog();                    // 重绘回显
}

function hkKeyCapture(e) {
  e.preventDefault();
  e.stopImmediatePropagation();   // 挂在 window 捕获阶段，确保拦在全局快捷键处理之前
  if (e.key === 'Escape') { stopHkRecord(null); return; }
  const norm = normalizeKeyEvent(e);
  if (norm === null) return;               // 只按了修饰键，等下一个键
  if (norm === undefined) {                // 不支持的键
    const el = $('#hkValue');
    if (el) el.textContent = '暂不支持该按键，请换一个';
    return;
  }
  const bare = norm.mods.length === 0;
  const isFn = /^F([1-9]|1[0-2])$/.test(norm.key);
  if (bare && !isFn) {
    const el = $('#hkValue');
    if (el) el.textContent = '单个按键请加 Ctrl / Alt 等修饰键';
    return;
  }
  stopHkRecord({ type: 'key', mods: norm.mods, key: norm.key });
}

function hkMouseCapture(e) {
  if (e.button !== 3 && e.button !== 4) return;   // 3/4 = 侧键 X1 / X2
  e.preventDefault();
  e.stopImmediatePropagation();
  stopHkRecord({ type: 'mouse', button: e.button === 3 ? 'x1' : 'x2' });
}

function startHkRecord() {
  hkRecording = true;
  const el = $('#hkValue');
  if (el) {
    el.classList.add('rec');
    el.textContent = '按下键盘组合键或鼠标侧键…（Esc 取消）';
  }
  // 挂 window 捕获阶段：比 document 上的全局键处理更早，录制期间按键不会误触其它功能
  window.addEventListener('keydown', hkKeyCapture, true);
  window.addEventListener('mousedown', hkMouseCapture, true);
}

function showSettingsDialog() {
  const box = $('#dialog');
  box.hidden = false;
  box.innerHTML =
    '<h3>设置</h3>' +
    '<div class="set-body">' +
    '<div class="set-row">' +
    '<div class="set-info"><b>全局唤出快捷键</b>' +
    '<p>在任意程序里按下快捷键即可显示 / 隐藏本窗口，支持鼠标侧键（后退 / 前进键）。设置立即生效并自动保存。</p></div>' +
    '<div class="hk-area">' +
    '<div class="hk-value" id="hkValue">' + esc(hkLabel(state.hotkey)) + '</div>' +
    '<div class="hk-actions">' +
    '<button type="button" class="btn tiny" id="btnHkRecord">' + (hkRecording ? '取消录制' : '录制快捷键') + '</button>' +
    '<button type="button" class="btn tiny" id="btnHkClear">清除</button>' +
    '</div></div></div>' +
    '<div class="set-row">' +
    '<div class="set-info"><b>外观</b><p>深色 / 浅色主题（也可按 Ctrl + T 快速切换）。</p></div>' +
    '<button type="button" class="btn tiny" id="btnThemeSet">切换主题</button>' +
    '</div>' +
    '</div>' +
    '<div class="dialog-foot"><button type="button" class="btn primary" data-close>完成</button></div>';
  $('#popmask').hidden = false;
  box.querySelector('[data-close]').onclick = () => { if (hkRecording) stopHkRecord(null); hideLayers(); };
  box.querySelector('#btnHkRecord').onclick = () => {
    if (hkRecording) { stopHkRecord(null); return; }
    if (!$('#dialog').hidden) { hideLayers(); showRecordBox(); }
  };
  box.querySelector('#btnHkClear').onclick = () => {
    state.hotkey = null;
    call('set_hotkey', null);
    showSettingsDialog();
  };
  box.querySelector('#btnThemeSet').onclick = () => { toggleTheme(); };
  box.querySelector('#btnThemeSet').focus();
}

/* 录制模式单独一个轻量弹层，避免整块重绘打断输入 */
function showRecordBox() {
  const box = $('#dialog');
  box.hidden = false;
  box.innerHTML =
    '<h3>录制全局快捷键</h3>' +
    '<div class="set-body"><div class="hk-value rec" id="hkValue" style="align-self:center">按下键盘组合键或鼠标侧键…（Esc 取消）</div>' +
    '<p class="set-info" style="text-align:center"><span style="font-size:11.5px;color:var(--fg-mute)">支持 Ctrl / Alt / Shift / Win + 任意键，或直接按鼠标侧键 X1 / X2</span></p></div>' +
    '<div class="dialog-foot"><button type="button" class="btn" data-cancel>取消</button></div>';
  $('#popmask').hidden = false;
  box.querySelector('[data-cancel]').onclick = () => { stopHkRecord(null); };
  startHkRecord();
}

function hideLayers() {
  $('#dialog').hidden = true;
  $('#menu').hidden = true;
  $('#popmask').hidden = true;
}

function showMenu(x, y, items) {
  const menu = $('#menu');
  menu.innerHTML = items.map((it) => {
    if (it.sep) return '<div class="menu-sep"></div>';
    const mark = it.checked ? icon('i-check') : (it.icon ? icon(it.icon) : '');
    return '<div class="menu-item' + (it.disabled ? ' disabled' : '') + (it.checked ? ' on' : '') +
      '" data-id="' + it.id + '">' + mark + '<span>' + esc(it.text) + '</span></div>';
  }).join('');
  menu.hidden = false;
  const rect = menu.getBoundingClientRect();
  menu.style.left = Math.min(x, window.innerWidth - rect.width - 10) + 'px';
  menu.style.top = Math.min(y, window.innerHeight - rect.height - 10) + 'px';
  $('#popmask').hidden = false;
  menu.onclick = (e) => {
    const item = e.target.closest('.menu-item');
    if (!item) return;
    hideLayers();
    const found = items.find((it) => String(it.id) === item.dataset.id);
    if (found && found.run) found.run();
  };
}

/* ------------------------------------------------------------------ 主题 */
function applyTheme(theme) {
  const value = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = value;
  store('fr-theme', value);
  const use = $('#btnTheme').querySelector('use');
  use.setAttribute('href', value === 'light' ? '#i-sun' : '#i-moon');
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  applyTheme(next);
  call('set_setting', 'theme', next);
}

/* ------------------------------------------------------------------ 渲染 */
function renderNav() {
  const nav = state.nav || { inside: false, crumbs: [], base: '' };
  document.body.classList.toggle('inside-dir', !!nav.inside);
  const parts = ['<button type="button" class="crumb' + (nav.inside ? '' : ' active') + '" data-crumb="-1"' +
    (nav.inside ? '' : ' aria-current="page"') + '>' +
    icon(nav.inside ? 'i-folder' : 'i-file') + '<span>导入列表</span></button>'];
  nav.crumbs.forEach((c, i) => {
    const last = i === nav.crumbs.length - 1;
    parts.push('<span class="crumb-sep" aria-hidden="true">' + icon('i-chev') + '</span>');
    parts.push('<button type="button" class="crumb' + (last ? ' active' : '') +
      '" data-crumb="' + i + '" title="' + esc(c.path) + '"' +
      (last ? ' aria-current="page"' : '') + '><span>' + esc(c.name) + '</span></button>');
  });
  $('#crumbs').innerHTML = parts.join('');
  $('#btnUp').hidden = !nav.inside;
}

function renderStats() {
  const s = state.stats || {};
  const pills = [];
  if (s.total) {
    pills.push('<span class="pill"><b>' + s.total + '</b>项</span>');
    if (s.dirs) pills.push('<span class="pill"><b>' + s.dirs + '</b>文件夹</span>');
    if (s.sel) pills.push('<span class="pill brand">已选 <b>' + s.sel + '</b></span>');
    if (s.todo) pills.push('<span class="pill">待处理 <b>' + s.todo + '</b></span>');
    if (s.done) pills.push('<span class="pill ok">已完成 <b>' + s.done + '</b></span>');
    if (s.failed) pills.push('<span class="pill bad">失败 <b>' + s.failed + '</b></span>');
  }
  $('#pills').innerHTML = pills.join('');
  $('#subtitle').textContent = s.total
    ? ('共 ' + s.total + ' 项' + (s.sel ? '，已选 ' + s.sel + ' 项' : '') + '，规则改动会实时预览')
    : '把文件拖进来，或点「选择文件」';
  $('#statusLeft').textContent = s.total
    ? ('共 ' + s.total + ' 项 / 待处理 ' + (s.todo || 0) + ' 项' +
      (s.manual ? ' / 手动改名 ' + s.manual + ' 项' : '') +
      (s.history ? ' / 可撤回 ' + s.history + ' 项' : ''))
    : '就绪';
  const mode = activeMode();
  const count = modeStepCount(mode);
  $('#statusRight').textContent = count
    ? ('当前模式「' + mode.name + '」· ' + count + ' 条规则生效')
    : '当前模式「' + mode.name + '」';
}

function renderButtons() {
  const c = state.can || {};
  $('#btnRun').disabled = !c.run || state.busy;
  $('#btnRevert').disabled = !c.revert;
  $('#btnRemove').disabled = !c.remove;
  $('#btnCopy').disabled = !c.copy;
  $('#btnClear').disabled = !c.clear;
}

/* ------------------------------------------------------------------ 模式栏 */
function renderModebar() {
  const mode = activeMode();
  const tabs = [];
  MODES.filter((m) => m.permanent || m.id === mode.id).forEach((m) => {
    tabs.push('<button type="button" class="mode-tab' + (m.id === mode.id ? ' active' : '') +
      '" data-mode="' + m.id + '" title="' + esc(m.name) + '">' +
      '<span class="mode-ico" aria-hidden="true">' + m.icon + '</span><span>' + esc(m.name) + '</span></button>');
  });
  const count = modeStepCount(mode);
  const flag = mode.id === 'manual'
    ? ''
    : ('<span class="mode-flag' + (count ? ' on' : '') + '">' +
      (count ? '当前标签页的规则生效中 · ' + count : '当前标签页暂无规则') + '</span>');
  $('#modebar').innerHTML = '<div class="mode-tabs">' + tabs.join('') +
    '<button type="button" class="mode-tab more" id="btnMoreModes" title="更多模式">' +
    '<span>更多</span><svg><use href="#i-chev"/></svg></button></div>' +
    '<div class="mode-side">' + flag + '</div>';
  $$('#modebar .mode-tab[data-mode]').forEach((tab) => {
    tab.onclick = () => switchMode(tab.dataset.mode);
  });
  $('#btnMoreModes').onclick = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    showMenu(rect.left, rect.bottom + 6, MORE_MODES.map((m) => ({
      id: m.id, text: m.name, run: () => switchMode(m.id),
    })));
  };
  renderStats();
}

function switchMode(id) {
  if (state.mode === id) return;
  state.mode = id;
  store('fr-mode', id);
  renderModebar();
  renderModePanel();
}

/* ------------------------------------------------------------------ 模式面板 */
function renderModePanel() {
  const panel = $('#modePanel');
  const mode = activeMode();
  $$('#modebar .mode-tab[data-mode]').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.mode === mode.id);
  });
  if (mode.id === 'manual') {
    const manual = state.stats.manual || 0;
    panel.innerHTML = '<div class="mode-empty">' +
      '<p>双击列表里的「新名称预览」直接改名，改完按回车确认、Esc 取消；' +
      '文件夹双击则进入目录。</p>' +
      '<p>也可以在 Excel / 文本里复制一列名字，选中文件后按 <b>Ctrl + V</b> 按行批量覆盖。</p>' +
      (manual ? '<div class="mode-empty-foot"><span>已手动改名 <b>' + manual + '</b> 项</span>' +
        '<button type="button" class="btn tiny" id="btnClearManual">清除全部手动改名</button></div>' : '') +
      '</div>';
    const btn = $('#btnClearManual');
    if (btn) btn.onclick = () => call('clear_manual', null);
    return;
  }
  const groups = (mode.rules || []).filter((id) => RULE_MAP[id]).map((ruleId) => {
    const meta = RULE_MAP[ruleId];
    const steps = state.workflow.map((step, index) => ({ step, index }))
      .filter((s) => s.step.id === ruleId);
    return '<div class="mode-group">' +
      '<div class="mode-group-head">' +
      '<span class="mode-group-name"><i aria-hidden="true">' + meta.icon + '</i>' + esc(meta.name) + '</span>' +
      '<button type="button" class="btn tiny add" data-add="' + ruleId + '">' +
      '<svg><use href="#i-plus"/></svg><span>添加' + esc(meta.name) + '</span></button>' +
      '</div>' +
      (steps.length
        ? steps.map((s) => modeRuleHtml(s.index, s.step, meta)).join('')
        : '<div class="mode-group-empty">暂无规则，点右上角「添加」新建一条</div>') +
      '</div>';
  }).join('');
  panel.innerHTML = groups;
  $$('#modePanel [data-add]').forEach((btn) => {
    btn.onclick = () => call('add_rule', btn.dataset.add);
  });
  bindModeInputs();
  renderStats();
}

function modeRuleHtml(index, step, meta) {
  const fields = (meta.fields || []).filter((f) => step.visible.indexOf(f.key) >= 0);
  return '<div class="mrule' + (step.enabled ? '' : ' off') + '" data-index="' + index + '">' +
    '<button type="button" class="switch' + (step.enabled ? ' on' : '') +
    '" data-act="toggle" role="switch" aria-checked="' + (step.enabled ? 'true' : 'false') +
    '" aria-label="启用或停用该规则" title="' + (step.enabled ? '停用' : '启用') + '"></button>' +
    '<div class="mrule-fields">' + fields.map((f) => fieldHtml(index, f, step)).join('') + '</div>' +
    '<button type="button" class="icon-btn del" data-act="del" title="移除规则" aria-label="移除规则">' +
    icon('i-close') + '</button>' +
    '</div>';
}

function fieldHtml(index, field, step) {
  const value = step.config[field.key];
  const attrs = ' data-index="' + index + '" data-key="' + field.key + '" data-type="' + field.type + '"';
  if (field.type === 'bool') {
    return '<div class="field switch-field">' +
      '<label title="' + esc(field.label) + '">' + esc(field.label) + '</label>' +
      '<button type="button" class="switch' + (value ? ' on' : '') + '"' + attrs +
      ' data-act="bool" role="switch" aria-checked="' + (value ? 'true' : 'false') +
      '" aria-label="' + esc(field.label) + '" title="' + esc(field.label) + '"></button></div>';
  }
  let control;
  if (field.type === 'textarea') {
    control = '<textarea' + attrs + ' rows="4" spellcheck="false" placeholder="' +
      esc(field.placeholder || '每行一组：旧=新') + '">' + esc(value == null ? '' : value) + '</textarea>';
    return '<div class="field wide"><label title="' + esc(field.label) + '">' + esc(field.label) +
      '</label>' + control + '</div>';
  }
  if (field.type === 'select') {
    // 原生 select 在 WebView2 下会出现文字错乱，这里统一用自绘下拉（菜单弹层）
    const opts = field.options || [];
    const cur = opts.find((o) => String(o[0]) === String(value));
    control = '<button type="button" class="fake-select"' + attrs + ' data-act="select"' +
      ' title="' + esc(field.label) + '"><span>' +
      esc(cur ? cur[1] : (value == null ? '' : value)) + '</span>' + icon('i-chevd') + '</button>';
  } else {
    const kind = field.type === 'number' ? 'number' : 'text';
    control = '<input type="' + kind + '"' + attrs + ' value="' + esc(value == null ? '' : value) +
      '" placeholder="' + esc(field.label) + '" />';
  }
  return '<div class="field"><label title="' + esc(field.label) + '">' + esc(field.label) +
    '</label>' + control + '</div>';
}

const pushRuleValue = debounce((index, key, value) => call('set_rule', index, key, value), 170);

function bindModeInputs() {
  $$('#modePanel [data-act="select"]').forEach((btn) => {
    btn.onclick = () => {
      const index = +btn.dataset.index;
      const key = btn.dataset.key;
      const step = state.workflow[index];
      const meta = step ? RULE_MAP[step.id] : null;
      const field = meta ? (meta.fields || []).find((f) => f.key === key) : null;
      const rect = btn.getBoundingClientRect();
      showMenu(rect.left, rect.bottom + 4, (field && field.options || []).map((opt) => ({
        id: opt[0],
        text: opt[1],
        checked: step ? String(opt[0]) === String(step.config[key]) : false,
        run: () => {
          const span = btn.querySelector('span');
          if (span) span.textContent = opt[1];
          call('set_rule', index, key, opt[0]);
        },
      })));
    };
  });
  $$('#modePanel input[data-key], #modePanel textarea[data-key]').forEach((input) => {
    input.oninput = () => {
      pushRuleValue(+input.dataset.index, input.dataset.key, input.value);
    };
    input.onkeydown = (e) => { if (e.key === 'Enter' && input.tagName === 'INPUT') input.blur(); };
  });
  $$('#modePanel [data-act="bool"]').forEach((el) => {
    const flip = () => {
      const on = !el.classList.contains('on');
      el.classList.toggle('on', on);
      el.setAttribute('aria-checked', on ? 'true' : 'false');
      call('set_rule', +el.dataset.index, el.dataset.key, on);
    };
    el.onclick = flip;
    el.onkeydown = (e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); flip(); } };
  });
  $$('#modePanel [data-act="toggle"]').forEach((el) => {
    el.onclick = () => {
      const index = +el.closest('.mrule').dataset.index;
      const on = !el.classList.contains('on');
      el.classList.toggle('on', on);
      el.setAttribute('aria-checked', on ? 'true' : 'false');
      el.closest('.mrule').classList.toggle('off', !on);
      call('toggle_rule', index, on);
    };
  });
  $$('#modePanel [data-act="del"]').forEach((el) => {
    el.onclick = () => call('remove_rule', +el.closest('.mrule').dataset.index);
  });
}

function renderItems() {
  const rows = state.items;
  $('#table').hidden = rows.length === 0;
  $('#empty').hidden = rows.length > 0;
  state.rowEls.clear();
  state.cursor = null;
  if (!rows.length) {
    $('#rows').innerHTML = '';
    return;
  }
  $('#rows').innerHTML = rows.map(rowHtml).join('');
  $$('#rows .row').forEach((el) => state.rowEls.set(el.dataset.tag, el));
  bindRowEvents();
}

function rowHtml(row) {
  const t = tagOf(row);
  const dup = row.dup && row.status !== 'done' && row.status !== 'error';
  const badge = dup
    ? '<span class="badge dup">重名</span>'
    : '<span class="badge ' + row.status + '">' + esc(row.statusText) + '</span>' +
      (row.manual ? '<span class="badge manual">手动</span>' : '');
  const dstClass = 'dst' + (row.changed ? '' : ' same') + (row.status === 'error' ? ' err' : '');
  const name = row.isDir
    ? '<button type="button" class="name-btn" data-act="enter" title="进入文件夹：' + esc(row.name) + '">' +
      '<span class="name-text">' + esc(row.name) + '</span></button>' +
      '<span class="enter-hint" aria-hidden="true">' + icon('i-enter') + '</span>'
    : '<span class="name-text">' + esc(row.name) + '</span>';
  const actions = row.isDir
    ? '<button type="button" class="icon-btn" data-act="enter" title="进入文件夹" aria-label="进入文件夹">' +
      icon('i-enter') + '</button>'
    : '';
  return '<div class="row' + (row.sel ? ' sel' : '') + '" data-tag="' + esc(row.tag) +
    '" draggable="true" tabindex="0" role="row" aria-selected="' + (row.sel ? 'true' : 'false') +
    '" title="' + esc(row.error || row.name) + '">' +
    '<span class="cell"><span class="check' + (row.sel ? ' on' : '') + '" data-act="sel" aria-hidden="true"></span></span>' +
    '<span class="cell c-name">' +
    '<span class="tag ' + t.cls + '" aria-hidden="true">' + t.text + '</span>' + name + '</span>' +
    '<span class="cell c-dir" title="' + esc(row.dir) + '">' + esc(row.dir || '·') + '</span>' +
    '<span class="cell c-new"><span class="' + dstClass + '" data-act="edit" title="双击可手动改名">' +
    esc(row.new) + '</span></span>' +
    '<span class="cell c-type">' + esc(row.type) + '</span>' +
    '<span class="cell c-size">' + esc(row.size) + '</span>' +
    '<span class="cell c-time">' + esc(row.mtime) + '</span>' +
    '<span class="cell c-status">' + badge + '</span>' +
    '<span class="cell c-act">' +
    '<button type="button" class="icon-btn" data-act="revert" title="撤回" aria-label="撤回">' +
    icon('i-undo') + '</button>' +
    '<button type="button" class="icon-btn del" data-act="remove" title="从列表移除" aria-label="从列表移除">' +
    icon('i-trash') + '</button>' + actions +
    '</span></div>';
}

function updateRows(rows) {
  rows.forEach((row) => {
    const el = state.rowEls.get(row.tag);
    if (!el) return;
    el.classList.toggle('sel', !!row.sel);
    el.setAttribute('aria-selected', row.sel ? 'true' : 'false');
    const check = el.querySelector('.check');
    if (check) check.classList.toggle('on', !!row.sel);

    const dst = el.querySelector('.dst');
    if (dst && dst.textContent !== row.new) {
      dst.textContent = row.new;
      dst.classList.remove('flash');
      void dst.offsetWidth;
      dst.classList.add('flash');
    }
    if (dst) {
      dst.classList.toggle('same', !row.changed);
      dst.classList.toggle('err', row.status === 'error');
    }
    const statusCell = el.querySelector('.c-status');
    if (statusCell) {
      const dup = row.dup && row.status !== 'done' && row.status !== 'error';
      statusCell.innerHTML =
        (dup ? '<span class="badge dup">重名</span>'
          : '<span class="badge ' + row.status + '">' + esc(row.statusText) + '</span>') +
        (row.manual ? '<span class="badge manual">手动</span>' : '');
    }
    const rowData = state.items.find((i) => i.tag === row.tag);
    if (rowData) {
      rowData.manual = row.manual;
      rowData.status = row.status;
      rowData.error = row.error;
      rowData.dup = row.dup;
      rowData.new = row.new;
      rowData.changed = row.changed;
      rowData.sel = row.sel;
    }
  });
}

/* 列表行事件 */
function bindRowEvents() {
  $$('#rows .row').forEach((el) => {
    el.addEventListener('click', (e) => {
      const tag = el.dataset.tag;
      const act = e.target.closest('[data-act]');
      if (act) {
        const kind = act.dataset.act;
        if (kind === 'revert') { call('revert', [tag]); return; }
        if (kind === 'remove') { call('remove_items', [tag]); return; }
        if (kind === 'enter') { call('enter_dir', tag); return; }
        if (kind === 'edit') return;
      }
      setCursor(el);
      call('toggle_select', tag);
    });
    el.addEventListener('focus', () => setCursor(el));
    el.addEventListener('dblclick', () => {
      const row = state.items.find((i) => i.tag === el.dataset.tag);
      if (row && row.isDir) { call('enter_dir', el.dataset.tag); return; }
      if (state.editing) return;
      startEdit(el);
    });
    el.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const tag = el.dataset.tag;
      const row = state.items.find((i) => i.tag === tag) || {};
      const selected = !el.classList.contains('sel');
      if (selected) call('toggle_select', tag, true);
      const items = [];
      if (row.isDir) {
        items.push({ id: 'enter', text: '进入文件夹', icon: 'i-enter', run: () => call('enter_dir', tag) });
        items.push({ sep: true });
      }
      items.push({ id: 'edit', text: '手动改名（双击新名称）', icon: 'i-plus', run: () => startEdit(el) });
      items.push({ id: 'clear', text: '清除手动改名', run: () => call('clear_manual', [tag]) });
      items.push({ sep: true });
      items.push({ id: 'revert', text: '撤回该行改名', icon: 'i-undo', run: () => call('revert', [tag]) });
      items.push({ sep: true });
      items.push({ id: 'copyPath', text: '复制完整路径', icon: 'i-copy', run: () => copyToClipboard('path', 'text') });
      items.push({ id: 'copyName', text: '复制文件名', icon: 'i-copy', run: () => copyToClipboard('name', 'text') });
      items.push({ sep: true });
      items.push({ id: 'remove', text: '从列表移除', icon: 'i-trash', run: () => call('remove_items', [tag]) });
      showMenu(e.clientX, e.clientY, items);
    });
  });
  bindRowDrag();
}

function setCursor(el) {
  if (!el) return;
  state.cursor = el.dataset.tag;
  $$('#rows .row.cursor').forEach((r) => r.classList.remove('cursor'));
  el.classList.add('cursor');
}

function moveCursor(delta) {
  const rows = $$('#rows .row');
  if (!rows.length) return;
  let index = rows.findIndex((r) => r.dataset.tag === state.cursor);
  if (index < 0) index = delta > 0 ? -1 : rows.length;
  index = Math.max(0, Math.min(rows.length - 1, index + delta));
  setCursor(rows[index]);
  rows[index].scrollIntoView({ block: 'nearest' });
  rows[index].focus({ preventScroll: true });
}

function startEdit(el) {
  const tag = el.dataset.tag;
  const dst = el.querySelector('.dst');
  const row = state.items.find((i) => i.tag === tag);
  if (!row || state.editing || row.isDir) return;
  const name = row.name;
  const dot = name.lastIndexOf('.');
  const base = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : '';
  state.editing = tag;

  const input = document.createElement('input');
  input.className = 'dst-input';
  input.value = row.manual || base;
  input.setAttribute('aria-label', '手动改名，回车确认，Esc 取消');
  dst.replaceWith(input);
  input.focus();
  input.select();

  const commit = (save) => {
    if (!state.editing) return;
    state.editing = null;
    const value = save ? input.value.trim() : '';
    const span = document.createElement('span');
    span.className = 'dst';
    span.dataset.act = 'edit';
    span.textContent = row.new;
    input.replaceWith(span);
    if (save) call('set_manual', tag, value);
    else call('set_manual', tag, row.manual || '');
  };

  input.onkeydown = (e) => {
    if (e.key === 'Enter') commit(true);
    if (e.key === 'Escape') commit(false);
  };
  input.onblur = () => commit(true);
  input.onclick = (e) => e.stopPropagation();
}

/* 列表拖动排序 */
let rowDragFrom = null;
function bindRowDrag() {
  $$('#rows .row').forEach((el) => {
    el.ondragstart = (e) => {
      if (state.editing) { e.preventDefault(); return; }
      rowDragFrom = el.dataset.tag;
      el.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', rowDragFrom); } catch (err) { /* ignore */ }
    };
    el.ondragend = () => {
      el.classList.remove('dragging');
      $$('#rows .row').forEach((r) => r.classList.remove('drop-target'));
    };
    el.ondragover = (e) => {
      if (!rowDragFrom) return;
      e.preventDefault();
      if (el.dataset.tag !== rowDragFrom) el.classList.add('drop-target');
    };
    el.ondragleave = () => el.classList.remove('drop-target');
    el.ondrop = (e) => {
      e.preventDefault();
      el.classList.remove('drop-target');
      const target = el.dataset.tag;
      if (!rowDragFrom || target === rowDragFrom) return;
      const order = state.items.map((i) => i.tag);
      const from = order.indexOf(rowDragFrom);
      const to = order.indexOf(target);
      if (from < 0 || to < 0) return;
      const moved = order.splice(from, 1)[0];
      order.splice(to, 0, moved);
      rowDragFrom = null;
      call('reorder_items', order);
    };
  });
}

/* ------------------------------------------------------------------ 复制 */
async function copyToClipboard(field, fmt) {
  const payload = await call('copy_to_clipboard', field, fmt);
  if (payload && payload.clip) fallbackClipboard(payload.clip);
}

async function fallbackClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    showDialog('剪贴板不可用', ['请手动复制下面的内容：', '', String(text).slice(0, 4000)]);
  }
}

/* ------------------------------------------------------------------ 事件 */
function bindWindowButtons() {
  $$('.win-btn[data-win]').forEach((btn) => {
    btn.onclick = async () => {
      const box = api();
      if (!box) return;
      if (btn.dataset.win === 'min') box.minimize();
      else if (btn.dataset.win === 'max') {
        const res = await box.toggle_max();
        btn.querySelector('use').setAttribute('href', res && res.maxed ? '#i-min' : '#i-max');
      } else box.close();
    };
  });
  $$('#resizers i').forEach((grip) => {
    grip.onmousedown = (e) => {
      const box = api();
      if (box) box.start_resize(+grip.dataset.ht);
      e.preventDefault();
    };
  });
}

function bindDrop() {
  const mask = $('#dropMask');
  const preview = $('#preview');
  const isFileDrag = (e) => {
    const types = e.dataTransfer && e.dataTransfer.types;
    if (!types) return false;
    return Array.prototype.indexOf.call(types, 'Files') >= 0;
  };

  preview.addEventListener('dragenter', (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    state.dropDepth += 1;
    mask.classList.add('show');
  });
  preview.addEventListener('dragover', (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  preview.addEventListener('dragleave', () => {
    state.dropDepth = Math.max(0, state.dropDepth - 1);
    if (!state.dropDepth) mask.classList.remove('show');
  });
  document.addEventListener('drop', () => {
    state.dropDepth = 0;
    setTimeout(() => mask.classList.remove('show'), 120);
  });
  document.addEventListener('dragend', () => {
    state.dropDepth = 0;
    mask.classList.remove('show');
  });
}

function bindToolbar() {
  $('#btnPickFiles').onclick = () => call('pick_files');
  $('#btnPickFolder').onclick = () => call('pick_folder');
  $('#btnPaste').onclick = () => call('paste_clipboard');
  $('#btnClear').onclick = () => call('clear_items');
  $('#btnRemove').onclick = () => call('remove_items', null);
  $('#btnTheme').onclick = toggleTheme;
  $('#btnHelp').onclick = showHelpDialog;
  $('#btnSettings').onclick = showSettingsDialog;
  $('#btnUp').onclick = () => {
    const depth = (state.nav.crumbs || []).length;
    call('goto_crumb', depth - 2);
  };
  $('#crumbs').onclick = (e) => {
    const btn = e.target.closest('[data-crumb]');
    if (!btn) return;
    call('goto_crumb', +btn.dataset.crumb);
  };
  $('#btnRevert').onclick = async () => {
    const payload = await call('revert', null);
    if (payload && payload.stats && payload.stats.failed) {
      toast('仍有失败项，可再试一次', 'warn');
    }
  };
  $('#btnRun').onclick = async () => {
    if (state.busy) return;
    state.busy = true;
    const btn = $('#btnRun');
    btn.classList.add('busy');
    btn.querySelector('span').textContent = '处理中…';
    await call('rename_now', null);
    state.busy = false;
    btn.classList.remove('busy');
    btn.querySelector('span').textContent = '开始重命名';
    renderButtons();
  };
  $('#btnCopy').onclick = (e) => {
    showMenu(e.clientX, e.clientY - 250, [
      { id: 'pt', text: '路径 · 纯文本', icon: 'i-copy', run: () => copyToClipboard('path', 'text') },
      { id: 'pj', text: '路径 · JSON', icon: 'i-copy', run: () => copyToClipboard('path', 'json') },
      { sep: true },
      { id: 'nt', text: '文件名 · 纯文本', icon: 'i-copy', run: () => copyToClipboard('name', 'text') },
      { id: 'nj', text: '文件名 · JSON', icon: 'i-copy', run: () => copyToClipboard('name', 'json') },
    ]);
  };
  $('#btnSelectAll').onclick = () => call('select_all', true);
  $('#btnInvert').onclick = () => call('invert_select');
  $('#sortBtn').onclick = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const opts = [
      ['', '默认顺序'], ['name:0', '名称 ↑'], ['name:1', '名称 ↓'],
      ['size:0', '大小 ↑'], ['size:1', '大小 ↓'],
      ['mtime:0', '修改时间 ↑'], ['mtime:1', '修改时间 ↓'],
      ['birthtime:0', '创建时间 ↑'], ['birthtime:1', '创建时间 ↓'],
    ];
    showMenu(rect.left, rect.bottom + 4, opts.map((o) => ({
      id: o[0] || 'default',
      text: o[1],
      checked: state.sort === o[0],
      run: () => {
        state.sort = o[0];
        $('#sortLabel').textContent = o[1];
        if (o[0]) {
          const [key, rev] = o[0].split(':');
          call('sort_items', key, rev === '1');
        }
      },
    })));
  };
  $('#popmask').onclick = hideLayers;
}

function bindKeys() {
  document.addEventListener('keydown', (e) => {
    if (hkRecording) return;    // 录制全局快捷键时，交给录制捕获层处理
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].indexOf((e.target.tagName || '')) >= 0;
    const key = (e.key || '').toLowerCase();

    if (e.key === 'Escape') {
      if (state.editing && e.target.blur) e.target.blur();
      hideLayers();
      return;
    }
    if (e.key === 'F1') { e.preventDefault(); showHelpDialog(); return; }
    if (typing) return;

    const ctrl = e.ctrlKey;
    if (e.altKey && e.key === 'ArrowUp') {
      e.preventDefault();
      call('goto_crumb', (state.nav.crumbs || []).length - 2);
      return;
    }
    if (e.key === 'Backspace') {
      e.preventDefault();
      call('goto_crumb', (state.nav.crumbs || []).length - 2);
      return;
    }
    if (ctrl && key === 'o') { e.preventDefault(); call(e.shiftKey ? 'pick_folder' : 'pick_files'); return; }
    if (ctrl && key === 'enter') { e.preventDefault(); if (!$('#btnRun').disabled) $('#btnRun').click(); return; }
    if (ctrl && key === 'z') { e.preventDefault(); if (!$('#btnRevert').disabled) $('#btnRevert').click(); return; }
    if (ctrl && key === 'a') { e.preventDefault(); call('select_all', true); return; }
    if (ctrl && key === 'i') { e.preventDefault(); call('invert_select'); return; }
    if (ctrl && key === 't') { e.preventDefault(); toggleTheme(); return; }
    if (ctrl && e.shiftKey && key === 'c') { e.preventDefault(); $('#btnCopy').click(); return; }
    if (ctrl && key === 'v') { e.preventDefault(); call('paste_clipboard'); return; }
    if (ctrl && key === 'r') { e.preventDefault(); call('reload'); return; }
    if (e.key === 'F5') { e.preventDefault(); call('reload'); return; }

    if (e.key === 'ArrowDown') { e.preventDefault(); moveCursor(1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); moveCursor(-1); return; }
    if (e.key === 'Home') { e.preventDefault(); moveCursor(-1e6); return; }
    if (e.key === 'End') { e.preventDefault(); moveCursor(1e6); return; }
    if (e.key === ' ') {
      const el = $$('#rows .row').find((r) => r.dataset.tag === state.cursor);
      if (!el) return;
      e.preventDefault();
      call('toggle_select', el.dataset.tag);
      return;
    }
    if (e.key === 'Enter') {
      const row = state.items.find((i) => i.tag === state.cursor);
      if (row && row.isDir) { e.preventDefault(); call('enter_dir', row.tag); }
      return;
    }
    if (e.key === 'Delete') { e.preventDefault(); call('remove_items', null); }
  });
}

/* ------------------------------------------------------------------ 启动 */
/* DOMContentLoaded 的兜底与 pywebviewready 可能都会触发初始化，
   重复初始化会重复给 document 挂 keydown / drop 监听（方向键一次跳两行、空格勾选又被抵消）。 */
let inited = false;
async function init() {
  if (inited) return;
  const box = api();
  if (!box) {
    setTimeout(init, 200);
    return;
  }
  inited = true;
  const meta = await box.get_meta();
  state.meta = meta || { rules: [] };
  (state.meta.rules || []).forEach((r) => { RULE_MAP[r.id] = r; });
  state.hotkey = (meta && meta.settings && meta.settings.hotkey) || null;

  let stored = null;
  try { stored = localStorage.getItem('fr-theme'); } catch (err) { stored = null; }
  applyTheme(stored || (meta && meta.settings && meta.settings.theme) || 'dark');

  let mode = 'replace';
  try { mode = localStorage.getItem('fr-mode') || 'replace'; } catch (err) { mode = 'replace'; }
  state.mode = MODES.some((m) => m.id === mode) ? mode : 'replace';

  bindToolbar();
  bindWindowButtons();
  bindDrop();
  bindKeys();

  const payload = await box.get_state();
  applyPayload(payload);
  renderModebar();
  renderModePanel();
  renderItems();
  renderStats();
  renderButtons();
}

window.onerror = function (message, source, line, column) {
  const box = api();
  if (box && box.report_error) {
    box.report_error([message, source, line, column].join(' | '));
  }
};
window.addEventListener('unhandledrejection', function (e) {
  const box = api();
  if (box && box.report_error) box.report_error('promise: ' + (e.reason && e.reason.message || e.reason));
});

window.addEventListener('pywebviewready', init);
document.addEventListener('DOMContentLoaded', () => {
  if (!api()) {
    setTimeout(() => { if (!api()) $('#statusLeft').textContent = '正在等待后端启动…'; }, 1200);
    setTimeout(init, 400);
  }
});
