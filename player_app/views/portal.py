"""Offline landing page for clients connected to the device hotspot."""

PORTAL_URL = "http://10.42.0.1/"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#141414">
<title>MagicBoxie Device</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:radial-gradient(ellipse 80% 28% at 50% -5%,rgba(70,70,70,.22),transparent),#141414;color:#f5f5f5;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;padding-bottom:calc(120px + env(safe-area-inset-bottom))}
header{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;height:60px;padding:0 16px;
background:linear-gradient(#000,rgba(0,0,0,.85) 60%,transparent)}
.logo{color:#e50914;font-weight:900;font-size:22px;text-transform:uppercase;letter-spacing:-.075em}
#conn{font-size:12px;color:#a3a3a3}
main{padding:0 16px;max-width:1100px;margin:0 auto}
h2{font-size:18px;margin:20px 0 12px}
.hero{border-radius:6px;overflow:hidden;background:#262626;position:relative;margin-top:4px}
.hero img{width:100%;height:220px;object-fit:cover;display:block;opacity:.75}
.hero .info{position:absolute;inset:auto 0 0 0;padding:40px 16px 16px;background:linear-gradient(transparent,#000)}
.hero .label{font-size:12px;color:#a3a3a3;text-transform:uppercase;letter-spacing:.08em}
.hero .title{font-size:24px;font-weight:800;margin:4px 0 0}
.hidden{display:none!important}
#search{width:100%;margin-top:16px;padding:12px 14px;border-radius:6px;border:1px solid #333;background:#1f1f1f;color:#fff;font:inherit}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:12px}
.card{position:relative;border:0;padding:0;border-radius:4px;overflow:hidden;background:#262626;cursor:pointer;color:inherit;text-align:left;
box-shadow:0 0 0 1px rgba(255,255,255,.06);font:inherit}
.card .poster{aspect-ratio:2/3;width:100%;display:flex;align-items:center;justify-content:center;padding:8px;text-align:center;color:#737373;font-size:14px}
.card img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.card .cap{position:absolute;inset:auto 0 0 0;padding:28px 8px 8px;background:linear-gradient(transparent,#000);font-size:12px;font-weight:700}
.card .cap small{display:block;font-weight:400;color:#a3a3a3;margin-top:2px}
.card.now{box-shadow:0 0 0 2px #e50914}
.card .badge{position:absolute;top:6px;left:6px;background:rgba(0,0,0,.8);font-size:10px;padding:2px 6px;border-radius:3px}
.empty{color:#a3a3a3;line-height:1.6;padding:24px 0}
.btn{font:inherit;font-weight:700;border:0;border-radius:4px;padding:12px 20px;cursor:pointer;background:#fff;color:#000}
.btn.red{background:#e50914;color:#fff}.btn.grey{background:rgba(109,109,110,.7);color:#fff}
#sheet{position:fixed;inset:0;z-index:30;background:rgba(0,0,0,.75);display:flex;align-items:flex-end;justify-content:center}
#sheet .panel{width:100%;max-width:520px;max-height:90vh;overflow:auto;background:#181818;border-radius:12px 12px 0 0;padding-bottom:env(safe-area-inset-bottom)}
#sheet img{width:100%;height:220px;object-fit:cover;display:block}
#sheet .body{padding:16px}#sheet h3{margin:0 0 6px;font-size:22px}
#sheet .meta{color:#a3a3a3;font-size:13px;margin-bottom:12px}#sheet p{line-height:1.5;color:#d4d4d4}
#sheet .row{display:flex;gap:8px;margin-top:16px}#sheet .row .btn{flex:1}
#bar{position:fixed;left:0;right:0;bottom:0;z-index:20;background:#0b0b0b;border-top:1px solid #2a2a2a;padding:10px 16px calc(10px + env(safe-area-inset-bottom))}
#bar .top{display:flex;align-items:center;gap:12px}
#bar .t{flex:1;min-width:0;font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#bar .t small{display:block;font-weight:400;color:#a3a3a3;font-size:12px}
.ic{width:44px;height:44px;border-radius:50%;border:0;background:#fff;color:#000;font-size:18px;cursor:pointer}
.ic.s{background:#333;color:#fff}
#seek{width:100%;margin:8px 0 0;accent-color:#e50914}
#toast{color:#fca5a5;font-size:13px;min-height:0;padding-top:4px}
</style>
</head>
<body>
<header><span class="logo">MagicBoxie</span><span id="conn">Connecting…</span></header>
<main>
<section class="hero hidden" id="hero"><img id="heroImg" alt=""><div class="info"><div class="label">Now playing</div><div class="title" id="heroTitle"></div></div></section>
<input id="search" type="search" placeholder="Search movies" aria-label="Search movies" autocomplete="off">
<h2>Movies</h2>
<div id="grid"><div class="empty">Loading movies…</div></div>
</main>
<div id="sheet" class="hidden" role="dialog" aria-modal="true"><div class="panel" id="panel"></div></div>
<div id="bar" class="hidden">
<div class="top"><div class="t" id="barTitle"></div>
<button class="ic s" id="back" aria-label="Back 15 seconds">⟲</button>
<button class="ic" id="toggle" aria-label="Play or pause">❚❚</button>
<button class="ic s" id="fwd" aria-label="Forward 15 seconds">⟳</button>
<button class="ic s" id="stop" aria-label="Stop">■</button></div>
<input id="seek" type="range" min="0" max="0" value="0" aria-label="Seek">
<div id="toast" role="status"></div>
</div>
<script>
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
</script>
</body></html>"""
