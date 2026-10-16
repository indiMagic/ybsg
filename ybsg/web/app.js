'use strict';
/* YBSG front end. No framework, no network dependency: everything is served by the local app.
   UI strings go through t(); locales/<lang>.json can translate them (tools/extract_strings.py builds en.json). */
let LOCALE = {};
const t = (s) => LOCALE[s] || s;
const TOKEN = document.querySelector('meta[name="ybsg-token"]').content;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ls = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage can be unavailable */ } } };

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-YBSG-Token': TOKEN }, body: JSON.stringify(body) };
  const r = await fetch('/api/' + path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

const S = { brand: null, project: null, v: null, cats: null, job: null, dirty: false, sel: { cat: 'usb_host', ic: null, uid: null, view: 'board' }, cmp: [], boardView: 'cards', socs: {}, jobPoll: null, dbStatus: null };
const SOCS = {};
async function getSoc(id) { return SOCS[id] || (SOCS[id] = await api('get/soc/' + encodeURIComponent(id))); }

/* ---------- small UI helpers ---------- */
function toast(msg, kind = 'ok', ms = 4200) {
  const el = document.createElement('div'); el.className = 'toast ' + kind; el.textContent = msg; $('#toasts').append(el);
  setTimeout(() => el.remove(), ms);
}
function modal(html, onClose) {
  const m = $('#modal'); m.innerHTML = `<div class="dialog" role="dialog" aria-modal="true">${html}</div>`; m.hidden = false;
  const close = () => { m.hidden = true; m.innerHTML = ''; onClose && onClose(); };
  m.onclick = (e) => { if (e.target === m) close(); };
  m.querySelector('[data-close]')?.focus();
  m._close = close; return m;
}
function closeModal() { const m = $('#modal'); if (m._close) m._close(); else { m.hidden = true; m.innerHTML = ''; } }
const stWord = { ok: 'OK', warn: 'Warning', fail: 'Fails', error: 'Error', info: 'Note', pending: 'Pending', running: 'Running', done: 'Done', warning: 'Warning', failed: 'Failed', skipped: 'Skipped' };
const st = (s, label) => `<span class="st ${esc(s)}">${esc(label || t(stWord[s] || s))}</span>`;
const conf = (c) => `<span class="conf ${esc(c)}" title="${esc(t('Data confidence'))}">${esc(c)}</span>`;
const tip = (text, link) => `<span class="tip" tabindex="0" role="note" aria-label="${esc(text)}">?<span class="pop">${esc(text)}${link ? `<br><a href="${esc(link)}" target="_blank" rel="noopener">${t('Documentation')}</a>` : ''}</span></span>`;
const fmtTime = (s) => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const fmtBytes = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n > 1024 ? (n / 1024).toFixed(0) + ' kB' : n + ' B';
const debounce = (fn, ms) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };

/* inline help for every peripheral and driver option (UI-8) */
const HELP = {
  usb_host: ['A USB host controller drives ports for keyboards, storage and cameras. Pick a hub IC if you need more ports.', 'https://www.kernel.org/doc/html/latest/driver-api/usb/index.html'],
  usb_device: ['Gadget mode makes the board look like a USB device (serial, Ethernet, storage) through ConfigFS.', 'https://www.kernel.org/doc/html/latest/usb/gadget_configfs.html'],
  camera: ['Image sensors connect over MIPI CSI-2 or a parallel bus and appear as V4L2 devices.', 'https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/v4l2.html'],
  display: ['Native HDMI, or a DSI/RGB link through a bridge chip. Handled by DRM/KMS.', 'https://www.kernel.org/doc/html/latest/gpu/index.html'],
  mmc: ['SD, SDIO and eMMC share the MMC subsystem. eMMC is soldered and non-removable.', 'https://www.kernel.org/doc/html/latest/driver-api/mmc/index.html'],
  flash: ['SPI NOR/NAND and parallel NAND are exposed as MTD devices with fixed partitions.', 'https://www.kernel.org/doc/html/latest/driver-api/mtd/index.html'],
  ram: ['RAM type, size and bus width set the memory node. DRAM timing and training are SoC specific and must be validated on hardware.', ''],
  net: ['Ethernet PHY/MAC pairs, Wi-Fi and Bluetooth. RGMII needs the right internal delay setting.', 'https://www.kernel.org/doc/html/latest/networking/index.html'],
  audio: ['An audio codec on I2S/TDM, controlled over I2C. Uses ALSA SoC (ASoC).', 'https://www.kernel.org/doc/html/latest/sound/soc/index.html'],
  i2c: ['Sensors and expanders on I2C. Each device needs a unique address on its bus.', 'https://www.kernel.org/doc/html/latest/i2c/index.html'],
};
const ICON = {
  home: 'M3 10 10 3l7 7v7a1 1 0 0 1-1 1h-3v-5H7v5H4a1 1 0 0 1-1-1z', folder: 'M2.5 5.5h5l1.5 2h8.5v8h-15z', board: 'M3 4h14v12H3zM7 8h6v4H7z', chip: 'M6 6h8v8H6zM3 8h3M3 12h3M14 8h3M14 12h3M8 3v3M12 3v3M8 14v3M12 14v3',
  code: 'm7 6-4 4 4 4M13 6l4 4-4 4', plug: 'M7 3v4M13 3v4M5 7h10v3a5 5 0 0 1-10 0zM10 15v3', layers: 'm10 3 7 4-7 4-7-4zM3 11l7 4 7-4', check: 'm4 10 4 4 8-8', play: 'M6 4l10 6-10 6z',
  db: 'M4 5c0-1.1 2.7-2 6-2s6 .9 6 2-2.7 2-6 2-6-.9-6-2zM4 5v10c0 1.1 2.7 2 6 2s6-.9 6-2V5M4 10c0 1.1 2.7 2 6 2s6-.9 6-2', doc: 'M5 3h7l3 3v11H5zM12 3v3h3', gear: 'M10 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM10 2v2M10 16v2M2 10h2M16 10h2M4.3 4.3l1.4 1.4M14.3 14.3l1.4 1.4M4.3 15.7l1.4-1.4M14.3 5.7l1.4-1.4',
};
const NAV = [
  ['Workspace', [['dashboard', 'Dashboard', 'home'], ['projects', 'Projects', 'folder']]],
  ['Library', [['boards', 'Boards', 'board'], ['socs', 'SoCs and ICs', 'chip'], ['drivers', 'Drivers', 'code']]],
  ['Build', [['peripherals', 'Peripherals', 'plug'], ['software', 'Software', 'layers'], ['review', 'Review', 'check'], ['generate', 'Generate', 'play']]],
  ['Data', [['database', 'Local database', 'db'], ['reports', 'Reports', 'doc']]],
  ['', [['settings', 'Settings', 'gear']]],
];

/* ---------- routing ---------- */
function parseHash() { const h = location.hash.replace(/^#\/?/, ''); const [path, qs] = h.split('?'); const parts = path.split('/').filter(Boolean); return { route: parts[0] || 'dashboard', arg: parts[1] ? decodeURIComponent(parts[1]) : null, q: new URLSearchParams(qs || '') }; }
function go(path) { if (location.hash === '#/' + path) route(); else location.hash = '#/' + path; }
async function route() {
  const { route: r, arg } = parseHash();
  if (r === 'generate' && S.v && !S.v.sections?.generate) { toast(t('Generate stays disabled until Peripherals, Software and Review are valid.'), 'warn'); go('review'); return; }
  const page = PAGES[r] || PAGES.dashboard;
  renderSide(r);
  const main = $('#main'); main.innerHTML = `<p class="muted">${t('Loading')}</p>`;
  try { await page(main, arg); } catch (e) { main.innerHTML = `<div class="banner err">${st('error')}<span>${esc(e.message)}</span></div>`; }
  if (S.focusNext !== false) main.focus({ preventScroll: true });
  main.scrollTop = 0;
}
window.addEventListener('hashchange', route);

/* ---------- chrome ---------- */
function renderSide(active) {
  const v = S.v, sec = v?.sections || {};
  const reason = !S.project ? t('Open a project first') : !sec.peripherals ? t('Fix Peripherals first') : !sec.software ? t('Fix Software first') : !sec.review ? t('Fix Review first') : '';
  $('#side').innerHTML = NAV.map(([g, items]) => (g ? `<div class="grp">${t(g)}</div>` : '<div class="grp" aria-hidden="true"></div>') + items.map(([id, label, ic]) => {
    const isBuild = ['peripherals', 'software', 'review'].includes(id);
    const ok = isBuild && S.project && sec[id];
    const dis = id === 'generate' && !sec.generate;
    let badge = '';
    if (id === 'peripherals' && v?.conflicts) badge = `<span class="badge" title="${t('Open conflicts')}">${v.conflicts}</span>`;
    if (id === 'generate' && S.job?.state === 'running') badge = `<span class="badge run" title="${t('Job running')}">1</span>`;
    return `<button class="nav" data-act="nav" data-r="${id}" ${active === id ? 'aria-current="page"' : ''} ${dis ? `aria-disabled="true" title="${esc(reason)}"` : ''}>
      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="${ICON[ic]}"/></svg><span class="label">${t(label)}</span>${badge}${ok ? `<svg class="tick" viewBox="0 0 20 20" role="img" aria-label="${t('Complete')}"><path d="m4 10 4 4 8-8"/></svg>` : ''}</button>`;
  }).join('')).join('');
}
function renderChrome() {
  const p = S.project, inp = $('#proj-name');
  inp.disabled = !p; if (document.activeElement !== inp) inp.value = p ? p.name : '';
  renderSide(parseHash().route);
}
function setProject(res) { S.project = res.project; S.v = res.validation; S.dirty = true; renderChrome(); }
async function refreshProject() { const r = await api('project'); S.project = r.project; S.v = r.validation; renderChrome(); }
async function mutate(ep, body, rerender = true) { try { const r = await api(ep, body || {}); if (r.project) setProject(r); if (rerender) route(); return r; } catch (e) { toast(e.message, 'err'); } }
function needProject(main) {
  if (S.project) return false;
  main.innerHTML = `<div class="empty"><h2>${t('No project open')}</h2><p>${t('Pick a board or a SoC in the Library to start, or open a saved project.')}</p><div class="row" style="justify-content:center"><button class="btn primary" data-act="nav" data-r="boards">${t('Browse boards')}</button><button class="btn" data-act="nav" data-r="socs">${t('Browse SoCs')}</button><button class="btn" data-act="nav" data-r="projects">${t('Open a project')}</button></div></div>`;
  return true;
}

/* ---------- pages ---------- */
const PAGES = {};

PAGES.dashboard = async (main) => {
  const s = await api('summary');
  const c = s.counts, res = s.resources;
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Dashboard')}</h1><p>${t('Choose a SoC or board, pick peripherals and drivers, and generate a bootable Yocto BSP with a PDF report.')}</p></div>
    <button class="btn primary" data-act="nav" data-r="boards">${t('Start from a board')}</button><button class="btn" data-act="nav" data-r="socs">${t('Start from a SoC')}</button><button class="btn" data-act="custom-soc">${t('Custom board on a SoC')}</button></div>
  <div class="grid3">
    <div class="panel"><h3>${t('Catalog')}</h3><dl class="kv"><dt>${t('SoCs')}</dt><dd>${c.soc}</dd><dt>${t('Boards')}</dt><dd>${c.board}</dd><dt>${t('ICs')}</dt><dd>${c.ic}</dd><dt>${t('Drivers')}</dt><dd>${c.driver}</dd><dt>${t('Your records')}</dt><dd>${c.user_records}</dd></dl>
      <p class="muted small" style="margin-top:8px">${t('Version')} ${esc(s.version)}. ${t('The tool lists the boards it has ingested; it cannot claim to list every board on the market.')}</p></div>
    <div class="panel"><h3>${t('Build backend')}</h3>${s.backend ? `<p>${st('ok', t('Found'))} <code>${esc(s.backend.join(' '))}</code></p>` : `<p>${st('warn', t('Not found'))} ${t('Files and the report can be generated without it. Install Docker or Podman with kas-container, or use WSL2 on Windows, to build images.')}</p>`}
      <dl class="kv"><dt>${t('CPU cores')}</dt><dd>${res.cpus}</dd><dt>${t('Memory')}</dt><dd>${res.mem_gb || '?'} GB</dd><dt>${t('Free disk')}</dt><dd>${res.disk_gb} GB</dd></dl>
      ${res.warnings.length ? `<p class="small" style="color:var(--warn)">${t('Below the recommended 16 cores, 32 GB RAM and 200 GB disk for a full build:')} ${esc(res.warnings.join('; '))}</p>` : ''}</div>
    <div class="panel"><h3>${t('Recent projects')}</h3>${s.saved.length ? s.saved.map((p) => `<div class="row" style="justify-content:space-between;padding:4px 0"><span>${esc(p.name)}</span><button class="btn sm" data-act="open-saved" data-uid="${esc(p.uid)}">${t('Open')}</button></div>`).join('') : `<p class="muted">${t('Nothing saved yet.')}</p>`}</div>
  </div>`;
};

PAGES.projects = async (main) => {
  const list = await api('projects');
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Projects')}</h1><p>${t('Every project autosaves every 30 seconds. Export one as a single .ybsg file to share it.')}</p></div>
    <label class="btn"><input type="file" id="imp" accept=".ybsg" hidden>${t('Import .ybsg')}</label>
    ${S.project ? `<button class="btn" data-act="dup">${t('Duplicate current')}</button><a class="btn" href="/api/export" download="${esc(S.project.name)}.ybsg">${t('Export current')}</a><button class="btn" data-act="save-as">${t('Save a copy...')}</button>` : ''}</div>
  <div class="panel flush"><table class="tbl"><thead><tr><th>${t('Name')}</th><th>${t('Target')}</th><th>${t('Modified')}</th><th></th></tr></thead><tbody>
  ${list.map((p) => `<tr><td><a href="#" data-act="open-saved" data-uid="${esc(p.uid)}">${esc(p.name)}</a></td><td>${esc(p.board || p.soc || '')}</td><td>${esc(p.modified)}</td><td class="num"><button class="btn sm" data-act="open-saved" data-uid="${esc(p.uid)}">${t('Open')}</button></td></tr>`).join('') || `<tr><td colspan="4" class="empty">${t('No saved projects yet. Start one from a board or a SoC.')}</td></tr>`}
  </tbody></table></div>`;
  $('#imp').onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return; const buf = new Uint8Array(await f.arrayBuffer()); let bin = ''; buf.forEach((b) => (bin += String.fromCharCode(b)));
    const r = await mutate('project/import', { data: btoa(bin) }, false); if (r) { toast(t('Imported ') + S.project.name); go('peripherals'); }
  };
};

/* boards */
PAGES.boards = async (main) => {
  const boards = await api('list/board');
  const vendors = [...new Set(boards.map((b) => b.vendor))].sort(), socs = [...new Set(boards.map((b) => b.soc_name))].sort(), stats = [...new Set(boards.map((b) => b.status))].sort();
  const F = S.bf || (S.bf = { q: '', vendor: '', soc: '', status: '' });
  const draw = () => {
    const rows = boards.filter((b) => (!F.q || (b.name + ' ' + b.vendor + ' ' + b.soc_name).toLowerCase().includes(F.q.toLowerCase())) && (!F.vendor || b.vendor === F.vendor) && (!F.soc || b.soc_name === F.soc) && (!F.status || b.status === F.status));
    $('#bcount').textContent = `${rows.length} ${t('of')} ${boards.length} ${t('boards from the local database')}`;
    const body = $('#bbody');
    if (!rows.length) { body.innerHTML = `<div class="empty"><p>${t('No board matches these filters. Clear a filter, or use Find more boards in the Local database page.')}</p></div>`; return; }
    if (S.boardView === 'cards') body.innerHTML = `<div class="cards">${rows.map((b) => `<div class="bcard ${S.cmp.includes(b.id) ? 'sel' : ''}"><label class="cmp"><input type="checkbox" data-act="cmp" data-id="${esc(b.id)}" ${S.cmp.includes(b.id) ? 'checked' : ''} aria-label="${t('Compare')} ${esc(b.name)}">${t('Compare')}</label>
      <button class="bcard" style="border:0" data-act="board" data-id="${esc(b.id)}"><img loading="lazy" src="/api/image/${encodeURIComponent(b.id)}" alt="${esc(b.name)}"><span class="meta"><div class="nm">${esc(b.name)}</div><div class="muted small">${esc(b.soc_name)}, ${esc(b.vendor)}</div><div style="margin-top:6px">${st(b.status === 'active' ? 'ok' : 'info', b.status)} ${conf(b._prov.confidence)}${b._prov.user ? ` <span class="tag">${t('yours')}</span>` : ''}</div></span></button></div>`).join('')}</div>`;
    else if (S.boardView === 'table') body.innerHTML = `<div class="panel flush"><table class="tbl"><thead><tr><th></th><th>${t('Board')}</th><th>${t('SoC')}</th><th>${t('Vendor')}</th><th>${t('Status')}</th><th>${t('Data')}</th></tr></thead><tbody>${rows.map((b) => `<tr><td><input type="checkbox" data-act="cmp" data-id="${esc(b.id)}" ${S.cmp.includes(b.id) ? 'checked' : ''} aria-label="${t('Compare')} ${esc(b.name)}"></td><td><a href="#/board/${encodeURIComponent(b.id)}">${esc(b.name)}</a></td><td>${esc(b.soc_name)}</td><td>${esc(b.vendor)}</td><td>${st(b.status === 'active' ? 'ok' : 'info', b.status)}</td><td>${conf(b._prov.confidence)}</td></tr>`).join('')}</tbody></table></div>`;
    else body.innerHTML = graphView(rows);
    $('#cmpbtn').disabled = S.cmp.length < 2; $('#cmpbtn').textContent = `${t('Compare')} (${S.cmp.length})`;
  };
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Boards')}</h1><p id="bcount" class="muted"></p></div>
    <div class="seg" role="group" aria-label="${t('View')}">${['cards', 'table', 'graph'].map((v) => `<button data-act="bview" data-v="${v}" aria-pressed="${S.boardView === v}">${t(v[0].toUpperCase() + v.slice(1))}</button>`).join('')}</div><button class="btn" id="cmpbtn" data-act="compare" disabled>${t('Compare')} (0)</button></div>
  <div class="row" style="margin-bottom:14px"><input id="bq" type="search" placeholder="${t('Search boards')}" aria-label="${t('Search boards')}" value="${esc(F.q)}" style="min-width:220px">
    ${[['vendor', 'Vendor', vendors], ['soc', 'SoC', socs], ['status', 'Status', stats]].map(([k, l, o]) => `<select data-ch="bf" data-k="${k}" aria-label="${t(l)}"><option value="">${t(l)}: ${t('all')}</option>${o.map((x) => `<option ${F[k] === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>`).join('')}</div><div id="bbody"></div>`;
  $('#bq').oninput = debounce((e) => { F.q = e.target.value; draw(); }, 80);
  S.drawBoards = draw; draw();
};
function graphView(rows) {
  const socs = [...new Set(rows.map((b) => b.soc_name))]; const H = Math.max(socs.length, rows.length) * 40 + 20;
  const sy = (i) => 30 + i * (H - 40) / Math.max(1, socs.length - 1 || 1), by = (i) => 30 + i * 40;
  return `<div class="panel"><svg viewBox="0 0 800 ${H}" style="width:100%;height:auto" role="img" aria-label="${t('SoC to board relationship graph')}">${rows.map((b, i) => { const si = socs.indexOf(b.soc_name); return `<path d="M250 ${socs.length === 1 ? 30 : sy(si)} L330 ${by(i)}" stroke="var(--trace)" fill="none"/>`; }).join('')}
  ${socs.map((s, i) => `<g><rect x="60" y="${(socs.length === 1 ? 30 : sy(i)) - 14}" width="190" height="28" rx="5" fill="var(--panel-2)" stroke="var(--gold)"/><text x="155" y="${(socs.length === 1 ? 30 : sy(i)) + 4}" text-anchor="middle" fill="var(--text)" font-size="12">${esc(s)}</text></g>`).join('')}
  ${rows.map((b, i) => `<a href="#/board/${encodeURIComponent(b.id)}"><rect x="330" y="${by(i) - 14}" width="300" height="28" rx="5" fill="var(--panel)" stroke="var(--line)"/><text x="344" y="${by(i) + 4}" fill="var(--text)" font-size="12">${esc(b.name)}</text></a>`).join('')}</svg></div>`;
}
PAGES.board = async (main, id) => {
  const b = await api('get/board/' + encodeURIComponent(id)); const soc = b.soc_rec || {}; const pv = b._prov;
  main.innerHTML = `<div class="crumbs"><a href="#/boards">${t('Boards')}</a> / ${esc(b.name)}</div>
  <div class="page-head"><div class="grow"><h1>${esc(b.name)}</h1><p>${esc(soc.name || b.soc)}, ${esc(b.vendor)}</p></div><button class="btn primary" data-act="use-board" data-id="${esc(b.id)}">${t('Use this board')}</button><a class="btn" href="#/soc/${encodeURIComponent(b.soc)}">${t('Open SoC')}</a></div>
  ${pv.disputed ? `<div class="banner warn" style="margin-bottom:14px">${st('warn', t('Disputed'))}<span>${esc(pv.dispute_note || '')} ${t('The device-tree value is used for generation until you choose.')}</span><button class="btn sm" data-act="resolve" data-e="board" data-id="${esc(b.id)}">${t('Accept and clear')}</button></div>` : ''}
  <div class="grid2"><div class="stack"><img class="hero-img" src="/api/image/${encodeURIComponent(b.id)}" alt="${esc(b.name)}"><p class="muted small">${b.image ? `${t('Picture')}: ${esc(b.image.licence)}. ${t('Source')}: ${esc(b.image.source_url)}. ${b.image.shareable ? '' : t('Kept on this machine; left out of exported reports and shared snapshots.')}` : ''}</p></div>
  <div class="panel"><dl class="kv"><dt>${t('SoC')}</dt><dd>${esc(soc.name || b.soc)}</dd><dt>${t('Architecture')}</dt><dd>${esc(soc.arch || '')}, ${soc.cores || '?'} ${t('cores')}, ${soc.clock_mhz || '?'} MHz</dd><dt>${t('Memory')}</dt><dd>${esc(b.ram?.type || '')} ${b.ram?.size_mb || 0} MB, ${b.ram?.bus_width || 0}-bit</dd><dt>${t('Yocto machine')}</dt><dd><code>${esc(b.machine || '')}</code></dd><dt>${t('BSP layers')}</dt><dd>${(b.layers || []).map((l) => `<code>${esc(l)}</code>`).join(', ')}</dd><dt>${t('Kernel support')}</dt><dd>${(soc.kernels || []).map(esc).join(', ')}</dd><dt>${t('Product page')}</dt><dd>${b.url ? `<a href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.url)}</a>` : ''}</dd><dt>${t('Datasheet')}</dt><dd>${soc.datasheet ? `<a href="${esc(soc.datasheet)}" target="_blank" rel="noopener">${esc(soc.datasheet)}</a>` : ''}</dd><dt>${t('Data source')}</dt><dd>${esc(pv.source_url)}</dd><dt>${t('Fetched')}</dt><dd>${esc(pv.fetched)}</dd><dt>${t('Confidence')}</dt><dd>${conf(pv.confidence)}</dd></dl></div></div>
  <h2 style="margin:18px 0 8px">${t('Peripherals filled in from the database')}</h2><div class="panel flush"><table class="tbl"><thead><tr><th>${t('Category')}</th><th>${t('IC')}</th><th>${t('Driver')}</th></tr></thead><tbody>${(b.preset || []).map((p) => `<tr><td>${esc(p.category)}</td><td>${esc(p.ic)}</td><td>${esc(p.driver)}</td></tr>`).join('')}</tbody></table></div>`;
};

/* SoCs and ICs */
PAGES.socs = async (main) => {
  const tab = S.socTab || 'socs'; const socs = await api('list/soc'); const ics = await api('list/ic');
  const F = S.sf || (S.sf = { q: '', vendor: '', arch: '', cores: 0, price: '', avail: '', cat: '' });
  const draw = () => {
    if (tab === 'socs') {
      const rows = socs.filter((s) => (!F.q || (s.name + s.vendor + s.family).toLowerCase().includes(F.q.toLowerCase())) && (!F.vendor || s.vendor === F.vendor) && (!F.arch || s.arch === F.arch) && s.cores >= (+F.cores || 0) && (!F.price || s.price_band === F.price) && (!F.avail || s.availability.toLowerCase().includes(F.avail.toLowerCase())));
      $('#sbody').innerHTML = `<div class="panel flush"><table class="tbl"><thead><tr><th>${t('SoC')}</th><th>${t('Vendor')}</th><th>${t('Arch')}</th><th class="num">${t('Cores')}</th><th class="num">${t('MHz')}</th><th>${t('Price')}</th><th>${t('Availability')}</th><th>${t('Data')}</th></tr></thead><tbody>${rows.map((s) => `<tr><td><a href="#/soc/${encodeURIComponent(s.id)}">${esc(s.name)}</a></td><td>${esc(s.vendor)}</td><td>${esc(s.arch)}</td><td class="num">${s.cores}</td><td class="num">${s.clock_mhz}</td><td>${esc(s.price_band)}</td><td>${esc(s.availability)}</td><td>${conf(s._prov.confidence)}</td></tr>`).join('') || `<tr><td colspan="8" class="empty">${t('No SoC matches these filters.')}</td></tr>`}</tbody></table></div>`;
    } else {
      const rows = ics.filter((i) => (!F.q || (i.part + i.vendor).toLowerCase().includes(F.q.toLowerCase())) && (!F.vendor || i.vendor === F.vendor) && (!F.cat || i.category === F.cat));
      $('#sbody').innerHTML = `<div class="panel flush"><table class="tbl"><thead><tr><th>${t('IC')}</th><th>${t('Vendor')}</th><th>${t('Category')}</th><th>${t('Interface')}</th><th>${t('Voltage')}</th><th>${t('Package')}</th><th>${t('Data')}</th></tr></thead><tbody>${rows.map((i) => `<tr><td><a href="#" data-act="ic" data-id="${esc(i.id)}">${esc(i.part)}</a></td><td>${esc(i.vendor)}</td><td>${esc(i.category)}</td><td>${esc(i.interface)}</td><td>${esc(i.voltage)}</td><td>${esc(i.package)}</td><td>${conf(i._prov.confidence)}</td></tr>`).join('')}</tbody></table></div>`;
    }
  };
  const vend = [...new Set((tab === 'socs' ? socs : ics).map((x) => x.vendor))].sort();
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('SoCs and ICs')}</h1><p>${t('Every record keeps its source and fetch date.')}</p></div><div class="seg"><button data-act="soctab" data-v="socs" aria-pressed="${tab === 'socs'}">${t('SoCs')}</button><button data-act="soctab" data-v="ics" aria-pressed="${tab === 'ics'}">${t('ICs')}</button></div></div>
  <div class="row" style="margin-bottom:14px"><input id="sq" type="search" placeholder="${t('Search')}" aria-label="${t('Search')}" value="${esc(F.q)}">
  <select data-ch="sf" data-k="vendor" aria-label="${t('Vendor')}"><option value="">${t('Vendor')}: ${t('all')}</option>${vend.map((x) => `<option ${F.vendor === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>
  ${tab === 'socs' ? `<select data-ch="sf" data-k="arch" aria-label="${t('Architecture')}"><option value="">${t('Architecture')}: ${t('all')}</option><option ${F.arch === 'arm32' ? 'selected' : ''}>arm32</option><option ${F.arch === 'arm64' ? 'selected' : ''}>arm64</option></select>
  <select data-ch="sf" data-k="cores" aria-label="${t('Core count')}"><option value="0">${t('Cores')}: ${t('any')}</option>${[2, 4].map((n) => `<option value="${n}" ${+F.cores === n ? 'selected' : ''}>${n}+</option>`).join('')}</select>
  <select data-ch="sf" data-k="price" aria-label="${t('Price band')}"><option value="">${t('Price')}: ${t('any')}</option>${['$', '$$', '$$$'].map((x) => `<option ${F.price === x ? 'selected' : ''}>${x}</option>`).join('')}</select>
  <select data-ch="sf" data-k="avail" aria-label="${t('Availability')}"><option value="">${t('Availability')}: ${t('any')}</option><option value="active" ${F.avail === 'active' ? 'selected' : ''}>active</option></select>` : `<select data-ch="sf" data-k="cat" aria-label="${t('Category')}"><option value="">${t('Category')}: ${t('all')}</option>${[...new Set(ics.map((i) => i.category))].sort().map((x) => `<option ${F.cat === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>`}</div><div id="sbody"></div>`;
  $('#sq').oninput = debounce((e) => { F.q = e.target.value; draw(); }, 80); S.drawSocs = draw; draw();
};
PAGES.soc = async (main, id) => {
  const s = await getSoc(id); const pv = s._prov;
  main.innerHTML = `<div class="crumbs"><a href="#/socs">${t('SoCs and ICs')}</a> / ${esc(s.name)}</div><div class="page-head"><div class="grow"><h1>${esc(s.name)}</h1><p>${esc(s.vendor)}, ${esc(s.arch)}, ${s.cores} ${t('cores')}</p></div><button class="btn primary" data-act="custom-board" data-id="${esc(s.id)}">${t('Start a custom board on this SoC')}</button></div>
  <div class="grid2"><div class="panel"><dl class="kv"><dt>${t('Clock')}</dt><dd>${s.clock_mhz} MHz</dd><dt>${t('Family')}</dt><dd>${esc(s.family)}</dd><dt>${t('Price band')}</dt><dd>${esc(s.price_band)}</dd><dt>${t('Availability')}</dt><dd>${esc(s.availability)}</dd><dt>${t('Existing BSP layers')}</dt><dd>${(s.layers || []).map((l) => `<code>${esc(l)}</code>`).join(', ')}</dd><dt>${t('Kernel and U-Boot')}</dt><dd>${t('Kernels')}: ${(s.kernels || []).map(esc).join(', ')}. U-Boot <code>${esc(s.uboot_defconfig)}</code></dd><dt>${t('Licence gate')}</dt><dd>${esc(s.licence_gate)}</dd><dt>${t('Datasheet')}</dt><dd>${s.datasheet ? `<a href="${esc(s.datasheet)}" target="_blank" rel="noopener">${esc(s.datasheet)}</a>` : ''}</dd><dt>${t('Source')}</dt><dd>${esc(pv.source_url)}, ${t('fetched')} ${esc(pv.fetched)}</dd><dt>${t('Confidence')}</dt><dd>${conf(pv.confidence)}</dd></dl></div>
  <div class="panel"><h3>${t('Boards with this SoC')}</h3>${(s.boards || []).map((b) => `<div><a href="#/board/${encodeURIComponent(b.id)}">${esc(b.name)}</a></div>`).join('') || `<p class="muted">${t('None in the local database yet.')}</p>`}<h3 style="margin-top:14px">${t('Boot chain')}</h3><p>${esc(s.boot)}</p><p class="muted small">${esc(s.dram)}</p></div></div>
  <h2 style="margin:18px 0 8px">${t('Controllers')}</h2><div class="panel flush"><table class="tbl"><thead><tr><th>${t('Controller')}</th><th>${t('Type')}</th><th class="num">${t('Count')}</th><th>${t('Modes')}</th><th>${t('Pins')}</th></tr></thead><tbody>${s.controllers.map((c) => `<tr><td>${esc(c.name)}</td><td>${esc(c.type)}</td><td class="num">${c.count}</td><td>${c.modes.map(esc).join(', ')}</td><td class="mono small">${c.pins.map(esc).join(', ')}</td></tr>`).join('')}</tbody></table></div>`;
};
PAGES.drivers = async (main) => {
  const [drivers, kernels] = await Promise.all([api('list/driver'), api('list/kernel')]);
  const kt = (() => { const k = kernels.find((x) => x.id === S.project?.kernel); return k ? [k.major, k.minor] : [6, 18]; })();
  const cmp = (s) => { const m = String(s).match(/^(\d+)\.(\d+)/); return m ? [+m[1], +m[2]] : null; };
  const lt = (a, b) => a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
  const status = (d) => { const rem = cmp(d.removed_in), first = cmp(d.first_kernel), last = cmp(d.last_verified); if (rem && !lt(kt, rem)) return ['fail', 'removed in ' + d.removed_in]; if (first && lt(kt, first)) return ['fail', 'since ' + d.first_kernel]; if (last && lt(last, kt)) return ['warn', 'last verified ' + d.last_verified]; return ['ok', 'in range']; };
  const F = S.df || (S.df = { q: '', src: '' });
  const draw = () => {
    const rows = drivers.filter((d) => (!F.q || (d.name + d.compatible + (d.kconfig || []).join(' ')).toLowerCase().includes(F.q.toLowerCase())) && (!F.src || d.source === F.src));
    $('#dbody').innerHTML = `<div class="panel flush scroll"><table class="tbl"><thead><tr><th>${t('Driver')}</th><th>${t('Source')}</th><th>${t('Kernel range')}</th><th>${t('Status on')} ${esc(S.project?.kernel || '6.18')}</th><th>${t('CONFIG symbols')}</th><th>${t('Licence')}</th></tr></thead><tbody>${rows.map((d) => { const [s, why] = status(d); return `<tr><td><b>${esc(d.name)}</b><div class="muted small mono">${esc(d.compatible)}</div></td><td>${esc(d.source)}</td><td>${esc(d.first_kernel)} to ${esc(d.removed_in || d.last_verified)}</td><td>${st(s, why)}</td><td class="mono small">${(d.kconfig || []).map(esc).join('<br>')}</td><td>${esc(d.licence)}</td></tr>`; }).join('')}</tbody></table></div>`;
  };
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Drivers')}</h1><p>${t('Every known driver with its source, kernel range and the CONFIG symbols it needs.')}</p></div></div><div class="row" style="margin-bottom:14px"><input id="dq" type="search" placeholder="${t('Search drivers')}" aria-label="${t('Search drivers')}" value="${esc(F.q)}"><select data-ch="df" data-k="src" aria-label="${t('Source')}"><option value="">${t('Source')}: ${t('all')}</option>${['mainline', 'vendor', 'third-party', 'bootloader'].map((x) => `<option ${F.src === x ? 'selected' : ''}>${x}</option>`).join('')}</select></div><div id="dbody"></div>`;
  $('#dq').oninput = debounce((e) => { F.q = e.target.value; draw(); }, 80); S.drawDrivers = draw; draw();
};

/* ---------- Peripherals workbench ---------- */
const TYPE_SIDE = { usb_host: 'L', csi: 'L', display: 'L', audio: 'L', mmc: 'R', flash: 'R', ram: 'R', net: 'R', i2c: 'R' };
const TYPE_LABEL = { usb_host: 'USB', csi: 'Camera', display: 'Display', audio: 'Audio', mmc: 'SD/eMMC', flash: 'Flash', ram: 'RAM', net: 'Network', i2c: 'I2C' };
PAGES.peripherals = async (main) => {
  if (needProject(main)) return;
  if (!S.cats) S.cats = await api('categories');
  const p = S.project, soc = await getSoc(p.soc); const v = S.v;
  const cat = S.sel.cat; const cmap = S.cats.controllers;
  const offers = cat === 'ram' ? await api('offer/ics?category=ram') : await api('offer/ics?category=' + cat);
  S.offers = offers;
  const sel = p.peripherals.find((x) => x.uid === S.sel.uid);
  const counts = {}; p.peripherals.forEach((x) => (counts[x.category] = (counts[x.category] || 0) + 1));
  const issues = v.issues.filter((i) => i.severity !== 'info');
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Peripherals')}</h1><p>${t('Target')}: ${esc(p.board ? (await api('get/board/' + p.board)).name : soc.name)}. ${t('Drag a part onto a free pad, or select it and press Enter on a pad.')}</p></div>
    <div class="seg"><button data-act="bview2" data-v="board" aria-pressed="${S.sel.view === 'board'}">${t('Board view')}</button><button data-act="bview2" data-v="pins" aria-pressed="${S.sel.view === 'pins'}">${t('Pin map')}</button></div>
    <button class="btn primary" data-act="nav" data-r="software" ${v.sections.peripherals ? '' : 'disabled'}>${t('Continue to Software')}</button></div>
  <div class="bench"><div class="panel"><div class="cats" role="group" aria-label="${t('Categories')}">${Object.entries(S.cats.labels).map(([k, l]) => `<button class="cat" data-act="cat" data-k="${k}" aria-pressed="${cat === k}"><span>${t(l)}</span>${counts[k] ? `<span class="n">${counts[k]}</span>` : ''}</button>`).join('')}</div></div>
  <div class="stack"><div id="boardwrap">${S.sel.view === 'board' ? boardSvg(soc, p, v, cmap) : pinMap(soc, v)}</div>
    <div class="legend" aria-hidden="true"><span><svg width="18" height="10"><rect x="1" y="1" width="16" height="8" rx="2" stroke="var(--trace)" fill="var(--panel)"/></svg> ${t('In use')}</span><span><svg width="18" height="10"><rect x="1" y="1" width="16" height="8" rx="2" stroke="var(--line)" stroke-dasharray="3 3" fill="none"/></svg> ${t('Free pad')}</span><span><svg width="18" height="10"><rect x="1" y="1" width="16" height="8" rx="2" stroke="var(--err)" stroke-width="2" fill="none"/></svg> ${t('Conflict')}</span></div>
    <div class="panel flush"><div class="panel-head">${t('Conflicts and warnings')} ${v.conflicts ? `<span class="badge">${v.conflicts}</span>` : st('ok', t('No conflicts'))}</div>${issues.map(issueRow).join('') || `<div class="empty">${t('Nothing to fix. Every choice fits the SoC.')}</div>`}</div></div>
  <div class="stack">${rightPanel(cat, offers, sel, soc)}</div></div>`;
  wireBoard();
};
function issueRow(i) {
  return `<div class="issue"><div>${st(i.severity === 'error' ? (i.waived ? 'info' : 'fail') : 'warn', i.waived ? t('Waived') : i.severity === 'error' ? t('Error') : t('Warning'))} ${esc(i.message)}</div>
  <div>${i.waivable && i.severity === 'error' ? (i.waived ? `<button class="btn sm" data-act="unwaive" data-id="${esc(i.id)}">${t('Undo waiver')}</button>` : `<button class="btn sm" data-act="waive" data-id="${esc(i.id)}">${t('Waive')}</button>`) : ''}${i.uid ? `<button class="btn sm ghost" data-act="selperi" data-uid="${esc(i.uid)}">${t('Show')}</button>` : ''}</div>${i.fix ? `<div class="fix">${t('Suggested fix')}: ${esc(i.fix)}</div>` : ''}</div>`;
}
function boardSvg(soc, p, v, cmap) {
  const ports = [];
  for (const c of soc.controllers) { if (!c.count) continue; if (c.type === 'i2c') { ports.push({ ...c, inst: 'I2C buses', type: 'i2c', multi: true }); continue; } for (let i = 0; i < c.count; i++) ports.push({ ...c, inst: c.count === 1 ? c.name : `${c.name}#${i}` }); }
  const L = ports.filter((x) => TYPE_SIDE[x.type] === 'L'), R = ports.filter((x) => TYPE_SIDE[x.type] === 'R');
  const rows = Math.max(L.length, R.length, 4), PITCH = 46, H = rows * PITCH + 40;
  const asg = v.assignment || {}, byInst = {}; Object.entries(asg).forEach(([uid, inst]) => (byInst[inst] = uid));
  const badUids = new Set(v.issues.filter((i) => i.severity === 'error' && !i.waived && i.uid).map((i) => i.uid));
  const badInst = new Set(v.issues.filter((i) => i.severity === 'error' && !i.waived && i.kind === 'pinmux').map((i) => i.message));
  const peri = (uid) => p.peripherals.find((x) => x.uid === uid);
  const draw = (list, side) => list.map((c, i) => {
    const y = 28 + i * PITCH, uid = byInst[c.inst], x0 = side === 'L' ? 262 : 608, bx = side === 'L' ? 24 : 736;
    let blk = '', cls = 'free', conflict = [...badInst].some((m) => m.includes(c.name));
    if (c.type === 'i2c') { const devs = p.peripherals.filter((x) => ['i2c', 'audio'].includes(x.category) || x.ic && S.offers && false); const n = p.peripherals.filter((x) => x.category === 'i2c').length + (p.custom_ics || []).length; cls = n ? 'used' : 'free'; blk = n ? `<g><path class="trace" d="M${side === 'L' ? 224 : 698} ${y + 15} H${side === 'L' ? 262 : 736}"/><rect class="blk" x="${bx}" y="${y}" width="200" height="30" rx="5"/><text x="${bx + 100}" y="${y + 19}" text-anchor="middle">${n} ${t('I2C device(s)')}</text></g>` : ''; }
    else if (uid) { const x = peri(uid); const ic = (S.icCache || {})[x.ic]; cls = 'used'; const bad = badUids.has(uid) || conflict; blk = `<g><path class="trace ${bad ? 'bad' : ''}" d="M${side === 'L' ? 224 : 698} ${y + 15} H${side === 'L' ? 262 : 736}"/><rect class="blk ${bad ? 'bad' : ''} ${S.sel.uid === uid ? 'sel' : ''}" x="${bx}" y="${y}" width="200" height="30" rx="5" data-uid="${esc(uid)}" tabindex="0" role="button" aria-label="${esc(x.category + ' ' + (ic || x.ic))}"/><text x="${bx + 100}" y="${y + 13}" text-anchor="middle" pointer-events="none">${esc((ic || x.ic || 'custom').slice(0, 30))}</text><text class="dim" x="${bx + 100}" y="${y + 25}" text-anchor="middle" pointer-events="none">${esc(x.driver || 'no driver chosen')}</text></g>`; }
    else if (c.type === 'ram' && p.ram?.type) { cls = 'used'; blk = `<g><path class="trace" d="M${side === 'L' ? 224 : 698} ${y + 15} H${side === 'L' ? 262 : 736}"/><rect class="blk" x="${bx}" y="${y}" width="200" height="30" rx="5"/><text x="${bx + 100}" y="${y + 13}" text-anchor="middle">${esc(p.ram.type)} ${p.ram.size_mb} MB</text><text class="dim" x="${bx + 100}" y="${y + 25}" text-anchor="middle">${p.ram.bus_width}-bit</text></g>`; }
    const lab = `${t(TYPE_LABEL[c.type])}: ${c.inst}`;
    return `${blk}<g><rect class="pad ${cls} ${conflict ? 'bad' : ''}" x="${x0}" y="${y}" width="90" height="30" rx="4" data-inst="${esc(c.inst)}" data-type="${esc(c.type)}" tabindex="0" role="button" aria-label="${esc(lab)}"><title>${esc(lab)}</title></rect><text x="${x0 + 45}" y="${y + 13}" text-anchor="middle" pointer-events="none">${esc(c.inst.replace(/\s*\(.*$/, '').slice(0, 15))}</text><text class="dim" x="${x0 + 45}" y="${y + 25}" text-anchor="middle" pointer-events="none">${esc(t(TYPE_LABEL[c.type]))}</text></g>`;
  }).join('');
  return `<svg class="board-svg" viewBox="0 0 960 ${H}" role="group" aria-label="${t('SoC board view')}"><defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--panel-2)"/><line x1="0" y1="0" x2="0" y2="6" stroke="var(--err)" stroke-width="3"/></pattern></defs>
  <rect class="pkg" x="352" y="14" width="256" height="${H - 28}" rx="10"/><text x="480" y="${H / 2 - 4}" text-anchor="middle" style="font-size:16px;font-weight:700">${esc(soc.name)}</text><text class="dim" x="480" y="${H / 2 + 14}" text-anchor="middle">${esc(soc.vendor)}, ${esc(soc.arch)}</text>${draw(L, 'L')}${draw(R, 'R')}</svg>`;
}
function pinMap(soc, v) {
  const used = new Set(Object.values(v.assignment || {})); const bad = v.issues.filter((i) => i.kind === 'pinmux' && !i.waived).map((i) => i.message).join(' ');
  return `<div class="panel stack"><p class="muted small">${t('Assigned pins are filled, free pins are dashed, conflicting pins are hatched. Shape and pattern carry the meaning, not colour alone.')}</p>${soc.controllers.filter((c) => c.count).map((c) => {
    const inst = c.count === 1 ? c.name : c.name + '#0'; const state = bad.includes(c.name) ? 'conflict' : (used.has(inst) || [...used].some((u) => u.startsWith(c.name))) ? 'assigned' : 'free';
    return `<div><div class="row" style="margin-bottom:4px"><b>${esc(c.name)}</b> ${st(state === 'conflict' ? 'fail' : state === 'assigned' ? 'ok' : 'pending', state)}</div><div class="balls">${c.pins.map((pn) => `<span class="ball ${state}" title="${esc(pn)}">${esc(pn.slice(0, 5))}</span>`).join('')}</div></div>`;
  }).join('')}</div>`;
}
function rightPanel(cat, offers, sel, soc) {
  const p = S.project, h = HELP[cat] || HELP[S.cats.ic_category[cat]] || ['', ''];
  offers.forEach((o) => { (S.icCache ||= {})[o.id] = o.part; });
  let html = `<div class="panel"><h3>${t(S.cats.labels[cat] || 'RAM')}${tip(h[0], h[1])}</h3>`;
  if (cat === 'ram') {
    const modes = (soc.controllers.find((c) => c.type === 'ram') || { modes: [] }).modes; const r = p.ram || {};
    html += `<div class="form-grid" style="margin-top:8px"><label class="f"><span>${t('Type')}</span><select data-ch="ram" data-k="type"><option value="">${t('Choose')}</option>${modes.map((m) => `<option ${r.type === m ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select></label>
    <label class="f"><span>${t('Size (MB)')}</span><input type="number" min="16" step="16" value="${r.size_mb || ''}" data-ch="ram" data-k="size_mb"></label><label class="f"><span>${t('Bus width (bit)')}</span><select data-ch="ram" data-k="bus_width">${[8, 16, 32, 64].map((n) => `<option ${+r.bus_width === n ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
    <label class="f"><span>${t('Ranks')}</span><select data-ch="ram" data-k="ranks">${[1, 2].map((n) => `<option ${+r.ranks === n ? 'selected' : ''}>${n}</option>`).join('')}</select></label><label class="f"><span>${t('Speed grade')}</span><input value="${esc(r.speed || '')}" placeholder="DDR3L-1600" data-ch="ram" data-k="speed"></label></div>
    <p class="muted small" style="margin-top:10px">${t('RAM timing and training for new silicon and chip combinations must be validated on hardware. YBSG reuses the vendor DRAM configuration and writes the memory node.')}</p></div>`;
    html += `<div class="panel"><h3>${t('RAM parts')}</h3>${offers.map((o) => icCard(o)).join('')}</div>`;
    return html;
  }
  html += `<div class="row" style="margin:8px 0">${cat.startsWith('usb') ? `<button class="btn sm" data-act="custom-usb">${t('Custom USB class...')}</button>` : ''}<button class="btn sm" data-act="custom-ic">${t('Custom IC...')}</button></div>`;
  html += offers.map((o) => icCard(o)).join('') || `<p class="muted">${t('No parts in this category yet. Add one as a custom IC.')}</p>`;
  html += '</div>';
  if (S.sel.ic) { const ic = offers.find((o) => o.id === S.sel.ic); if (ic) html += `<div class="panel" id="drvpanel"><h3>${t('Drivers for')} ${esc(ic.part)}${tip(t('A driver binds an IC to the kernel. In-tree is mainline, vendor lives in a vendor tree, out-of-tree needs patching. Pick the one that covers your kernel.'), 'https://www.kernel.org/doc/html/latest/driver-api/index.html')}</h3><div id="drvlist" class="muted">${t('Loading')}</div></div>`; }
  if (sel) html += periEditor(sel, soc);
  return html;
}
function icCard(o) {
  return `<button class="iccard ${S.sel.ic === o.id ? 'sel' : ''}" draggable="${o.enabled}" data-act="selic" data-id="${esc(o.id)}" data-cat="${esc(S.sel.cat)}" aria-disabled="${!o.enabled}" ${o.enabled ? '' : `title="${esc(o.reason)}"`}>
    <div><b>${esc(o.part)}</b> <span class="muted small">${esc(o.vendor)}</span></div><div class="muted small">${esc(o.interface)}, ${esc(o.voltage)}, ${esc(o.package)} ${o.no_driver ? `<span style="color:var(--warn)">${t('no driver')}</span>` : `${o.driver_count} ${t('driver(s)')}`}</div>${o.enabled ? '' : `<div class="why">${st('warn', t('Disabled'))} ${esc(o.reason)}</div>`}</button>`;
}
function periEditor(x, soc) {
  const usb = x.category.startsWith('usb');
  const classes = ['', 'mass_storage', 'uvc', 'hid', 'cdc_acm', 'cdc_ecm', 'audio', 'printer'];
  const funcs = ['serial', 'mass_storage', 'ecm', 'ncm', 'rndis', 'hid', 'uvc', 'midi', 'composite'];
  return `<div class="panel" id="peri-edit"><div class="row" style="justify-content:space-between"><h3>${t('Selected')}: ${esc(x.category)}</h3><button class="btn sm danger" data-act="rm" data-uid="${esc(x.uid)}">${t('Remove')}</button></div>
  <div class="stack" style="margin-top:8px"><label class="f"><span>${t('IC')}</span><select data-ch="peri" data-uid="${esc(x.uid)}" data-k="ic">${(S.offers || []).map((o) => `<option value="${esc(o.id)}" ${o.id === x.ic ? 'selected' : ''} ${o.enabled || o.id === x.ic ? '' : 'disabled'}>${esc(o.part)}${o.enabled ? '' : ' (' + t('disabled') + ')'}</option>`).join('')}</select></label>
  <label class="f"><span>${t('Driver')}</span><select data-ch="peri" data-uid="${esc(x.uid)}" data-k="driver" id="drvsel"><option value="${esc(x.driver)}">${esc(x.driver || t('none'))}</option></select></label>
  ${usb ? `<label class="f"><span>${t('Mode')}</span><select data-ch="peri" data-uid="${esc(x.uid)}" data-k="mode">${['host', 'device', 'otg'].map((m) => `<option ${x.mode === m ? 'selected' : ''}>${m}</option>`).join('')}</select></label>` : ''}
  ${x.category === 'usb_host' ? `<label class="f"><span>${t('Device class to support')}${tip(t('Adds the kernel symbols for this class, for example UVC webcams or mass storage.'))}</span><select data-ch="peri" data-uid="${esc(x.uid)}" data-k="class" data-prop="1">${classes.map((c) => `<option value="${c}" ${x.props?.class === c ? 'selected' : ''}>${c || t('(any)')}</option>`).join('')}</select></label>` : ''}
  ${x.category === 'usb_device' ? `<label class="f"><span>${t('Gadget function')}${tip(t('Created through ConfigFS by a generated setup script.'), 'https://www.kernel.org/doc/html/latest/usb/gadget_configfs.html')}</span><select data-ch="peri" data-uid="${esc(x.uid)}" data-k="function" data-prop="1">${funcs.map((c) => `<option ${x.props?.function === c ? 'selected' : ''}>${c}</option>`).join('')}</select></label>` : ''}
  ${['camera', 'audio', 'i2c', 'display'].includes(x.category) ? `<div class="form-grid"><label class="f"><span>${t('I2C bus')}</span><input value="${esc(x.props?.bus || '')}" placeholder="i2c1" data-ch="peri" data-uid="${esc(x.uid)}" data-k="bus" data-prop="1"></label><label class="f"><span>${t('Address')}</span><input value="${esc(x.props?.address || '')}" placeholder="0x10" data-ch="peri" data-uid="${esc(x.uid)}" data-k="address" data-prop="1"></label></div>` : ''}
  ${x.category === 'flash' ? `<label class="f"><span>${t('Partitions (name offset size, hex)')}</span><textarea rows="4" data-ch="parts" data-uid="${esc(x.uid)}" placeholder="bootloader 0x0 0x100000">${(x.props?.partitions || []).map((q) => `${q.name} 0x${q.offset.toString(16)} 0x${q.size.toString(16)}`).join('\n')}</textarea></label>` : ''}</div></div>`;
}
function wireBoard() {
  const svg = $('.board-svg'); if (svg) {
    $$('.pad', svg).forEach((pad) => {
      pad.addEventListener('dragover', (e) => { const ok = dropOk(pad); if (ok.ok) e.preventDefault(); pad.classList.toggle('ok-drop', ok.ok); pad.classList.toggle('no-drop', !ok.ok); });
      pad.addEventListener('dragleave', () => pad.classList.remove('ok-drop', 'no-drop'));
      pad.addEventListener('drop', (e) => { e.preventDefault(); pad.classList.remove('ok-drop', 'no-drop'); placeOn(pad, true); });
      pad.addEventListener('click', () => placeOn(pad, false));
      pad.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); placeOn(pad, false); } });
    });
    $$('.blk[data-uid]', svg).forEach((b) => { b.addEventListener('click', () => { S.sel.uid = b.dataset.uid; S.sel.cat = S.project.peripherals.find((x) => x.uid === b.dataset.uid)?.category || S.sel.cat; route(); }); b.addEventListener('keydown', (e) => { if (e.key === 'Enter') b.dispatchEvent(new Event('click')); }); });
  }
  $$('.iccard[draggable="true"]').forEach((c) => c.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', c.dataset.id); S.dragIc = c.dataset.id; S.sel.ic = c.dataset.id; }));
  if (S.sel.ic) loadDrivers();
  const x = S.project.peripherals.find((q) => q.uid === S.sel.uid); if (x && x.ic) loadDriverSelect(x);
}
function dropOk(pad) {
  const ic = (S.offers || []).find((o) => o.id === (S.dragIc || S.sel.ic)); if (!ic) return { ok: false, reason: t('Select a part first') };
  const need = S.cats.controllers[S.sel.cat]; const type = pad.dataset.type;
  if (type !== need) return { ok: false, reason: `${t('This part does not fit a')} ${TYPE_LABEL[type] || type} ${t('pad')}` };
  if (!ic.enabled) return { ok: false, reason: ic.reason };
  if (pad.classList.contains('used') && type !== 'i2c') return { ok: false, reason: t('That pad is already in use') };
  return { ok: true, ic };
}
async function placeOn(pad, dropped) {
  const r = dropOk(pad); S.dragIc = null;
  if (!r.ok) { toast(r.reason, 'warn'); return; }
  const cat = S.sel.cat; const drivers = await api('offer/drivers?ic=' + encodeURIComponent(r.ic.id)); const drv = drivers.find((d) => d.enabled);
  const mode = cat === 'usb_device' ? 'device' : cat === 'usb_host' ? 'host' : '';
  const res = await mutate('project/add', { category: cat, ic: r.ic.id, driver: drv ? drv.id : '', mode }, false);
  if (res) { S.sel.uid = S.project.peripherals.at(-1).uid; route(); toast(`${t('Added')} ${r.ic.part}`); }
}
async function loadDrivers() {
  const box = $('#drvlist'); if (!box) return; const ds = await api('offer/drivers?ic=' + encodeURIComponent(S.sel.ic));
  box.innerHTML = ds.length ? ds.map((d) => `<label class="iccard ${d.enabled ? '' : ''}" style="cursor:default" aria-disabled="${!d.enabled}"><div><b>${esc(d.name)}</b> ${st(d.status === 'ok' ? 'ok' : d.status === 'unverified' ? 'warn' : 'fail', d.status === 'ok' ? t('In range') : d.status)}</div><div class="muted small">${esc(d.source)}, ${esc(d.first_kernel)} to ${esc(d.removed_in || d.last_verified)}, ${esc(d.licence)}</div><div class="mono small muted">${(d.kconfig || []).map(esc).join(' ')}</div>${d.enabled ? '' : `<div class="why">${esc(d.reason)}</div>`}</label>`).join('')
    : `<div class="banner warn">${st('warn', t('No driver'))}<span>${t('No known driver. Add one yourself: a local path, git URL or patch file.')}</span></div>`;
}
async function loadDriverSelect(x) {
  const sel = $('#drvsel'); if (!sel) return; const ds = await api('offer/drivers?ic=' + encodeURIComponent(x.ic));
  sel.innerHTML = `<option value="">${t('none')}</option>` + ds.map((d) => `<option value="${esc(d.id)}" ${d.id === x.driver ? 'selected' : ''} ${d.enabled || d.id === x.driver ? '' : 'disabled'}>${esc(d.name)} (${esc(d.source)}${d.enabled ? '' : ', ' + esc(d.status)})</option>`).join('');
}

/* ---------- Software ---------- */
PAGES.software = async (main) => {
  if (needProject(main)) return;
  const p = S.project, soc = await getSoc(p.soc); const [meta, compat] = await Promise.all([api('releases'), api('compat')]);
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Software')}</h1><p>${t('Checks run live as you choose.')}</p></div><button class="btn primary" data-act="nav" data-r="review" ${S.v.sections.software ? '' : 'disabled'}>${t('Continue to Review')}</button></div>
  <div class="grid2"><div class="stack"><div class="panel"><h3>${t('Yocto release')}${tip(t('LTS releases get four years of fixes. Pick the release your vendor layer supports.'), 'https://docs.yoctoproject.org/')}</h3><select data-ch="proj" data-k="release" style="width:100%;margin-top:8px">${meta.releases.map((r) => `<option value="${esc(r.id)}" ${p.release === r.id ? 'selected' : ''}>${esc(r.name)} ${esc(r.version)}${r.lts ? ' LTS' : ''} - ${esc(r.status)}</option>`).join('')}</select></div>
  <div class="panel"><h3>${t('Kernel')}</h3><div class="stack" style="margin-top:8px">${meta.kernels.map((k) => { const ok = soc.kernels.includes(k.id); return `<label class="row"><input type="radio" name="kern" data-ch="proj" data-k="kernel" value="${esc(k.id)}" ${p.kernel === k.id ? 'checked' : ''}> <span>${esc(k.label)}</span> ${ok ? st('ok', t('Supported')) : st('fail', t('Not supported by') + ' ' + soc.name)}</label>`; }).join('')}</div></div>
  <div class="panel"><h3>${t('Image type')}</h3><div class="stack" style="margin-top:8px">${Object.entries(meta.images).map(([k, l]) => `<label class="row"><input type="radio" name="img" data-ch="proj" data-k="image" value="${k}" ${p.image === k ? 'checked' : ''}> <span>${t(l)}</span></label>`).join('')}
  ${p.image === 'distro' ? `<label class="f"><span>${t('Distribution')}</span><select data-ch="proj" data-k="distro"><option value="">${t('Choose')}</option>${meta.distros.map((d) => `<option ${p.distro === d ? 'selected' : ''}>${d}</option>`).join('')}</select></label><div class="banner warn">${st('warn', t('Note'))}<span>${t('The rootfs is assembled from the distribution and is not Yocto-built. It is outside Yocto SBOM and CVE tracking.')}</span></div>` : ''}</div></div></div>
  <div class="stack"><div class="panel flush"><div class="panel-head">${t('Compatibility checks')}</div>${compat.map((c) => `<div class="issue"><div>${st(c.state, c.label)}</div><div></div><div class="fix">${esc(c.detail)}</div></div>`).join('')}</div>
  <div class="panel"><h3>${t('How to generate')}${tip(t('Thin overlay reuses an existing vendor or community BSP and adds only your changes. From scratch writes a full machine definition.'))}</h3><div class="stack" style="margin-top:8px"><label class="row"><input type="radio" name="ov" data-ch="proj" data-k="overlay_mode" value="overlay" ${p.overlay_mode !== 'scratch' ? 'checked' : ''}> ${t('Thin overlay on the existing BSP (recommended when one exists)')}</label><label class="row"><input type="radio" name="ov" data-ch="proj" data-k="overlay_mode" value="scratch" ${p.overlay_mode === 'scratch' ? 'checked' : ''}> ${t('From scratch')}</label>
  <label class="f"><span>${t('Maintainer name in the layer')}</span><input value="${esc(p.author || '')}" placeholder="${esc(S.brand.designer)}" data-ch="proj" data-k="author"></label></div></div></div></div>`;
};

/* ---------- Review ---------- */
PAGES.review = async (main) => {
  if (needProject(main)) return;
  const p = S.project, plan = await api('review'); const v = S.v;
  const tree = {}; (plan.files || []).forEach((f) => { let n = tree; f.path.split('/').forEach((seg, i, a) => { if (i === a.length - 1) n[seg] = f; else n = n[seg] ||= {}; }); });
  const render = (n) => Object.entries(n).map(([k, val]) => val.path ? `<div class="f"><span>${esc(k)}</span><span>${esc(val.purpose)}${val.bytes ? ', ' + fmtBytes(val.bytes) : ''}</span></div>` : `<details open><summary>${esc(k)}/</summary>${render(val)}</details>`).join('');
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Review')}</h1><p>${t('Everything that will be produced, before you generate it.')}</p></div><button class="btn primary" data-act="nav" data-r="generate" ${v.sections.generate ? '' : 'disabled'}>${t('Continue to Generate')}</button></div>
  ${v.can_generate ? '' : `<div class="banner err" style="margin-bottom:14px">${st('fail', t('Blocked'))}<span>${v.blocking} ${t('blocking issue(s). Fix or waive them on the Peripherals or Software page.')}</span></div>`}
  <div class="grid2"><div class="panel"><h3>${t('Your selection')}</h3><div class="tree"><details open><summary>${esc(p.name)}</summary><div class="f"><span>${t('SoC')}</span><span>${esc(p.soc)}</span></div><div class="f"><span>${t('Release')}</span><span>${esc(p.release)}</span></div><div class="f"><span>${t('Kernel')}</span><span>${esc(p.kernel)}</span></div><div class="f"><span>${t('Image')}</span><span>${esc(p.image)}${p.distro ? ' / ' + esc(p.distro) : ''}</span></div><div class="f"><span>RAM</span><span>${esc(p.ram.type)} ${p.ram.size_mb} MB</span></div>
  <details open><summary>${t('Peripherals')} (${p.peripherals.length})</summary>${p.peripherals.map((x) => `<div class="f"><span>${esc(x.category)}: ${esc((S.icCache || {})[x.ic] || x.ic)}</span><span>${esc(x.driver || t('no driver'))}</span></div>`).join('')}</details></details></div></div>
  <div class="panel"><h3>${t('Files that will be produced')}</h3>${plan.ok ? `<div class="tree">${render(tree)}</div>` : `<p>${st('fail', t('Cannot plan'))} ${esc(plan.error)}</p>`}</div></div>
  <div class="panel flush" style="margin-top:14px"><div class="panel-head">${t('Warnings and waivers')}</div>${v.issues.filter((i) => i.severity !== 'info').map(issueRow).join('') || `<div class="empty">${t('No warnings.')}</div>`}</div>`;
};

/* ---------- Generate ---------- */
PAGES.generate = async (main) => {
  if (needProject(main)) return;
  const sum = await api('summary'); const o = S.genOpts ||= { build: false, policy: 'merge', outdir: '', notify: true };
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Generate')}</h1><p id="gen-sub">${t('One action writes the layer, machine, device tree, U-Boot and kernel config, image recipe, pinned build file, flashing guide and the PDF report.')}</p></div>
    <button class="btn primary" id="gen-go" data-act="gen">${t('Generate')}</button><button class="btn" data-act="job" data-a="pause" id="b-pause">${t('Pause')}</button><button class="btn" data-act="job" data-a="resume" id="b-resume">${t('Resume')}</button><button class="btn danger" data-act="job" data-a="cancel" id="b-cancel">${t('Cancel')}</button></div>
  <div class="panel" style="margin-bottom:14px"><div class="row"><label class="row"><input type="checkbox" id="o-build" ${o.build ? 'checked' : ''} ${sum.backend ? '' : 'title="' + t('No build backend found') + '"'}> ${t('Also build images with BitBake')}${tip(t('Needs a build backend (kas with Docker or Podman, or WSL2). Without it, files and the report are still generated.'))}</label>
  <label class="f" style="flex-direction:row;align-items:center;gap:8px"><span>${t('If you edited a generated file')}</span><select id="o-policy"><option value="merge" ${o.policy === 'merge' ? 'selected' : ''}>${t('Write side by side to merge')}</option><option value="keep" ${o.policy === 'keep' ? 'selected' : ''}>${t('Keep my file')}</option><option value="replace" ${o.policy === 'replace' ? 'selected' : ''}>${t('Replace with the new one')}</option></select></label>
  <label class="row"><input type="checkbox" id="o-notify" ${o.notify ? 'checked' : ''}> ${t('Notify me when done')}</label></div>
  ${o.build && !sum.backend ? `<p class="small" style="color:var(--warn);margin-top:8px">${t('No build backend found. The build stage will stop with instructions; retry from that stage after installing one.')}</p>` : ''}
  ${o.build && sum.resources.warnings.length ? `<p class="small" style="color:var(--warn);margin-top:8px">${t('This host is below the recommended size for a full build:')} ${esc(sum.resources.warnings.join('; '))}</p>` : ''}</div>
  <div class="grid2"><div class="stack"><div class="panel flush"><div class="panel-head">${t('Stages')}</div><ol class="stages" id="stages"></ol></div></div>
  <div class="stack"><div class="panel"><div class="row" style="justify-content:space-between"><b id="banner">${t('Idle')}</b><span class="muted small" id="timing"></span></div><div class="bigbar" role="progressbar" aria-label="${t('Overall progress')}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="pbar" style="margin:10px 0 6px"><i style="width:0"></i></div><div class="row" style="justify-content:space-between"><span id="pct" class="muted small">0%</span><span class="muted small">${t('Weighted by how long each stage usually takes. Time left is an estimate.')}</span></div></div>
  <div id="fail"></div><div id="after"></div></div></div>
  <div class="panel" style="margin-top:14px"><div class="row" style="margin-bottom:8px"><b>${t('Live log')}</b><input id="log-q" type="search" placeholder="${t('Search log')}" aria-label="${t('Search log')}"><select id="log-lvl" aria-label="${t('Severity')}"><option value="">${t('All severities')}</option><option value="warning">${t('Warnings and errors')}</option><option value="error">${t('Errors only')}</option></select><button class="btn sm" data-act="copy-err">${t('Copy error')}</button></div><div class="log" id="log" role="log" aria-live="off" tabindex="0"></div></div>`;
  $('#o-build').onchange = (e) => { o.build = e.target.checked; route(); }; $('#o-policy').onchange = (e) => (o.policy = e.target.value); $('#o-notify').onchange = (e) => (o.notify = e.target.checked);
  $('#log-q').oninput = () => drawLog(S.job); $('#log-lvl').onchange = () => drawLog(S.job);
  const snap = await api('job'); S.job = snap; updateGenerate(snap); startJobPoll();
};
let lastLogN = -1;
function drawLog(snap) {
  const box = $('#log'); if (!box || !snap) return; const q = ($('#log-q')?.value || '').toLowerCase(), lvl = $('#log-lvl')?.value || '';
  const rows = (snap.log || []).filter((l) => (!q || l.msg.toLowerCase().includes(q) || l.stage.includes(q)) && (!lvl || (lvl === 'error' ? l.level === 'error' : ['warning', 'error'].includes(l.level))));
  const sig = rows.length + ':' + q + lvl; if (sig === lastLogN) return; lastLogN = sig;
  const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.innerHTML = rows.map((l) => `<div class="l-${esc(l.level)}"><span class="t">${l.t.toFixed(1)}s</span> [${esc(l.stage)}] ${esc(l.msg)}</div>`).join('') || `<span class="muted">${t('The log appears here while a job runs.')}</span>`;
  if (atEnd) box.scrollTop = box.scrollHeight;
}
function updateGenerate(s) {
  if (!$('#stages')) return;
  $('#stages').innerHTML = s.stages.map((x, i) => `<li class="${s.current?.stage === x.id && s.state === 'running' ? 'cur' : ''}"><span class="muted small">${i + 1}</span><span>${t(x.label)}<div class="muted small">${esc(x.message || '')}</div></span>${st(x.state)}</li>`).join('');
  const bar = $('#pbar'); bar.setAttribute('aria-valuenow', s.percent); bar.firstElementChild.style.width = s.percent + '%'; bar.className = 'bigbar ' + (s.state === 'failed' ? 'fail' : s.state === 'done' ? 'done' : '');
  $('#pct').textContent = Math.round(s.percent) + '%'; $('#banner').textContent = s.state === 'idle' ? t('Idle') : s.banner;
  $('#timing').textContent = s.state === 'idle' ? '' : `${t('Elapsed')} ${fmtTime(s.elapsed)}${s.eta != null && s.state === 'running' ? `, ${t('about')} ${fmtTime(s.eta)} ${t('left (estimate)')}` : ''}`;
  const running = ['running', 'paused'].includes(s.state);
  $('#gen-go').disabled = running || !S.v?.sections?.generate; $('#b-pause').disabled = s.state !== 'running'; $('#b-resume').disabled = s.state !== 'paused'; $('#b-cancel').disabled = !running;
  const f = s.failure; $('#fail').innerHTML = f ? `<div class="panel" style="border-color:var(--err)"><h3>${st('fail', t('Stopped at') + ' ' + t(s.stages.find((x) => x.id === f.stage)?.label || f.stage))}</h3><p>${esc(f.message)}</p><pre class="log" style="height:auto;max-height:140px">${esc(f.excerpt || '')}</pre><div class="row"><button class="btn primary" data-act="job" data-a="retry">${t('Retry from this stage')}</button>${f.uid ? `<button class="btn" data-act="selperi" data-uid="${esc(f.uid)}">${t('Go to the choice that caused it')}</button>` : `<button class="btn" data-act="nav" data-r="peripherals">${t('Review your choices')}</button>`}</div></div>` : '';
  const ch = s.changes || {}; const n = (k) => (ch[k] || []).length;
  $('#after').innerHTML = s.state === 'done' ? `<div class="panel"><h3>${st('done', t('Finished'))}</h3><p>${t('Added')} ${n('added')}, ${t('changed')} ${n('changed')}, ${t('unchanged')} ${n('unchanged')}, ${t('removed')} ${n('removed')}. ${n('needs_merge') ? `<b>${n('needs_merge')} ${t('file(s) need a merge')}</b> (${t('see the .ybsg-new files')}).` : ''} ${n('user_kept') ? `${n('user_kept')} ${t('of your edited files kept.')}` : ''}</p><details><summary>${t('Show the diff of changes')}</summary><div class="tree" style="margin-top:6px">${['added', 'changed', 'removed', 'needs_merge', 'user_kept'].map((k) => (ch[k] || []).map((f) => `<div class="f"><span>${esc(f)}</span><span>${t(k.replace('_', ' '))}</span></div>`).join('')).join('') || ''}</div></details><div class="row" style="margin-top:8px"><button class="btn primary" data-act="nav" data-r="reports">${t('Open the report')}</button><a class="btn" href="/api/export_out?kind=zip" download>${t('Export as zip')}</a><a class="btn" href="/api/export_out?kind=kas" download>${t('Export kas file')}</a><button class="btn" data-act="export-git">${t('Export as git repository...')}</button></div></div>` : '';
  drawLog(s);
}
function startJobPoll() {
  if (S.jobPoll) return;
  S.jobPoll = setInterval(async () => {
    try {
      const s = await api('job'); S.job = s; const prev = S.jobState; S.jobState = s.state;
      $('#sb-job').textContent = s.state === 'idle' ? t('No job running') : `${t('Job')}: ${s.banner} (${Math.round(s.percent)}%)`;
      if (parseHash().route === 'generate') updateGenerate(s);
      if (prev !== s.state) { renderSide(parseHash().route); if (['done', 'failed', 'cancelled'].includes(s.state) && prev) toast(t('Generation') + ' ' + t(s.state), s.state === 'done' ? 'ok' : 'warn'); }
      if (!['running', 'paused'].includes(s.state) && parseHash().route !== 'generate' && S.jobPoll) { clearInterval(S.jobPoll); S.jobPoll = null; }
    } catch { /* server busy */ }
  }, 500);
}

/* ---------- Local database ---------- */
PAGES.database = async (main, arg) => {
  const boards = await api('list/board'); const sel = arg || S.dbBoard || boards[0]?.id; S.dbBoard = sel;
  const [st0, b] = await Promise.all([api('db/status'), sel ? api('get/board/' + encodeURIComponent(sel)) : null]);
  const soc = b?.soc_rec || {};
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Local database')}</h1><p>${t('Boards, pictures and details are stored on this machine and read without a network. You decide when to look for updates.')}</p></div>
    <button class="btn primary" data-act="dbcheck" data-apply="0">${t('Check for updates')}</button><button class="btn" data-act="dbcheck" data-apply="1">${t('Update')}</button><button class="btn" data-act="dbfind">${t('Find more boards')}</button><button class="btn" data-act="dbctl" data-a="pause" id="dbp">${t('Pause')}</button><button class="btn" data-act="dbctl" data-a="resume">${t('Resume')}</button><button class="btn danger" data-act="dbctl" data-a="cancel">${t('Cancel')}</button></div>
  <div id="dbbanner" class="banner info" style="margin-bottom:14px"></div>
  <div class="grid2" style="grid-template-columns:260px minmax(0,1fr) 300px;align-items:start"><div class="panel flush scroll"><div class="panel-head">${t('Boards')}</div>${boards.map((x) => `<button class="nav" style="height:auto;padding:8px 12px" ${x.id === sel ? 'aria-current="page"' : ''} data-act="dbsel" data-id="${esc(x.id)}"><span class="label">${esc(x.name)}<div class="muted small">${esc(x.soc_name)}</div></span></button>`).join('')}</div>
  <div class="stack">${b ? `<div class="panel"><div class="row" style="align-items:flex-start"><img class="hero-img" style="max-width:280px" src="/api/image/${encodeURIComponent(b.id)}" alt="${esc(b.name)}"><div style="flex:1;min-width:220px"><h2>${esc(b.name)}</h2><p class="muted">${esc(soc.name || '')}, ${esc(b.vendor)}, ${esc(b.status)} ${conf(b._prov.confidence)}</p><p class="muted small">${b.image ? `${t('Picture source')}: ${esc(b.image.source_url)}. ${t('Licence')}: ${esc(b.image.licence)}. ${t('Fetched')} ${esc(b.image.fetched)}.` : t('No picture stored. A placeholder is shown.')}</p><div class="row"><button class="btn sm" data-act="use-board" data-id="${esc(b.id)}">${t('Use this board')}</button><button class="btn sm danger" data-act="dbremove" data-id="${esc(b.id)}">${t('Remove from database')}</button></div></div></div></div>
    <div class="grid2"><div class="panel"><h3>${t('Peripherals')}</h3>${(b.preset || []).map((x) => `<div>${esc(x.category)}: ${esc(x.ic)}</div>`).join('') || `<span class="muted">${t('None recorded')}</span>`}<h3 style="margin-top:12px">${t('BSP layers')}</h3>${(b.layers || []).map((x) => `<code>${esc(x)}</code>`).join(' ')}</div>
    <div class="panel"><h3>${t('Drivers and versions')}</h3>${(b.preset || []).map((x) => `<div>${esc(x.driver)}</div>`).join('') || `<span class="muted">${t('None recorded')}</span>`}<h3 style="margin-top:12px">${t('Datasheet links')}</h3>${soc.datasheet ? `<a href="${esc(soc.datasheet)}" target="_blank" rel="noopener">${esc(soc.datasheet)}</a>` : `<span class="muted">${t('None')}</span>`}<p class="muted small" style="margin-top:8px">${t('Source')}: ${esc(b._prov.source_url)}, ${t('fetched')} ${esc(b._prov.fetched)}</p></div></div>` : `<div class="empty">${t('No boards yet. Use Find more boards.')}</div>`}
  <div class="panel"><h3>${t('Add your own record')}${tip(t('Your records are marked unverified and are never overwritten by a sync.'))}</h3><div class="form-grid" style="margin-top:8px"><label class="f"><span>${t('Id')}</span><input id="ur-id" placeholder="my-board"></label><label class="f"><span>${t('Name')}</span><input id="ur-name" placeholder="My board"></label><label class="f"><span>${t('Vendor')}</span><input id="ur-vendor"></label><label class="f"><span>${t('SoC')}</span><select id="ur-soc">${Object.keys(SOCS).length ? '' : ''}${(await api('list/soc')).map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</select></label></div><div class="row" style="margin-top:10px"><button class="btn" data-act="adduser">${t('Add board')}</button></div></div></div>
  <div class="stack"><div class="panel"><h3>${t('New boards')}</h3><div id="newboards"></div></div><div class="panel"><dl class="kv" style="grid-template-columns:110px 1fr"><dt>${t('Size on disk')}</dt><dd id="dbsize">${fmtBytes(st0.size)}</dd><dt>${t('Last checked')}</dt><dd id="dbchk">${esc(st0.last_checked || t('never'))}</dd></dl></div></div></div>`;
  S.dbStatus = st0; drawDb(st0); startDbPoll();
};
function drawDb(s) {
  const x = s.status; drawChip(x); const b = $('#dbbanner'); if (!b) return; const map = { idle: 'info', looking: 'info', found: 'info', downloading: 'info', verifying: 'info', saved: 'ok', up_to_date: 'ok', not_found: 'warn', failed: 'err', new_boards: 'ok' };
  b.className = 'banner ' + (map[x.state] || 'info'); const lab = { idle: t('Idle'), looking: t('Looking'), found: t('Found'), downloading: t('Downloading'), verifying: t('Verifying'), saved: t('Saved'), up_to_date: t('Up to date'), not_found: t('Not found'), failed: t('Failed'), new_boards: t('New boards found') }[x.state] || x.state;
  b.innerHTML = `${st(x.state === 'failed' ? 'fail' : x.state === 'not_found' ? 'warn' : ['saved', 'up_to_date', 'new_boards'].includes(x.state) ? 'ok' : 'info', lab)}<span style="flex:1">${esc(x.message || '')}</span>${x.percent != null ? `<span class="muted small">${Math.round(x.percent)}%</span>` : ''}${x.retry ? `<button class="btn sm" data-act="dbfind">${t('Retry')}</button>` : ''}`;
  const nb = $('#newboards'); if (nb) nb.innerHTML = s.discovered.length ? `<div style="max-height:260px;overflow:auto">${s.discovered.map((d) => `<label class="row" style="padding:3px 0"><input type="checkbox" class="nbx" value="${esc(d.id)}"><span>${esc(d.name)}<div class="muted small">${esc(d.source)}</div></span></label>`).join('')}</div><div class="row" style="margin-top:8px"><button class="btn sm primary" data-act="dbdl">${t('Download selected')}</button></div>` : `<p class="muted">${t('Run Find more boards. Boards not yet in the local database are listed here for you to choose.')}</p>`;
  if ($('#dbsize')) { $('#dbsize').textContent = fmtBytes(s.size); $('#dbchk').textContent = s.last_checked || t('never'); }
  drawChip(x);
}
function drawChip(x) {
  const chip = $('#db-chip'); chip.className = 'chip ' + (['looking', 'found', 'downloading', 'verifying'].includes(x.state) ? 'busy' : x.state === 'failed' ? 'bad' : ''); chip.querySelector('span').textContent = t('Database') + ': ' + (['looking', 'found', 'downloading', 'verifying'].includes(x.state) ? t('syncing') : x.state === 'failed' ? t('sync failed') : t('synced'));
  $('#sb-db').textContent = x.message && x.state !== 'idle' ? x.message : t('Local database ready');
}
let dbPoll = null;
function startDbPoll() {
  if (dbPoll) return;
  dbPoll = setInterval(async () => { try { const s = await api('db/status'); S.dbStatus = s; drawDb(s); const active = ['looking', 'found', 'downloading', 'verifying'].includes(s.status.state); if (!active && parseHash().route !== 'database') { clearInterval(dbPoll); dbPoll = null; } } catch { /* ignore */ } }, 700);
}

/* ---------- Reports / Settings ---------- */
PAGES.reports = async (main) => {
  const list = await api('reports'); const pdf = list.find((f) => f.name.endsWith('.pdf'));
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Reports')}</h1><p>${t('Each generation ends with a PDF built only from the pinned manifest, so it matches the files exactly. It is also exported as Markdown and JSON.')}</p></div></div>
  ${list.length ? `<div class="row" style="margin-bottom:12px">${list.map((f) => `<a class="btn" target="_blank" rel="noopener" href="/api/file?path=${encodeURIComponent(f.path)}">${esc(f.name)}</a>`).join('')}</div>${pdf ? `<iframe title="${t('PDF report')}" src="/api/file?path=${encodeURIComponent(pdf.path)}" style="width:100%;height:calc(100vh - 260px);border:1px solid var(--line);border-radius:var(--r);background:#fff"></iframe>` : ''}` : `<div class="empty"><h2>${t('No report yet')}</h2><p>${t('Generate a project to create its PDF report.')}</p><button class="btn primary" data-act="nav" data-r="generate">${t('Go to Generate')}</button></div>`}`;
};
PAGES.settings = async (main) => {
  main.innerHTML = `<div class="page-head"><div class="grow"><h1>${t('Settings')}</h1></div></div><div class="grid2"><div class="panel stack"><h3>${t('Appearance')}</h3>
  <label class="f"><span>${t('Theme')}</span><select id="set-theme"><option value="">${t('Follow the system')}</option><option value="dark" ${ls.get('theme') === 'dark' ? 'selected' : ''}>${t('Dark')}</option><option value="light" ${ls.get('theme') === 'light' ? 'selected' : ''}>${t('Light')}</option></select></label>
  <label class="row"><input type="checkbox" id="set-motion" ${ls.get('motion') === 'off' ? 'checked' : ''}> ${t('Turn animations off')}</label></div>
  <div class="panel stack"><h3>${t('Privacy')}</h3><label class="row"><input type="checkbox" disabled> ${t('Share anonymous usage data')}</label><p class="muted small">${t('Off. Telemetry is opt-in and no project content leaves this machine without your consent.')}</p><h3>${t('About')}</h3><button class="btn" data-act="about">${t('About YBSG')}</button></div></div>`;
  $('#set-theme').onchange = (e) => { ls.set('theme', e.target.value); applyTheme(); }; $('#set-motion').onchange = (e) => { ls.set('motion', e.target.checked ? 'off' : 'on'); applyTheme(); };
};
function applyTheme() { const th = ls.get('theme', ''); if (th) document.documentElement.dataset.theme = th; else delete document.documentElement.dataset.theme; document.body.classList.toggle('motion-off', ls.get('motion') === 'off'); }

/* ---------- dialogs ---------- */
function aboutDialog() {
  const b = S.brand;
  modal(`<h2>${t('About')} ${esc(b.product)}</h2><div class="row" style="align-items:center;margin-bottom:12px"><svg viewBox="0 0 64 64" width="56" height="56" style="color:var(--accent)"><rect x="18" y="18" width="28" height="28" rx="4" fill="none" stroke="currentColor" stroke-width="3"/><path d="M6 26h12M6 38h12M46 26h12M46 38h12M26 6v12M38 6v12M26 46v12M38 46v12" stroke="var(--trace)" stroke-width="3" fill="none"/></svg><div><b style="font-size:var(--t4)">${esc(b.product)}</b><div class="muted">${esc(b.product_long)}</div></div></div>
  <dl class="kv"><dt>${t('Version')}</dt><dd>${esc(b.version)}, ${t('build')} ${esc(b.build_number)}, ${esc(b.build_date)}</dd><dt>${t('Tool designer')}</dt><dd>${esc(b.designer)}</dd><dt>${t('Company')}</dt><dd>${esc(b.company)}</dd><dt>${t('Website')}</dt><dd><a href="${esc(b.website_url)}" data-act="site" target="_blank" rel="noopener">${esc(b.website)}</a></dd><dt>${t('Yocto baseline')}</dt><dd>${esc(b.yocto_baseline)}</dd><dt>${t('Local database')}</dt><dd>${t('version')} ${esc(b.about_db.version)}, ${t('last sync')} ${esc(b.about_db.last_sync)}</dd></dl>
  <p class="muted" style="margin-top:12px">${esc(b.rights)}</p>
  <details style="margin-top:8px"><summary>${t('Open-source licences of bundled components')}</summary><ul class="small"><li>Python and standard library: PSF License</li><li>ReportLab: BSD license</li><li>PyInstaller (bootloader): GPL-2.0 with the bootloader exception</li><li>pywebview (optional): BSD-3-Clause</li><li>SQLite: public domain</li></ul></details>
  <div class="foot"><button class="btn" data-act="copyver">${t('Copy version info')}</button><button class="btn primary" data-close data-act="closem">${t('Close')}</button></div>`);
}
function newProjectDialog(socs) {
  modal(`<h2>${t('Custom board on a SoC')}</h2><label class="f"><span>${t('SoC')}</span><select id="np-soc">${socs.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} (${esc(s.vendor)})</option>`).join('')}</select></label><label class="f" style="margin-top:10px"><span>${t('Project name')}</span><input id="np-name" placeholder="${t('My custom board')}"></label><div class="foot"><button class="btn" data-act="closem">${t('Cancel')}</button><button class="btn primary" data-act="np-go">${t('Create project')}</button></div>`);
}
function customIcDialog() {
  modal(`<h2>${t('Custom IC')}</h2><p class="muted">${t('Enter what the device tree node needs. YBSG writes the node and a kernel config fragment.')}</p><div class="form-grid"><label class="f"><span>${t('Name')}</span><input id="ci-id" placeholder="acme-sensor"></label><label class="f"><span>${t('Compatible string')}</span><input id="ci-comp" placeholder="acme,sensor-x1"></label><label class="f"><span>${t('Bus')}</span><input id="ci-bus" placeholder="i2c1"></label><label class="f"><span>${t('Address')}</span><input id="ci-addr" placeholder="0x48"></label><label class="f"><span>${t('Interrupts')}</span><input id="ci-irq" placeholder="12 IRQ_TYPE_EDGE_FALLING"></label><label class="f"><span>${t('GPIOs')}</span><input id="ci-gpio" placeholder="&amp;gpio1 5 GPIO_ACTIVE_HIGH"></label><label class="f"><span>${t('Clocks')}</span><input id="ci-clk"></label><label class="f"><span>${t('Regulators')}</span><input id="ci-reg" placeholder="vdd-3v3"></label><label class="f" style="grid-column:1/-1"><span>${t('Kernel config symbols (optional)')}</span><input id="ci-kc" placeholder="CONFIG_MY_DRIVER"></label></div><div class="foot"><button class="btn" data-act="closem">${t('Cancel')}</button><button class="btn primary" data-act="ci-go">${t('Add IC')}</button></div>`);
}
function customUsbDialog() {
  modal(`<h2>${t('Custom USB class')}</h2><p class="muted">${t('Creates a FunctionFS gadget function and the kernel symbols it needs.')}</p><div class="form-grid"><label class="f"><span>${t('Function name')}</span><input id="cu-name" placeholder="myfunc"></label><label class="f"><span>${t('Class (hex)')}</span><input id="cu-class" value="ff"></label><label class="f"><span>${t('Subclass (hex)')}</span><input id="cu-sub" value="00"></label><label class="f"><span>${t('Protocol (hex)')}</span><input id="cu-proto" value="00"></label></div><div class="foot"><button class="btn" data-act="closem">${t('Cancel')}</button><button class="btn primary" data-act="cu-go">${t('Add function')}</button></div>`);
}
async function icDialog(id) {
  const ic = await api('get/ic/' + encodeURIComponent(id));
  modal(`<h2>${esc(ic.part)}</h2><dl class="kv"><dt>${t('Vendor')}</dt><dd>${esc(ic.vendor)}</dd><dt>${t('Category')}</dt><dd>${esc(ic.category)}</dd><dt>${t('Interface')}</dt><dd>${esc(ic.interface)}</dd><dt>${t('Voltage')}</dt><dd>${esc(ic.voltage)}</dd><dt>${t('Package')}</dt><dd>${esc(ic.package)}</dd><dt>${t('Datasheet')}</dt><dd>${ic.datasheet ? `<a href="${esc(ic.datasheet)}" target="_blank" rel="noopener">${esc(ic.datasheet)}</a>` : t('none recorded')}</dd><dt>${t('Confidence')}</dt><dd>${conf(ic._prov.confidence)}</dd></dl>
  <h3 style="margin-top:12px">${t('Drivers')}</h3>${ic.drivers.map((d) => `<div style="padding:4px 0"><b>${esc(d.name)}</b> <span class="muted small">${esc(d.source)}, ${esc(d.first_kernel)} to ${esc(d.removed_in || d.last_verified)}</span></div>`).join('') || `<p>${st('warn', t('No driver'))} ${t('Use the custom driver slot when you add this IC.')}</p>`}<div class="foot"><button class="btn primary" data-close data-act="closem">${t('Close')}</button></div>`);
}
function compareDialog(boards, socs) {
  const rows = [[t('SoC'), (b) => socs[b.soc]?.name], [t('Vendor'), (b) => b.vendor], [t('Architecture'), (b) => socs[b.soc]?.arch], [t('Cores'), (b) => socs[b.soc]?.cores], [t('Clock (MHz)'), (b) => socs[b.soc]?.clock_mhz], [t('Memory'), (b) => `${b.ram?.type} ${b.ram?.size_mb} MB`], [t('BSP layers'), (b) => (b.layers || []).join(', ')], [t('Kernel support'), (b) => (socs[b.soc]?.kernels || []).join(', ')], [t('Status'), (b) => b.status], [t('Peripherals'), (b) => (b.preset || []).map((p) => p.category).join(', ')], [t('Data confidence'), (b) => b._prov.confidence]];
  modal(`<h2>${t('Compare')} ${boards.length}</h2><div style="overflow:auto"><table class="tbl"><thead><tr><th></th>${boards.map((b) => `<th>${esc(b.name)}</th>`).join('')}</tr></thead><tbody>${rows.map(([l, f]) => `<tr><th>${esc(l)}</th>${boards.map((b) => `<td>${esc(f(b) ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div><div class="foot"><button class="btn primary" data-close data-act="closem">${t('Close')}</button></div>`);
}

/* ---------- actions (event delegation) ---------- */
const ACT = {
  nav: (el) => { if (el.getAttribute('aria-disabled') === 'true') { toast(el.title || t('Not available yet'), 'warn'); return; } go(el.dataset.r); },
  board: (el) => go('board/' + encodeURIComponent(el.dataset.id)),
  cmp: (el) => { const id = el.dataset.id; if (el.checked) { if (S.cmp.length >= 4) { el.checked = false; toast(t('You can compare up to four.'), 'warn'); return; } S.cmp.push(id); } else S.cmp = S.cmp.filter((x) => x !== id); S.drawBoards?.(); },
  compare: async () => { const bs = await Promise.all(S.cmp.map((id) => api('get/board/' + encodeURIComponent(id)))); const socs = {}; bs.forEach((b) => (socs[b.soc] = b.soc_rec)); compareDialog(bs, socs); },
  bview: (el) => { S.boardView = el.dataset.v; route(); },
  soctab: (el) => { S.socTab = el.dataset.v; route(); },
  'use-board': async (el) => { const r = await mutate('project/new', { board: el.dataset.id }, false); if (r) { toast(`${t('Project created from')} ${S.project.name}`); S.sel = { cat: 'usb_host', ic: null, uid: null, view: 'board' }; go('peripherals'); } },
  'custom-board': async (el) => { const r = await mutate('project/new', { soc: el.dataset.id }, false); if (r) go('peripherals'); },
  'custom-soc': async () => newProjectDialog(await api('list/soc')),
  'np-go': async () => { const r = await mutate('project/new', { soc: $('#np-soc').value, name: $('#np-name').value || undefined }, false); closeModal(); if (r) go('peripherals'); },
  'open-saved': async (el, e) => { e?.preventDefault(); const r = await mutate('project/open', { uid: el.dataset.uid }, false); if (r) go('peripherals'); },
  dup: async () => { const r = await mutate('project/duplicate', {}, false); if (r) toast(t('Duplicated')); },
  'export-git': async () => { const dest = prompt(t('New git repository folder (full path, must be empty or new)')); if (dest) { const r = await mutate('export_git', { dest }, false); if (r) toast(`${t('Git repository created at')} ${r.repo}`); } },
  'save-as': async () => { const path = prompt(t('Save a copy as (full path ending in .ybsg)')); if (path) { const r = await mutate('project/save', { path }, false); if (r) toast(`${t('Saved')} ${r.saved}`); } },
  cat: (el) => { S.sel.cat = el.dataset.k; S.sel.ic = null; S.sel.uid = null; route(); },
  bview2: (el) => { S.sel.view = el.dataset.v; route(); },
  selic: (el) => { const o = (S.offers || []).find((x) => x.id === el.dataset.id); if (o && !o.enabled) { toast(o.reason, 'warn'); return; } S.sel.ic = el.dataset.id; route(); },
  selperi: (el) => { const x = S.project.peripherals.find((q) => q.uid === el.dataset.uid); if (x) { S.sel.uid = x.uid; S.sel.cat = x.category; go('peripherals'); route(); } else go('peripherals'); },
  rm: async (el) => { S.sel.uid = null; await mutate('project/remove', { uid: el.dataset.uid }); },
  waive: (el) => mutate('project/waive', { id: el.dataset.id }), unwaive: (el) => mutate('project/unwaive', { id: el.dataset.id }),
  'custom-ic': () => customIcDialog(), 'custom-usb': () => customUsbDialog(),
  'ci-go': async () => { const r = await mutate('project/custom_ic', { id: $('#ci-id').value.trim(), compatible: $('#ci-comp').value.trim(), bus: $('#ci-bus').value.trim() || 'i2c1', address: $('#ci-addr').value.trim() || '0x50', interrupts: $('#ci-irq').value, gpios: $('#ci-gpio').value, clocks: $('#ci-clk').value, regulators: $('#ci-reg').value, kconfig: $('#ci-kc').value }, false); if (r) { closeModal(); toast(t('Custom IC added')); route(); } },
  'cu-go': async () => { const n = $('#cu-name').value.trim(); if (!n) { toast(t('Give the function a name'), 'warn'); return; } const r = await mutate('project/custom_usb', { name: n, class: $('#cu-class').value, subclass: $('#cu-sub').value, protocol: $('#cu-proto').value }, false); if (r) { closeModal(); toast(t('Custom USB function added')); route(); } },
  closem: () => closeModal(),
  about: () => { $('#help-menu').hidden = true; aboutDialog(); },
  copyver: async () => { const b = S.brand; const txt = `${b.product} ${b.version} (build ${b.build_number}, ${b.build_date})\nYocto baseline: ${b.yocto_baseline}\nDatabase: ${b.about_db.version}, last sync ${b.about_db.last_sync}\n${b.company}`; try { await navigator.clipboard.writeText(txt); toast(t('Version info copied')); } catch { toast(t('Copy failed. Select the text manually.'), 'warn'); } },
  site: (el, e) => { e.preventDefault(); api('open_url', { url: el.href }).catch(() => window.open(el.href, '_blank')); },
  ic: (el, e) => { e?.preventDefault(); icDialog(el.dataset.id); },
  gen: async () => { const o = S.genOpts; lastLogN = -1; try { await api('generate', { build: o.build, policy: o.policy, notify: o.notify }); toast(t('Generation started')); startJobPoll(); } catch (e) { toast(e.message, 'err'); } },
  job: async (el) => { try { await api('job/' + el.dataset.a, {}); startJobPoll(); } catch (e) { toast(e.message, 'err'); } },
  'copy-err': async () => { const f = S.job?.failure; const txt = f ? `${f.stage}: ${f.message}\n${f.excerpt || ''}` : (S.job?.log || []).filter((l) => l.level === 'error').map((l) => l.msg).join('\n'); if (!txt) { toast(t('No error to copy'), 'ok'); return; } try { await navigator.clipboard.writeText(txt); toast(t('Error copied')); } catch { toast(t('Copy failed'), 'warn'); } },
  dbcheck: async (el) => { await api('db/check', { apply: el.dataset.apply === '1', ids: S.dbBoard && el.dataset.single ? [S.dbBoard] : undefined }); startDbPoll(); },
  dbfind: async () => { await api('db/find', {}); startDbPoll(); },
  dbctl: async (el) => { await api('db/' + el.dataset.a, {}); },
  dbdl: async () => { const ids = $$('.nbx:checked').map((x) => x.value); if (!ids.length) { toast(t('Tick the boards to download'), 'warn'); return; } await api('db/download', { ids }); startDbPoll(); },
  dbsel: (el) => { S.dbBoard = el.dataset.id; route(); },
  dbremove: async (el) => { if (confirm(t('Remove this board and its picture from the local database?'))) { await api('db/remove', { id: el.dataset.id }); S.dbBoard = null; toast(t('Removed')); route(); } },
  adduser: async () => { const id = $('#ur-id').value.trim(); if (!id || !$('#ur-name').value.trim()) { toast(t('Give the board an id and a name'), 'warn'); return; } try { await api('db/user_record', { entity: 'board', record: { id, name: $('#ur-name').value.trim(), vendor: $('#ur-vendor').value, soc: $('#ur-soc').value, status: 'user', layers: [], machine: '', url: '', preset: [], ram: { type: '', size_mb: 0, bus_width: 32 } } }); toast(t('Added. It is marked unverified.')); route(); } catch (e) { toast(e.message, 'err'); } },
  resolve: async (el) => { await api('db/resolve_dispute', { entity: el.dataset.e, id: el.dataset.id }); toast(t('Dispute cleared')); route(); },
};
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act]'); if (!el) { if (!e.target.closest('.menu-wrap')) $('#help-menu').hidden = true; return; }
  const fn = ACT[el.dataset.act]; if (!fn) return;
  if (el.tagName === 'INPUT' && el.type === 'checkbox') return; // handled on change
  fn(el, e);
});
document.addEventListener('change', async (e) => {
  const el = e.target;
  if (el.dataset.act === 'cmp') return ACT.cmp(el);
  const k = el.dataset.ch; if (!k) return;
  if (k === 'bf') { S.bf[el.dataset.k] = el.value; S.drawBoards?.(); }
  else if (k === 'sf') { S.sf[el.dataset.k] = el.value; S.drawSocs?.(); }
  else if (k === 'df') { S.df[el.dataset.k] = el.value; S.drawDrivers?.(); }
  else if (k === 'proj') { const body = { [el.dataset.k]: el.value }; await mutate('project/set', body); }
  else if (k === 'ram') { const key = el.dataset.k; const val = ['size_mb', 'bus_width', 'ranks'].includes(key) ? +el.value : el.value; await mutate('project/set', { ram: { [key]: val } }); }
  else if (k === 'peri') {
    const body = { uid: el.dataset.uid }; if (el.dataset.prop) body.props = { [el.dataset.k]: el.value }; else body[el.dataset.k] = el.value;
    if (el.dataset.k === 'ic') { const ds = await api('offer/drivers?ic=' + encodeURIComponent(el.value)); body.driver = (ds.find((d) => d.enabled) || {}).id || ''; }
    await mutate('project/update_peripheral', body);
  } else if (k === 'parts') {
    const parts = el.value.split('\n').map((l) => l.trim().split(/\s+/)).filter((a) => a.length === 3).map((a) => ({ name: a[0], offset: parseInt(a[1], 16), size: parseInt(a[2], 16) })).filter((q) => !isNaN(q.offset) && !isNaN(q.size));
    await mutate('project/update_peripheral', { uid: el.dataset.uid, props: { partitions: parts } });
  }
});

/* ---------- palette, shortcuts, boot ---------- */
let palIdx = 0, palRes = [];
function openPalette() { $('#palette').hidden = false; const i = $('#pal-input'); i.value = ''; i.focus(); palSearch(''); }
function closePalette() { $('#palette').hidden = true; }
const palSearch = debounce(async (q) => { palRes = await api('search?q=' + encodeURIComponent(q) + '&limit=12'); palIdx = 0; drawPal(); }, 60);
function drawPal() { $('#pal-list').innerHTML = palRes.map((r, i) => `<li role="option" data-i="${i}" aria-selected="${i === palIdx}"><span class="k">${esc(r.type)}</span><span>${esc(r.name)}${r.vendor ? ` <span class="muted small">${esc(r.vendor)}</span>` : ''}</span></li>`).join('') || `<li class="muted">${t('Nothing matches. Check the spelling or try a vendor name.')}</li>`; }
function palGo(i) { const r = palRes[i]; if (!r) return; closePalette(); if (r.type === 'board') go('board/' + encodeURIComponent(r.id)); else if (r.type === 'soc') go('soc/' + encodeURIComponent(r.id)); else if (r.type === 'ic') icDialog(r.id); else { S.df = { q: r.name, src: '' }; go('drivers'); } }
$('#pal-input').addEventListener('input', (e) => palSearch(e.target.value));
$('#pal-input').addEventListener('keydown', (e) => { if (e.key === 'ArrowDown') { palIdx = Math.min(palRes.length - 1, palIdx + 1); drawPal(); e.preventDefault(); } else if (e.key === 'ArrowUp') { palIdx = Math.max(0, palIdx - 1); drawPal(); e.preventDefault(); } else if (e.key === 'Enter') palGo(palIdx); });
$('#pal-list').addEventListener('click', (e) => { const li = e.target.closest('li[data-i]'); if (li) palGo(+li.dataset.i); });
$('#palette').addEventListener('click', (e) => { if (e.target.id === 'palette') closePalette(); });
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  else if (e.key === 'Escape') { if (!$('#palette').hidden) closePalette(); else if (!$('#modal').hidden) closeModal(); $('#help-menu').hidden = true; }
});
$('#cmd-open').onclick = openPalette;
$('#theme-btn').onclick = () => { const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'); ls.set('theme', cur === 'dark' ? 'light' : 'dark'); applyTheme(); };
$('#menu-toggle').onclick = () => { const c = document.body.classList.toggle('nav-collapsed'); ls.set('nav', c ? 'collapsed' : 'open'); $('#menu-toggle').setAttribute('aria-label', c ? t('Expand side menu') : t('Collapse side menu')); };
$('#help-btn').onclick = () => { const m = $('#help-menu'); m.hidden = !m.hidden; $('#help-btn').setAttribute('aria-expanded', String(!m.hidden)); };
$('#help-menu').addEventListener('click', (e) => { const a = e.target.dataset.act; $('#help-menu').hidden = true; if (a === 'about') aboutDialog(); else if (a === 'shortcuts') modal(`<h2>${t('Keyboard shortcuts')}</h2><dl class="kv"><dt><kbd>Ctrl K</kbd></dt><dd>${t('Jump to any SoC, board, IC or driver')}</dd><dt><kbd>Tab</kbd></dt><dd>${t('Move between pads, parts and buttons')}</dd><dt><kbd>Enter</kbd></dt><dd>${t('Place the selected part on the focused pad')}</dd><dt><kbd>Esc</kbd></dt><dd>${t('Close a dialog or the search')}</dd></dl><div class="foot"><button class="btn primary" data-close data-act="closem">${t('Close')}</button></div>`); });
$('#proj-name').addEventListener('change', (e) => mutate('project/set', { name: e.target.value }, false));
setInterval(() => { if (S.dirty && S.project) api('project/autosave', {}).then(() => (S.dirty = false)).catch(() => {}); }, 30000);

async function boot() {
  applyTheme(); if (ls.get('nav') === 'collapsed' || innerWidth < 860) document.body.classList.add('nav-collapsed');
  const nosplash = new URLSearchParams(location.search).has('nosplash');
  if (nosplash) $('#splash').classList.add('gone');
  try { LOCALE = await (await fetch('/locales/' + (navigator.language || 'en').slice(0, 2) + '.json')).json(); } catch { LOCALE = {}; }
  let b;
  for (;;) {
    try { b = await api('boot'); } catch { await new Promise((r) => setTimeout(r, 120)); continue; }
    const done = b.steps.filter((s) => s.state === 'done').length, run = b.steps.find((s) => s.state === 'running');
    $('#splash-step').textContent = run ? t(run.name) : b.ready ? t('Ready') : t('Starting'); const bar = $('#splash-bar'); bar.setAttribute('aria-valuenow', Math.round(100 * done / b.steps.length)); bar.firstElementChild.style.width = (100 * done / b.steps.length) + '%';
    if (b.error) { $('#splash-err').hidden = false; $('#splash-err-text').textContent = b.error; $('#splash-log').onclick = async () => { const txt = await (await fetch('/api/log')).text(); modal(`<h2>${t('Start-up log')}</h2><pre class="log" style="height:300px">${esc(txt)}</pre><div class="foot"><button class="btn primary" data-close data-act="closem">${t('Close')}</button></div>`); $('#splash').classList.add('gone'); }; return; }
    if (b.ready) break; await new Promise((r) => setTimeout(r, 60));
  }
  S.brand = await api('brand');
  document.title = `${S.brand.product} - ${S.brand.product_long}`; $('#splash-ver').textContent = `${t('Version')} ${S.brand.version}`;
  $$('[data-brand]').forEach((el) => (el.textContent = S.brand[el.dataset.brand]));
  $('#app').hidden = false; $('#splash').classList.add('gone'); // closes as soon as the main window is ready
  const pr = await api('project'); S.project = pr.project; S.v = pr.validation; renderChrome();
  const d = await api('db/status'); drawDb(d);
  S.focusNext = false; await route(); S.focusNext = true;
  const j = await api('job'); S.job = j; if (['running', 'paused'].includes(j.state)) startJobPoll();
}
boot();
