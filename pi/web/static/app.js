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
    // Only one movie is transcoded at a time; the rest of the backlog is just queued.
    const badge = m.id === state.transcoding_movie_id ? 'Optimizing…' : m.needs_transcoding ? 'Queued' : '';
    if (badge) {const b = document.createElement('span'); b.className = 'badge'; b.textContent = badge; card.append(b);}
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
function closeSheet() {$('sheet').classList.add('hidden'); clearInterval(settingsTimer);}
$('sheet').addEventListener('click', e => {if (e.target === $('sheet')) closeSheet();});
$('search').addEventListener('input', render);
$('toggle').addEventListener('click', () => command(state.status === 'playing' ? 'pause' : 'play'));
$('stop').addEventListener('click', () => command('stop'));
$('back').addEventListener('click', () => command('seek', Math.max(0, Math.floor(state.position_seconds) - 15)));
$('fwd').addEventListener('click', () => command('seek', Math.floor(state.position_seconds) + 15));
$('seek').addEventListener('input', () => {seeking = true;});
$('seek').addEventListener('change', () => {seeking = false; command('seek', Number($('seek').value));});
// While a movie plays we assume it keeps playing: tick the elapsed time up
// every second locally, and stop that ticker as soon as a poll returns (the
// device's own position replaces it, and the ticker restarts from there).
let ticker = null;
function stopTicker() {clearInterval(ticker); ticker = null;}
function showTime(m) {
  $('barTitle').textContent = m.title;
  const bt = document.createElement('small');
  bt.textContent = (state.status === 'paused' ? 'Paused · ' : '') + fmt(state.position_seconds) + (m.duration_seconds ? ' / ' + fmt(m.duration_seconds) : '');
  $('barTitle').append(bt);
  if (!seeking) $('seek').value = state.position_seconds;
}
function startTicker(m) {
  stopTicker();
  if (state.status !== 'playing' || document.hidden) return;
  ticker = setInterval(() => {
    if (document.hidden) {stopTicker(); return;}
    state.position_seconds += 1;
    if (m.duration_seconds) state.position_seconds = Math.min(state.position_seconds, m.duration_seconds);
    showTime(m);
  }, 1000);
}
async function status() {
  try {
    const previous = state.transcoding_movie_id;
    state = await api('/api/status'); $('conn').textContent = 'Connected';
    stopTicker();
    // A transcode started or finished: reload so "Queued" badges are current.
    if (previous !== undefined && previous !== state.transcoding_movie_id) await load();
    const m = movies.find(x => x.id === state.movie_id), active = m && state.status !== 'stopped';
    // Top-right spinner while the device is transcoding (one movie at a time).
    const optimizing = movies.find(x => x.id === state.transcoding_movie_id);
    const busyLabel = optimizing ? 'Optimizing ' + optimizing.title : 'Optimizing a movie';
    $('busy').classList.toggle('hidden', state.transcoding_movie_id == null);
    $('busy').title = busyLabel; $('busy').setAttribute('aria-label', busyLabel);
    $('bar').classList.toggle('hidden', !active); $('hero').classList.toggle('hidden', !active);
    if (active) {
      $('heroTitle').textContent = m.title;
      $('toggle').textContent = state.status === 'playing' ? '❚❚' : '▶';
      $('heroImg').src = thumb(m);
      $('seek').max = m.duration_seconds || 0; $('seek').classList.toggle('hidden', !m.duration_seconds);
      showTime(m); startTicker(m);
    }
    render();
  } catch (error) {$('conn').textContent = 'Waiting for device…';}
}
async function load() {
  try {movies = await api('/api/movies');} catch (error) {$('grid').textContent = 'Could not load movies. Reload this page to try again.'; return;}
  render();
}
// Poll gently: not at all while the tab is hidden, and less often while a
// movie plays, so the page never competes with playback on the device.
function pollStatus() {
  // The device says how often to ask: slower while it plays or transcodes.
  const delay = Math.min(30, Math.max(2, state.poll_seconds || 3)) * 1000;
  setTimeout(async () => {
    if (!document.hidden) await status();
    pollStatus();
  }, delay);
}
document.addEventListener('visibilitychange', () => {if (!document.hidden) status();});
load().then(status); pollStatus();

// ---- Settings (gear): device details and reboot ----
let settingsTimer = null;
const dur = s => {
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return (d ? d + 'd ' : '') + (h || d ? h + 'h ' : '') + m + 'm';
};
function section(title, rows) {
  const box = document.createElement('div'); box.className = 'info';
  const h = document.createElement('h4'); h.textContent = title;
  const dl = document.createElement('dl');
  for (const [name, value, tone] of rows) {
    if (value === null || value === undefined || value === '') continue;
    const dt = document.createElement('dt'); dt.textContent = name;
    const dd = document.createElement('dd'); dd.textContent = value; if (tone) dd.className = tone;
    dl.append(dt, dd);
  }
  box.append(h, dl); return box;
}
function renderSettings(info) {
  const body = $('settingsBody'); body.replaceChildren();
  const ips = (info.addresses || []).map(a => a.address + ' (' + a.interface + ')').join(', ') || info.ip_address;
  const conns = (info.connections || []).map(c => c.name + ' · ' + c.type).join(', ');
  const mem = info.memory_mb ? (info.memory_mb.total - info.memory_mb.available) + ' / ' + info.memory_mb.total + ' MB used' : null;
  const disk = d => d ? d.free + ' GB free of ' + d.total + ' GB' : null;
  const temp = info.cpu_temperature_celsius;
  const power = info.under_voltage ? ['Power', 'Under-voltage now', 'bad'] : info.throttled ? ['Power', 'CPU throttled', 'warn']
    : info.under_voltage === false ? ['Power', 'OK', 'good'] : ['Power', null];
  body.append(
    section('Network', [
      ['Hostname', info.hostname], ['Address on network', info.mdns_name],
      ['IP address', ips], ['Connection', conns],
      ['Internet', info.internet_reachable ? 'Reachable' : 'Not reachable', info.internet_reachable ? 'good' : 'warn'],
    ]),
    section('Device', [
      ['Model', info.model], ['Uptime', info.uptime_seconds != null ? dur(info.uptime_seconds) : null],
      ['CPU temperature', temp != null ? temp.toFixed(1) + ' °C' : null, temp > 75 ? 'bad' : temp > 65 ? 'warn' : ''],
      power, ['Load average', info.load_average ? info.load_average.join(' · ') : null],
      ['Memory', mem], ['Movie storage', disk(info.disk_movies_gb)], ['System storage', disk(info.disk_system_gb)],
    ]),
    section('Playback', [
      ['Status', info.playback_status], ['Movies', info.movie_count],
      ['Activity', info.activity || info.update_status],
      ['Keyboard', info.keyboards && info.keyboards.length ? info.keyboards.join(', ') : 'None detected'],
    ]),
    section('Software', [
      ['Version', info.software ? info.software.commit + ' · ' + info.software.date : null],
      ['Latest change', info.software && info.software.subject], ['API version', info.api_version],
    ]),
  );
}
async function refreshSettings() {
  try {renderSettings(await api('/api/info'));}
  catch (error) {$('settingsBody').textContent = 'Could not load device details: ' + error.message;}
}
function openSettings() {
  const panel = $('panel'); panel.replaceChildren();
  const body = document.createElement('div'); body.className = 'body';
  const h = document.createElement('h3'); h.textContent = 'Device settings';
  const details = document.createElement('div'); details.id = 'settingsBody'; details.textContent = 'Loading…';
  const note = document.createElement('p'); note.id = 'settingsNote'; note.className = 'meta';
  const row = document.createElement('div'); row.className = 'row';
  const reboot = document.createElement('button'); reboot.className = 'btn danger'; reboot.textContent = 'Reboot device';
  reboot.addEventListener('click', async () => {
    if (!confirm('Reboot the device? Playback stops and the device is unavailable for about a minute.')) return;
    try {
      await api('/api/reboot', {method:'POST'});
      note.textContent = 'Rebooting… this page reconnects when the device is back.';
    } catch (error) {note.textContent = error.message;}
  });
  const shutdown = document.createElement('button'); shutdown.className = 'btn danger'; shutdown.textContent = 'Shut down';
  shutdown.addEventListener('click', async () => {
    if (!confirm('Shut down the device? It stays off until it is unplugged and plugged back in.')) return;
    try {
      await api('/api/shutdown', {method:'POST'});
      note.textContent = 'Shutting down… wait for the activity light to stop before unplugging.';
    } catch (error) {note.textContent = error.message;}
  });
  const close = document.createElement('button'); close.className = 'btn grey'; close.textContent = 'Close';
  close.addEventListener('click', closeSheet);
  row.append(reboot, shutdown, close); body.append(h, details, note, row); panel.append(body);
  $('sheet').classList.remove('hidden');
  refreshSettings(); clearInterval(settingsTimer); settingsTimer = setInterval(() => {if (!document.hidden) refreshSettings();}, 5000);
}
$('gear').addEventListener('click', openSettings);
$('busy').addEventListener('click', openSettings);

// ---- Upload movies from this browser ----
function uploadOne(file) {
  const row = document.createElement('div'); row.className = 'upl';
  const label = document.createElement('div'); label.textContent = file.name + ' — waiting…';
  const bar = document.createElement('div'); bar.className = 'bar'; const fill = document.createElement('i'); bar.append(fill);
  row.append(label, bar); $('uploads').append(row);
  return new Promise(resolve => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/movies');
    xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
    xhr.setRequestHeader('X-Filename-Encoding', 'uri');
    const fail = message => {row.classList.add('err'); label.textContent = file.name + ' — ' + message; bar.remove(); resolve(false);};
    xhr.upload.onprogress = e => {
      if (!e.lengthComputable) return;
      const pct = Math.floor(e.loaded / e.total * 100);
      label.textContent = file.name + ' — uploading ' + pct + '%'; fill.style.width = pct + '%';
    };
    xhr.onload = () => {
      if (xhr.status === 201) {label.textContent = file.name + ' — uploaded'; fill.style.width = '100%'; setTimeout(() => row.remove(), 4000); resolve(true); return;}
      let message = 'upload failed (' + xhr.status + ')';
      try {message = JSON.parse(xhr.responseText).error || message;} catch (e) {}
      fail(message);
    };
    xhr.onerror = () => fail('connection lost');
    xhr.send(file);
  });
}
async function uploadFiles(files) {
  for (const file of files) await uploadOne(file);
  await load(); await status();
}
$('upload').addEventListener('click', () => $('file').click());
$('file').addEventListener('change', () => {uploadFiles(Array.from($('file').files)); $('file').value = '';});
