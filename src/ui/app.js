const api = '/api/test/v1';
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const months = ['ЯНВАРЬ','ФЕВРАЛЬ','МАРТ','АПРЕЛЬ','МАЙ','ИЮНЬ','ИЮЛЬ','АВГУСТ','СЕНТЯБРЬ','ОКТЯБРЬ','НОЯБРЬ','ДЕКАБРЬ'];
let state = null;
let selectedTicks = 12;
let run = { active: false, paused: false, cancelled: false, startTick: 0, target: 0, started: 0 };
let lastPaint = 0;
let flowPage = 0;

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
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function worldDate(tick) {
  const base = new Date(`${$('#startDate').value || '2200-01-01'}T00:00:00Z`);
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
  buildTree();
  drawChart();
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
    const data = await request('/reset', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ seed:Number($('#seed').value) }) });
    renderState(data, true); toast('Тестовый мир создан'); switchView('simulation');
  } catch (error) { toast(`Ошибка: ${error.message}`); }
  finally { button.disabled = false; button.innerHTML = '<span>＋</span> Создать тестовый мир'; }
});

$$('.period-grid button').forEach(button => button.addEventListener('click', () => {
  $$('.period-grid button').forEach(item => item.classList.remove('selected'));
  button.classList.add('selected'); selectedTicks = Number(button.dataset.ticks);
}));
$('#customPeriod').addEventListener('input', () => { $$('.period-grid button').forEach(item => item.classList.remove('selected')); selectedTicks = Number($('#customPeriod').value) * Number($('#customUnit').value); });
$('#customUnit').addEventListener('change', () => { selectedTicks = Number($('#customPeriod').value) * Number($('#customUnit').value); });

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
  const workers = $('#workers').value === 'auto' ? (navigator.hardwareConcurrency || 4) : $('#workers').value;
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

function treeNodes() {
  if (!state) return [];
  const rows = [{ id:'world', level:0, label:'TerraNore', type:'World', expandable:true, open:true }];
  state.world.regions.forEach((region, index) => {
    rows.push({ id:region.id, level:1, label:`Sector ${String(index + 1).padStart(2,'0')}`, type:'Sector', expandable:true, open:index === 0, region });
    if (index === 0) {
      rows.push({ id:'system-1', level:2, label:'Helios', type:'System', expandable:true, open:true, region });
      rows.push({ id:'planet-1', level:3, label:'Aurelia Prime', type:'Planet', expandable:true, open:true, region });
      rows.push({ id:'settlement-1', level:4, label:region.name, type:'Settlement', expandable:true, open:false, region });
      rows.push({ id:'station-1', level:3, label:'Lagrange Station', type:'Station', expandable:false, region });
    }
  }); return rows;
}

function buildTree() {
  const tree = $('#tree'); if (!tree || !state) return;
  const rows = treeNodes(); setText('#loadedNodes', `${rows.length} узлов загружено`);
  tree.innerHTML = rows.map((row, i) => `<div class="tree-row ${i === 0 ? 'selected' : ''}" role="treeitem" aria-level="${row.level + 1}" data-index="${i}" style="padding-left:${10 + row.level * 16}px"><span class="twisty">${row.expandable ? (row.open ? '⌄' : '›') : ''}</span><span class="node-icon">${row.type === 'World' ? '◎' : row.type === 'Sector' ? '◇' : row.type === 'System' ? '✦' : '○'}</span><span>${row.label}</span></div>`).join('');
  $$('.tree-row').forEach(node => node.addEventListener('click', () => selectNode(rows[Number(node.dataset.index)], node)));
}

function selectNode(row, node) {
  $$('.tree-row').forEach(item => item.classList.remove('selected')); node.classList.add('selected');
  setText('#objectName', row.label); setText('#objectType', row.type.toUpperCase());
  setText('#objectPath', `WORLD / ${row.type.toUpperCase()} / ${row.id.toUpperCase()}`);
  const region = row.region;
  setText('#objectPopulation', fmt.format(region ? region.population : state.summary.population));
  setText('#objectTreasury', `${fmt.format(region ? region.economy.treasury : state.summary.treasury)} TN`);
}

$$('.lod-switch button').forEach(button => button.addEventListener('click', () => { $$('.lod-switch button').forEach(item => item.classList.remove('active')); button.classList.add('active'); toast(`Режим детализации: ${button.textContent}`); }));
$$('#tabs button').forEach(button => button.addEventListener('click', () => { $$('#tabs button').forEach(item => item.classList.remove('active')); button.classList.add('active'); setText('#chartTitle', button.textContent); drawChart(); }));

function drawChart() {
  const canvas = $('#chart'); if (!canvas || !state || !canvas.offsetWidth) return;
  const ratio = devicePixelRatio || 1; canvas.width = canvas.offsetWidth * ratio; canvas.height = 190 * ratio;
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio); const w = canvas.offsetWidth, h = 190;
  ctx.strokeStyle = '#26342f'; ctx.lineWidth = 1;
  for (let y=20;y<h;y+=38){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke()}
  const points = state.series?.length > 1 ? state.series : Array.from({length:18},(_,i)=>({treasury:3200+i*55+Math.sin(i)*180,production:370+i*7+Math.cos(i*.8)*35}));
  [['treasury','#9bdaa6'],['production','#e0ad62']].forEach(([key,color],line) => {
    const values=points.map(p=>Number(p[key] ?? 0)), min=Math.min(...values), max=Math.max(...values); ctx.beginPath(); ctx.strokeStyle=color; ctx.lineWidth=1.7;
    values.forEach((value,i)=>{const x=i/(values.length-1)*w,y=15+(1-(value-min)/(max-min||1))*(h-35); i?ctx.lineTo(x,y):ctx.moveTo(x,y)}); ctx.stroke();
  });
}

const flows = [['Продовольствие','North Reach','+ 8 420','in'],['Композиты','Iron Vale','− 3 180','out'],['Энергия','Amber Coast','+ 2 760','in'],['Механизмы','Verdant Basin','− 1 940','out'],['Медицина','Helios','+ 840','in'],['Топливо','Lagrange','− 620','out']];
function renderFlows(){const page=flows.slice(flowPage*3,flowPage*3+3);$('#flows').innerHTML=page.map(x=>`<div class="table-row"><b>${x[0]}</b><small>${x[1]}</small><b class="${x[3]}">${x[2]}</b></div>`).join('');setText('#flowPage',`${flowPage+1} / 2`)}
$('#prevFlow').addEventListener('click',()=>{flowPage=Math.max(0,flowPage-1);renderFlows()});$('#nextFlow').addEventListener('click',()=>{flowPage=Math.min(1,flowPage+1);renderFlows()});
const changes=[['Aurelia Prime · население','+12 480'],['Helios · производство','+18.6%'],['Iron Vale · казна','−8.2%'],['Линия H-4 · поток','+5 820'],['Продовольствие · цена','−4.1%'],['Station-01 · запасы','+2 110']];
$('#changes').innerHTML=changes.map(x=>`<div class="change-item"><b>${x[0]}</b><span>${x[1]}</span></div>`).join('');renderFlows();

window.addEventListener('resize', drawChart);
document.addEventListener('keydown', event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') execute(selectedTicks); });
setText('#cpuHint', `${navigator.hardwareConcurrency || '—'} логических процессоров доступно`);
request('/state').then(data => renderState(data, true)).catch(error => toast(`API недоступен: ${error.message}`));
connectStream();
if (location.hash && $(location.hash)) switchView(location.hash.slice(1));
