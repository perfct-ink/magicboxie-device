const $ = id => document.getElementById(id);
let movies = [], state = {status:'stopped', movie_id:null, position_seconds:0}, seeking = false;
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'The device could not complete the request.');
  return data;
}
function fmt(s) {
  s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0');
}
function runtime(m) {
  if (!m.duration_seconds) return '';
  const h = Math.floor(m.duration_seconds / 3600), mm = Math.floor(m.duration_seconds % 3600 / 60);
  return h ? h + 'h ' + mm + 'm' : mm + 'm';
}
const meta = m => [m.year, runtime(m)].filter(Boolean).join(' · ');
const thumb = m => '/api/movies/' + m.id + '/thumbnail';
function poster(m) {
  const img = document.createElement('img'); img.src = thumb(m); img.alt = ''; img.loading = 'lazy';
  img.addEventListener('error', () => img.remove()); return img;
}
async function command(opcode, argument) {
  try {
    await api('/api/command', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({opcode, argument})});
    $('toast').textContent = ''; await status();
  } catch (error) {$('toast').textContent = error.message;}
}
function render() {
  const q = $('search').value.trim().toLowerCase(), grid = $('grid');
  const shown = movies.filter(m => !q || m.title.toLowerCase().includes(q));
  grid.replaceChildren();
  if (!shown.length) {
    const d = document.createElement('div'); d.className = 'empty';
    d.textContent = movies.length ? 'No movies match your search.' : 'No movies yet. Add movies to the device to get started.';
    grid.append(d); return;
  }
  for (const m of shown) {
    const card = document.createElement('button'); card.className = 'card' + (m.id === state.movie_id && state.status !== 'stopped' ? ' now' : '');
    const p = document.createElement('div'); p.className = 'poster'; p.textContent = m.title; p.append(poster(m));
    const cap = document.createElement('div'); cap.className = 'cap'; cap.textContent = m.title;
    if (meta(m)) {const s = document.createElement('small'); s.textContent = meta(m); cap.append(s);}
    card.append(p, cap);
    if (m.needs_transcoding) {const b = document.createElement('span'); b.className = 'badge'; b.textContent = 'Optimizing…'; card.append(b);}
    card.addEventListener('click', () => openSheet(m)); grid.append(card);
  }
}
function openSheet(m) {
  const panel = $('panel'); panel.replaceChildren();
  const img = poster(m); panel.append(img);
  const body = document.createElement('div'); body.className = 'body';
  const h = document.createElement('h3'); h.textContent = m.title;
  const mt = document.createElement('div'); mt.className = 'meta'; mt.textContent = meta(m);
  body.append(h, mt);
  if (m.description) {const p = document.createElement('p'); p.textContent = m.description; body.append(p);}
  const row = document.createElement('div'); row.className = 'row';
  const play = document.createElement('button'); play.className = 'btn'; play.textContent = '▶ Play on device';
  play.addEventListener('click', () => {closeSheet(); command('select_movie', m.id);});
  const close = document.createElement('button'); close.className = 'btn grey'; close.textContent = 'Close';
  close.addEventListener('click', closeSheet);
  row.append(play, close); body.append(row); panel.append(body); $('sheet').classList.remove('hidden');
}
function closeSheet() {$('sheet').classList.add('hidden');}
$('sheet').addEventListener('click', e => {if (e.target === $('sheet')) closeSheet();});
$('search').addEventListener('input', render);
$('toggle').addEventListener('click', () => command(state.status === 'playing' ? 'pause' : 'play'));
$('stop').addEventListener('click', () => command('stop'));
$('back').addEventListener('click', () => command('seek', Math.max(0, Math.floor(state.position_seconds) - 15)));
$('fwd').addEventListener('click', () => command('seek', Math.floor(state.position_seconds) + 15));
$('seek').addEventListener('input', () => {seeking = true;});
$('seek').addEventListener('change', () => {seeking = false; command('seek', Number($('seek').value));});
async function status() {
  try {
    state = await api('/api/status'); $('conn').textContent = 'Connected';
    const m = movies.find(x => x.id === state.movie_id), active = m && state.status !== 'stopped';
    $('bar').classList.toggle('hidden', !active); $('hero').classList.toggle('hidden', !active);
    if (active) {
      $('barTitle').textContent = m.title; $('heroTitle').textContent = m.title;
      const bt = document.createElement('small'); bt.textContent = (state.status === 'paused' ? 'Paused · ' : '') + fmt(state.position_seconds) + (m.duration_seconds ? ' / ' + fmt(m.duration_seconds) : '');
      $('barTitle').append(bt);
      $('toggle').textContent = state.status === 'playing' ? '❚❚' : '▶';
      $('heroImg').src = thumb(m);
      $('seek').max = m.duration_seconds || 0; $('seek').classList.toggle('hidden', !m.duration_seconds);
      if (!seeking) $('seek').value = state.position_seconds;
    }
    render();
  } catch (error) {$('conn').textContent = 'Waiting for device…';}
}
async function load() {
  try {movies = await api('/api/movies');} catch (error) {$('grid').textContent = 'Could not load movies. Reload this page to try again.'; return;}
  render();
}
load().then(status); setInterval(status, 3000);
