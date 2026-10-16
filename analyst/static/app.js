'use strict';
/* ===================== helpers ===================== */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) { if (k === 'class') e.className = v; else if (k.startsWith('on')) e[k] = v; else if (v !== null && v !== undefined) e.setAttribute(k, v); }
  kids.flat().forEach(c => { if (c !== null && c !== undefined && c !== false) e.append(c); });
  return e;
};
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const isNumStr = v => typeof v === 'number' || (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v));
const fmtNum = (v, col) => { const n = Number(v); if (/(^|_)id$/i.test(col) || !isFinite(n)) return String(v); return n.toLocaleString(undefined, { maximumFractionDigits: 2 }); };
const fnum = v => v == null ? '' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
const compact = v => Math.abs(v) >= 1e6 ? +(v / 1e6).toFixed(1) + 'M' : Math.abs(v) >= 1e3 ? +(v / 1e3).toFixed(1) + 'K' : v;
const store = { get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } }, set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { } } };
const num = (v, d = 0) => (Number(v) || 0).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });   // coerces to a number, so it can never carry markup
const sleep = ms => new Promise(r => setTimeout(r, ms));

const ICON = {
  logo: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/><path d="M7.5 15h9"/>',
  bi: '<rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/>',
  dq: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  analyze: '<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  data: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>',
  pins: '<path d="M12 17v5M9 3h6l-1 7 3 3v2H7v-2l3-3z"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  print: '<path d="M6 9V3h12v6M6 18H4v-7h16v7h-2"/><rect x="6" y="14" width="12" height="7"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  sheet: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h8M12 11v10"/>',
  flask: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/><path d="M7.5 15h9"/>',
};
const IMG = { bi: 'dashboard', dq: 'market-research', analyze: 'analyze', data: 'data', pins: 'pinned', lab: 'lab_operations', custom: 'data' };
const pic = (name, cls = 'ti', size = 22) => el('img', { class: cls, src: `/static/icons/${name}.png`, alt: '', width: size, height: size, draggable: 'false' });
const emptyState = (img, title, text) => el('div', { class: 'empty-state' }, pic(img, '', 112), el('b', {}, title), el('span', {}, text));
const monthLabel = m => new Date(m + '-15').toLocaleString('en', { month: 'short', year: 'numeric' });
function stagger(root) { root.classList.remove('stagger'); [...root.children].forEach((c, i) => c.style.setProperty('--i', i)); void root.offsetWidth; root.classList.add('stagger'); }
function countUp(node, to, fmt, ms = 800) {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || document.hidden || !isFinite(to)) { node.textContent = fmt(to); return; }
  const t0 = performance.now(); node.textContent = fmt(0);
  const tick = now => { const p = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - p, 3); node.textContent = p < 1 ? fmt(to * e) : fmt(to); if (p < 1) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
}
function spark(vals, sel) {
  const ns = 'http://www.w3.org/2000/svg', w = 160, h = 36, pad = 3; let last = null;
  const v = vals.map(x => { if (x == null) return last; last = x; return x; }).map(x => x ?? 0);
  const min = Math.min(...v), max = Math.max(...v), rng = (max - min) || 1;
  const pts = v.map((x, i) => [(pad + i * (w - 2 * pad) / Math.max(1, v.length - 1)).toFixed(1), (h - pad - ((x - min) / rng) * (h - 2 * pad)).toFixed(1)]);
  const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('class', 'spark'); svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('aria-hidden', 'true');
  const pl = document.createElementNS(ns, 'polyline'); pl.setAttribute('points', pts.map(q => q.join(',')).join(' ')); svg.append(pl);
  if (sel != null && pts[sel]) { const c = document.createElementNS(ns, 'circle'); c.setAttribute('cx', pts[sel][0]); c.setAttribute('cy', pts[sel][1]); c.setAttribute('r', '2.8'); svg.append(c); }
  return svg;
}
const icon = (n, big) => { const s = el('span'); s.innerHTML = `<svg class="i" viewBox="0 0 24 24"${big ? ' style="width:30px;height:30px"' : ''}>${ICON[n]}</svg>`; return s.firstChild; };  // static, trusted markup only

function toast(msg, kind = 'info', ms = 5000) {
  const t = el('div', { class: 'toast ' + kind, role: kind === 'err' ? 'alert' : 'status' }, msg);
  $('#toasts').append(t); setTimeout(() => t.remove(), ms);
}
class ApiError extends Error { constructor(m, status, retry) { super(m); this.status = status; this.retry = retry; } }
let inflight = 0;
const bar = d => { inflight = Math.max(0, inflight + d); document.body.classList.toggle('loading', inflight > 0); };
async function failure(r, path) {
  let m = r.statusText; try { const d = (await r.json()).detail; m = typeof d === 'string' ? d : 'The request was not valid.'; } catch { }
  if (r.status === 401 && !path.startsWith('/api/auth/')) sessionExpired();
  return new ApiError(m, r.status, r.headers.get('retry-after'));
}
async function api(path, { method = 'GET', headers = {}, body } = {}) {
  bar(1);
  try {
    const opts = { method, credentials: 'same-origin', headers: { ...headers, ...(method !== 'GET' ? { 'X-Requested-With': 'labops' } : {}) } };
    if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
    const r = await fetch(path, opts);
    if (!r.ok) throw await failure(r, path);
    return await r.json();
  } finally { bar(-1); }
}

/* ===================== state ===================== */
const S = { config: {}, datasets: [], dataset: 'lab', kind: 'lab', tab: 'bi', cache: {}, abort: null, run: null, user: null };
const C = () => (S.cache[S.dataset] ??= {});
const meta = () => S.datasets.find(d => d.id === S.dataset) || {};
const qs = (path, extra = {}) => `${path}?${new URLSearchParams({ dataset: S.dataset, ...extra })}`;
const LAB_EXAMPLES = ['Why was the OOS rate on HPLC-03 so high in Mar 2025?', 'Why did Related Substances on-time delivery drop in Feb 2025?', 'Why did Documentation deviations increase in Apr 2025, and who raised them?', 'Which records need data review?'];


/* ===================== authentication, loading screens, RBAC ===================== */
const can = perm => !!(S.user && S.user.permissions && S.user.permissions.includes(perm));
let bootTimer = 0, expiring = false, authMode = 'login', pwForced = false, pwDone = null;

function setState(st, msg) {
  document.body.dataset.state = st;
  const boot = $('#boot');
  if (st === 'boot') { clearTimeout(bootTimer); boot.hidden = false; boot.classList.remove('out'); }
  else if (!boot.hidden) { boot.classList.add('out'); bootTimer = setTimeout(() => { boot.hidden = true; }, 400); }
  $('#auth').hidden = st !== 'auth'; $('.top').hidden = st !== 'app'; $('main').hidden = st !== 'app';
  if (msg) bootMsg(msg);
}
function bootMsg(m) { $('#boot-msg').textContent = m; }
function bootError(m) {
  bootMsg(m); $('#boot .boot-bar').hidden = true;
  if (!$('#boot-retry')) $('#boot .boot-card').append(el('button', { class: 'btn', id: 'boot-retry', onclick: () => location.reload() }, 'Try again'));
}
function sessionExpired() {
  if (!S.user || expiring || !S.config.auth) return;
  expiring = true; clearClientState(); showAuth('Your session has ended. Please sign in again.', 'info', 'login');
  setTimeout(() => { expiring = false; }, 800);
}
function clearClientState() {
  abortRun(); S.user = null; S.datasets = []; S.cache = {}; S.run = null; curTable = null; dqRule = null;
  try { if (typeof killCharts === 'function') killCharts(); pins.length = 0; Object.keys(localStorage).filter(k => k.startsWith('recent.') || k === 'dataset' || k === 'dash.v1').forEach(k => localStorage.removeItem(k)); } catch { }
  $$('dialog[open]').forEach(d => d.close()); $('#log').replaceChildren(); toggleMenu(false);
}

/* ---- sign-in / first-run screen ---- */
const authMsg = (msg, kind = 'err') => $('#auth-msg').replaceChildren(msg ? el('div', { class: kind }, msg) : '');
function setAuthBusy(b) { $('#au-submit').disabled = b; $('#au-submit .spinner').hidden = !b; }
async function showAuth(msg, kind = 'err', mode) {
  setState('auth');
  if (!mode) { try { mode = (await (await fetch('/api/auth/status')).json()).setup_required ? 'setup' : 'login'; } catch { mode = 'login'; } }
  authMode = mode; const setup = mode === 'setup';
  $('#auth-title').textContent = setup ? 'Create the admin account' : 'Sign in';
  $('#auth-sub').textContent = setup ? 'First run: this account becomes the administrator and can add everyone else.' : 'Welcome back. Sign in to LabOps Insight.';
  $('#au-confirm-wrap').hidden = !setup; $('#au-meter').hidden = !setup;
  $('#au-pass').autocomplete = setup ? 'new-password' : 'current-password';
  $('#au-submit .lbl').textContent = setup ? 'Create account' : 'Sign in';
  $('#auth-foot').textContent = setup ? 'Passwords are stored only as salted scrypt hashes on this computer.' : '';
  authMsg(msg, kind); $('#au-pass').value = ''; $('#au-pass2').value = ''; paintMeter($('#au-meter'), ''); setAuthBusy(false);
  setTimeout(() => ($('#au-user').value ? $('#au-pass') : $('#au-user')).focus(), 60);
}
function strength(pw) {
  let n = 0; if (pw.length >= 10) n++; if (pw.length >= 14) n++; if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) n++; if (/\d/.test(pw)) n++; if (/[^A-Za-z0-9]/.test(pw)) n++;
  if (new Set(pw).size < 5) n = Math.min(n, 1); return Math.min(n, 4);
}
function paintMeter(m, pw) {
  const s = pw ? strength(pw) : 0; m.querySelector('i').style.width = (s / 4 * 100) + '%';
  m.querySelector('i').style.background = `var(${['--err', '--err', '--warn', '--ok', '--ok'][s]})`; m.querySelector('span').textContent = pw ? ['', 'Weak', 'Fair', 'Good', 'Strong'][s] : '';
}
function lockout(btn, labelEl, seconds) {                      // disable the button with a live countdown after a 429
  let left = Math.max(1, Math.ceil(seconds)); const label = labelEl.textContent; btn.disabled = true;
  const tick = () => { labelEl.textContent = `Try again in ${left}s`; if (left-- <= 0) { clearInterval(iv); btn.disabled = false; labelEl.textContent = label; authMsg(''); } };
  const iv = setInterval(tick, 1000); tick();
}
$('#au-show').onclick = () => { const p = $('#au-pass'), show = p.type === 'password'; p.type = show ? 'text' : 'password'; $('#au-pass2').type = p.type; $('#au-show').textContent = show ? 'Hide' : 'Show'; };
$('#au-pass').oninput = () => { if (authMode === 'setup') paintMeter($('#au-meter'), $('#au-pass').value); };
$('#auth-form').onsubmit = async e => {
  e.preventDefault();
  const username = $('#au-user').value.trim(), password = $('#au-pass').value;
  if (!username || !password) return authMsg('Enter your username and password.');
  if (authMode === 'setup' && password !== $('#au-pass2').value) return authMsg('The two passwords do not match.');
  setAuthBusy(true); authMsg('');
  try {
    const r = await api(authMode === 'setup' ? '/api/auth/setup' : '/api/auth/login', { method: 'POST', body: { username, password } });
    $('#au-pass').value = ''; $('#au-pass2').value = ''; setAuthBusy(false);
    await afterSignIn(r.user);
  } catch (err) {
    setAuthBusy(false); $('#au-pass').value = ''; authMsg(err.message);
    if (err.status === 429) lockout($('#au-submit'), $('#au-submit .lbl'), Number(err.retry) || 5); else $('#au-pass').focus();
  }
};
async function afterSignIn(user) {
  S.user = user; setState('boot', 'Signing you in…');
  try {
    if (user.must_change) { bootMsg('Choose a new password to continue…'); await openPw(true); S.user = (await api('/api/auth/status')).user; }
    await initApp();
  } catch (e) { if (e.message !== 'signed out') bootError('Could not finish signing in: ' + e.message); }
}
async function initApp() {
  setState('boot'); $('#boot .boot-bar').hidden = false; $('#boot-retry')?.remove(); bootMsg('Loading your workspace…');
  try {
    S.config = await api('/api/config');
    $('#model').textContent = S.config.demo ? 'DEMO · scripted model' : S.config.model; $('#model').classList.toggle('demo', !!S.config.demo);
    $('#py').textContent = S.config.python ? 'python sandbox on' : 'python sandbox off';
    bootMsg('Loading datasets…'); await refreshDatasets(); paintUser();
    bootMsg('Preparing your dashboard…'); await applyDataset();
    setState('app');
  } catch (e) { if (e.status !== 401) bootError('Could not load the app: ' + e.message); }
}

/* ---- user menu ---- */
function paintUser() {
  const local = !S.config.auth; $('#usermenu').hidden = local; $('#local-badge').hidden = !local; $('#btn-upload').hidden = !can('upload');
  if (local || !S.user) return;
  const u = S.user; $('#avatar').textContent = u.username[0]; $('#user-name').textContent = u.username; $('#um-name').textContent = u.username;
  const rp = $('#um-role'); rp.textContent = u.role; rp.className = 'rolepill ' + u.role; $('#um-users').hidden = !can('users'); $('#um-audit').hidden = !can('audit');
}
function toggleMenu(open) {
  const m = $('#user-menu'); m.hidden = open === undefined ? !m.hidden : !open; $('#btn-user').setAttribute('aria-expanded', String(!m.hidden));
  if (m.hidden) return;
  m.style.left = ''; m.style.right = '0'; const r = m.getBoundingClientRect();         // keep the dropdown on screen whichever edge the button sits near
  if (r.left < 8) { m.style.left = '0'; m.style.right = 'auto'; } else if (r.right > innerWidth - 8) { m.style.right = '0'; }
  m.querySelector('button')?.focus();
}
$('#btn-user').onclick = e => { e.stopPropagation(); toggleMenu(); };
document.addEventListener('click', e => { if (!e.target.closest('#usermenu')) toggleMenu(false); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') toggleMenu(false); });
$('#um-pw').onclick = () => { toggleMenu(false); openPw(false).catch(() => { }); };
$('#um-users').onclick = () => { toggleMenu(false); openUsers(); };
$('#um-audit').onclick = () => { toggleMenu(false); openAudit(); };
$('#um-logout').onclick = () => doLogout();
async function doLogout() { try { await api('/api/auth/logout', { method: 'POST' }); } catch { } clearClientState(); showAuth('You have been signed out.', 'info', 'login'); }

/* ---- change password (can be forced on first sign-in or after an admin reset) ---- */
const dlgPw = $('#dlg-pw'); const pwMsg = (m, k = 'err') => $('#pw-msg').replaceChildren(m ? el('div', { class: k }, m) : '');
function openPw(forced) {
  return new Promise((resolve, reject) => {
    pwForced = forced; pwDone = { resolve, reject }; pwMsg('');
    $('#pw-note').textContent = forced ? 'You must choose a new password before continuing.' : ''; $('#pw-x').textContent = forced ? 'Sign out' : '✕';
    ['#pw-cur', '#pw-new', '#pw-new2'].forEach(s => { $(s).value = ''; }); paintMeter($('#pw-meter'), ''); dlgPw.showModal(); setTimeout(() => $('#pw-cur').focus(), 60);
  });
}
dlgPw.addEventListener('cancel', e => { if (pwForced) e.preventDefault(); });
$('#pw-x').onclick = async () => { if (pwForced) { dlgPw.close(); await doLogout(); pwDone?.reject(new Error('signed out')); } else { dlgPw.close(); pwDone?.resolve(false); } };
$('#pw-new').oninput = () => paintMeter($('#pw-meter'), $('#pw-new').value);
$('#pw-form').onsubmit = async e => {
  e.preventDefault(); const cur = $('#pw-cur').value, nw = $('#pw-new').value;
  if (!cur || !nw) return pwMsg('Fill in all the fields.');
  if (nw !== $('#pw-new2').value) return pwMsg('The new passwords do not match.');
  $('#pw-submit').disabled = true; $('#pw-submit .spinner').hidden = false; pwMsg('');
  try { await api('/api/auth/password', { method: 'POST', body: { current: cur, new: nw } }); pwForced = false; dlgPw.close(); toast('Password updated. Other devices were signed out.', 'ok'); pwDone?.resolve(true); }
  catch (err) { pwMsg(err.message); }
  finally { $('#pw-submit').disabled = false; $('#pw-submit .spinner').hidden = true; }
};

/* ---- admin: users + audit log ---- */
const dlgUsers = $('#dlg-users'), dlgReset = $('#dlg-reset'), dlgAudit = $('#dlg-audit');
const adminMsg = (id, m, k = 'err') => $(id).replaceChildren(m ? el('div', { class: k }, m) : '');
const ago = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'never';
async function openUsers() { adminMsg('#us-msg', ''); dlgUsers.showModal(); await loadUsers(); }
async function loadUsers() {
  try {
    const d = await api('/api/users'); $('#us-role').replaceChildren(...d.roles.map(r => el('option', { value: r, ...(r === 'viewer' ? { selected: '' } : {}) }, r)));
    $('#us-body').replaceChildren(...d.users.map(u => {
      const me = u.id === S.user.id, sel = el('select', { 'aria-label': `Role of ${u.username}`, ...(me ? { disabled: '' } : {}) }, ...d.roles.map(r => el('option', { value: r, ...(r === u.role ? { selected: '' } : {}) }, r)));
      sel.onchange = async () => { try { await api(`/api/users/${u.id}`, { method: 'PATCH', body: { role: sel.value } }); toast(`${u.username} is now ${sel.value}. They must sign in again.`, 'ok'); } catch (e) { adminMsg('#us-msg', e.message); } loadUsers(); };
      const del = el('button', { class: 'btn sm danger', type: 'button' }, 'Delete'); let armed = false;
      del.onclick = async () => { if (!armed) { armed = true; del.textContent = 'Confirm'; setTimeout(() => { armed = false; del.textContent = 'Delete'; }, 3500); return; } try { await api(`/api/users/${u.id}`, { method: 'DELETE' }); toast(`Deleted ${u.username}`, 'ok', 3000); } catch (e) { adminMsg('#us-msg', e.message); } loadUsers(); };
      const toggle = el('button', { class: 'btn sm', type: 'button', onclick: async () => { try { await api(`/api/users/${u.id}`, { method: 'PATCH', body: { disabled: !u.disabled } }); } catch (e) { adminMsg('#us-msg', e.message); } loadUsers(); } }, u.disabled ? 'Enable' : 'Disable');
      const reset = el('button', { class: 'btn sm', type: 'button', onclick: () => openReset(u) }, 'Reset password');
      return el('tr', {}, el('td', {}, u.username + (me ? ' (you)' : ''), u.must_change ? el('span', { class: 'note', style: 'margin-left:6px' }, 'must change password') : null), el('td', {}, sel),
        el('td', {}, el('span', { class: 'status-dot' + (u.disabled ? ' off' : '') }), u.disabled ? 'disabled' : 'active'), el('td', {}, ago(u.last_login)), el('td', {}, me ? '' : el('div', { class: 'acts' }, reset, toggle, del)));
    }));
  } catch (e) { adminMsg('#us-msg', e.message); }
}
$('#us-x').onclick = () => dlgUsers.close();
$('#us-form').onsubmit = async e => {
  e.preventDefault(); adminMsg('#us-msg', '');
  try { const u = await api('/api/users', { method: 'POST', body: { username: $('#us-name').value, password: $('#us-pass').value, role: $('#us-role').value, must_change: true } }); $('#us-name').value = ''; $('#us-pass').value = ''; adminMsg('#us-msg', `Created ${u.username} (${u.role}). They will choose their own password at first sign-in.`, 'ok'); loadUsers(); }
  catch (err) { adminMsg('#us-msg', err.message); }
};
let resetTarget = null;
function openReset(u) { resetTarget = u; adminMsg('#rs-msg', ''); $('#rs-note').textContent = `Set a temporary password for ${u.username}. All their sessions end and they must choose a new password at next sign-in.`; $('#rs-pw').value = ''; dlgReset.showModal(); setTimeout(() => $('#rs-pw').focus(), 60); }
$('#rs-x').onclick = () => dlgReset.close();
$('#rs-form').onsubmit = async e => { e.preventDefault(); try { await api(`/api/users/${resetTarget.id}`, { method: 'PATCH', body: { password: $('#rs-pw').value } }); dlgReset.close(); toast(`Password reset for ${resetTarget.username}.`, 'ok'); loadUsers(); } catch (err) { adminMsg('#rs-msg', err.message); } };
const AUDIT_ACTIONS = ['', 'login_success', 'login_failed', 'login_locked', 'logout', 'setup', 'password_change', 'dataset_upload', 'dataset_delete', 'ask', 'user_create', 'user_update', 'user_delete', 'denied'];
async function openAudit() { $('#ad-filter').replaceChildren(...AUDIT_ACTIONS.map(a => el('option', { value: a }, a || 'All actions'))); dlgAudit.showModal(); await loadAudit(); }
async function loadAudit() {
  try {
    const f = $('#ad-filter').value, d = await api('/api/audit?' + new URLSearchParams({ limit_: 200, ...(f ? { action: f } : {}) }));
    $('#ad-body').replaceChildren(...(d.events.length ? d.events.map(e => el('tr', {}, el('td', {}, ago(e.ts)), el('td', {}, e.username || ''), el('td', {}, el('span', { class: 'rolepill ' + (/fail|lock|denied/.test(e.action) ? 'admin' : 'viewer') }, e.action)), el('td', { class: 'wrap' }, e.detail || ''), el('td', {}, e.ip || ''))) : [el('tr', {}, el('td', { colspan: '5', class: 'note' }, 'No events yet.'))]));
  } catch (e) { toast(e.message, 'err'); }
}
$('#ad-x').onclick = () => dlgAudit.close(); $('#ad-refresh').onclick = loadAudit; $('#ad-filter').onchange = loadAudit;
[dlgUsers, dlgReset, dlgAudit].forEach(d => d.addEventListener('click', e => { if (e.target === d) d.close(); }));

/* ---- loading overlay for slow views ---- */
let busyCount = 0, busyTimer = 0;
function trackBusy(p, label = 'Loading…') {
  if (!p || !p.then) return; busyCount++; clearTimeout(busyTimer);
  busyTimer = setTimeout(() => { if (busyCount > 0) { $('#busy-msg').textContent = label; $('#busy').hidden = false; } }, 250);
  p.finally(() => { busyCount = Math.max(0, busyCount - 1); if (!busyCount) { clearTimeout(busyTimer); $('#busy').hidden = true; } }).catch(() => { });
}

/* ===================== theme ===================== */
function applyUiTheme(t) {
  if (t) { document.documentElement.dataset.theme = t; store.set('ui.theme', t); }
  const dark = (document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')) === 'dark';
  $('#btn-theme').replaceChildren(icon(dark ? 'sun' : 'moon'));
}
const BI_THEMES = [['th-glass', 'Glass'], ['th-mint', 'Mint'], ['th-corp', 'Corporate'], ['th-navy', 'Navy']];
function setBiTheme(t) {
  $$('.bi').forEach(b => { b.className = 'bi ' + t; });
  $$('.theme-sel').forEach(s => { s.value = t; });
  store.set('bi.theme2', t);
}
const bc = v => getComputedStyle($('#bi')).getPropertyValue(v).trim();
const rgba = (rgbVar, a) => `rgba(${bc(rgbVar)},${a})`;

/* ===================== tabs ===================== */
const TABS = ['bi', 'dq', 'analyze', 'data', 'pins'];
function tabLabel(t) { return { bi: S.kind === 'lab' ? 'Dashboard' : 'Overview', dq: 'Data Review', analyze: 'Analyze', data: 'Data', pins: 'Pinned' }[t]; }
function buildTabs() {
  $$('#tabs button').forEach(b => {
    const t = b.dataset.tab;
    b.replaceChildren(...[pic(IMG[t]), el('span', {}, tabLabel(t)), t === 'pins' ? el('span', { class: 'n', id: 'pincount', hidden: '' }, '0') : null].filter(Boolean));
    b.onclick = () => showTab(t);
    b.onkeydown = e => { const i = TABS.indexOf(t); if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { const n = TABS[(i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length]; showTab(n); $(`[data-tab=${n}]`).focus(); } };
  });
  savePins(); moveInd();
}
function moveInd() {
  const ind = $('#tab-ind'), tabs = $('#tabs'), b = $('#tabs button[aria-selected=true]');
  if (!ind || !tabs || !b) return;
  const tr = tabs.getBoundingClientRect(), br = b.getBoundingClientRect();
  if (br.width > 0 && br.height > 0) {
    ind.style.width = br.width + 'px';
    ind.style.height = br.height + 'px';
    ind.style.left = (br.left - tr.left + tabs.scrollLeft) + 'px';
    ind.style.top = (br.top - tr.top + tabs.scrollTop) + 'px';
  }
}
addEventListener('resize', moveInd);
window.addEventListener('load', moveInd);
if (document.fonts && document.fonts.ready) { document.fonts.ready.then(() => { moveInd(); requestAnimationFrame(moveInd); }); }
const loaders = { bi: () => S.kind === 'lab' ? loadBI() : loadOverview(), dq: () => S.kind === 'lab' ? loadDQ() : loadReview(), data: loadTables, pins: renderPins, analyze: () => { } };
function showTab(t) {
  S.tab = t;
  $$('#tabs button').forEach(b => { const on = b.dataset.tab === t; b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1; });
  TABS.forEach(x => { $('#tab-' + x).hidden = x !== t; });
  moveInd(); requestAnimationFrame(moveInd); setTimeout(moveInd, 50); setTimeout(moveInd, 200);
  if (S.shown !== t) { const sec = $('#tab-' + t); sec.classList.remove('tab-in'); void sec.offsetWidth; sec.classList.add('tab-in'); S.shown = t; }
  try { history.replaceState(null, '', '#' + t); } catch { }
  const p = loaders[t](); trackBusy(p); return p;
}
function refreshTab() { showTab(S.tab); }

/* ===================== skeleton / errors ===================== */
function skeleton(root) { root.replaceChildren(el('div', { class: 'skel-row' }, el('div', { class: 'skel' }), el('div', { class: 'skel' }), el('div', { class: 'skel' }), el('div', { class: 'skel' })), el('div', { class: 'skel tall' })); }
function showError(root, msg, retry) { root.replaceChildren(el('div', { class: 'banner' }, msg, ' ', retry ? el('button', { class: 'link', onclick: retry }, 'Retry') : null)); }

/* ===================== spreadsheet-style grid ===================== */
function csvCell(v) { let s = v == null ? '' : String(v); if (!isNumStr(s) && /^[=+\-@\t\r]/.test(s)) s = "'" + s; return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
function downloadCsv(name, cols, rows) {
  const blob = new Blob([[cols, ...rows].map(r => r.map(csvCell).join(',')).join('\n')], { type: 'text/csv' });
  const a = el('a', { href: URL.createObjectURL(blob), download: name + '.csv' }); a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function grid(cols, rows, { onSort, name = 'data', note = '', sortBy = null, sortDesc = false } = {}) {
  const numeric = cols.map((_, i) => rows.length > 0 && rows.every(r => r[i] == null || isNumStr(r[i])));
  let sortCol = sortBy ? cols.indexOf(sortBy) : -1, desc = sortDesc, q = '';
  const info = el('span'), filter = el('input', { type: 'text', placeholder: 'Filter rows…', 'aria-label': 'Filter rows' });
  const thead = el('thead'), tbody = el('tbody');
  const view = () => {
    let r = rows.filter(row => !q || row.some(c => String(c ?? '').toLowerCase().includes(q)));
    if (sortCol >= 0 && !onSort) r = [...r].sort((a, b) => { const x = a[sortCol], y = b[sortCol]; if (x == null) return 1; if (y == null) return -1; const c = numeric[sortCol] ? Number(x) - Number(y) : String(x).localeCompare(String(y)); return desc ? -c : c; });
    return r;
  };
  const paint = () => {
    const r = view();
    tbody.replaceChildren(...r.map(row => el('tr', {}, ...row.map((c, i) => el('td', { class: numeric[i] ? 'n' : '' }, c == null ? '' : numeric[i] ? fmtNum(c, cols[i]) : String(c))))));
    thead.replaceChildren(el('tr', {}, ...cols.map((c, i) => el('th', { class: numeric[i] ? 'n' : '', title: 'Click to sort', onclick: () => { desc = sortCol === i ? !desc : false; sortCol = i; if (onSort) onSort(c, desc); paint(); } }, c + (sortCol === i ? (desc ? ' ▼' : ' ▲') : '')))));
    info.textContent = (q ? `${r.length} of ${rows.length}` : `${rows.length}`) + ' rows' + (note ? ' · ' + note : '');
  };
  filter.oninput = () => { q = filter.value.trim().toLowerCase(); paint(); };
  paint();
  return el('div', { class: 'grid' },
    el('div', { class: 'gbar' }, filter, info, el('span', { class: 'sp' }), el('button', { class: 'link', onclick: () => downloadCsv(name, cols, view()) }, 'Download CSV')),
    el('div', { class: 'gscroll' }, el('table', {}, thead, tbody)));
}

/* ===================== charts for Analyze + Pinned ===================== */
const palette = () => Array.from({ length: 12 }, (_, i) => css(`--c${i + 1}`));
let pins = store.get('dash.v1', []);
const savePins = () => { store.set('dash.v1', pins); const n = $('#pincount'); if (n) { n.textContent = pins.length; n.hidden = !pins.length; } };
const pinKey = s => JSON.stringify([s.type, s.title, s.labels, s.datasets, s.cards]);
function drawChart(canvas, s) {
  applyChartDefaults();
  const PAL = palette(), mute = css('--mute'), line = css('--line'), text = css('--text');
  const multi = s.datasets.length > 1, round = s.type === 'pie' || s.type === 'doughnut';
  const type = { hbar: 'bar', area: 'line' }[s.type] || s.type;
  const datasets = s.datasets.map((d, i) => {
    const c = PAL[i % PAL.length];
    if (round) return { label: d.label, data: d.data, backgroundColor: s.labels.map((_, j) => PAL[j % PAL.length]), borderColor: css('--panel'), borderWidth: 3, hoverOffset: 10 };
    if (s.type === 'scatter') return { label: d.label, data: d.data, backgroundColor: c, pointRadius: 4 };
    if (type === 'line') return { label: d.label, data: d.data, borderColor: c, backgroundColor: c + (s.type === 'area' ? '44' : ''), fill: s.type === 'area', tension: .25, pointRadius: 3, spanGaps: true };
    return { label: d.label, data: d.data, backgroundColor: c, borderRadius: 6, borderSkipped: false };
  });
  const axis = title => ({ ticks: { color: mute }, grid: { color: line }, title: { display: !!title, text: title || '', color: mute } });
  return new Chart(canvas, { type, data: { labels: s.labels, datasets }, options: { responsive: true, maintainAspectRatio: false, animation: { duration: 400 }, indexAxis: s.type === 'hbar' ? 'y' : 'x',
    plugins: { legend: { display: multi || round, labels: { color: text } }, tooltip: { callbacks: { label: c => { const v = c.parsed.y ?? c.parsed.x ?? c.parsed; const n = typeof v === 'object' ? '' : v; return `${c.dataset.label || c.label}: ${Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`; } } } },
    scales: round ? {} : { x: axis(s.type === 'hbar' ? s.ylabel : s.xlabel), y: axis(s.type === 'hbar' ? s.xlabel : s.ylabel) } } });
}
function chartCard(s, { dash = false } = {}) {
  const head = el('div', { class: 'chead' }, el('h3', {}, s.title));
  const card = el('div', { class: 'card' }, head);
  const pinBtn = el('button', { class: 'link' });
  const sync = () => { pinBtn.textContent = dash ? 'Unpin' : pins.some(p => pinKey(p) === pinKey(s)) ? 'Pinned ✓' : 'Pin to dashboard'; };
  pinBtn.onclick = () => { const i = pins.findIndex(p => pinKey(p) === pinKey(s)); if (i >= 0) pins.splice(i, 1); else if (!dash) pins.push(s); savePins(); sync(); if (dash) renderPins(); };
  sync();
  if (s.type === 'kpi') {
    card.append(el('div', { class: 'kpis' }, ...s.cards.map(c => el('div', { class: 'kpi' }, el('div', { class: 'v' }, typeof c.value === 'number' ? c.value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(c.value)), el('div', { class: 'l' }, c.label)))));
    head.append(pinBtn); return card;
  }
  if (typeof Chart === 'undefined') {
    const cols = ['label', ...s.datasets.map(d => d.label)];
    card.append(el('div', { class: 'sum' }, 'Chart library failed to load; showing the data instead.'), grid(cols, (s.labels || []).map((l, i) => [l, ...s.datasets.map(d => d.data[i])]), { name: s.title }));
    head.append(pinBtn); return card;
  }
  const canvas = el('canvas', { role: 'img', 'aria-label': s.title });
  head.append(el('button', { class: 'link', onclick: () => { const a = el('a', { href: canvas.toDataURL('image/png'), download: s.title.replace(/\W+/g, '_') + '.png' }); a.click(); } }, 'PNG'), pinBtn);
  card.append(el('div', { class: 'cv' }, canvas));
  requestAnimationFrame(() => drawChart(canvas, s));
  return card;
}
function renderPins() {
  $('#dash').replaceChildren(...(pins.length ? pins.map(s => chartCard(s, { dash: true })) : [emptyState('pinned', 'Nothing pinned yet', 'Ask a question, then click “Pin to dashboard” on a chart.')]));
}

/* ===================== themed (BI) chart helpers ===================== */
const isDark = () => (document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')) === 'dark';
function applyChartDefaults() {
  if (typeof Chart === 'undefined') return;
  const D = Chart.defaults, dark = isDark();
  D.font.family = '-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Inter,Roboto,sans-serif'; D.font.size = 12.5; D.font.weight = '500';
  D.elements.line.borderWidth = 3.5; D.elements.line.borderCapStyle = 'round'; D.elements.line.borderJoinStyle = 'round';
  D.elements.point.radius = 4; D.elements.point.hoverRadius = 8; D.elements.point.borderWidth = 2.5; D.elements.point.borderColor = dark ? 'rgba(255,255,255,.95)' : '#ffffff';
  D.elements.bar.borderRadius = 6; D.elements.bar.borderSkipped = false;
  const T = D.plugins.tooltip; T.backgroundColor = 'rgba(4,10,22,.96)'; T.titleColor = '#ffffff'; T.bodyColor = '#f1f5ff'; T.borderColor = 'rgba(255,255,255,.32)'; T.borderWidth = 1;
  T.padding = 11; T.cornerRadius = 12; T.boxPadding = 5; T.titleFont = { size: 13, weight: '700' }; T.bodyFont = { size: 13, weight: '600' };
}
if (typeof Chart !== 'undefined') Chart.register({   // soft neon glow on lines in dark mode makes them pop off the glass
  id: 'glow',
  beforeDatasetDraw(c, a) { if (!isDark()) return; const ds = c.data.datasets[a.index]; if ((ds.type || c.config.type) !== 'line') return; c.ctx.save(); c.ctx.shadowColor = ds.borderColor; c.ctx.shadowBlur = 18; },
  afterDatasetDraw(c, a) { if (!isDark()) return; const ds = c.data.datasets[a.index]; if ((ds.type || c.config.type) !== 'line') return; c.ctx.restore(); },
});
const bcharts = [];
const killCharts = () => bcharts.splice(0).forEach(c => c.destroy());
const axisOpts = (extra = {}) => ({ ticks: { color: bc('--b-mute'), ...(extra.num ? { callback: extra.fmt || (v => compact(v)) } : {}), ...(extra.ticks || {}) }, grid: { color: bc('--b-border'), drawOnChartArea: !extra.noGrid }, border: { display: false }, ...(extra.min !== undefined ? { min: extra.min } : {}), ...(extra.max !== undefined ? { max: extra.max } : {}) });
const legend = () => ({ labels: { color: bc('--b-text'), usePointStyle: true, boxWidth: 9, boxHeight: 9, padding: 14, font: { size: 12.5, weight: '700' } } });
function chartBox(title, cls, build) {
  applyChartDefaults();
  const canvas = el('canvas'), card = el('div', { class: 'bcard ' + cls }, el('h3', {}, title), el('div', { class: 'bcv' }, canvas));
  if (typeof Chart === 'undefined') card.append(el('div', { class: 'kp' }, 'Chart library failed to load (offline?).'));
  else requestAnimationFrame(() => bcharts.push(build(canvas)));
  return card;
}
function kc(label, value, cls, sub) { return el('div', { class: 'bcard kcard' }, el('h3', {}, label), el('div', { class: 'kv ' + (cls || '') }, value), sub ? el('div', { class: 'kp' }, sub) : null); }
function insightRow(i) {
  const row = el('div', { class: 'insight ' + i.level }, el('i', { class: 'ins-dot ' + i.level }), el('span', {}, i.text));
  if (i.ask) row.append(el('button', { class: 'link', onclick: () => askAgent(i.ask) }, 'Investigate →'));
  else if (i.goto) row.append(el('button', { class: 'link', onclick: () => showTab(i.goto) }, 'Open Data Review →'));
  return row;
}
function askAgent(q) { if (!can('ask')) return toast(`Your role (${S.user?.role}) cannot run analyses. Ask an administrator for the analyst role.`, 'warn'); $('#q').value = q; showTab('analyze'); $('#f').requestSubmit(); }

/* ===================== lab dashboard ===================== */
const cnt = v => v >= 1e4 ? `${(v / 1e3).toFixed(1)}K` : Math.round(v).toLocaleString();
const FMT = { count: cnt, pct: v => v.toFixed(1) + '%', days: v => v.toFixed(2) + ' d' };
function dirClass(delta, good) { if (delta == null || Math.abs(delta) < 0.05) return 'flat'; return (delta > 0) === (good === 'up') ? 'good' : 'bad'; }
const activate = fn => e => { if (e.type === 'click' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } };
function kpiCard(k, P, T) {
  const cls = dirClass(k.delta, k.good), arrow = k.delta == null || Math.abs(k.delta) < 0.05 ? '' : (k.delta > 0 ? '↑' : '↓');
  const d = k.delta == null ? '—' : `${k.delta > 0 ? '+' : ''}${k.delta.toFixed(1)}${k.delta_kind === 'pp' ? ' pp' : '%'}`;
  const series = { tests: T.tests, oos_rate: T.oos_rate, on_time: T.on_time, tat: T.tat }[k.key];
  const val = el('span', { class: 'val' }); countUp(val, k.value, FMT[k.fmt]);
  const ask = () => askAgent(P.prev_label ? `Why did ${k.label.toLowerCase()} change in ${P.label} compared with ${P.prev_label}?` : `Summarize ${k.label.toLowerCase()} in ${P.label}`);
  const delta = k.prev == null ? null : el('span', { class: 'dchip ' + cls }, (arrow ? arrow + ' ' : '') + d);
  return el('div', { class: 'bcard kcard click', role: 'button', tabindex: '0', title: 'Click to ask the analyst about this metric', onclick: activate(ask), onkeydown: activate(ask) }, el('h3', {}, k.label),
    el('div', { class: 'kv' }, val, delta), el('div', { class: 'kp' }, k.prev == null ? 'no prior period' : `${P.prev_label}: ${FMT[k.fmt](k.prev)}`),
    series ? spark(series, T.selected) : null);
}
const gaugeColor = v => v >= 92 ? '--b-good' : v >= 85 ? '--b-warn' : '--b-bad';
function gaugeCard(g, prevLabel) {
  const canvas = el('canvas'), cv = gaugeColor(g.value);
  const card = el('div', { class: 'bcard gauge' }, el('h3', {}, g.label), el('div', { class: 'gwrap' }, canvas, el('div', { class: 'gval', style: `color:${bc(cv)}` }, g.value.toFixed(1) + '%')), el('div', { class: 'gprev' }, g.prev == null ? '' : `${prevLabel}: ${g.prev.toFixed(1)}%`));
  if (typeof Chart !== 'undefined') requestAnimationFrame(() => bcharts.push(new Chart(canvas, { type: 'doughnut', data: { datasets: [{ data: [g.value, 100 - g.value], backgroundColor: [bc(cv), bc('--b-track')], borderWidth: 0 }] },
    options: { rotation: -90, circumference: 180, cutout: '68%', responsive: true, maintainAspectRatio: false, animation: { duration: 500 }, plugins: { legend: { display: false }, tooltip: { enabled: false } } } })));
  return card;
}
function renderBI(d) {
  C().bi = d; killCharts();
  const f = d.filters, P = d.period, T = d.trend, sel = T.selected;
  $('#bi-h').textContent = `Lab Operations — ${P.label}`;
  $('#bi-sub').textContent = (P.prev_label ? `vs ${P.prev_label}` : 'first month in data') + ` · ${f.lab || 'All labs'} · ${f.service_line || 'All service lines'} · synthetic, anonymised data`;
  $('#bi-ins').replaceChildren(...d.insights.map(i => insightRow({ ...i, goto: i.ask ? null : 'dq' })));
  $('#bi-k').replaceChildren(...d.kpis.map(x => kpiCard(x, P, T)), ...d.gauges.map(x => gaugeCard(x, P.prev_label)));
  stagger($('#bi-k'));
  const hl = (base, hi) => T.labels.map((_, i) => i === sel ? hi : base);
  const pickMonth = idx => { const m = T.months[idx]; if (!m || m === f.month) return; $('#f-month').value = m; reloadBI(); toast(`Showing ${T.labels[idx]}`, 'info', 1800); };
  const hover = (e, els) => { e.native.target.style.cursor = els.length ? 'pointer' : 'default'; };
  const monthClick = { onClick: (e, els) => { if (els.length) pickMonth(els[0].index); }, onHover: hover };
  const volume = chartBox('Tests completed & OOS rate', 's7', cv => new Chart(cv, { data: { labels: T.labels, datasets: [
    { type: 'bar', order: 2, label: 'Tests completed', data: T.tests, yAxisID: 'y', backgroundColor: hl(bc('--b-a1'), bc('--b-a2')), borderRadius: 7, borderSkipped: false },
    { type: 'line', order: 1, label: 'OOS rate (%)', data: T.oos_rate, yAxisID: 'y1', borderColor: bc('--b-bad'), backgroundColor: bc('--b-bad'), tension: .3, pointRadius: hl(4, 9), pointHoverRadius: 10 }] },
    options: { ...monthClick, responsive: true, maintainAspectRatio: false, animation: { duration: 600 }, interaction: { mode: 'index', intersect: false }, plugins: { legend: legend(), tooltip: { callbacks: { label: c => c.dataset.yAxisID === 'y1' ? `OOS rate: ${c.parsed.y.toFixed(2)}%` : `Tests: ${c.parsed.y.toLocaleString()}` } } },
      scales: { x: axisOpts({ ticks: { maxRotation: 55 } }), y: axisOpts({ num: true }), y1: { position: 'right', min: 0, ticks: { color: bc('--b-mute'), callback: v => v + '%' }, grid: { drawOnChartArea: false }, border: { display: false } } } } }));
  const delivery = chartBox('On-time delivery & turnaround', 's5', cv => new Chart(cv, { type: 'line', data: { labels: T.labels, datasets: [
    { label: 'On-time delivery (%)', data: T.on_time, yAxisID: 'y', borderColor: bc('--b-a1'), backgroundColor: bc('--b-a1'), tension: .3, pointRadius: hl(4, 9) },
    { label: 'Avg turnaround (days)', data: T.tat, yAxisID: 'y1', borderColor: bc('--b-a2'), backgroundColor: bc('--b-a2'), borderDash: [7, 5], tension: .3, pointRadius: hl(3, 8) }] },
    options: { ...monthClick, responsive: true, maintainAspectRatio: false, animation: { duration: 600 }, interaction: { mode: 'index', intersect: false }, plugins: { legend: legend() },
      scales: { x: axisOpts({ ticks: { maxRotation: 55 } }), y: axisOpts({ num: true, fmt: v => v + '%', min: 80, max: 100 }), y1: { position: 'right', ticks: { color: bc('--b-mute'), callback: v => (+v.toFixed(2)) + ' d' }, grid: { drawOnChartArea: false }, border: { display: false } } } } }));
  const cats = d.deviation_categories;
  const dev = chartBox(`Deviations opened by category — ${P.label}`, 's6', cv => new Chart(cv, { type: 'bar', data: { labels: cats.map(c => c.label), datasets: [{ label: P.label, data: cats.map(c => c.value), backgroundColor: bc('--b-a1'), borderRadius: 7, borderSkipped: false }, ...(P.prev_label ? [{ label: P.prev_label, data: cats.map(c => c.prev), backgroundColor: bc('--b-mute') + '88', borderRadius: 7, borderSkipped: false }] : [])] },
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: 600 }, onHover: hover, onClick: (e, els) => { if (els.length) askAgent(`Why did ${cats[els[0].index].label} deviations ${cats[els[0].index].value >= cats[els[0].index].prev ? 'increase' : 'change'} in ${P.label}, and who raised them?`); }, plugins: { legend: legend() }, scales: { x: axisOpts({}), y: axisOpts({ num: true, fmt: v => Number.isInteger(v) ? v : '' }) } } }));
  const M = d.methods;
  const meth = chartBox(`Avg turnaround by method (days) — ${P.label}`, 's6', cv => new Chart(cv, { type: 'bar', data: { labels: M.map(m => m.label), datasets: [{ data: M.map(m => m.tat), backgroundColor: M.map(m => m.on_time != null && m.on_time < 85 ? bc('--b-bad') : bc('--b-a2')), borderRadius: 7, borderSkipped: false }] },
    options: { indexAxis: 'y', onHover: hover, onClick: (e, els) => { if (els.length) askAgent(`Why is ${M[els[0].index].label} turnaround ${M[els[0].index].on_time != null && M[els[0].index].on_time < 85 ? 'so slow' : 'what it is'} in ${P.label}? Check on-time delivery.`); }, responsive: true, maintainAspectRatio: false, animation: { duration: 600 }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.parsed.x.toFixed(2)} days · ${M[c.dataIndex].on_time ?? '—'}% on time · ${M[c.dataIndex].tests} tests` } } }, scales: { x: axisOpts({ num: true, fmt: v => (+v.toFixed(2)) + ' d' }), y: axisOpts({ noGrid: true }) } } }));
  const it = el('div', { class: 'bcard s6' }, el('h3', {}, `Instruments — ${P.label}`), el('table', { class: 'btab inst' },
    el('thead', {}, el('tr', {}, ...['Instrument', 'Tests', 'Utilization', 'OOS %', 'Δ pp'].map((h, i) => el('th', { class: i === 1 || i === 3 || i === 4 ? 'num' : '' }, h)))),
    el('tbody', {}, ...d.instruments.map(r => el('tr', { class: 'click', title: 'Click to ask the analyst about this instrument', onclick: () => askAgent(`What happened with ${r.instrument} in ${P.label}? Check its OOS rate and downtime.`) }, el('td', { title: `${r.type} · ${r.lab}` }, r.instrument), el('td', { class: 'num' }, r.tests.toLocaleString()),
      el('td', {}, el('span', { class: 'dtrack' }, el('div', { class: 'dbar', style: `width:${Math.max(2, r.utilization)}%;background:${r.utilization >= 95 ? bc('--b-bad') : bc('--b-a1')}` })), r.utilization.toFixed(0) + '%'),
      el('td', { class: 'num ' + (r.oos_rate >= 10 ? 'bad' : '') }, r.oos_rate.toFixed(1)),
      el('td', { class: 'num ' + dirClass(r.oos_delta, 'down') }, r.oos_delta == null ? '—' : `${r.oos_delta > 0 ? '+' : ''}${r.oos_delta.toFixed(1)}`))))));
  const H = d.heatmap;
  const heat = el('div', { class: 'bcard s6' }, el('h3', {}, 'OOS rate by instrument, last 6 months (ignores filters)'),
    el('table', { class: 'btab heat' }, el('thead', {}, el('tr', {}, el('th', {}, 'Instrument'), ...H.months.map(m => el('th', { class: 'num' }, m)))),
      el('tbody', {}, ...H.rows.map(row => el('tr', {}, el('td', { title: row.lab }, row.instrument), ...row.cells.map(c => {
        const a = c.rate == null ? 0 : Math.min(c.rate / 25, 1) * .8;
        return el('td', { class: 'cell' + (c.rate == null ? ' na' : ''), style: c.rate == null ? '' : `background:${rgba('--b-bad-rgb', a.toFixed(2))}`, title: `${c.oos} OOS of ${c.tests} tests` }, c.rate == null ? '—' : c.rate.toFixed(1) + '%');
      }))))));
  $('#bi-g').replaceChildren(volume, delivery, dev, meth, it, heat); stagger($('#bi-g'));
}
async function loadBI() {
  const c = C();
  if (c.bi && !c.biDirty) return renderBI(c.bi);
  if (!c.bi) { $('#bi-k').replaceChildren(...skeleton2()); $('#bi-g').replaceChildren(); }
  const p = new URLSearchParams({ dataset: S.dataset });
  [['month', '#f-month'], ['lab', '#f-lab'], ['service_line', '#f-svc']].forEach(([k, s]) => { const v = $(s).value; if (v) p.set(k, v); });
  try {
    const d = await api('/api/dashboard?' + p);
    if (!c.biOpts) {
      c.biOpts = d.options;
      $('#f-month').replaceChildren(...d.options.months.map(m => el('option', { value: m }, new Date(m + '-15').toLocaleString('en', { month: 'short', year: 'numeric' }))));
      $('#f-lab').replaceChildren(el('option', { value: '' }, 'All labs'), ...d.options.labs.map(x => el('option', { value: x }, x)));
      $('#f-svc').replaceChildren(el('option', { value: '' }, 'All service lines'), ...d.options.service_lines.map(x => el('option', { value: x }, x)));
      $('#f-month').value = d.filters.month;
    }
    renderBI(d);
  } catch (e) { showError($('#bi-g'), 'Could not load the dashboard: ' + e.message, loadBI); }
}
const skeleton2 = () => [1, 2, 3, 4].map(() => el('div', { class: 'skel', style: 'height:92px' }));
function reloadBI() { C().biDirty = true; return loadBI().then(() => { C().biDirty = false; }); }
['#f-month', '#f-lab', '#f-svc'].forEach(s => { $(s).onchange = reloadBI; });
$('#f-reset').onclick = () => { $('#f-lab').value = ''; $('#f-svc').value = ''; $('#f-month').value = '2025-03'; reloadBI(); };
$('#f-ask').onclick = () => { const P = C().bi?.period; if (!P) return; askAgent(P.prev_label ? `What changed in lab operations in ${P.label} compared with ${P.prev_label}? Check the OOS rate, on-time delivery and deviations.` : `Summarize lab performance in ${P.label}`); };

/* ===================== lab data review ===================== */
let dqRule = null;
async function loadDQ() {
  const c = C();
  if (c.dq) return renderDQ(c.dq);
  skeleton($('#dq-g'));
  try { c.dq = await api('/api/dq'); renderDQ(c.dq); } catch (e) { showError($('#dq-g'), 'Could not load the data review: ' + e.message, loadDQ); }
}
function renderDQ(s) {
  const crit = s.issues_by_severity.Critical;
  $('#dq-k').replaceChildren(kc('Clean test records', s.clean_pct.toFixed(1) + '%', s.clean_pct >= 99 ? 'good' : 'bad'), kc('Records needing review', s.flagged_tests.toLocaleString()), kc('Critical issues', crit.toLocaleString(), crit ? 'bad' : 'good'), kc('Rules failing', `${s.open_rules} of ${s.rules.length}`, s.open_rules ? 'bad' : 'good'));
  const tbl = el('div', { class: 'bcard s12' }, el('h3', {}, 'Checks'), el('table', { class: 'btab' },
    el('thead', {}, el('tr', {}, ...['Rule', 'Check', 'ALCOA+', 'Severity', 'Issues', '% of records', 'Result', ''].map((h, i) => el('th', { class: i === 4 || i === 5 ? 'num' : '' }, h)))),
    el('tbody', {}, ...s.rules.map(r => el('tr', { class: dqRule === r.id ? 'sel' : '', title: r.description }, el('td', {}, r.id), el('td', {}, r.title), el('td', {}, r.alcoa),
      el('td', {}, el('span', { class: 'pill ' + r.severity }, r.severity)), el('td', { class: 'num' }, r.count.toLocaleString()), el('td', { class: 'num' }, r.pct.toFixed(2) + '%'),
      el('td', {}, el('span', { class: 'pill ' + r.status }, r.status)), el('td', {}, r.count ? el('button', { class: 'link', onclick: () => showRule(r.id) }, 'View query list') : ''))))));
  $('#dq-g').replaceChildren(tbl, el('div', { id: 'dq-list', class: 's12' }));
  if (dqRule) showRule(dqRule, true);
}
async function showRule(id, quiet) {
  dqRule = id;
  try {
    const d = await api(`/api/dq/${encodeURIComponent(id)}?limit=200`);
    $$('#dq-g .btab tbody tr').forEach(tr => tr.classList.toggle('sel', tr.firstChild.textContent === id));
    const box = el('div', { class: 'bcard', style: 'margin-top:10px' }, el('h3', {}, `${d.rule.id} · ${d.rule.title}: ${d.total} record${d.total === 1 ? '' : 's'}` + (d.total > d.limit ? ` (showing first ${d.limit})` : '')),
      el('div', { class: 'kp', style: 'text-align:left;margin-bottom:6px' }, 'Each row is a query to raise with the owning lab. Nothing has been changed in the source data.'), grid(d.columns, d.rows, { name: `${d.rule.id}_queries` }));
    $('#dq-list').replaceChildren(box); if (!quiet) box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  } catch (e) { toast('Could not load that query list: ' + e.message, 'err'); }
}

/* ===================== overview + review for uploaded data ===================== */
async function getProfile() { const c = C(); if (!c.profile) c.profile = await api(qs('/api/profile')); return c.profile; }
const when = iso => { try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); } catch { return ''; } };
const SEV_LEVEL = { Critical: 'bad', Major: 'warn', Minor: 'info' };
function askForIssue(i) { return `Check "${i.title.toLowerCase()}" in ${i.table}${i.column ? '.' + i.column : ''} and tell me how it affects the numbers.`; }
function specCard(spec) {
  return chartBox(spec.title, 's6', cv => {
    const pal = [bc('--b-a1'), bc('--b-a2'), bc('--b-a4'), bc('--b-a3'), bc('--b-a5')], isLine = spec.type === 'line', hb = spec.type === 'hbar';
    return new Chart(cv, { type: hb ? 'bar' : spec.type, data: { labels: spec.labels, datasets: spec.datasets.map((d, i) => ({ label: d.label, data: d.data, backgroundColor: isLine ? pal[i % 5] + '40' : pal[i % 5], borderColor: pal[i % 5], borderRadius: 7, borderSkipped: false, tension: .3, pointRadius: isLine ? 3 : 0, fill: isLine })) },
      options: { indexAxis: hb ? 'y' : 'x', responsive: true, maintainAspectRatio: false, animation: { duration: 400 }, plugins: { legend: { display: spec.datasets.length > 1, ...legend() }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${Number(c.parsed.y ?? c.parsed.x).toLocaleString(undefined, { maximumFractionDigits: 2 })}` } } },
        scales: { x: axisOpts(hb ? { num: true } : { ticks: { maxRotation: 45, autoSkip: true } }), y: axisOpts(hb ? { noGrid: true } : { num: true }) } } });
  });
}
function columnProfile(t) {
  const row = c => {
    let vals;
    if (c.kind === 'numeric') vals = el('span', {}, `${fnum(c.min)} – ${fnum(c.max)} · mean ${fnum(c.mean)} · median ${fnum(c.median)}`);
    else if (c.kind === 'date') vals = el('span', {}, `${(c.min || '').slice(0, 10)} → ${(c.max || '').slice(0, 10)}`);
    else if (c.top) vals = el('div', { class: 'topvals' }, ...c.top.map(([v, n]) => el('span', {}, el('b', {}, String(v)), ` ${n.toLocaleString()}`)));
    else vals = el('span', { class: 'mute' }, `${c.distinct.toLocaleString()} distinct values`);
    const filled = 100 - c.null_pct;
    return el('tr', {}, el('td', {}, c.name), el('td', {}, c.type.toLowerCase()), el('td', {}, el('span', { class: 'dtrack' }, el('div', { class: 'dbar', style: `width:${Math.max(2, filled)}%;background:${filled < 80 ? bc('--b-bad') : bc('--b-a1')}` })), filled.toFixed(0) + '%'), el('td', { class: 'num' }, c.distinct.toLocaleString()), el('td', { class: 'wrap' }, vals));
  };
  return el('div', { class: 'bcard s12', style: 'margin-top:10px' }, el('details', t.name === C().profile.tables[0].name ? { open: '' } : {}, el('summary', {}, `${t.name} · ${t.rows.toLocaleString()} rows · ${t.columns.length} columns`),
    el('div', { style: 'margin-top:8px;overflow-x:auto' }, el('table', { class: 'btab' }, el('thead', {}, el('tr', {}, ...['Column', 'Type', 'Filled', 'Distinct', 'Values'].map((h, i) => el('th', { class: i === 3 ? 'num' : '' }, h)))), el('tbody', {}, ...t.columns.map(row))))));
}
async function loadOverview() {
  const c = C(), m = meta();
  if (!c.profile) skeleton($('#ov-g'));
  try {
    const p = await getProfile(); killCharts();
    const s = p.summary, sev = s.issues_by_severity;
    $('#ov-h').textContent = m.name || 'Your data';
    $('#ov-sub').textContent = `${s.tables} table${s.tables === 1 ? '' : 's'} · ${s.rows.toLocaleString()} rows · ${s.columns} columns · added ${when(m.created)} · stored on this computer only`;
    $('#ov-notes').replaceChildren(...(m.warnings && m.warnings.length ? [el('div', { class: 'bcard', style: 'margin-bottom:10px' }, el('details', {}, el('summary', {}, `Import notes (${m.warnings.length})`), el('ul', { style: 'margin:8px 0 0;padding-left:18px;color:var(--b-mute)' }, ...m.warnings.map(w => el('li', {}, w)))))] : []));
    const worst = sev.Critical ? 'bad' : sev.Major ? 'bad' : sev.Minor ? 'flat' : 'good';
    $('#ov-k').replaceChildren(kc('Rows', s.rows.toLocaleString()), kc('Columns', String(s.columns)), kc('Cells filled', s.completeness_pct.toFixed(1) + '%', s.completeness_pct >= 95 ? 'good' : s.completeness_pct >= 80 ? '' : 'bad'),
      kc('Duplicate rows', s.duplicate_rows.toLocaleString(), s.duplicate_rows ? 'bad' : 'good'), kc('Review findings', String(s.issues), worst, s.issues ? `${sev.Critical} critical · ${sev.Major} major · ${sev.Minor} minor` : 'all clear'));
    const top = p.issues.slice(0, 3).map(i => ({ level: SEV_LEVEL[i.severity], text: `${i.title}${i.column ? ' in ' + i.table + '.' + i.column : ' in ' + i.table}: ${i.detail}`, ask: askForIssue(i) }));
    if (p.issues.length > 3) top.push({ level: 'info', text: `${p.issues.length - 3} more finding${p.issues.length - 3 === 1 ? '' : 's'} in the data review.`, goto: 'dq' });
    if (!p.issues.length) top.push({ level: 'info', text: 'No data-quality findings. Nice and clean.' });
    $('#ov-ins').replaceChildren(...top.map(insightRow));
    $('#ov-g').replaceChildren(...(p.charts.length ? p.charts.map(specCard) : [el('div', { class: 'bcard s12 kp' }, 'Not enough varied numeric, category or date columns to draw automatic charts. Use the Analyze tab or the Data tab.')]));
    $('#ov-tables').replaceChildren(...p.tables.map(columnProfile)); stagger($('#ov-k')); stagger($('#ov-g'));
  } catch (e) { showError($('#ov-g'), 'Could not profile this dataset: ' + e.message, loadOverview); }
}
$('#ov-ask').onclick = () => askAgent('Give me an overview of this data: key totals, and anything unusual I should know about.');
let rvFilter = 'All', rvText = '';
async function loadReview() {
  const root = $('#rv-body'); if (!C().profile) skeleton(root);
  try { renderReview(await getProfile()); } catch (e) { showError(root, 'Could not review this dataset: ' + e.message, loadReview); }
}
function renderReview(p) {
  const s = p.summary, sev = s.issues_by_severity;
  $('#rv-k').replaceChildren(kc('Findings', String(s.issues), s.issues ? '' : 'good'), kc('Critical', String(sev.Critical), sev.Critical ? 'bad' : 'good'), kc('Major', String(sev.Major), sev.Major ? 'bad' : 'good'), kc('Minor', String(sev.Minor)), kc('Cells filled', s.completeness_pct.toFixed(1) + '%', s.completeness_pct >= 95 ? 'good' : ''));
  const root = $('#rv-body');
  if (!p.issues.length) { root.replaceChildren(emptyState('market-research', 'All clear', 'Every check passed on this data.')); return; }
  const draw = () => {
    const list = p.issues.filter(i => (rvFilter === 'All' || i.severity === rvFilter) && (!rvText || JSON.stringify(i).toLowerCase().includes(rvText)));
    body.replaceChildren(el('table', { class: 'btab' }, el('thead', {}, el('tr', {}, ...['Severity', 'Where', 'Check', 'Affected', 'What it means', ''].map((h, i) => el('th', { class: i === 3 ? 'num' : '' }, h)))),
      el('tbody', {}, ...list.map(i => el('tr', {}, el('td', {}, el('span', { class: 'pill ' + i.severity }, i.severity)), el('td', {}, i.table + (i.column ? '.' + i.column : '')), el('td', {}, `${i.id} · ${i.title}`),
        el('td', { class: 'num' }, `${i.count.toLocaleString()} (${i.pct}%)`), el('td', { class: 'wrap' }, i.detail, i.examples.length ? el('span', { class: 'ex' }, 'e.g. ' + i.examples.slice(0, 3).join(' · ')) : null),
        el('td', {}, el('button', { class: 'link', onclick: () => askAgent(askForIssue(i)) }, 'Ask')))))), list.length ? null : el('div', { class: 'kp', style: 'padding:14px' }, 'No findings match.'));
  };
  const body = el('div', { style: 'overflow-x:auto' });
  const chips = ['All', 'Critical', 'Major', 'Minor'].map(k => el('button', { class: 'chip', 'aria-pressed': String(rvFilter === k), onclick: e => { rvFilter = k; chips.forEach(c => c.setAttribute('aria-pressed', c === e.currentTarget)); draw(); } }, k === 'All' ? `All (${p.issues.length})` : `${k} (${sev[k]})`));
  const search = el('input', { type: 'text', placeholder: 'Search findings…', 'aria-label': 'Search findings', style: 'height:30px;font-size:12.5px;width:200px', oninput: e => { rvText = e.target.value.trim().toLowerCase(); draw(); } });
  const csv = el('button', { class: 'link', onclick: () => downloadCsv('data_review_findings', ['severity', 'rule', 'table', 'column', 'check', 'affected_rows', 'affected_pct', 'detail', 'examples'], p.issues.map(i => [i.severity, i.id, i.table, i.column || '', i.title, i.count, i.pct, i.detail, i.examples.join(' | ')])) }, 'Download findings CSV');
  root.replaceChildren(el('div', { class: 'bcard' }, el('div', { class: 'chips', style: 'margin:0 0 10px' }, ...chips, el('span', { class: 'sp' }), search, csv), body));
  draw();
}

/* ===================== data browser ===================== */
let curTable = null, tstate = { offset: 0, sort: null, desc: false };
async function loadTables() {
  const c = C();
  if (c.tables) return paintTables();
  try { c.tables = await api(qs('/api/tables')); paintTables(); } catch (e) { showError($('#datagrid'), 'Could not load tables: ' + e.message, loadTables); }
}
function paintTables() {
  const t = C().tables;
  $('#tables').replaceChildren(...t.map(x => { const b = el('button', { class: 'chip', 'aria-pressed': String(curTable === x.name), onclick: () => { curTable = x.name; tstate = { offset: 0, sort: null, desc: false }; $$('#tables .chip').forEach(c => c.setAttribute('aria-pressed', c === b)); showTable(); } }, `${x.name} (${x.rows.toLocaleString()})`); return b; }));
  if (!curTable || !t.some(x => x.name === curTable)) { curTable = null; $('#datagrid').replaceChildren(emptyState('data', 'Browse your tables', 'Choose a table above to explore it like a spreadsheet.')); if (t.length === 1) $('#tables .chip').click(); } else showTable();
}
async function showTable() {
  const { offset, sort, desc } = tstate, L = 100;
  try {
    const d = await api(qs(`/api/table/${encodeURIComponent(curTable)}`, { limit: L, offset, ...(sort ? { sort, desc } : {}) }));
    const last = Math.min(offset + d.rows.length, d.total);
    const pager = el('div', { class: 'gbar', style: 'border:0;padding:8px 0' }, el('button', { class: 'link', disabled: offset === 0 ? '' : null, onclick: () => { tstate.offset = Math.max(0, offset - L); showTable(); } }, '← Prev'), el('span', {}, `${d.total ? offset + 1 : 0}–${last} of ${d.total.toLocaleString()}`),
      el('button', { class: 'link', disabled: last >= d.total ? '' : null, onclick: () => { tstate.offset = offset + L; showTable(); } }, 'Next →'), el('span', { class: 'sp' }), el('span', {}, sort ? `sorted by ${sort} ${desc ? 'desc' : 'asc'} (whole table)` : 'click a header to sort the whole table'));
    $('#datagrid').replaceChildren(pager, grid(d.columns, d.rows, { name: d.name, sortBy: sort, sortDesc: desc, onSort: (c, ds) => { tstate.sort = c; tstate.desc = ds; tstate.offset = 0; showTable(); } }));
  } catch (e) { showError($('#datagrid'), 'Could not load the table: ' + e.message, showTable); }
}
$('#clearpins').onclick = () => { pins.length = 0; savePins(); renderPins(); };

/* ===================== analyze ===================== */
function md(s) {
  const h = esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\*(.+?)\*/g, '<i>$1</i>').replace(/`(.+?)`/g, '<code>$1</code>');
  return h.split(/\n{2,}/).map(p => /^\s*- /m.test(p) ? '<ul>' + p.split('\n').filter(x => x.trim()).map(x => `<li>${x.replace(/^\s*- /, '')}</li>`).join('') + '</ul>' : `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
}
const LABELS = { get_schema: 'Schema', run_sql: 'SQL', run_python: 'Python', chart: 'Chart', data_quality: 'Data review' };
function addStep(ev) {
  const code = ev.input.sql || ev.input.code || '';
  const head = ev.name === 'chart' ? `${ev.input.type || ''}: ${ev.input.title || ''}` : ev.name === 'data_quality' ? 'run rule-based data review' : code.split('\n')[0] || 'inspect tables';
  const d = el('details', { class: 'step', open: '' }, el('summary', {}, el('span', { class: 'tag' }, LABELS[ev.name] || ev.name), el('span', { class: 'sum' }, head), el('span', { class: 'spin' })), el('div', { class: 'body' }, code ? el('pre', {}, code) : null, el('div', { class: 'res' })));
  $('#log').append(d); d.scrollIntoView({ block: 'nearest' }); return d;
}
function finishStep(d, ev) {
  d.querySelector('.spin')?.remove();
  const res = d.querySelector('.res');
  if (ev.is_error) { d.querySelector('.tag').classList.add('err'); res.append(el('div', { class: 'err-text' }, ev.text)); return; }
  if (ev.table) res.append(grid(ev.table.columns, ev.table.rows, { name: 'query_result', note: ev.table.truncated ? 'truncated at 200' : '' }));
  else if (ev.chart) { res.append(chartCard(ev.chart)); d.querySelector('pre')?.remove(); }
  else res.append(el('pre', {}, ev.text));
  (ev.files || []).filter(f => f.endsWith('.png')).forEach(f => res.append(el('img', { src: '/artifacts/' + encodeURIComponent(f), alt: 'chart', style: 'max-width:100%;border-radius:8px;margin-top:10px;background:#fff' })));
  d.open = ev.name !== 'get_schema';
}
function reportMarkdown(run) {
  const m = meta(), L = [`# ${run.q}`, '', `*Dataset: ${m.name || S.dataset} · ${new Date().toLocaleString()}*`, ''];
  run.events.forEach(ev => {
    if (ev.kind === 'tool_call') { const code = ev.input.sql || ev.input.code; L.push(`### ${LABELS[ev.name] || ev.name}${ev.name === 'chart' ? ': ' + (ev.input.title || '') : ''}`); if (code) L.push('```' + (ev.input.sql ? 'sql' : 'python'), code, '```'); }
    if (ev.kind === 'tool_result' && !ev.is_error && ev.name !== 'get_schema' && ev.name !== 'chart') L.push('```', ev.text.slice(0, 1500), '```');
    if (ev.kind === 'tool_result' && ev.is_error) L.push(`> Error: ${ev.text.slice(0, 300)}`);
  });
  L.push('', '## Answer', '', run.answer, '', '---', '*Generated by LabOps Insight. Verify important figures against the source data.*');
  return L.join('\n');
}
function answerActions(run) {
  return el('div', { class: 'stats no-print', style: 'margin:10px 0 0' },
    el('button', { class: 'btn sm', onclick: async () => { try { await navigator.clipboard.writeText(run.answer); toast('Answer copied', 'ok', 2000); } catch { toast('Copy failed: select the text and copy manually', 'warn'); } } }, 'Copy answer'),
    el('button', { class: 'btn sm', onclick: () => { const a = el('a', { href: URL.createObjectURL(new Blob([reportMarkdown(run)], { type: 'text/markdown' })), download: 'investigation.md' }); a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); } }, 'Download report (.md)'));
}
const recentKey = () => 'recent.' + S.dataset;
function remember(q) { const r = [q, ...store.get(recentKey(), []).filter(x => x !== q)].slice(0, 5); store.set(recentKey(), r); paintChips(); }
function paintChips() {
  const ex = S.kind === 'lab' ? LAB_EXAMPLES : (meta().examples || []), rec = store.get(recentKey(), []);
  const mk = t => el('button', { type: 'button', class: 'chip', onclick: () => { if (!can('ask')) return; $('#q').value = t; $('#f').requestSubmit(); } }, t);
  const fresh = rec.filter(r => !ex.includes(r));
  $('#chips').replaceChildren(...ex.map(mk), ...(fresh.length ? [el('span', { class: 'lbl', style: 'margin-left:8px' }, 'Recent:'), ...fresh.map(mk)] : []));
}
function setBusy(b) { $('#go').disabled = b || !can('ask'); $('#stop').hidden = !b; $('#go').textContent = b ? 'Working…' : 'Analyze'; }
let waitTimer = 0;
function clearWait() { clearInterval(waitTimer); waitTimer = 0; $('#wait-note')?.remove(); }
function showWait(ev) {                                        // live notice while the model provider rate-limits us (so a wait never looks like a freeze)
  clearWait(); let left = Number(ev.seconds) || 5;
  const note = el('div', { class: 'notice', id: 'wait-note', role: 'status' });
  const paint = () => { note.textContent = `${ev.status === 429 ? 'The model provider is rate limiting this request (normal on free tiers)' : 'The model provider returned an error'}. Retrying in ${left}s (attempt ${num(ev.attempt)} of ${num(ev.of)})…`; };
  paint(); $('#log').append(note); waitTimer = setInterval(() => { left = Math.max(0, left - 1); paint(); }, 1000);
}
function abortRun() { clearWait(); if (S.abort) { S.abort.abort(); S.abort = null; } }
async function streamAsk(q, onEvent, signal) {            // fetch-based SSE: unlike EventSource it exposes 401/429 and can really be cancelled
  bar(1);
  try {
    const r = await fetch('/api/ask?' + new URLSearchParams({ q, dataset: S.dataset }), { signal, credentials: 'same-origin', headers: { Accept: 'text/event-stream' } });
    if (!r.ok) throw await failure(r, '/api/ask');
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let i;
      while ((i = buf.indexOf('\n\n')) >= 0) { const chunk = buf.slice(0, i); buf = buf.slice(i + 2); const line = chunk.split('\n').find(l => l.startsWith('data: ')); if (line) onEvent(JSON.parse(line.slice(6))); }
    }
  } finally { bar(-1); }
}
async function runQuestion(q) {
  if (!can('ask')) return toast('Your role cannot run analyses.', 'warn');
  abortRun();
  const log = $('#log'); log.replaceChildren(); $('#an-empty').replaceChildren(); setBusy(true); remember(q);
  const run = S.run = { q, events: [], answer: '', t0: performance.now() };
  let cur = null; const ctl = S.abort = new AbortController();
  const handle = ev => {
    if (ev.kind === 'waiting') return showWait(ev);
    clearWait(); run.events.push(ev);
    if (ev.kind === 'tool_call') cur = addStep(ev);
    else if (ev.kind === 'tool_result' && cur) finishStep(cur, ev);
    else if (ev.kind === 'answer') { run.answer = ev.text; log.append(el('section', { class: 'answer' })); const a = log.lastChild; a.innerHTML = `<h2>Answer</h2>${md(ev.text)}`; a.append(answerActions(run)); }
    else if (ev.kind === 'stopped') log.append(el('div', { class: 'banner' }, `Stopped early: ${ev.reason} limit reached. The agent did not finish.`));
    else if (ev.kind === 'error') log.append(el('div', { class: 'banner' }, ev.message));
    else if (ev.kind === 'done') log.insertAdjacentHTML('beforeend', `<div class="stats"><span><b>${num((performance.now() - run.t0) / 1000, 1)}s</b> elapsed</span><span><b>${num(ev.steps)}</b> steps</span><span><b>${num(ev.sql_calls)}</b> SQL (${num(ev.sql_errors)} errors)</span><span><b>${num(ev.chart_calls)}</b> charts</span><span><b>${num(ev.python_calls)}</b> Python</span><span><b>${num(Number(ev.in_tok) + Number(ev.out_tok))}</b> tokens</span></div>`);
  };
  try { await streamAsk(q, handle, ctl.signal); }
  catch (e) {
    if (e.name === 'AbortError') return;
    log.append(el('div', { class: 'banner' }, e.status === 429 ? e.message : e.status === 401 ? 'Your session has ended. Please sign in again.' : e.status === 403 ? 'Your role cannot run analyses.' : `Could not run the analysis: ${e.message}`));
  } finally { clearWait(); if (S.abort === ctl) { S.abort = null; setBusy(false); } }
}
$('#f').onsubmit = e => { e.preventDefault(); const q = $('#q').value.trim(); if (q) runQuestion(q); };
$('#stop').onclick = () => { abortRun(); setBusy(false); $('#log').append(el('div', { class: 'banner' }, 'Stopped. The analysis was cancelled in this view.')); };

/* ===================== datasets + upload dialog ===================== */
const dlg = $('#dlg');
function fmtBytes(b) { return b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1e3)) + ' KB'; }
async function refreshDatasets(selectId) {
  S.datasets = await api('/api/datasets');
  const stored = selectId || store.get('dataset', 'lab');
  S.dataset = S.datasets.some(d => d.id === stored) ? stored : 'lab';
  $('#ds-select').replaceChildren(...S.datasets.map(d => el('option', { value: d.id }, d.name)));
  $('#ds-select').value = S.dataset;
  paintDatasetList();
}
function paintDatasetList() {
  const ups = S.datasets.filter(d => d.kind === 'custom');
  $('#dlist').replaceChildren(...(ups.length ? ups.map(d => {
    const mine = d.owner_id === S.user?.id, deletable = can('delete_any') || (can('delete_own') && mine);
    const del = deletable ? el('button', { class: 'btn sm danger', type: 'button' }, 'Delete') : null;
    let armed = false, timer;
    if (del) del.onclick = async () => {
      if (!armed) { armed = true; del.textContent = 'Click again to confirm'; timer = setTimeout(() => { armed = false; del.textContent = 'Delete'; }, 3500); return; }
      clearTimeout(timer);
      try { await api(`/api/datasets/${d.id}`, { method: 'DELETE' }); delete S.cache[d.id]; const was = S.dataset === d.id; await refreshDatasets(was ? 'lab' : S.dataset); toast(`Deleted “${d.name}”`, 'ok', 3000); if (was) applyDataset(); } catch (e) { toast('Could not delete: ' + e.message, 'err'); }
    };
    return el('div', { class: 'drow' }, pic('data', '', 30), el('div', { class: 't' }, el('b', {}, d.name), el('small', {}, `${d.tables.length} table${d.tables.length === 1 ? '' : 's'} · ${d.tables.reduce((a, t) => a + t.rows, 0).toLocaleString()} rows · ${fmtBytes(d.bytes)} · added ${when(d.created)}${!mine && d.owner_name ? ' · owner ' + d.owner_name : ''}`)),
      el('button', { class: 'btn sm', type: 'button', onclick: () => { S.dataset = d.id; $('#ds-select').value = d.id; store.set('dataset', d.id); dlg.close(); applyDataset(); } }, 'Open'), del);
  }) : [el('div', { class: 'note' }, 'Nothing uploaded yet. Your uploads will appear here.')]));
}
const upMsg = (kind, text) => { $('#up-msg').replaceChildren(text ? el('div', { class: kind === 'err' ? 'err-box' : 'ok-box' }, text) : ''); };
function uploadFile(file) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest(); x.open('POST', '/api/datasets');
    x.setRequestHeader('X-Filename', encodeURIComponent(file.name)); x.setRequestHeader('X-Requested-With', 'labops');
    x.upload.onprogress = e => { if (e.lengthComputable) $('#prog > i').style.width = (100 * e.loaded / e.total) + '%'; };
    x.upload.onload = () => upMsg('ok', 'Uploaded. Reading the sheets and building tables…');
    x.onload = () => { let b = {}; try { b = JSON.parse(x.responseText); } catch { } if (x.status === 401) sessionExpired(); x.status < 300 ? resolve(b) : reject(new ApiError(typeof b.detail === 'string' ? b.detail : `Upload failed (${x.status})`, x.status, x.getResponseHeader('retry-after'))); };
    x.onerror = () => reject(new Error('Network error: is the server still running?'));
    x.send(file);
  });
}
async function handleFile(file) {
  if (!can('upload')) return upMsg('err', 'Your role cannot upload data. Ask an administrator for the analyst role.');
  upMsg(); const ext = (file.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
  if (!['.xlsx', '.csv'].includes(ext)) return upMsg('err', ext === '.xls' ? 'Legacy .xls files are not supported. Open it in Excel and choose Save As → .xlsx.' : ext === '.xlsm' ? 'Macro-enabled workbooks are not accepted. Save a copy as a plain .xlsx.' : 'Only .xlsx and .csv files are supported.');
  const max = S.config.max_upload_mb || 15;
  if (file.size > max * 1e6) return upMsg('err', `That file is ${(file.size / 1e6).toFixed(1)} MB; the limit is ${max} MB. Remove unused sheets or old rows and try again.`);
  $('#prog').hidden = false; $('#prog > i').style.width = '0'; $('#drop').style.pointerEvents = 'none';
  try {
    const m = await uploadFile(file);
    await refreshDatasets(m.id); store.set('dataset', m.id);
    dlg.close(); applyDataset(); showTab('bi');
    toast(`Imported “${m.name}”: ${m.tables.length} table${m.tables.length === 1 ? '' : 's'}, ${m.tables.reduce((a, t) => a + t.rows, 0).toLocaleString()} rows.` + (m.warnings.length ? ` ${m.warnings.length} import note${m.warnings.length === 1 ? '' : 's'} on the Overview.` : ''), 'ok', 7000);
  } catch (e) { upMsg('err', e.message); }
  finally { $('#prog').hidden = true; $('#drop').style.pointerEvents = ''; $('#file').value = ''; }
}
function openDialog() { upMsg(); paintDatasetList(); $('#max-mb').textContent = S.config.max_upload_mb || 15; if (!dlg.open) dlg.showModal(); }
$('#btn-upload').onclick = openDialog;
$('#dlg-x').onclick = () => dlg.close();
dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
$('#drop').onclick = () => $('#file').click();
$('#drop').onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('#file').click(); } };
$('#file').onchange = e => { if (e.target.files[0]) handleFile(e.target.files[0]); };
['dragenter', 'dragover'].forEach(t => $('#drop').addEventListener(t, e => { e.preventDefault(); $('#drop').classList.add('over'); }));
['dragleave', 'drop'].forEach(t => $('#drop').addEventListener(t, e => { e.preventDefault(); $('#drop').classList.remove('over'); }));
$('#drop').addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) handleFile(f); });
window.addEventListener('dragover', e => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) e.preventDefault(); });
window.addEventListener('drop', e => { if (e.dataTransfer && e.dataTransfer.files.length && !e.defaultPrevented) { e.preventDefault(); if (!can('upload') || document.body.dataset.state !== 'app') return toast('Your role cannot upload data.', 'warn'); openDialog(); handleFile(e.dataTransfer.files[0]); } });

/* ===================== switching datasets ===================== */
function applyDataset() {
  const m = meta(); S.kind = m.kind || 'lab'; store.set('dataset', S.dataset);
  abortRun(); setBusy(false); $('#log').replaceChildren();
  curTable = null; dqRule = null; rvFilter = 'All'; rvText = '';
  buildTabs(); $('#ds-icon').replaceChildren(pic(S.kind === 'lab' ? 'lab_operations' : 'data', 'ti', 24));
  const lab = S.kind === 'lab';
  $('#bi-lab').hidden = !lab; $('#bi-custom').hidden = lab; $('#dq-lab').hidden = !lab; $('#dq-custom').hidden = lab;
  $('#q').placeholder = lab ? 'Ask a question about lab operations, e.g. why did OOS rise in March?' : `Ask a question about “${m.name}”…`;
  $('#an-notice').replaceChildren(S.config.demo && !lab ? el('div', { class: 'notice' }, 'Demo mode can’t answer questions about your own data (its answers are scripted for the built-in lab data). The Overview, Data Review and Data tabs work now; to ask questions, start the server with a real model, e.g. ', el('code', {}, 'python -m analyst.server --provider ollama'), '.') : '');
  paintChips(); $('#an-empty').replaceChildren(emptyState('analyze', 'Ask anything about this data', 'Pick a suggestion below, or press / to jump here from anywhere.'));
  const ask = can('ask'); $('#q').disabled = !ask; $('#go').disabled = !ask; $('#chips').classList.toggle('locked', !ask);
  if (!ask) $('#an-notice').replaceChildren(el('div', { class: 'locked-note' }, `Your role (${S.user?.role}) can explore dashboards, data and reviews, but running analyses needs the analyst role. Ask an administrator to upgrade your account.`));
  return showTab(TABS.includes(S.tab) ? S.tab : 'bi');
}
$('#ds-select').onchange = e => { S.dataset = e.target.value; applyDataset(); };

/* ===================== header + init ===================== */
$('#btn-theme').onclick = () => { const dark = (document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')) === 'dark'; applyUiTheme(dark ? 'light' : 'dark'); refreshTab(); };
$('#btn-print').onclick = () => window.print();
$$('.theme-sel').forEach(s => { s.replaceChildren(...BI_THEMES.map(([v, n]) => el('option', { value: v }, n))); s.onchange = () => { setBiTheme(s.value); refreshTab(); }; });
(async function init() {
  $('#ico-upload').replaceChildren(icon('upload')); $('#btn-print').replaceChildren(icon('print')); $('#ico-search').replaceChildren(icon('search'));
  applyUiTheme(); const t = store.get('bi.theme2', 'th-glass'); setBiTheme(BI_THEMES.some(x => x[0] === t) ? t : 'th-glass');
  const hash = location.hash.slice(1); S.tab = TABS.includes(hash) ? hash : 'bi';
  setState('boot', 'Checking your session…');
  try {
    const st = await api('/api/auth/status');
    S.config.auth = st.enabled;
    if (st.enabled && !st.user) return showAuth('', 'err', st.setup_required ? 'setup' : 'login');
    S.user = st.user;
    if (st.user.must_change) return afterSignIn(st.user);
    await initApp();
  } catch (e) { bootError('Could not reach the server: ' + e.message); }
})();

/* ===================== interaction polish ===================== */
let spotRaf = 0;
document.addEventListener('pointermove', e => {
  if (spotRaf) return;
  spotRaf = requestAnimationFrame(() => {
    spotRaf = 0;
    const t = e.target.closest && e.target.closest('.card,.step,.answer,.bcard,.drow,.insight');
    if (t) { const r = t.getBoundingClientRect(); t.style.setProperty('--mx', (e.clientX - r.left) + 'px'); t.style.setProperty('--my', (e.clientY - r.top) + 'px'); }
    const a = $('.aurora'); if (a) { a.style.setProperty('--px', ((e.clientX / innerWidth - .5) * -28).toFixed(1) + 'px'); a.style.setProperty('--py', ((e.clientY / innerHeight - .5) * -28).toFixed(1) + 'px'); }
  });
}, { passive: true });

/* ---------- command palette (Cmd/Ctrl+K) ---------- */
const pal = $('#pal'); let palItems = [], palSel = 0;
function palCommands(q) {
  const cmds = [];
  TABS.forEach(t => cmds.push({ label: `Go to ${tabLabel(t)}`, sub: 'Navigate', img: IMG[t], run: () => showTab(t) }));
  S.datasets.filter(d => d.id !== S.dataset).forEach(d => cmds.push({ label: `Switch to ${d.name}`, sub: 'Dataset', img: d.kind === 'lab' ? 'lab_operations' : 'data', run: () => { S.dataset = d.id; $('#ds-select').value = d.id; applyDataset(); } }));
  if (can('upload')) cmds.push({ label: 'Upload Excel or CSV…', sub: 'Data', svg: 'upload', run: openDialog });
  if (S.config.auth) cmds.push({ label: 'Change password', sub: 'Account', svg: 'dq', run: () => openPw(false).catch(() => { }) }, { label: 'Sign out', sub: 'Account', svg: 'moon', run: doLogout });
  if (can('users')) cmds.push({ label: 'Manage users and roles', sub: 'Admin', svg: 'dq', run: openUsers });
  if (can('audit')) cmds.push({ label: 'Audit log', sub: 'Admin', svg: 'dq', run: openAudit });
  cmds.push({ label: 'Toggle light / dark', sub: 'Appearance', svg: 'moon', run: () => $('#btn-theme').click() }, { label: 'Print / save as PDF', sub: 'Export', svg: 'print', run: () => window.print() });
  BI_THEMES.forEach(([v, n]) => cmds.push({ label: `Dashboard theme: ${n}`, sub: 'Appearance', svg: 'sun', run: () => { setBiTheme(v); refreshTab(); } }));
  if (S.kind === 'lab' && C().biOpts) C().biOpts.months.forEach(m => cmds.push({ label: `Show ${new Date(m + '-15').toLocaleString('en', { month: 'long', year: 'numeric' })}`, sub: 'Month', img: 'dashboard', run: () => { $('#f-month').value = m; showTab('bi'); reloadBI(); } }));
  const text = q.trim(), terms = text.toLowerCase().split(/\s+/).filter(Boolean);
  const out = cmds.filter(c => terms.every(t => (c.label + ' ' + c.sub).toLowerCase().includes(t)));
  if (text.length >= 3 && can('ask')) {      // a short query is probably a command search; a sentence is probably a question
    const ask = { label: `Ask the analyst: ${text}`, sub: 'Analyze', img: 'analyze', run: () => askAgent(text) };
    if (out.length && terms.length < 3) out.push(ask); else out.unshift(ask);
  }
  return out.slice(0, 40);
}
function palRender() {
  const q = $('#pal-q').value; palItems = palCommands(q); palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
  $('#pal-list').replaceChildren(...(palItems.length ? palItems.map((c, i) => el('li', { role: 'option', 'aria-selected': String(i === palSel), onclick: () => palRun(i), onmousemove: () => { if (palSel !== i) { palSel = i; palMark(); } } }, c.img ? pic(c.img) : icon(c.svg), el('span', {}, c.label), el('span', { class: 'sub' }, c.sub))) : [el('li', { 'aria-selected': 'false' }, 'No matches. Type a question (3+ letters) to ask the analyst.')]));
}
function palMark() { $$('#pal-list li').forEach((li, i) => li.setAttribute('aria-selected', String(i === palSel))); $('#pal-list li[aria-selected=true]')?.scrollIntoView({ block: 'nearest' }); }
function palRun(i) { const c = palItems[i]; if (!c) return; pal.close(); c.run(); }
function openPalette() { if (!pal.open) { $('#pal-q').value = ''; palSel = 0; pal.showModal(); palRender(); $('#pal-q').focus(); } }
$('#pal-q').oninput = () => { palSel = 0; palRender(); };
$('#pal-q').onkeydown = e => {
  if (e.key === 'ArrowDown') { e.preventDefault(); palSel = Math.min(palItems.length - 1, palSel + 1); palMark(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); palSel = Math.max(0, palSel - 1); palMark(); }
  else if (e.key === 'Enter') { e.preventDefault(); palRun(palSel); }
};
pal.addEventListener('click', e => { if (e.target === pal) pal.close(); });
$('#btn-pal').onclick = openPalette;
document.addEventListener('keydown', e => {
  if (document.body.dataset.state !== 'app') return;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); pal.open ? pal.close() : openPalette(); return; }
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
  if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey && !document.querySelector('dialog[open]')) { e.preventDefault(); showTab('analyze'); $('#q').focus(); }
});
