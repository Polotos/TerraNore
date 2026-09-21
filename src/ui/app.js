const api = '/api/test/v1';
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
let state = null;
let selectedTicks = 12;
let run = { active: false, paused: false, taskId: null, cancellationToken: null, startTick: 0, targetTick: 0, completed: 0, started: 0 };
let taskPollTimer = null;
let lastPaint = 0;
let flowPage = 0;
let selectedObjectId = null;
let selectedMetric = 'treasury';
let treeRoots = [];
const expandedNodes = new Set();
const childNodes = new Map();
let explorerRequest = 0;
let streamRefresh = null;

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const setText = (selector, value) => { const node = $(selector); if (node) node.textContent = value; };

function toast(message) {
  const node = $('#toast'); node.textContent = message; node.classList.add('show');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.remove('show'), 2200);
}

function switchView(id) {
  $$('.view').forEach(view => view.classList.toggle('active', view.id === id));
  $$('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === id));
  location.hash = id;
  if (id === 'explore') requestAnimationFrame(drawChart);
}

$$('.nav-item').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));

async function request(path, options = {}) {
  const response = await fetch(api + path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.diagnostic = payload.error;
    throw error;
  }
  return payload;
}

function renderState(data, force = false) {
  state = data;
  updateLodAvailability();
  const now = performance.now();
  if (!force && now - lastPaint < 125) return; // cap expensive DOM paints at 8 Hz
  lastPaint = now;
  setText('#currentDate', data.world.currentDate);
  setText('#footerSeed', data.world.seed);
  setText('#saveFormat', data.saveFormat);
  setText('#objectPopulation', fmt.format(data.summary.population));
  setText('#objectTreasury', `${fmt.format(data.summary.treasury)} TN`);
  $('#seed').value = data.world.seed;
  $('#systems').value = data.world.systemCount;
  $('#settlements').value = data.world.settlementCount;
  $('#startDate').value = data.world.startDate;
  $('#accuracy').value = data.world.accuracyProfile;
  $('#workers').value = String(data.world.workers);
  refreshExplorer();
}

function connectStream() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}${api}/ws`);
  socket.onopen = () => { $('.connection').classList.remove('offline'); setText('#connectionText', 'Поток данных активен'); };
  socket.onmessage = event => {
    const update = JSON.parse(event.data);
    if (state && update.revision <= state.revision) return;
    // Tick notifications are intentionally compact; coalesce fast runs and
    // obtain the complete DTO through the regular HTTP consistency boundary.
    clearTimeout(streamRefresh);
    streamRefresh = setTimeout(async () => {
      try { renderState(await request('/state')); }
      catch (error) { $('.connection').classList.add('offline'); }
    }, 50);
  };
  socket.onerror = () => { $('.connection').classList.add('offline'); setText('#connectionText', 'Режим HTTP'); };
  socket.onclose = () => setTimeout(connectStream, 4000);
}

$('#randomSeed').addEventListener('click', () => { $('#seed').value = crypto.getRandomValues(new Uint32Array(1))[0]; });
['systems','settlements'].forEach(id => $(`#${id}`).addEventListener('input', () => {
  const estimate = Number($('#systems').value) * 20 + Number($('#settlements').value) * 10;
  setText('#worldEstimate', `≈ ${fmt.format(estimate)} объектов`);
}));

$('#worldForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true; button.textContent = 'Создание…';
  try {
    const data = await request('/reset', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      seed:Number($('#seed').value), systemCount:Number($('#systems').value),
      settlementCount:Number($('#settlements').value), startDate:$('#startDate').value,
      accuracyProfile:$('#accuracy').value,
      workers:$('#workers').value === 'auto' ? 'auto' : Number($('#workers').value)
    }) });
    renderState(data, true); toast('Тестовый мир создан'); switchView('simulation');
  } catch (error) { toast(`Ошибка: ${error.message}`); }
  finally { button.disabled = false; button.innerHTML = '<span>＋</span> Создать тестовый мир'; }
});

$$('.period-grid button').forEach(button => button.addEventListener('click', () => {
  $$('.period-grid button').forEach(item => item.classList.remove('selected'));
  button.classList.add('selected'); selectedTicks = Number(button.dataset.ticks);
  if (selectedObjectId) { loadAnomalies(); loadSeries(selectedObjectId); }
}));
function updateCustomPeriod() {
  $$('.period-grid button').forEach(item => item.classList.remove('selected'));
  selectedTicks = Number($('#customPeriod').value) * Number($('#customUnit').value);
  if (selectedObjectId) { loadAnomalies(); loadSeries(selectedObjectId); }
}
$('#customPeriod').addEventListener('input', updateCustomPeriod);
$('#customUnit').addEventListener('change', updateCustomPeriod);

function setRunning(active) {
  run.active = active;
  $('.sim-state').classList.toggle('running', active);
  setText('#runState', active ? (run.paused ? 'ПРИОСТАНОВЛЕНО' : 'РАСЧЁТ ВЫПОЛНЯЕТСЯ') : 'ГОТОВА К ЗАПУСКУ');
  $('#runButton').disabled = active; $('#pauseButton').disabled = !active || run.paused;
  $('#resumeButton').disabled = !active || !run.paused; $('#cancelButton').disabled = !active;
  $('#stepButton').disabled = active;
}

function paintProgress(task) {
  const completed = Number(task.completed) || 0;
  const total = Math.max(0, Number(task.targetTick) - Number(task.startTick));
  const percent = total ? Math.min(100, completed / total * 100) : 0;
  $('#progressBar').style.width = `${percent}%`; setText('#progressPercent', `${Math.round(percent)}%`);
  setText('#progressLabel', `${fmt.format(completed)} / ${fmt.format(total)} тиков`);
  const elapsed = Math.max(.1, (performance.now() - run.started) / 1000);
  const speed = Math.round(completed / elapsed); setText('#speed', `${speed} тиков/с`);
  setText('#queue', Math.max(0, total - completed));
  const workers = state?.world?.workers || 1;
  setText('#workerUsage', `${run.active ? workers : 0} / ${workers}`);
  setText('#eta', speed ? `${Math.ceil((total - completed) / speed)} с` : '—');
}

function rememberTask(task) {
  run = {
    active: ['queued', 'running', 'paused'].includes(task.status),
    paused: task.status === 'paused', taskId: task.id,
    cancellationToken: task.cancellationToken, startTick: task.startTick,
    targetTick: task.targetTick, completed: task.completed, started: run.started || performance.now()
  };
  localStorage.setItem('terranore.activeTask', JSON.stringify({
    taskId: run.taskId, cancellationToken: run.cancellationToken
  }));
  setRunning(run.active);
  paintProgress(task);
}

function stopTaskPolling() {
  clearTimeout(taskPollTimer);
  taskPollTimer = null;
}

async function finalizeTask(task) {
  // Keep controls locked until the authoritative state includes every tick the
  // task completed. Terminal task responses can arrive from control endpoints,
  // not only from the polling loop.
  setRunning(true);
  const latest = await request('/state');
  renderState(latest, true);
  localStorage.removeItem('terranore.activeTask');
  setRunning(false);
  if (task.status === 'completed') toast(`Рассчитано тиков: ${task.completed}`);
  else if (task.status === 'cancelled') toast('Расчёт отменён');
  else if (task.status === 'failed') toast(`Расчёт остановлен: ${task.error || 'ошибка сервера'}`);
}

async function pollTask() {
  if (!run.active || !run.taskId) return;
  try {
    const payload = await request(`/tasks/${encodeURIComponent(run.taskId)}`);
    const task = payload.task;
    rememberTask(task);
    if (run.active) {
      taskPollTimer = setTimeout(pollTask, 150);
      return;
    }
    await finalizeTask(task);
  } catch (error) {
    stopTaskPolling();
    setRunning(false);
    toast(`Не удалось получить состояние задачи: ${error.message}`);
  }
}

function trackTask(task) {
  stopTaskPolling();
  rememberTask(task);
  if (run.active) taskPollTimer = setTimeout(pollTask, 0);
  else finalizeTask(task).catch(error => {
    setRunning(false);
    toast(`Не удалось получить состояние задачи: ${error.message}`);
  });
}

async function execute(ticks) {
  if (run.active || !state) return;
  run.started = performance.now();
  try {
    const payload = await request('/simulation/run', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ targetTick: state.world.tick + ticks })
    });
    trackTask(payload.task);
  } catch (error) {
    setRunning(false);
    $('#balanceWarning').hidden = false;
    toast(`Расчёт не запущен: ${error.message}`);
  }
}

$('#runButton').addEventListener('click', () => execute(Math.max(1, selectedTicks)));
$('#stepButton').addEventListener('click', async () => {
  if (run.active || !state) return;
  $('#stepButton').disabled = true;
  try {
    const data = await request('/simulation/step', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ ticks:1 })
    });
    renderState(data, true);
    toast('Выполнен один тик');
  } catch (error) { toast(`Шаг не выполнен: ${error.message}`); }
  finally { $('#stepButton').disabled = run.active; }
});

async function controlTask(operation) {
  if (!run.active || !run.taskId) return;
  try {
    const payload = await request(`/simulation/${operation}`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        taskId: run.taskId, cancellationToken: run.cancellationToken
      })
    });
    trackTask(payload.task);
  } catch (error) { toast(`Операция не выполнена: ${error.message}`); }
}
$('#pauseButton').addEventListener('click', () => controlTask('pause'));
$('#resumeButton').addEventListener('click', () => controlTask('resume'));
$('#cancelButton').addEventListener('click', () => controlTask('cancel'));

const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
  '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
}[character]));

function normalizeNode(item, level) {
  return {
    id: String(item.id), label: item.name || item.label || item.id,
    type: item.type || 'object', level,
    expandable: item.hasChildren !== false
  };
}

function visibleTreeNodes(nodes = treeRoots, result = []) {
  nodes.forEach(node => {
    result.push(node);
    if (expandedNodes.has(node.id)) visibleTreeNodes(childNodes.get(node.id) || [], result);
  });
  return result;
}

function buildTree() {
  const tree = $('#tree');
  if (!tree) return;
  const rows = visibleTreeNodes();
  setText('#loadedNodes', `${rows.length} узлов загружено`);
  tree.innerHTML = rows.map(row => `<div class="tree-row ${row.id === selectedObjectId ? 'selected' : ''}" role="treeitem" aria-level="${row.level + 1}" aria-expanded="${row.expandable ? expandedNodes.has(row.id) : ''}" data-id="${escapeHtml(row.id)}" style="padding-left:${10 + row.level * 16}px"><span class="twisty">${row.expandable ? (expandedNodes.has(row.id) ? '⌄' : '›') : ''}</span><span class="node-icon">${row.type === 'world' ? '◎' : row.type === 'region' ? '◇' : '○'}</span><span>${escapeHtml(row.label)}</span></div>`).join('');
  $$('.tree-row').forEach(element => element.addEventListener('click', event => {
    const row = rows.find(item => item.id === element.dataset.id);
    if (!row) return;
    if (event.target.classList.contains('twisty')) toggleNode(row);
    else selectNode(row);
  }));
}

async function refreshExplorer() {
  if (!state) return;
  const sequence = ++explorerRequest;
  try {
    const payload = await request('/nodes/world/children');
    if (sequence !== explorerRequest) return;
    treeRoots = payload.items.map(item => normalizeNode(item, 0));
    const selected = visibleTreeNodes().find(item => item.id === selectedObjectId)
      || treeRoots.find(item => item.id === selectedObjectId)
      || treeRoots[0];
    buildTree();
    if (selected) await selectNode(selected, false);
  } catch (error) {
    if (sequence === explorerRequest) {
      treeRoots = [];
      $('#tree').innerHTML = '<p class="data-message">Не удалось загрузить структуру мира</p>';
    }
  }
}

async function toggleNode(row) {
  if (expandedNodes.has(row.id)) {
    expandedNodes.delete(row.id);
    buildTree();
    return;
  }
  if (!childNodes.has(row.id)) {
    try {
      const payload = await request(`/nodes/${encodeURIComponent(row.id)}/children`);
      childNodes.set(row.id, payload.items.map(item => normalizeNode(item, row.level + 1)));
      if (!payload.items.length) row.expandable = false;
    } catch (error) {
      showUnavailable($('#tree'), 'Не удалось загрузить дочерние элементы');
      return;
    }
  }
  expandedNodes.add(row.id);
  buildTree();
}

async function selectNode(row, refreshTree = true) {
  selectedObjectId = row.id;
  flowPage = 0;
  if (refreshTree) buildTree();
  const selection = selectedObjectId;
  await Promise.all([
    loadObjectCard(selection), loadFlows(selection), loadAnomalies(), loadSeries(selection)
  ]);
}

async function loadObjectCard(objectId) {
  try {
    const payload = await request(`/objects/${encodeURIComponent(objectId)}`);
    if (objectId !== selectedObjectId) return;
    renderObjectCard(payload.object);
  } catch (error) {
    if (objectId === selectedObjectId) {
      setText('#objectName', 'данные пока не моделируются');
      setText('#objectType', 'ОБЪЕКТ НЕДОСТУПЕН');
      setText('#objectPopulation', '—'); setText('#objectTreasury', '—');
    }
  }
}

function updateLodAvailability(busy = false) {
  const disabled = busy || !selectedObjectId || Boolean(state?.readOnly);
  $$('.lod-switch button').forEach(button => { button.disabled = disabled; });
  $('.lod-switch')?.setAttribute('aria-busy', String(busy));
  if (state?.readOnly) setText('#lodStatus', 'СНИМОК ТОЛЬКО ДЛЯ ЧТЕНИЯ');
}

function renderObjectCard(object) {
  setText('#objectName', object.name || object.id);
  setText('#objectType', String(object.type || 'object').toUpperCase());
  setText('#objectPath', `WORLD / ${String(object.type || 'OBJECT').toUpperCase()} / ${object.id.toUpperCase()}`);
  setText('#objectPopulation', object.population == null ? '—' : fmt.format(object.population));
  setText('#objectTreasury', object.economy?.treasury == null ? '—' : `${fmt.format(object.economy.treasury)} TN`);
  const manualLod = object.manualLod || object.detailLevel || 'auto';
  const effectiveLod = object.effectiveLod || (manualLod === 'auto' ? 'lod-0' : manualLod);
  $$('.lod-switch button').forEach(button => button.classList.toggle('active', button.dataset.level === manualLod));
  setText('#lodStatus', `РУЧНОЙ: ${manualLod.toUpperCase()} · ЭФФЕКТИВНЫЙ: ${effectiveLod.toUpperCase()}`);
  updateLodAvailability();
}

function showUnavailable(container, message = 'данные пока не моделируются') {
  if (container) container.innerHTML = `<p class="data-message">${escapeHtml(message)}</p>`;
}

let flowItems = [];
function renderFlows() {
  const container = $('#flows');
  if (!flowItems.length) { showUnavailable(container); setText('#flowPage', '—'); return; }
  const pages = Math.ceil(flowItems.length / 3);
  flowPage = Math.min(flowPage, pages - 1);
  container.innerHTML = flowItems.slice(flowPage * 3, flowPage * 3 + 3).map(item => {
    const direction = item.direction === 'incoming' || item.direction === 'in' ? 'in' : 'out';
    const sign = direction === 'in' ? '+' : '−';
    return `<div class="table-row"><b>${escapeHtml(item.name || item.metric || 'Поток')}</b><small>${escapeHtml(item.peerName || item.peerId || '')}</small><b class="${direction}">${sign} ${fmt.format(Math.abs(Number(item.value ?? item.amount ?? 0)))}</b></div>`;
  }).join('');
  setText('#flowPage', `${flowPage + 1} / ${pages}`);
}

async function loadFlows(objectId) {
  try {
    const payload = await request(`/objects/${encodeURIComponent(objectId)}/flows`);
    if (objectId !== selectedObjectId) return;
    flowItems = payload.items || [];
    renderFlows();
  } catch (error) {
    if (objectId === selectedObjectId) { flowItems = []; renderFlows(); }
  }
}

function selectedRange() {
  const end = state?.world?.tick || 0;
  return { start: Math.max(0, end - Math.max(1, selectedTicks)), end };
}

async function loadAnomalies() {
  const { start, end } = selectedRange();
  try {
    const payload = await request(`/anomalies?from=${start}&to=${end}`);
    const container = $('#changes');
    if (!payload.items.length) {
      showUnavailable(container, 'Аномалий за выбранный период не обнаружено');
      return;
    }
    container.innerHTML = payload.items.map(item => `<div class="change-item"><b>${escapeHtml(item.kind || 'Аномалия')}</b><span>тик ${fmt.format(item.tick)}</span></div>`).join('');
  } catch (error) { showUnavailable($('#changes')); }
}

let chartPoints = [];
async function loadSeries(objectId) {
  const { start, end } = selectedRange();
  try {
    const payload = await request(`/timeseries?from=${start}&to=${end}&metric=${encodeURIComponent(selectedMetric)}&objectId=${encodeURIComponent(objectId)}`);
    if (objectId !== selectedObjectId) return;
    chartPoints = payload.items || [];
    drawChart();
  } catch (error) {
    if (objectId === selectedObjectId) { chartPoints = []; drawChart(); }
  }
}

function drawChart() {
  const canvas = $('#chart');
  const message = $('#chartMessage');
  if (!canvas || !canvas.offsetWidth) return;
  if (!chartPoints.length) {
    canvas.hidden = true; message.hidden = false;
    return;
  }
  canvas.hidden = false; message.hidden = true;
  const ratio = devicePixelRatio || 1;
  canvas.width = canvas.offsetWidth * ratio; canvas.height = 190 * ratio;
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
  const width = canvas.offsetWidth, height = 190;
  ctx.strokeStyle = '#26342f'; ctx.lineWidth = 1;
  for (let y = 20; y < height; y += 38) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
  const values = chartPoints.map(point => Number(point[selectedMetric])).filter(Number.isFinite);
  if (!values.length) { canvas.hidden = true; message.hidden = false; return; }
  const min = Math.min(...values), max = Math.max(...values);
  ctx.beginPath(); ctx.strokeStyle = '#9bdaa6'; ctx.lineWidth = 1.7;
  values.forEach((value, index) => {
    const x = values.length === 1 ? width / 2 : index / (values.length - 1) * width;
    const y = 15 + (1 - (value - min) / (max - min || 1)) * (height - 35);
    index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
}

$('#prevFlow').addEventListener('click', () => { flowPage = Math.max(0, flowPage - 1); renderFlows(); });
$('#nextFlow').addEventListener('click', () => { flowPage += 1; renderFlows(); });
$$('.lod-switch button').forEach(button => button.addEventListener('click', async () => {
  if (!selectedObjectId || state?.readOnly) return;
  // Capture the real object, not the mutable tree selection, for this request.
  const objectId = selectedObjectId;
  const level = button.dataset.level;
  const previousLevel = $('.lod-switch button.active')?.dataset.level || 'auto';
  $$('.lod-switch button').forEach(item => item.classList.toggle('active', item === button));
  updateLodAvailability(true);
  try {
    const data = await request(`/objects/${encodeURIComponent(objectId)}/lod`, {
      method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ level })
    });
    if (objectId !== selectedObjectId) return;
    renderState(data, true);
    renderObjectCard(data.object);
  } catch (error) {
    if (objectId === selectedObjectId) {
      $$('.lod-switch button').forEach(item => item.classList.toggle('active', item.dataset.level === previousLevel));
      toast(`Не удалось изменить детализацию: ${error.diagnostic || error.message}`);
    }
  } finally {
    if (objectId === selectedObjectId) updateLodAvailability();
  }
}));
$$('#tabs button').forEach(button => button.addEventListener('click', () => {
  $$('#tabs button').forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  const metrics = { 'Экономика':'treasury', 'Население':'population', 'Производство':'production' };
  selectedMetric = metrics[button.textContent] || button.textContent.toLowerCase();
  setText('#chartTitle', button.textContent);
  if (selectedObjectId) loadSeries(selectedObjectId);
}));

window.addEventListener('resize', drawChart);
document.addEventListener('keydown', event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') execute(selectedTicks); });
setText('#cpuHint', `${navigator.hardwareConcurrency || '—'} логических процессоров доступно`);
request('/state').then(data => {
  renderState(data, true);
  if (data.activeTask) trackTask(data.activeTask);
  else localStorage.removeItem('terranore.activeTask');
}).catch(error => toast(`API недоступен: ${error.message}`));
connectStream();
if (location.hash && $(location.hash)) switchView(location.hash.slice(1));
