const api = '/api/test/v1';
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const months = ['ЯНВАРЬ','ФЕВРАЛЬ','МАРТ','АПРЕЛЬ','МАЙ','ИЮНЬ','ИЮЛЬ','АВГУСТ','СЕНТЯБРЬ','ОКТЯБРЬ','НОЯБРЬ','ДЕКАБРЬ'];
let state = null;
let selectedTicks = 12;
let run = { active: false, paused: false, cancelled: false, startTick: 0, target: 0, started: 0 };
let lastPaint = 0;
let flowPage = 0;
let selectedObjectId = null;
let selectedMetric = 'treasury';
let treeRoots = [];
const expandedNodes = new Set();
const childNodes = new Map();
let explorerRequest = 0;

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
  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function worldDate(tick) {
  const base = new Date(`${state?.world?.startDate || '2200-01-01'}T00:00:00Z`);
  const month = base.getUTCMonth() + tick;
  return { year: base.getUTCFullYear() + Math.floor(month / 12), month: ((month % 12) + 12) % 12 };
}

function renderState(data, force = false) {
  state = data;
  const now = performance.now();
  if (!force && now - lastPaint < 125) return; // cap expensive DOM paints at 8 Hz
  lastPaint = now;
  const date = worldDate(data.world.tick);
  setText('#currentDate', `${date.year} · ${months[date.month]}`);
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
  socket.onmessage = event => renderState(JSON.parse(event.data));
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
}

function paintProgress(completed, total) {
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

async function execute(ticks) {
  if (run.active || !state) return;
  run = { active:true, paused:false, cancelled:false, startTick:state.world.tick, target:ticks, started:performance.now() };
  setRunning(true); paintProgress(0, ticks);
  // Chunking keeps requests short, makes cancellation useful and never blocks the DOM.
  let completed = 0;
  try {
    while (completed < ticks && !run.cancelled) {
      if (run.paused) { await new Promise(resolve => setTimeout(resolve, 100)); continue; }
      const chunk = Math.min(12, ticks - completed);
      const data = await request('/tick', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ ticks:chunk }) });
      completed += chunk; renderState(data); paintProgress(completed, ticks);
      await new Promise(resolve => requestAnimationFrame(resolve));
    }
    if (!run.cancelled) { renderState(state, true); toast(`Рассчитано тиков: ${completed}`); }
  } catch (error) { $('#balanceWarning').hidden = false; toast(`Расчёт остановлен: ${error.message}`); }
  finally { run.paused = false; setRunning(false); setText('#queue', '0'); }
}

$('#runButton').addEventListener('click', () => execute(Math.max(1, selectedTicks)));
$('#stepButton').addEventListener('click', () => execute(1));
$('#pauseButton').addEventListener('click', () => { run.paused = true; setRunning(true); });
$('#resumeButton').addEventListener('click', () => { run.paused = false; setRunning(true); });
$('#cancelButton').addEventListener('click', () => { run.cancelled = true; toast('Отмена после текущего пакета'); });

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
    const object = payload.object;
    setText('#objectName', object.name || object.id);
    setText('#objectType', String(object.type || 'object').toUpperCase());
    setText('#objectPath', `WORLD / ${String(object.type || 'OBJECT').toUpperCase()} / ${object.id.toUpperCase()}`);
    setText('#objectPopulation', object.population == null ? '—' : fmt.format(object.population));
    setText('#objectTreasury', object.economy?.treasury == null ? '—' : `${fmt.format(object.economy.treasury)} TN`);
  } catch (error) {
    if (objectId === selectedObjectId) {
      setText('#objectName', 'данные пока не моделируются');
      setText('#objectType', 'ОБЪЕКТ НЕДОСТУПЕН');
      setText('#objectPopulation', '—'); setText('#objectTreasury', '—');
    }
  }
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
  if (!selectedObjectId) return;
  const level = button.textContent.trim().toLowerCase().replace(' ', '-');
  try {
    const data = await request(`/objects/${encodeURIComponent(selectedObjectId)}/lod`, {
      method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ level })
    });
    $$('.lod-switch button').forEach(item => item.classList.toggle('active', item === button));
    renderState(data, true);
  } catch (error) { toast(`Не удалось изменить детализацию: ${error.message}`); }
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
request('/state').then(data => renderState(data, true)).catch(error => toast(`API недоступен: ${error.message}`));
connectStream();
if (location.hash && $(location.hash)) switchView(location.hash.slice(1));
