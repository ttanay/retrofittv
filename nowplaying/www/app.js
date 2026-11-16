// Now Playing TV page. Reads the bridge's read-only SSE feed and draws it.
'use strict';

const $ = (sel) => document.querySelector(sel);
const stage = $('#stage');
const player = $('#player');
const fillBar = $('#fill');
const scenes = [...document.querySelectorAll('.scene')];
scenes[1].innerHTML = scenes[0].innerHTML;

let current = null;     // latest snapshot
let front = 0;          // index of the visible scene
let shownKey = null;    // track shown in the visible scene
let pendingKey = null;  // track being loaded into the hidden scene
let progress = null;    // {pos, dur} for the progress bar
let progressKey = null;

// The layout is 1920x1080; zoom it to whatever the screen is (720p, 1080p, 4K).
function fit() {
  stage.style.zoom = Math.min(innerWidth / 1920, innerHeight / 1080);
}

function rgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

function fmt(ms) {
  const s = Math.floor(Math.max(0, ms) / 1000);
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

function setSrc(img, url) {
  if (url) { if (img.getAttribute('src') !== url) img.src = url; } else img.removeAttribute('src');
}

function fill(scene, s) {
  const t = s.track;
  const base = (t.palette && t.palette.base) || '#2a2a2a';
  scene.style.setProperty('--base', base);
  scene.querySelector('.tint').style.background =
    `linear-gradient(180deg, ${rgba(base, .85)} 0%, ${rgba(base, .55)} 40%, rgba(18, 18, 18, .95) 90%)`;
  setSrc(scene.querySelector('.bg'), t.bg);
  setSrc(scene.querySelector('.cover img'), t.art);
  scene.querySelector('.context').textContent = s.context ? `${s.context.label} · ${s.context.name}` : '';
  const title = scene.querySelector('.title');
  title.textContent = t.title;
  // Step down to 60px if the title needs more than 2 lines at 72px (measured unclamped).
  title.classList.remove('small');
  title.classList.add('measure');
  if (title.offsetHeight > 2 * 80 + 4) title.classList.add('small');
  title.classList.remove('measure');
  const subtitle = scene.querySelector('.subtitle');
  subtitle.textContent = t.subtitle;
  if (t.explicit) {  // like Spotify, the "E" badge sits before the artist
    const e = document.createElement('span');
    e.className = 'explicit';
    e.textContent = 'E';
    subtitle.prepend(e);
  }
  scene.querySelector('.collection').textContent = t.collection;
}

// Load the new track into the hidden scene, wait for its images, then crossfade.
async function showTrack(s) {
  const key = `${s.track.key}|${s.track.art}`;
  if (key === pendingKey) return;
  if (key === shownKey) { fill(scenes[front], s); return; }
  pendingKey = key;
  const back = scenes[1 - front];
  fill(back, s);
  await Promise.all([...back.querySelectorAll('img[src]')].map((img) => img.decode().catch(() => {})));
  if (pendingKey !== key) return;  // a newer track arrived meanwhile
  back.classList.add('shown');
  scenes[front].classList.remove('shown');
  front = 1 - front;
  shownKey = key;
  pendingKey = null;
}

function positionNow() {
  if (!progress || !progress.pos) return 0;
  const { position_ms, timestamp_ms, speed } = progress.pos;
  return Math.min(Math.max(position_ms + (Date.now() - timestamp_ms) * speed, 0), progress.dur || 0);
}

// Jump the bar to "now", then let one CSS transition run it to the end.
function setProgress(pos, dur) {
  const key = JSON.stringify([pos, dur]);
  if (key === progressKey) return;
  progressKey = key;
  progress = { pos, dur };
  const p = positionNow();
  fillBar.style.transition = 'none';
  fillBar.style.transform = `scaleX(${dur ? p / dur : 0})`;
  if (pos && pos.speed > 0 && dur) {
    void fillBar.offsetWidth;  // apply the jump before starting the transition
    fillBar.style.transition = `transform ${(dur - p) / pos.speed}ms linear`;
    fillBar.style.transform = 'scaleX(1)';
  }
}

function render(s) {
  current = s;
  document.body.dataset.screen = s.screen;
  $('#device-name').textContent = s.device_name || '';
  const lp = s.last_played;
  $('#last-played').textContent = lp ? `Last played · ${lp.title}${lp.subtitle ? ' — ' + lp.subtitle : ''}` : '';
  if (s.track) {
    player.classList.toggle('ad', s.track.kind === 'ad');
    showTrack(s);
    setProgress(s.position, s.track.duration_ms);
  }
  if (s.up_next) {
    setSrc($('#upnext img'), s.up_next.thumb);
    $('#upnext .text').textContent = [s.up_next.title, s.up_next.subtitle].filter(Boolean).join(' · ');
  }
  tick();
}

// Once a second: time labels, Up Next card, clock.
function tick() {
  const now = new Date();
  $('#clock').textContent = now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  $('#date').textContent = now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
  if (!current || !current.track) return;
  const dur = current.track.duration_ms, pos = positionNow();
  $('#elapsed').textContent = fmt(pos);
  $('#total').textContent = fmt(dur);
  const showNext = current.screen === 'playing' && current.up_next && dur >= 60000 && dur - pos <= 20000;
  $('#upnext').classList.toggle('shown', Boolean(showNext));
}

let volumeTimer;
function showVolume(v) {
  $('.vfill').style.transform = `scaleX(${v / 100})`;
  $('#volume').classList.add('shown');
  clearTimeout(volumeTimer);
  volumeTimer = setTimeout(() => $('#volume').classList.remove('shown'), 2000);
}

// Move the idle clock a little every minute so it doesn't burn in.
function drift() {
  const r = () => Math.round(Math.random() * 48 - 24);
  $('.drift').style.transform = `translate(${r()}px, ${r()}px)`;
}

function connect() {
  const events = new EventSource('/events');
  let offTimer;
  events.addEventListener('state', (e) => { clearTimeout(offTimer); render(JSON.parse(e.data)); });
  events.addEventListener('volume', (e) => showVolume(JSON.parse(e.data).volume));
  events.onerror = () => {
    clearTimeout(offTimer);
    offTimer = setTimeout(() => { document.body.dataset.screen = 'off'; }, 10000);
    if (events.readyState === EventSource.CLOSED) setTimeout(() => location.reload(), 5000);
  };
}

fit();
addEventListener('resize', fit);
setInterval(tick, 1000);
setInterval(drift, 60000);
tick();
connect();
