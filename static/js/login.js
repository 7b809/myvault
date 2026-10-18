// ---------------------------------------------------------------------------
// MyVault login flow — two modes
//
//   Password mode:   password → (TOTP challenge if enabled) → session
//   TOTP-only mode:  6-digit code → session  (requires totp_enabled)
//
// The TOTP-only tab is disabled while /api/auth/status reports
// totp_enabled=false. The server also rejects TOTP-only attempts in that
// state with a clear 400, so a client that ignores the UI still can't
// bypass setup.
// ---------------------------------------------------------------------------

const state = {
  challengeToken: null,
  loginMode: 'password',  // "password" | "totp"
  loginState: 'login',    // "login" | "verify" | "setup"
  totpEnabled: false,
  provisioned: true,
};

const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];

function showError(msg) {
  const el = $('#error-msg');
  if (el) el.textContent = msg || '';
}

// Robust field extraction — never throws if a field is missing.
function fieldValue(form, name) {
  try {
    const fd = new FormData(form);
    const v = fd.get(name);
    if (v !== null) return String(v);
  } catch {}
  const el =
    (form.elements && form.elements.namedItem(name)) ||
    form.querySelector(`[name="${name}"]`);
  return el && 'value' in el ? String(el.value || '') : '';
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const ct = res.headers.get('content-type') || '';
  const data = ct.includes('application/json') ? await res.json() : null;

  if (!res.ok) {
    const msg =
      (data && (data.detail || data.message)) ||
      `HTTP ${res.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

function showState(name) {
  state.loginState = name;
  const isLogin = name === 'login';

  const tabs = $('#mode-tabs');
  if (tabs) tabs.classList.toggle('hidden', !isLogin);

  const pwForm = $('#login-form');
  if (pwForm) {
    pwForm.classList.toggle(
      'hidden', !(isLogin && state.loginMode === 'password')
    );
  }

  const totpForm = $('#totp-form');
  if (totpForm) {
    totpForm.classList.toggle(
      'hidden', !(isLogin && state.loginMode === 'totp')
    );
  }

  const verifyForm = $('#verify-form');
  if (verifyForm) verifyForm.classList.toggle('hidden', name !== 'verify');

  const setupForm = $('#setup-form');
  if (setupForm) setupForm.classList.toggle('hidden', name !== 'setup');

  // Auto-focus the first input in whichever pane is now visible.
  setTimeout(() => {
    const pane =
      name === 'login'
        ? $(state.loginMode === 'password' ? '#password' : '#totp-only-code')
        : name === 'verify'
          ? $('#verify-code')
          : $('#setup-code');
    if (pane && typeof pane.focus === 'function') pane.focus();
  }, 30);
}

function switchMode(mode) {
  if (mode === 'totp' && !state.totpEnabled) {
    showError(
      'TOTP is not set up yet — use password login first to complete setup.'
    );
    return;
  }

  state.loginMode = mode;
  showError('');

  $$('#mode-tabs .login-tab').forEach(b => {
    const on = b.dataset.mode === mode;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });

  showState('login');

  // Remember preference — restored on next visit (only if enabled).
  try {
    localStorage.setItem('mv_login_mode', mode);
  } catch {}
}

// ---------------------------------------------------------------------------
// Status check — enable / disable the TOTP tab
// ---------------------------------------------------------------------------

async function loadStatus() {
  try {
    const res = await fetch('/api/auth/status', { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();

    state.totpEnabled = !!data.totp_enabled;
    state.provisioned = !!data.provisioned;

    const totpTab = $('#mode-tabs .login-tab[data-mode="totp"]');
    if (totpTab) {
      if (!state.totpEnabled) {
        totpTab.disabled = true;
        totpTab.title =
          'Set up 2FA first by logging in with your password';
      } else {
        totpTab.disabled = false;
        totpTab.title =
          'Enter the 6-digit code from your authenticator app';
      }
    }

    // Restore saved preference if TOTP is available.
    try {
      const saved = localStorage.getItem('mv_login_mode');
      if (saved === 'totp' && state.totpEnabled) {
        switchMode('totp');
      }
    } catch {}
  } catch {
    // Network / status check failed — leave the tab disabled by default.
    const totpTab = $('#mode-tabs .login-tab[data-mode="totp"]');
    if (totpTab) totpTab.disabled = true;
  }
}

// ---------------------------------------------------------------------------
// Mode tab clicks
// ---------------------------------------------------------------------------

$$('#mode-tabs .login-tab').forEach(btn => {
  btn.addEventListener('click', () => switchMode(btn.dataset.mode));
});

// ---------------------------------------------------------------------------
// Password form submit
// ---------------------------------------------------------------------------

$('#login-form').addEventListener('submit', async e => {
  e.preventDefault();
  showError('');

  const form = e.target;
  const btn = form.querySelector('button[type="submit"]');
  if (btn) btn.disabled = true;

  try {
    const password = fieldValue(form, 'password');
    if (!password) throw new Error('Please enter your password.');

    const data = await postJSON('/api/auth/login', {
      mode: 'password',
      password,
    });

    state.challengeToken = data.challenge_token;

    if (data.status === 'totp_required') {
      showState('verify');
      return;
    }

    if (data.status === 'setup_required') {
      const qrBox = $('#qr-container');
      if (qrBox) {
        qrBox.innerHTML = data.qr_data_url
          ? `<img src="${data.qr_data_url}" alt="TOTP QR">`
          : '<span class="muted" style="color:#333">QR unavailable — use the secret below.</span>';
      }
      const noteEl = $('#setup-note');
      if (noteEl && data.note) {
        noteEl.innerHTML =
          '<i class="bi bi-info-circle"></i> ' + data.note;
      }
      const secretEl = $('#secret-display');
      if (secretEl) secretEl.textContent = data.secret || '';
      showState('setup');
      return;
    }

    location.href = '/';
  } catch (err) {
    console.error('[login/password] failed:', err);
    showError(err && err.message ? err.message : 'Login failed.');
  } finally {
    if (btn) btn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// TOTP-only form submit
// ---------------------------------------------------------------------------

$('#totp-form').addEventListener('submit', async e => {
  e.preventDefault();
  showError('');

  const form = e.target;
  const btn = form.querySelector('button[type="submit"]');
  if (btn) btn.disabled = true;

  try {
    const code = fieldValue(form, 'code').trim();
    if (!code || code.length !== 6) {
      throw new Error('Enter the 6-digit code from your authenticator app.');
    }

    await postJSON('/api/auth/login', { mode: 'totp', code });
    location.href = '/';
  } catch (err) {
    console.error('[login/totp] failed:', err);
    showError(err && err.message ? err.message : 'Login failed.');
  } finally {
    if (btn) btn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Challenge verification (password → TOTP)
// ---------------------------------------------------------------------------

async function submitCode(code) {
  if (!state.challengeToken) {
    showError('Session expired — please sign in again.');
    showState('login');
    return;
  }
  showError('');

  const active = document.querySelector('form:not(.hidden)');
  const btn = active && active.querySelector('button[type="submit"]');
  if (btn) btn.disabled = true;

  try {
    await postJSON('/api/auth/verify', {
      challenge_token: state.challengeToken,
      code,
    });
    location.href = '/';
  } catch (err) {
    console.error('[verify] failed:', err);
    showError(err && err.message ? err.message : 'Verification failed.');
  } finally {
    if (btn) btn.disabled = false;
  }
}

$('#verify-form').addEventListener('submit', e => {
  e.preventDefault();
  submitCode(fieldValue(e.target, 'code').trim());
});

$('#setup-form').addEventListener('submit', e => {
  e.preventDefault();
  submitCode(fieldValue(e.target, 'code').trim());
});

// ---------------------------------------------------------------------------
// 6-digit auto-submit / digit filtering
// ---------------------------------------------------------------------------

function wireSixDigitInput(sel, opts = {}) {
  const el = $(sel);
  if (!el) return;
  el.addEventListener('input', e => {
    const v = e.target.value.replace(/\D/g, '').slice(0, 6);
    e.target.value = v;
    if (opts.autoSubmit && v.length === 6 && e.target.form) {
      e.target.form.requestSubmit();
    }
  });
}

wireSixDigitInput('#totp-only-code', { autoSubmit: true });
wireSixDigitInput('#verify-code',    { autoSubmit: true });
wireSixDigitInput('#setup-code');

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

showState('login');   // render password pane immediately
loadStatus();         // asynchronously enable TOTP tab if available