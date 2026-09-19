const api = '/api/test/v1';
const fmt = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});

function render(data) {
  const {world, summary} = data;
  document.querySelector('#population').textContent = fmt.format(summary.population);
  document.querySelector('#treasury').textContent = fmt.format(summary.treasury) + ' TN';
  document.querySelector('#production').textContent = fmt.format(summary.production);
  document.querySelector('#tick').textContent = fmt.format(world.tick);
  document.querySelector('#clock').textContent = `Год ${summary.year} · Месяц ${summary.month}`;
  document.querySelector('#regions').innerHTML = world.regions.map(region => `
    <article class="region"><div><h3>${region.name}</h3><p>${fmt.format(region.population)} жителей · ${fmt.format(region.resources)} ресурсов</p><strong>${fmt.format(region.economy.treasury)} TN</strong></div>
    <select data-id="${region.id}" aria-label="Детализация ${region.name}">${['summary','standard','detailed'].map(level => `<option ${level === region.detail_level ? 'selected' : ''} value="${level}">${{summary:'Обзор',standard:'Стандарт',detailed:'Подробно'}[level]}</option>`).join('')}</select></article>`).join('');
  document.querySelectorAll('select').forEach(select => select.onchange = () => post('/lod', {regionId: select.dataset.id, level: select.value}));
}

async function post(path, body) {
  const response = await fetch(api + path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  render(await response.json());
}

document.querySelector('#month').onclick = () => post('/tick', {ticks:1});
document.querySelector('#year').onclick = () => post('/tick', {ticks:12});
fetch(api + '/state').then(response => response.json()).then(render);
