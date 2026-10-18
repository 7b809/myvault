// ---------------------------------------------------------------------------
// Vaultra dashboard
// ---------------------------------------------------------------------------

const state = {
  kind: 'file',
  items: [],
  search: '',
  cameraStream: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function fmtSize(n) {
  if (!n && n !== 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, x = n;
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(x < 10 ? 2 : 1)} ${units[i]}`;
}

function emoji(cat) {
  return ({ pdf: '📕', excel: '📊', json: '🧾', png: '🖼️', jpg: '🖼️', other: '📁' })[cat] || '📁';
}

async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.detail || msg; } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res.text();
}

// ---------------------------------------------------------------------------
// Load & render
// ---------------------------------------------------------------------------
async function loadItems() {
  try {
    const data = await api(`/api/items?kind=${state.kind}`);
    state.items = data.items || [];
    render();
  } catch (err) {
    $('#grid').innerHTML = `<div class="empty">⚠️ ${esc(err.message)}</div>`;
  }
}

function render() {
  const grid = $('#grid');
  const q = state.search.trim().toLowerCase();
  const items = !q ? state.items : state.items.filter(it => {
    const hay = JSON.stringify(it).toLowerCase();
    return hay.includes(q);
  });

  $('#empty').classList.toggle('hidden', items.length > 0);
  grid.innerHTML = items.map(cardHtml).join('');
  $('#stats').textContent =
    `${items.length} ${state.kind}${items.length === 1 ? '' : 's'}`;

  bindCardActions();
}

function cardHtml(item) {
  if (state.kind === 'file') return fileCard(item);
  if (state.kind === 'link') return linkCard(item);
  return noteCard(item);
}

function fileCard(item) {
  const hasImg = item.category === 'png' || item.category === 'jpg';
  const preview = hasImg
    ? `<img src="/raw/${item.id}" alt="${esc(item.name)}" loading="lazy">`
    : `<div class="preview-icon">${emoji(item.category)}</div>`;

  return `
    <article class="card" data-id="${item.id}">
      <div class="card-preview" data-open="${item.id}">${preview}</div>
      <div class="card-body">
        <div class="card-head">
          <span class="badge badge-${esc(item.category)}">${esc(item.category)}</span>
          <span class="size">${fmtSize(item.size)}</span>
        </div>
        <h3 class="card-title">${esc(item.name)}</h3>
        ${item.description ? `<p class="card-desc">${esc(item.description)}</p>` : ''}
        <div class="card-actions">
          <a class="btn-sm" href="/viewer/${item.id}">
            <i class="bi bi-box-arrow-up-right"></i> Open
          </a>
          <a class="btn-sm" href="/download/${item.id}">
            <i class="bi bi-download"></i> Download
          </a>
          <button class="btn-sm danger" data-delete="${item.id}">
            <i class="bi bi-trash"></i> Delete
          </button>
        </div>
      </div>
    </article>`;
}

function linkCard(item) {
  return `
    <article class="card" data-id="${item.id}">
      <div class="card-body">
        <div class="card-head">
          <span class="badge"><i class="bi bi-link-45deg"></i> link</span>
        </div>
        <h3 class="card-title">${esc(item.title)}</h3>
        <a class="link-url" href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.url)}</a>
        ${item.description ? `<p class="card-desc">${esc(item.description)}</p>` : ''}
        ${item.what_it_does ? `<p class="card-desc"><strong>Does:</strong> ${esc(item.what_it_does)}</p>` : ''}
        <div class="card-actions">
          <a class="btn-sm" href="${esc(item.url)}" target="_blank" rel="noopener">
            <i class="bi bi-box-arrow-up-right"></i> Open
          </a>
          <button class="btn-sm" data-copy="${esc(item.url)}">
            <i class="bi bi-clipboard"></i> Copy
          </button>
          <a class="btn-sm" href="/viewer/${item.id}">
            <i class="bi bi-info-circle"></i> Details
          </a>
          <button class="btn-sm danger" data-delete="${item.id}">
            <i class="bi bi-trash"></i> Delete
          </button>
        </div>
      </div>
    </article>`;
}

function noteCard(item) {
  const color = item.color || '#6366f1';
  return `
    <article class="card card-note" data-id="${item.id}" style="--note-color:${esc(color)}">
      <div class="note-strip"></div>
      <div class="card-body">
        <div class="card-head">
          <span class="badge"><i class="bi bi-journal-text"></i> note</span>
        </div>
        <h3 class="card-title">${esc(item.title)}</h3>
        ${item.description ? `<p class="card-desc">${esc(item.description)}</p>` : ''}
        <pre class="note-body">${esc(item.body)}</pre>
        <div class="card-actions">
          <button class="btn-sm" data-copy="${esc(item.body)}">
            <i class="bi bi-clipboard"></i> Copy text
          </button>
          <a class="btn-sm" href="/viewer/${item.id}">
            <i class="bi bi-box-arrow-up-right"></i> Open
          </a>
          <button class="btn-sm danger" data-delete="${item.id}">
            <i class="bi bi-trash"></i> Delete
          </button>
        </div>
      </div>
    </article>`;
}

function bindCardActions() {
  $$('[data-open]').forEach(b => {
    b.onclick = () => { location.href = `/viewer/${b.dataset.open}`; };
  });
  $$('[data-delete]').forEach(b => {
    b.onclick = () => deleteItem(b.dataset.delete);
  });
  $$('[data-copy]').forEach(b => {
    b.onclick = async () => {
      try {
        await navigator.clipboard.writeText(b.dataset.copy);
        const t = b.innerHTML;
        b.innerHTML = '<i class="bi bi-check2"></i> Copied';
        setTimeout(() => { b.innerHTML = t; }, 1200);
      } catch {}
    };
  });
}

async function deleteItem(id) {
  if (!confirm('Delete this item permanently?')) return;
  try {
    await api(`/api/items/${id}`, { method: 'DELETE' });
    await loadItems();
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

// ---------------------------------------------------------------------------
// Kind tabs (top bar)
// ---------------------------------------------------------------------------
function syncKindTabs() {
  $$('#kind-tabs .tab').forEach(b => {
    const on = b.dataset.kind === state.kind;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

function switchKind(kind) {
  if (state.kind === kind && !state.search) return;
  state.kind = kind;
  state.search = '';
  $('#search').value = '';
  syncKindTabs();
  syncWrapOptions();
  loadItems();
}

$$('#kind-tabs .tab').forEach(b => {
  b.addEventListener('click', () => switchKind(b.dataset.kind));
});

$('#search').addEventListener('input', e => {
  state.search = e.target.value;
  render();
});

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------
function openModal(tab = 'file') {
  closeWrap();              // collapse the wrap options if open
  $('#modal-backdrop').classList.remove('hidden');
  setModalTab(tab);
}

function closeModal() {
  $('#modal-backdrop').classList.add('hidden');
  stopCamera();
}

function setModalTab(tab) {
  $$('#modal-tabs .mtab').forEach(b =>
    b.classList.toggle('active', b.dataset.mtab === tab)
  );
  $$('.modal-body').forEach(f =>
    f.classList.toggle('hidden', f.dataset.pane !== tab)
  );
  if (tab !== 'camera') stopCamera();
}

$('#fab').addEventListener('click', () =>
  openModal(state.kind === 'note' ? 'note' : 'file')
);
$('#modal-close').addEventListener('click', closeModal);
$('#modal-backdrop').addEventListener('click', e => {
  if (e.target.id === 'modal-backdrop') closeModal();
});
$$('#modal-tabs .mtab').forEach(b => {
  b.addEventListener('click', () => setModalTab(b.dataset.mtab));
});

// ---------------------------------------------------------------------------
// File form
// ---------------------------------------------------------------------------
$('#form-file').addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.target;
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Uploading…';
  try {
    const fd = new FormData();
    fd.append('file', form.file.files[0]);
    fd.append('name', form.name.value);
    fd.append('description', form.description.value);
    await api('/api/upload/file', { method: 'POST', body: fd });
    form.reset();
    closeModal();
    state.kind = 'file'; syncKindTabs(); syncWrapOptions();
    await loadItems();
  } catch (err) {
    alert('Upload failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-cloud-upload"></i> Upload to MEGA';
  }
});

// ---------------------------------------------------------------------------
// Camera
// ---------------------------------------------------------------------------
async function startCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
    state.cameraStream = stream;
    $('#camera-video').srcObject = stream;
    $('#camera-capture').disabled = false;
    $('#camera-start').disabled = true;
  } catch (err) {
    alert('Camera unavailable: ' + err.message);
  }
}

function stopCamera() {
  if (state.cameraStream) {
    state.cameraStream.getTracks().forEach(t => t.stop());
    state.cameraStream = null;
  }
  const v = $('#camera-video');
  if (v) v.srcObject = null;
  const cap = $('#camera-capture'), start = $('#camera-start');
  if (cap) cap.disabled = true;
  if (start) start.disabled = false;
}

async function captureAndUpload() {
  const video = $('#camera-video');
  const canvas = $('#camera-canvas');
  if (!video.videoWidth) return;

  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);

  const blob = await new Promise(resolve =>
    canvas.toBlob(b => resolve(b), 'image/png', 0.95)
  );
  if (!blob) return;

  const form = $('#form-camera');
  const btn = $('#camera-capture');
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Uploading…';

  try {
    const fd = new FormData();
    fd.append('file', blob, 'capture.png');
    fd.append('name', form.name.value);
    fd.append('description', form.description.value);
    await api('/api/upload/capture', { method: 'POST', body: fd });
    form.reset();
    closeModal();
    state.kind = 'file'; syncKindTabs(); syncWrapOptions();
    await loadItems();
  } catch (err) {
    alert('Capture upload failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-camera"></i> Capture &amp; upload';
  }
}

$('#camera-start').addEventListener('click', startCamera);
$('#camera-stop').addEventListener('click', stopCamera);
$('#camera-capture').addEventListener('click', captureAndUpload);

// ---------------------------------------------------------------------------
// Link form
// ---------------------------------------------------------------------------
$('#form-link').addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.target;
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Saving…';
  try {
    await api('/api/upload/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: form.title.value,
        url: form.url.value,
        description: form.description.value,
        what_it_does: form.what_it_does.value,
      }),
    });
    form.reset();
    closeModal();
    state.kind = 'link'; syncKindTabs(); syncWrapOptions();
    await loadItems();
  } catch (err) {
    alert('Failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-link-45deg"></i> Save link';
  }
});

// ---------------------------------------------------------------------------
// Note form
// ---------------------------------------------------------------------------
const colorPicker = $('#note-color');
const colorHex = $('#note-color-hex');
colorPicker.addEventListener('input', () => { colorHex.value = colorPicker.value; });
colorHex.addEventListener('input', () => {
  if (/^#[0-9a-fA-F]{6}$/.test(colorHex.value)) colorPicker.value = colorHex.value;
});

$('#form-note').addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.target;
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Saving…';
  try {
    await api('/api/upload/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: form.title.value,
        description: form.description.value,
        body: form.body.value,
        color: colorHex.value || colorPicker.value,
      }),
    });
    form.reset();
    colorPicker.value = '#6366f1';
    colorHex.value = '#6366f1';
    closeModal();
    state.kind = 'note'; syncKindTabs(); syncWrapOptions();
    await loadItems();
  } catch (err) {
    alert('Failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-journal-plus"></i> Save note';
  }
});

// ---------------------------------------------------------------------------
// Wrap FAB → "Show only" section picker
// Selecting an option SWITCHES THE VIEW — it never opens the upload modal.
// ---------------------------------------------------------------------------
const wrapBtn = $('#fab-wrap');
const optionsBox = $('#fab-options');

function syncWrapOptions() {
  $$('.fab-option').forEach(b => {
    const on = b.dataset.viewKind === state.kind;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function openWrap() {
  if (!optionsBox || !wrapBtn) return;
  syncWrapOptions();
  optionsBox.classList.add('open');
  optionsBox.setAttribute('aria-hidden', 'false');
  wrapBtn.classList.add('active');
  wrapBtn.setAttribute('aria-expanded', 'true');
}

function closeWrap() {
  if (!optionsBox || !wrapBtn) return;
  optionsBox.classList.remove('open');
  optionsBox.setAttribute('aria-hidden', 'true');
  wrapBtn.classList.remove('active');
  wrapBtn.setAttribute('aria-expanded', 'false');
}

function toggleWrap() {
  if (optionsBox.classList.contains('open')) closeWrap();
  else openWrap();
}

wrapBtn.addEventListener('click', e => {
  e.stopPropagation();
  toggleWrap();
});

// View selector — filters the dashboard, does NOT open the upload modal.
$$('.fab-option').forEach(btn => {
  btn.addEventListener('click', e => {
    e.stopPropagation();
    const kind = btn.dataset.viewKind || 'file';
    closeWrap();
    switchKind(kind);
  });
});

// Click outside → close the picker.
document.addEventListener('click', e => {
  if (!optionsBox.classList.contains('open')) return;
  if (wrapBtn.contains(e.target) || optionsBox.contains(e.target)) return;
  closeWrap();
});

// Esc closes the picker (but not when the modal is open).
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (!$('#modal-backdrop').classList.contains('hidden')) return;
  if (optionsBox.classList.contains('open')) closeWrap();
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
syncKindTabs();
syncWrapOptions();
loadItems();