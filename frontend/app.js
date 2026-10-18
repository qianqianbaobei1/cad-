/* 配电箱图纸识别 · 前端
 *
 * 只有两屏：
 *   #/          图纸列表：上传图纸、查看识别状态
 *   #/job/<id>  识别结果：左 图纸 | 中 箱柜列表 | 右 选中箱柜的元器件，顶栏导出 Excel
 *
 * 数据全部来自后端 /api/jobs*，前端不造数据。
 */
'use strict';

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtNum = (n) => {
  const v = Number(n);
  if (!Number.isFinite(v)) return '';
  return Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100);
};
const fmtTime = (iso) => (iso ? String(iso).replace('T', ' ').slice(0, 16) : '');

/* ---------------- 接口 ---------------- */

// 旧版页面的登录令牌：有效就沿用，保证已登录用户仍能看到自己企业下的图纸
const TOKEN_KEY = 'dgzh_auth_token';
let COPPER_REFERENCE = 72000;  // 由 /api/settings 覆盖，避免前端写死
let TOKEN = '';
const readToken = () => {
  try { return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; }
};
const writeToken = (value, remember) => {
  try {
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
    if (!value) return;
    (remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, value);
  } catch (_) { /* 无痕模式：只在内存里留着 */ }
};
const clearToken = () => {
  try { localStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(TOKEN_KEY); } catch (_) { /* 无痕模式 */ }
};
TOKEN = readToken();

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
  const res = await fetch(path, { ...opts, headers });
  if (!res.ok) {
    if (res.status === 401 || (res.status === 403 && TOKEN)) {
      const wasLoggedIn = !!TOKEN;
      TOKEN = '';
      clearToken();
      if (wasLoggedIn && !path.startsWith('/api/auth/')) Auth.onUnauthorized();
    }
    let msg = `请求失败 (${res.status})`;
    try {
      const body = await res.json();
      const d = body.detail;
      msg = typeof d === 'string' ? d : (d && d.message) || msg;
    } catch (_) { /* 非 JSON */ }
    throw new Error(msg);
  }
  return res.json();
}

// <img>、下载链接带不了请求头，令牌走查询参数（后端中间件支持）
const withToken = (url) => (TOKEN ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(TOKEN)}` : url);

/* 密码可见性切换（只切 input type，不动取值） */
function togglePassword(inputId, btn) {
  const input = $(inputId);
  if (!input) return;
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  const icon = btn.querySelector('.material-symbols-outlined');
  if (icon) icon.textContent = show ? 'visibility_off' : 'visibility';
}

/* ---------------- 登录 / 注册 ---------------- */

const Auth = {
  user: null,

  init() {
    document.querySelectorAll('.auth-tab').forEach((btn) => {
      btn.addEventListener('click', () => this.setMode(btn.dataset.mode));
    });
    $('goRegister')?.addEventListener('click', () => this.setMode('register'));
    $('goLogin')?.addEventListener('click', () => this.setMode('login'));
    $('btnFillDemoAccount')?.addEventListener('click', () => {
      if ($('loginUsername')) $('loginUsername').value = 'admin';
      if ($('loginPassword')) $('loginPassword').value = 'admin123';
      this.setError('loginError', '');
      toast('已填入演示管理员账号 admin / admin123');
    });
    $('loginForm')?.addEventListener('submit', (e) => this.login(e));
    document.querySelectorAll('[data-eye]').forEach((btn) => {
      btn.addEventListener('click', () => togglePassword(btn.dataset.eye, btn));
    });
    $('registerForm')?.addEventListener('submit', (e) => this.register(e));

    // 全景介绍页交互与身份闸门
    $('btnCloseAuthModal')?.addEventListener('click', () => {
      $('authModal')?.classList.add('hidden');
    });
    const requireAuthOrGo = (mode = 'login') => {
      if (this.user) {
        this.hide();
        route();
      } else {
        this.show(mode);
      }
    };
    $('btnIntroLogin')?.addEventListener('click', () => this.show('login'));
    $('btnIntroExperience')?.addEventListener('click', () => requireAuthOrGo('login'));
    $('btnHeroStart')?.addEventListener('click', () => requireAuthOrGo('login'));
    $('btnPlanFree')?.addEventListener('click', () => requireAuthOrGo('register'));
    $('btnTakeoffToQuote')?.addEventListener('click', () => {
      const el = $('pricing-preview');
      if (el) el.scrollIntoView({ behavior: 'smooth' });
    });
    $('btnIntroEnterWorkspace')?.addEventListener('click', () => {
      this.hide();
      route();
    });
    const showIntro = () => {
      $('authModal')?.classList.add('hidden');
      document.body.classList.add('auth-locked');
      const authScreen = $('authScreen');
      if (authScreen) authScreen.scrollTop = 0;
      this.updateIntroControls();
    };
    $('btnOpenIntro')?.addEventListener('click', showIntro);
    $('btnDropdownIntro')?.addEventListener('click', showIntro);
  },

  setMode(mode) {
    const login = mode !== 'register';
    if ($('loginForm')) $('loginForm').hidden = !login;
    if ($('registerForm')) $('registerForm').hidden = login;
    if ($('authTitle')) $('authTitle').textContent = login ? '欢迎登录' : '注册企业账号';
    if ($('authSubtitle')) $('authSubtitle').textContent = login
      ? '快速进入智能识图与组价工作台'
      : '注册后即创建独立企业空间，图纸只见于本企业';
    if ($('authSpeech')) $('authSpeech').textContent = login
      ? '欢迎登录！图纸解析引擎已就绪'
      : '注册后会为你单独开一套数据空间';
    // 滑动指示器
    const ind = $('authTabIndicator');
    if (ind) ind.style.left = login ? '4px' : 'calc(50% + 0px)';
    document.querySelectorAll('.auth-tab').forEach((btn) => {
      const active = (btn.dataset.mode === 'register') !== login;
      btn.classList.toggle('text-primary', active);
      btn.classList.toggle('text-on-surface-variant', !active);
      btn.classList.toggle('hover:text-on-surface', !active);
    });
    this.setError('loginError', '');
    this.setError('registerError', '');
  },

  setError(id, msg) {
    const el = $(id);
    if (!el) return;
    el.textContent = msg || '';
    el.hidden = !msg;
    el.classList.toggle('hidden', !msg);
    el.style.display = msg ? 'block' : 'none';
  },

  busy(btn, on, label) {
    if (!btn) return;
    btn.disabled = on;
    const textEl = btn.querySelector('span:not(.material-symbols-outlined)') || btn;
    textEl.textContent = label;
  },

  applyUser(user) {
    this.user = user;
    if ($('userNameLabel')) $('userNameLabel').textContent = user.display_name || user.username || '系统工程师';
    // 账号管理只给管理员看；普通成员连入口都不显示（后端同样会 403）
    const accountsBtn = $('btnOpenAccounts');
    if (accountsBtn) accountsBtn.hidden = user.role !== 'admin';
  },

  async restore() {
    if (!TOKEN) return false;
    const drop = () => {
      // 令牌无效就彻底清掉，否则每次刷新都带着一个死 token 反复试
      TOKEN = '';
      clearToken();
    };
    try {
      const me = await api('/api/auth/me');
      if (!me.authenticated || !me.user || me.user.role === 'guest') { drop(); return false; }
      this.applyUser(me.user);
      return true;
    } catch (_) {
      drop();
      return false;
    }
  },

  show(mode = 'login', message = '') {
    this.user = null;
    document.body.classList.add('auth-locked');
    $('authModal')?.classList.remove('hidden');
    this.setMode(mode);
    if (message) this.setError(mode === 'register' ? 'registerError' : 'loginError', message);
    setTimeout(() => (mode === 'register' ? $('regTenant') : $('loginUsername'))?.focus(), 60);
    this.updateIntroControls();
  },

  hide() {
    $('authModal')?.classList.add('hidden');
    document.body.classList.remove('auth-locked');
    this.updateIntroControls();
  },

  updateIntroControls() {
    const authCtrls = $('introAuthControls');
    const userCtrls = $('introUserControls');
    const nameLabel = $('introUserName');
    if (this.user) {
      if (authCtrls) authCtrls.classList.add('hidden');
      if (userCtrls) userCtrls.classList.remove('hidden');
      if (nameLabel) nameLabel.textContent = `${this.user.display_name || this.user.username}（${this.user.tenant_name || ''}）`;
    } else {
      if (authCtrls) authCtrls.classList.remove('hidden');
      if (userCtrls) userCtrls.classList.add('hidden');
    }
  },

  onUnauthorized() {
    if (document.body.classList.contains('auth-locked')) return;
    this.show('login', '登录状态已失效，请重新登录');
  },

  finish(res) {
    TOKEN = res.token || '';
    const remember = $('loginRemember') ? $('loginRemember').checked : true;
    writeToken(TOKEN, remember);
    this.applyUser(res.user || {});
    this.hide();
    if ($('loginPassword')) $('loginPassword').value = '';
    if ($('regPassword')) $('regPassword').value = '';
    if ($('regPassword2')) $('regPassword2').value = '';
    toast(`欢迎，${res.user?.display_name || res.user?.username || '用户'}（${res.user?.tenant_name || ''}）`);
    loadModelSettingsSummary();
    route();
  },

  async login(e) {
    e.preventDefault();
    const username = ($('loginUsername').value || '').trim();
    const password = $('loginPassword').value || '';
    this.setError('loginError', '');
    if (!username || !password) return this.setError('loginError', '请输入账号和密码');
    this.busy($('loginSubmit'), true, '登录中…');
    try {
      const res = await api('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      this.finish(res);
    } catch (err) {
      this.setError('loginError', err.message || '登录失败');
    } finally {
      this.busy($('loginSubmit'), false, '进入组价工作台');
    }
  },

  async register(e) {
    e.preventDefault();
    const tenantName = ($('regTenant').value || '').trim();
    const displayName = ($('regDisplayName').value || '').trim();
    const username = ($('regUsername').value || '').trim();
    const password = $('regPassword').value || '';
    const password2 = $('regPassword2').value || '';
    const contact = ($('regContact').value || '').trim();
    this.setError('registerError', '');

    if (!tenantName) return this.setError('registerError', '请填写企业 / 工区名称');
    if (!username) return this.setError('registerError', '请填写登录账号');
    if (password.length < 6) return this.setError('registerError', '密码至少 6 位');
    if (password !== password2) return this.setError('registerError', '两次输入的密码不一致');

    this.busy($('registerSubmit'), true, '注册中…');
    try {
      const res = await api('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, display_name: displayName, tenant_name: tenantName, contact }),
      });
      this.finish(res);
    } catch (err) {
      this.setError('registerError', err.message || '注册失败');
    } finally {
      this.busy($('registerSubmit'), false, '注册并进入企业空间');
    }
  },

  async signOut(notify = false) {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* 令牌可能已失效 */ }
    TOKEN = '';
    this.user = null;
    clearToken();
    $('user-menu-dropdown')?.classList.add('hidden');
    if (notify) toast('已退出登录');
    this.show('login');
    if (location.hash) location.hash = '';
  },
};

let toastTimer = 0;
function toast(msg, ms = 2600) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

/* ---------------- 全景介绍页交互 ---------------- */

window.setBilling = function(cycle) {
  const btnMonthly = $('billing-monthly');
  const btnAnnual = $('billing-annual');
  const pricePro = $('price-pro');
  const cyclePro = $('cycle-pro');
  const subPro = $('sub-pro');
  const priceEnt = $('price-ent');
  const cycleEnt = $('cycle-ent');
  const subEnt = $('sub-ent');

  if (cycle === 'annual') {
    if (btnAnnual) btnAnnual.className = 'px-4 py-1.5 rounded-full text-xs font-semibold bg-white text-on-surface shadow-xs transition-all flex items-center gap-1 cursor-pointer';
    if (btnMonthly) btnMonthly.className = 'px-4 py-1.5 rounded-full text-xs font-semibold text-on-surface-variant hover:text-on-surface transition-all cursor-pointer';
    if (pricePro) pricePro.innerText = '¥1,069';
    if (cyclePro) cyclePro.innerText = '/ 年';
    if (subPro) subPro.innerText = '折合 ¥89/月 (9折已享)';
    if (priceEnt) priceEnt.innerText = '¥2,149';
    if (cycleEnt) cycleEnt.innerText = '/ 年';
    if (subEnt) subEnt.innerText = '折合 ¥179/月 (9折已享)';
  } else {
    if (btnMonthly) btnMonthly.className = 'px-4 py-1.5 rounded-full text-xs font-semibold bg-white text-on-surface shadow-xs transition-all cursor-pointer';
    if (btnAnnual) btnAnnual.className = 'px-4 py-1.5 rounded-full text-xs font-semibold text-on-surface-variant hover:text-on-surface transition-all flex items-center gap-1 cursor-pointer';
    if (pricePro) pricePro.innerText = '¥99';
    if (cyclePro) cyclePro.innerText = '/ 月';
    if (subPro) subPro.innerText = '免自填 Key · 包含官方全托管算力';
    if (priceEnt) priceEnt.innerText = '¥199';
    if (cycleEnt) cycleEnt.innerText = '/ 月';
    if (subEnt) subEnt.innerText = '盘厂级私有模板定制 · 团队协作';
  }
};

window.setQuotationBrand = function(brand) {
  const btnChint = $('brandBtnChint');
  const btnDelixi = $('brandBtnDelixi');
  const btnSchneider = $('brandBtnSchneider');
  const btns = { chint: btnChint, delixi: btnDelixi, schneider: btnSchneider };
  Object.keys(btns).forEach(k => {
    if (btns[k]) {
      if (k === brand) {
        btns[k].className = 'px-2.5 py-1 rounded bg-white text-primary font-semibold shadow-xs cursor-pointer';
      } else {
        btns[k].className = 'px-2.5 py-1 rounded text-on-surface-variant hover:text-on-surface cursor-pointer';
      }
    }
  });

  const bNames = {
    chint: { tag: 'CHINT 正泰', cls: 'bg-blue-50 text-blue-700', m01: '¥126.00', m02: '¥18.50', t02: '¥37.00', m03: '¥64.20', t03: '¥128.40', m04: '¥142.00', t04: '¥284.00' },
    delixi: { tag: 'DELIXI 德力西', cls: 'bg-amber-50 text-amber-800', m01: '¥118.00', m02: '¥16.80', t02: '¥33.60', m03: '¥59.00', t03: '¥118.00', m04: '¥135.00', t04: '¥270.00' },
    schneider: { tag: '施耐德 Acti9', cls: 'bg-emerald-50 text-emerald-800', m01: '¥210.00', m02: '¥36.00', t02: '¥72.00', m03: '¥115.00', t03: '¥230.00', m04: '¥248.00', t04: '¥496.00' },
  };
  const conf = bNames[brand] || bNames.chint;
  for (let i = 1; i <= 4; i++) {
    const pad = String(i).padStart(2, '0');
    const bEl = $(`quoteBrand${pad}`);
    if (bEl) {
      bEl.textContent = conf.tag;
      bEl.className = `px-2 py-0.5 rounded font-label-sm text-[11px] ${conf.cls}`;
    }
  }
  if ($('quotePrice01')) $('quotePrice01').textContent = conf.m01;
  if ($('quoteTotal01')) $('quoteTotal01').textContent = conf.m01;
  if ($('quotePrice02')) $('quotePrice02').textContent = conf.m02;
  if ($('quoteTotal02')) $('quoteTotal02').textContent = conf.t02;
  if ($('quotePrice03')) $('quotePrice03').textContent = conf.m03;
  if ($('quoteTotal03')) $('quoteTotal03').textContent = conf.t03;
  if ($('quotePrice04')) $('quotePrice04').textContent = conf.m04;
  if ($('quoteTotal04')) $('quoteTotal04').textContent = conf.t04;
};

window.handleLeadSubmit = async function(e) {
  e.preventDefault();
  const phone = ($('user-phone')?.value || '').trim();
  const name = ($('user-name')?.value || '').trim();
  const company = ($('user-company')?.value || '').trim();
  const feedback = $('form-feedback');
  const btn = $('btnSubmitLead');
  if (!phone || !name) return;
  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/api/leads', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, name, company, source: 'landing_priority_access' })
    });
    const data = await res.json();
    if (feedback) {
      feedback.textContent = data.message || '预约已登记！工程顾问将在工作日尽快与您联系。';
      feedback.classList.remove('hidden');
    }
    e.target.reset();
  } catch (err) {
    if (feedback) {
      feedback.textContent = '预约已提交登记，工程顾问稍后与您联络！';
      feedback.classList.remove('hidden');
    }
  } finally {
    if (btn) btn.disabled = false;
  }
};

const STATUS = {
  queued: '排队中',
  converting: '转换 CAD',
  rendering: '渲染图纸',
  extracting: '识别中',
  building_excel: '生成清单',
};

/**
 * 页面内小表单，替代浏览器原生 prompt()。
 * 输入框一律不预填业务值，必填项留空就不提交，不往数据里写任何兜底值。
 */
function openFieldForm({ title, fields, submitText = '确定', onOk }) {
  const host = $('inlineFormHost');
  if (!host) return;
  const inputsHtml = fields.map((f, i) => `
    <label class="flex flex-col gap-0.5 text-[11px] text-slate-600">
      <span>${esc(f.label)}${f.required ? ' <span class="text-rose-500">*</span>' : ''}</span>
      <input data-idx="${i}" type="${f.type || 'text'}" ${f.type === 'number' ? 'min="1" step="1"' : ''}
        value="${esc(f.value || '')}" placeholder="${esc(f.placeholder || '')}"
        class="h-7 px-2 rounded border border-slate-300 text-xs text-slate-800 outline-none focus:border-blue-600 focus:ring-1 focus:ring-blue-600 bg-white">
    </label>`).join('');
  host.hidden = false;
  host.innerHTML = `
    <div class="fixed inset-0 z-[45] bg-slate-900/30 flex items-center justify-center" id="inlineFormBackdrop">
      <form class="w-[300px] bg-white rounded-lg shadow-xl border border-slate-200 p-3 flex flex-col gap-2" id="inlineForm">
        <div class="flex items-center justify-between">
          <span class="text-xs font-bold text-slate-800">${esc(title)}</span>
          <button type="button" class="text-slate-400 hover:text-slate-700 cursor-pointer" data-cancel title="取消">
            <span class="material-symbols-outlined text-[16px]">close</span>
          </button>
        </div>
        ${inputsHtml}
        <div class="flex items-center justify-end gap-2 pt-1">
          <button type="button" class="h-7 px-2.5 rounded border border-slate-300 text-slate-600 text-xs bg-white hover:bg-slate-50 cursor-pointer" data-cancel>取消</button>
          <button type="submit" class="h-7 px-2.5 rounded bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium cursor-pointer">${esc(submitText)}</button>
        </div>
      </form>
    </div>`;

  const form = host.querySelector('#inlineForm');
  const close = () => { host.hidden = true; host.innerHTML = ''; };
  host.querySelectorAll('[data-cancel]').forEach((btn) => { btn.onclick = close; });
  host.querySelector('#inlineFormBackdrop').addEventListener('click', (e) => {
    if (e.target.id === 'inlineFormBackdrop') close();
  });
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const values = {};
    let missing = '';
    fields.forEach((f, i) => {
      const el = form.querySelector(`input[data-idx="${i}"]`);
      const raw = String((el && el.value) || '').trim();
      if (f.required && !raw) missing = missing || f.label;
      values[f.key] = f.type === 'number' ? (raw === '' ? '' : Number(raw)) : raw;
    });
    if (missing) { toast(`请填写${missing}`); return; }
    if (onOk(values) === false) return;
    close();
  });
  const first = form.querySelector('input');
  if (first) first.focus();
}

const isDone = (s) => s === 'done';
const isFailed = (s) => s === 'failed' || s === 'error';
function statusPill(job) {
  const s = job.status || 'queued';
  if (isDone(s)) return '<span class="pill ok">已完成</span>';
  if (isFailed(s)) return '<span class="pill bad">识别失败</span>';
  const pct = Number(job.progress) > 0 ? ` ${Math.round(job.progress)}%` : '';
  return `<span class="pill run">${esc(STATUS[s] || s)}${pct}</span>`;
}

/* ---------------- 路由 ---------------- */

function route() {
  if (!Auth.user) { Auth.show('login'); return; }
  const mPricing = location.hash.match(/^#\/job\/([0-9a-f]{6,32})\/pricing/i);
  if (mPricing) {
    PricingWorkbench.open(mPricing[1]);
    return;
  }
  const mExtract = location.hash.match(/^#\/job\/([0-9a-f]{6,32})\/extract/i);
  if (mExtract) {
    ExtractWorkbench.open(mExtract[1]);
    return;
  }
  const m = location.hash.match(/^#\/job\/([0-9a-f]{6,32})/i);
  if (m) {
    openSmartJob(m[1]);
    return;
  }
  showList();
}
window.addEventListener('hashchange', route);

async function openSmartJob(id) {
  let j = List.jobs.find((x) => x.job_id === id);
  if (!j) {
    try { j = await api(`/api/jobs/${id}`); } catch (_) {}
  }
  const isExcel = j && ['xlsx', 'xls', 'csv'].includes((j.file_type || String(j.filename || '').split('.').pop()).toLowerCase());
  location.hash = isExcel ? `#/job/${id}/pricing` : `#/job/${id}/extract`;
}

function setCrumbs(html) { $('crumbs').innerHTML = html; }

/* ================= ① 落地页与任务列表 ================= */

const List = {
  jobs: [],
  uploading: [],
  timer: 0,
  openWhenDone: '',
  openTarget: 'extract',
  filterProject: '全部项目',
  searchQuery: ''
};

function showList() {
  ExtractWorkbench.stopPoll();
  PricingWorkbench.stopPoll();
  if ($('globalHeader')) $('globalHeader').hidden = false;
  $('viewList').hidden = false;
  if ($('viewExtract')) $('viewExtract').hidden = true;
  if ($('viewPricing')) $('viewPricing').hidden = true;
  setCrumbs('');
  document.title = '配电箱图纸识别与组价';
  loadJobs();
}

async function loadJobs() {
  clearTimeout(List.timer);
  try {
    const res = await api('/api/jobs');
    List.jobs = (res.jobs || []).filter(isRealJob);
  } catch (e) {
    toast(`任务列表加载失败：${e.message}`);
  }
  if (List.openWhenDone && !$('viewList').hidden) {
    const j = List.jobs.find((x) => x.job_id === List.openWhenDone);
    if (j && isDone(j.status)) {
      const isExcel = ['xlsx', 'xls', 'csv'].includes((j.file_type || String(j.filename || '').split('.').pop()).toLowerCase()) || List.openTarget === 'pricing';
      List.openWhenDone = '';
      location.hash = isExcel ? `#/job/${j.job_id}/pricing` : `#/job/${j.job_id}/extract`;
      return;
    }
    if (j && isFailed(j.status)) List.openWhenDone = '';
  }
  renderJobs();
  // 有未完成的任务就继续轮询刷新
  const running = List.uploading.length || List.jobs.some((j) => !isDone(j.status) && !isFailed(j.status));
  if (running && !$('viewList').hidden) List.timer = setTimeout(loadJobs, 2500);
}

// 过滤掉 work/ 目录下的非顶层任务与平替导出临时文件
function isRealJob(j) {
  const id = String(j.job_id || '');
  return j.filename && !id.includes('.') && !/_[^\x00-\x7f]+$/.test(id);
}

function count(v) {
  if (Array.isArray(v)) return v.length;
  const n = Number(v);
  return Number.isFinite(n) ? n : '';
}

function getSourceBadge(filename, fileType) {
  const ext = (fileType || String(filename || '').split('.').pop() || '').toLowerCase();
  if (ext === 'dwg') {
    return `<span class="font-label-sm text-label-sm px-1.5 py-0.5 rounded bg-primary-fixed text-on-primary-fixed font-semibold tracking-wider uppercase">DWG</span>`;
  }
  if (ext === 'dxf') {
    return `<span class="font-label-sm text-label-sm px-1.5 py-0.5 rounded bg-surface-container-highest text-on-surface-variant font-semibold tracking-wider uppercase">DXF</span>`;
  }
  if (ext === 'pdf') {
    return `<span class="font-label-sm text-label-sm px-1.5 py-0.5 rounded bg-error-container text-on-error-container font-semibold tracking-wider uppercase">PDF</span>`;
  }
  if (['xlsx', 'xls', 'csv'].includes(ext)) {
    return `<span class="font-label-sm text-label-sm px-1.5 py-0.5 rounded bg-tertiary text-on-tertiary font-semibold tracking-wider uppercase">清单</span>`;
  }
  return `<span class="font-label-sm text-label-sm px-1.5 py-0.5 rounded bg-surface-container-high text-on-surface-variant font-semibold tracking-wider uppercase">${esc(ext.toUpperCase() || '图纸')}</span>`;
}

function isQuoted(j) {
  const price = Number(j.total_price);
  return Number.isFinite(price) && price > 0;
}

function renderStatusCell(j) {
  const s = j.status || 'queued';
  if (isDone(s)) {
    // 只有真的算出了报价合计才叫“已组价”，只识别完的图纸只是待组价
    if (isQuoted(j)) {
      return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded font-label-sm text-label-sm bg-surface-container text-tertiary-container font-medium" title="已组价，可直接导出组价明细"><span class="material-symbols-outlined text-[14px]">check_circle</span><span>已组价</span></span>`;
    }
    return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded font-label-sm text-label-sm bg-primary-fixed text-on-primary-fixed font-medium" title="图纸已识别完成，尚未生成报价"><span class="material-symbols-outlined text-[14px]">fact_check</span><span>已识别 · 待组价</span></span>`;
  }
  if (isFailed(s)) {
    return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded font-label-sm text-label-sm bg-error-container text-on-error-container font-medium"><span class="material-symbols-outlined text-[14px]">error</span><span>识别失败</span></span>`;
  }
  const pct = Math.round(Number(j.progress) || 0);
  const statusText = STATUS[s] || '处理中';
  return `
    <div class="inline-flex flex-col gap-1 w-28 text-left">
      <div class="flex items-center justify-between font-label-sm text-label-sm">
        <span class="inline-flex items-center gap-1 text-primary font-medium">
          <span class="w-1.5 h-1.5 rounded-full bg-primary animate-ping"></span>${esc(statusText)}
        </span>
        <span class="text-primary font-semibold">${pct}%</span>
      </div>
      <div class="w-full h-1.5 bg-surface-container-high rounded-full overflow-hidden">
        <div class="bg-primary h-full rounded-full transition-all duration-300" style="width: ${Math.max(5, pct)}%"></div>
      </div>
    </div>`;
}

function updateProjectFilterMenu() {
  const menu = $('project-filter-menu');
  if (!menu) return;
  const projects = Array.from(new Set(List.jobs.map((j) => (j.project || '').trim()).filter(Boolean)));
  const cur = List.filterProject || '全部项目';
  const items = ['全部项目', ...projects];
  menu.innerHTML = items.map((p) => {
    const on = p === cur;
    return `
      <button class="w-full text-left px-space-md py-1.5 font-body-md text-body-md ${on ? 'text-primary bg-surface-container-low font-medium' : 'text-on-surface hover:bg-surface-container'} flex items-center justify-between transition-colors" data-proj="${esc(p)}" type="button">
        <span>${esc(p)}</span>
        ${on ? '<span class="material-symbols-outlined text-[16px]">check</span>' : ''}
      </button>
    `;
  }).join('');
}

function renderJobs() {
  updateProjectFilterMenu();

  const query = (List.searchQuery || '').trim().toLowerCase();
  const filterProj = List.filterProject || '全部项目';
  const rows = [];

  // 正在上传中的临时行
  for (const name of List.uploading) {
    const badge = getSourceBadge(name);
    rows.push(`
      <tr class="drawing-row bg-surface-container-low/30">
        <td class="px-space-md py-2.5 text-center">${badge}</td>
        <td class="px-space-md py-2.5"><span class="font-body-md text-body-md font-medium text-on-surface">${esc(name)}</span></td>
        <td class="px-space-md py-2.5 text-outline">—</td>
        <td class="px-space-md py-2.5 text-right text-outline font-label-md">—</td>
        <td class="px-space-md py-2.5 text-right text-outline font-label-md">—</td>
        <td class="px-space-md py-2.5 text-center text-outline font-label-md">—</td>
        <td class="px-space-md py-2.5 text-right text-outline font-label-md">—</td>
        <td class="px-space-md py-2.5 text-center">
          <span class="inline-flex items-center gap-1.5 text-primary font-label-sm">
            <span class="w-1.5 h-1.5 rounded-full bg-primary animate-ping"></span>上传中…
          </span>
        </td>
        <td class="px-space-md py-2.5 text-right">
          <button class="px-2.5 py-1 rounded bg-surface-container text-outline font-body-sm text-body-sm cursor-not-allowed" disabled>上传中</button>
        </td>
      </tr>
    `);
  }

  let visibleCount = 0;
  for (const j of List.jobs) {
    const name = String(j.filename || '').toLowerCase();
    const proj = String(j.project || '');
    const matchesQuery = !query || name.includes(query) || proj.toLowerCase().includes(query);
    const matchesProj = (filterProj === '全部项目') || (proj === filterProj) || (!proj && filterProj === '未分组');
    if (!matchesQuery || !matchesProj) continue;

    visibleCount++;
    const done = isDone(j.status);
    const failed = isFailed(j.status);
    const badge = getSourceBadge(j.filename, j.file_type);

    const boxCnt = (j.boxes_count != null && done) ? String(j.boxes_count) : (done && j.summary?.boxes ? String(count(j.summary.boxes)) : '—');
    const compCnt = (j.components_count != null && done) ? String(j.components_count) : (done && j.summary?.components ? String(count(j.summary.components)) : '—');

    let uncHtml = '<span class="text-outline font-label-md">—</span>';
    const uncCount = Number(j.uncertainties_count || 0);
    if (done && uncCount > 0) {
      uncHtml = `<span class="inline-flex items-center px-2 py-0.5 rounded font-label-sm text-label-sm bg-error-container text-on-error-container font-semibold">${uncCount} 条待核对</span>`;
    }

    let priceHtml = '<span class="text-outline font-label-md">—</span>';
    const priceVal = Number(j.total_price);
    if (done && Number.isFinite(priceVal) && priceVal > 0) {
      priceHtml = `<span class="font-label-md text-label-md font-semibold text-on-surface">¥${priceVal.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}</span>`;
    }

    let opsHtml = '';
    if (done) {
      const isExcel = ['xlsx', 'xls', 'csv'].includes((j.file_type || String(j.filename || '').split('.').pop()).toLowerCase());
      const exportStage = isQuoted(j) ? 'pricing' : 'takeoff';
      const exportTitle = isQuoted(j) ? '下载组价明细 Excel' : '下载拆量清单 Excel（尚未组价）';
      if (isExcel) {
        opsHtml = `
          <div class="inline-flex items-center gap-1 justify-end" onclick="event.stopPropagation()">
            <a class="px-2.5 py-1 rounded bg-tertiary text-on-tertiary font-body-sm text-body-sm hover:opacity-90 transition-opacity shadow-sm" href="#/job/${esc(j.job_id)}/pricing">直接组价</a>
            <button class="p-1 rounded text-outline hover:text-on-surface hover:bg-surface-container transition-colors" data-export="${esc(j.job_id)}" data-stage="${exportStage}" title="${exportTitle}" type="button"><span class="material-symbols-outlined text-[18px]">download</span></button>
            <button class="p-1 rounded text-outline hover:text-error hover:bg-error-container transition-colors" data-delete="${esc(j.job_id)}" title="删除任务（含识别结果与已导出文件）" type="button"><span class="material-symbols-outlined text-[18px]">delete_outline</span></button>
          </div>`;
      } else {
        opsHtml = `
          <div class="inline-flex items-center gap-1 justify-end" onclick="event.stopPropagation()">
            <a class="px-2.5 py-1 rounded bg-primary text-on-primary font-body-sm text-body-sm hover:bg-primary-container transition-colors shadow-sm text-white" href="#/job/${esc(j.job_id)}/extract">核对拆量</a>
            <a class="px-2 py-1 rounded border border-outline-variant text-primary font-body-sm text-body-sm hover:bg-surface-container transition-colors" href="#/job/${esc(j.job_id)}/pricing" title="进入组价核算">组价</a>
            <button class="p-1 rounded text-outline hover:text-on-surface hover:bg-surface-container transition-colors" data-export="${esc(j.job_id)}" data-stage="${exportStage}" title="${exportTitle}" type="button"><span class="material-symbols-outlined text-[18px]">download</span></button>
            <button class="p-1 rounded text-outline hover:text-on-surface hover:bg-surface-container transition-colors" data-reparse="${esc(j.job_id)}" title="重新识别图纸（覆盖当前识别结果）" type="button"><span class="material-symbols-outlined text-[18px]">replay</span></button>
            <button class="p-1 rounded text-outline hover:text-error hover:bg-error-container transition-colors" data-delete="${esc(j.job_id)}" title="删除任务（含识别结果与已导出文件）" type="button"><span class="material-symbols-outlined text-[18px]">delete_outline</span></button>
          </div>`;
      }
    } else if (failed) {
      opsHtml = `
        <div class="inline-flex items-center gap-1 justify-end" onclick="event.stopPropagation()">
          <button class="px-2.5 py-1 rounded bg-surface-container hover:bg-surface-container-high text-on-surface font-body-sm text-body-sm flex items-center gap-1 transition-colors" data-reparse="${esc(j.job_id)}" title="重新识别这张图纸" type="button"><span class="material-symbols-outlined text-[15px]">replay</span><span>重试</span></button>
          <button class="p-1 rounded text-outline hover:text-error hover:bg-error-container transition-colors" data-delete="${esc(j.job_id)}" title="删除任务（含识别结果与已导出文件）" type="button"><span class="material-symbols-outlined text-[18px]">delete_outline</span></button>
        </div>`;
    } else {
      opsHtml = `
        <div class="inline-flex items-center gap-1 justify-end">
          <button class="px-2.5 py-1 rounded bg-surface-container text-outline font-body-sm text-body-sm cursor-not-allowed" disabled type="button">处理中…</button>
        </div>`;
    }

    rows.push(`
      <tr class="drawing-row group hover:bg-surface-container-low/70 transition-colors ${done ? 'cursor-pointer' : ''} bg-surface-container-lowest" data-id="${esc(j.job_id)}">
        <td class="px-space-md py-2.5 text-center">${badge}</td>
        <td class="px-space-md py-2.5">
          <div class="font-body-md text-body-md font-medium text-on-surface group-hover:text-primary transition-colors">${esc(j.filename)}</div>
          ${failed && j.error ? `<div class="text-error font-body-sm text-[12px] mt-0.5 line-clamp-1">${esc(j.error)}</div>` : ''}
        </td>
        <td class="px-space-md py-2.5 text-on-surface-variant font-body-sm">${esc(j.project || '–')}</td>
        <td class="px-space-md py-2.5 text-right font-label-md text-label-md font-semibold text-on-surface">${esc(boxCnt)}</td>
        <td class="px-space-md py-2.5 text-right font-label-md text-label-md text-on-surface-variant">${esc(compCnt)}</td>
        <td class="px-space-md py-2.5 text-center">${uncHtml}</td>
        <td class="px-space-md py-2.5 text-right font-label-md text-label-md font-semibold">${priceHtml}</td>
        <td class="px-space-md py-2.5 text-center">${renderStatusCell(j)}</td>
        <td class="px-space-md py-2.5 text-right">${opsHtml}</td>
      </tr>
    `);
  }

  $('drawings-tbody').innerHTML = rows.join('');
  $('jobEmpty').hidden = (rows.length > 0);
  $('total-drawings-count').textContent = `共 ${List.jobs.length} 项`;
  $('table-count-summary').textContent = `显示 ${visibleCount} 项，共 ${List.jobs.length} 项`;
}

// 表格操作委托（查看、导出、重试、删除）
$('drawings-tbody').addEventListener('click', async (e) => {
  const delBtn = e.target.closest('[data-delete]');
  if (delBtn) {
    e.stopPropagation();
    const id = delBtn.dataset.delete;
    if (!confirm('确认删除该任务及其所有相关文件吗？删除后不可撤销。')) return;
    try {
      await api(`/api/jobs/${id}`, { method: 'DELETE' });
      toast('已成功删除任务');
      loadJobs();
    } catch (err) { toast(`删除失败：${err.message}`); }
    return;
  }

  const expBtn = e.target.closest('[data-export]');
  if (expBtn) {
    e.stopPropagation();
    const id = expBtn.dataset.export;
    const stage = expBtn.dataset.stage === 'takeoff' ? 'takeoff' : 'pricing';
    window.location.href = withToken(`/api/jobs/${id}/excel?stage=${stage}`);
    return;
  }

  const repBtn = e.target.closest('[data-reparse]');
  if (repBtn) {
    e.stopPropagation();
    const id = repBtn.dataset.reparse;
    if (!confirm('重新识别会再次调用识图模型（会产生费用），并覆盖这张图纸当前的识别结果。继续吗？')) return;
    try {
      await api(`/api/jobs/${id}/reparse`, { method: 'POST' });
      toast('已开始重新识别');
      loadJobs();
    } catch (err) { toast(err.message); }
    return;
  }

  const tr = e.target.closest('tr.drawing-row');
  if (tr && tr.dataset.id) {
    const j = List.jobs.find((x) => x.job_id === tr.dataset.id);
    if (j && isDone(j.status)) {
      const isExcel = ['xlsx', 'xls', 'csv'].includes((j.file_type || String(j.filename || '').split('.').pop()).toLowerCase());
      location.hash = isExcel ? `#/job/${tr.dataset.id}/pricing` : `#/job/${tr.dataset.id}/extract`;
    }
  }
});

/* 上传处理与多源适配 */
const ACCEPT_BLUEPRINT = /\.(pdf|dwg|dxf)$/i;
const ACCEPT_EXCEL = /\.(xlsx|xls|csv)$/i;

async function uploadFiles(files, kind = 'blueprint') {
  const list = [...files];
  const checker = (kind === 'excel') ? ACCEPT_EXCEL : ((kind === 'blueprint') ? ACCEPT_BLUEPRINT : /\.(pdf|dwg|dxf|xlsx|xls|csv)$/i);
  const bad = list.filter((f) => !checker.test(f.name));
  if (bad.length) {
    toast(`不支持的文件格式：${bad.map((f) => f.name).join('、')}`);
  }
  const good = list.filter((f) => checker.test(f.name));
  if (!good.length) return;

  if (kind === 'blueprint' && currentSettings && !currentSettings.vision_configured) {
    toast('⚠️ 视觉模型 API Key 未配置，请先在右上角「配置识图模型与密钥」中保存密钥', 4500);
    openSettingsModal();
    return;
  }

  List.uploading.push(...good.map((f) => f.name));
  renderJobs();

  const created = [];
  await Promise.all(good.map(async (f) => {
    const fd = new FormData();
    fd.append('file', f);
    try {
      const res = await api('/api/jobs', { method: 'POST', body: fd });
      created.push(res.job_id);
    } catch (err) {
      toast(`${f.name} 上传失败：${err.message}`, 4000);
    } finally {
      const idx = List.uploading.indexOf(f.name);
      if (idx !== -1) List.uploading.splice(idx, 1);
    }
  }));

  if (created.length === 1) {
    List.openWhenDone = created[0];
    toast('已上传，正在处理与组价，完成后自动打开结果', 4000);
  } else if (created.length) {
    toast(`已上传 ${created.length} 份文件，正在处理中`, 4000);
  }
  loadJobs();
}

// 绑定图纸识别上传
$('blueprint-input').addEventListener('change', (e) => {
  uploadFiles(e.target.files, 'blueprint');
  e.target.value = '';
});
$('btnSelectBlueprint').addEventListener('click', (e) => {
  e.stopPropagation();
  $('blueprint-input').click();
});

// 绑定设备清单组价上传
$('excel-input').addEventListener('change', (e) => {
  uploadFiles(e.target.files, 'excel');
  e.target.value = '';
});
$('btnSelectExcel').addEventListener('click', (e) => {
  e.stopPropagation();
  $('excel-input').click();
});

// 下载标准配电箱设备清单模板（带令牌导航式下载，不用 fetch+blob）
if ($('btnDownloadTemplate')) {
  $('btnDownloadTemplate').addEventListener('click', (e) => {
    e.currentTarget.href = withToken('/api/template/bom');
  });
}

// 拖拽高亮交互绑定
function setupDropZone(elemId, kind) {
  const el = $(elemId);
  if (!el) return;
  ['dragenter', 'dragover'].forEach((ev) => el.addEventListener(ev, (e) => {
    e.preventDefault();
    el.classList.add(kind === 'excel' ? 'border-tertiary-container' : 'border-primary', 'bg-surface-container-low');
  }));
  ['dragleave', 'drop'].forEach((ev) => el.addEventListener(ev, (e) => {
    e.preventDefault();
    el.classList.remove('border-primary', 'border-tertiary-container', 'bg-surface-container-low');
  }));
  el.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadFiles(e.dataTransfer.files, kind);
  });
}
setupDropZone('drop-zone-blueprint', 'blueprint');
setupDropZone('drop-zone-excel', 'excel');

window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => e.preventDefault());

// 项目过滤下拉菜单
$('project-filter-btn').addEventListener('click', (e) => {
  e.stopPropagation();
  $('project-filter-menu').classList.toggle('hidden');
});
document.addEventListener('click', (e) => {
  const btn = $('project-filter-btn');
  const menu = $('project-filter-menu');
  if (btn && menu && !btn.contains(e.target) && !menu.contains(e.target)) {
    menu.classList.add('hidden');
  }
});
$('project-filter-menu').addEventListener('click', (e) => {
  const b = e.target.closest('[data-proj]');
  if (!b) return;
  List.filterProject = b.dataset.proj;
  $('current-project-label').textContent = List.filterProject;
  $('project-filter-menu').classList.add('hidden');
  renderJobs();
});

// 搜索栏输入过滤
$('search-input').addEventListener('input', (e) => {
  List.searchQuery = e.target.value;
  renderJobs();
});

// 用户下拉菜单交互
$('user-menu-btn').addEventListener('click', (e) => {
  e.stopPropagation();
  const dd = $('user-menu-dropdown');
  dd.classList.toggle('hidden');
  $('user-menu-arrow').classList.toggle('rotate-180', !dd.classList.contains('hidden'));
});
document.addEventListener('click', (e) => {
  const container = $('user-menu-container');
  if (container && !container.contains(e.target)) {
    $('user-menu-dropdown').classList.add('hidden');
    $('user-menu-arrow').classList.remove('rotate-180');
  }
});
$('btnLogout').addEventListener('click', () => Auth.signOut(true));

/* ---------------- 账号与注册（管理员） ---------------- */

async function loadAccounts() {
  const body = $('accountsBody');
  const empty = $('accountsEmpty');
  const meta = $('accountsMeta');
  if (!body) return;
  body.innerHTML = '<tr><td class="py-6 text-outline" colspan="8">读取中…</td></tr>';
  try {
    const res = await api('/api/admin/accounts');
    const list = res.accounts || [];
    if (meta) meta.textContent = list.length ? `共 ${list.length} 个账号 / ${(res.tenants || []).length} 家企业` : '';
    if (empty) empty.hidden = list.length > 0;
    body.innerHTML = list.map((a) => `
      <tr class="border-b border-surface-container/70">
        <td class="py-2 pr-3">${esc(a.tenant_name)}</td>
        <td class="py-2 pr-3">${esc(a.display_name)}</td>
        <td class="py-2 pr-3 font-mono">${esc(a.username)}</td>
        <td class="py-2 pr-3">${a.contact ? esc(a.contact) : '<span class="text-outline">—</span>'}</td>
        <td class="py-2 pr-3">${a.role === 'admin' ? '管理员' : esc(a.role)}</td>
        <td class="py-2 pr-3 text-outline">${esc(fmtTime(a.created_at))}</td>
        <td class="py-2 pr-3 text-outline">${a.last_login_at ? esc(fmtTime(a.last_login_at)) : '<span class="text-outline">从未登录</span>'}</td>
        <td class="py-2 text-right font-mono-nums">${a.jobs_count} / ${a.projects_count}</td>
      </tr>`).join('');
  } catch (err) {
    body.innerHTML = `<tr><td class="py-6 text-error" colspan="8">${esc(err.message)}</td></tr>`;
    if (meta) meta.textContent = '';
  }
}

function openAccounts() {
  $('user-menu-dropdown')?.classList.add('hidden');
  $('modalAccounts').hidden = false;
  loadAccounts();
}
$('btnOpenAccounts')?.addEventListener('click', openAccounts);
['btnCloseAccounts', 'btnCloseAccounts2'].forEach((id) => {
  $(id)?.addEventListener('click', () => { $('modalAccounts').hidden = true; });
});
$('btnRefreshAccounts')?.addEventListener('click', loadAccounts);
$('modalAccounts')?.addEventListener('click', (e) => {
  if (e.target === $('modalAccounts')) $('modalAccounts').hidden = true;
});

// ==================== AI 视觉模型与端点设置 (BYOK 多服务商支持) ====================

const PROVIDER_PRESETS = {
  qwen: {
    name: "通义千问 (Qwen)",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: [
      { id: "qwen-vl-max", name: "qwen-vl-max (推荐 · 高精度CAD器件解析)", isVision: true },
      { id: "qwen-vl-plus", name: "qwen-vl-plus (快速 · 经济型蓝图识别)", isVision: true },
      { id: "qwen3-vl-plus", name: "qwen3-vl-plus (新一代超清多模态)", isVision: true },
      { id: "qwen-turbo", name: "qwen-turbo (⚠️ 纯文本模型 - 无法识图)", isVision: false }
    ]
  },
  glm: {
    name: "智谱清言 (GLM)",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    models: [
      { id: "glm-4.6v", name: "glm-4.6v (推荐 · 多模态图纸增强版)", isVision: true },
      { id: "glm-4v-plus", name: "glm-4v-plus (高分辨率视觉推理)", isVision: true },
      { id: "glm-4-air", name: "glm-4-air (⚠️ 纯文本模型 - 无法识图)", isVision: false }
    ]
  },
  gemini: {
    name: "Google Gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    models: [
      { id: "gemini-3.8-flash", name: "gemini-3.8-flash (超前多模态旗舰)", isVision: true },
      { id: "gemini-2.5-flash", name: "gemini-2.5-flash (推荐 · 原生视觉与超低延迟)", isVision: true },
      { id: "gemini-1.5-pro", name: "gemini-1.5-pro (超长上下文 · 百万Token图纸)", isVision: true },
      { id: "gemini-1.5-flash", name: "gemini-1.5-flash (轻量级图纸识别)", isVision: true }
    ]
  },
  openai: {
    name: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    models: [
      { id: "gpt-4o", name: "gpt-4o (推荐 · 全能旗舰多模态视觉)", isVision: true },
      { id: "gpt-4o-mini", name: "gpt-4o-mini (快速视觉与低成本拆量)", isVision: true },
      { id: "gpt-5.6-luna", name: "gpt-5.6-luna (超前推理旗舰)", isVision: true },
      { id: "gpt-3.5-turbo", name: "gpt-3.5-turbo (🚫 纯文本 - 严禁选择)", isVision: false }
    ]
  },
  deepseek: {
    name: "DeepSeek (深度求索)",
    baseUrl: "https://api.deepseek.com/v1",
    models: [
      { id: "deepseek-flash", name: "deepseek-flash (多模态推理版)", isVision: true },
      { id: "deepseek-vl2", name: "deepseek-vl2 (社区多模态兼容实验版)", isVision: true },
      { id: "deepseek-chat", name: "deepseek-chat (🚫 纯文本V3模型 - 不支持图纸)", isVision: false },
      { id: "deepseek-reasoner", name: "deepseek-reasoner (🚫 R1推理模型 - 不支持视觉)", isVision: false }
    ]
  },
  volcengine: {
    name: "字节火山方舟 (Doubao)",
    baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
    models: [
      { id: "doubao-seed-2-1-pro-260628", name: "doubao-seed-2-1-pro (豆包高精度视觉旗舰)", isVision: true },
      { id: "doubao-vision-pro-32k", name: "doubao-vision-pro-32k (火山旗舰视觉)", isVision: true },
      { id: "doubao-vision-lite", name: "doubao-vision-lite (高吞吐极速版)", isVision: true },
      { id: "doubao-pro-4k", name: "doubao-pro-4k (🚫 纯文本 - 无法解析图纸)", isVision: false }
    ]
  },
  xai: {
    name: "xAI (Grok)",
    baseUrl: "https://api.x.ai/v1",
    models: [
      { id: "grok-2-vision-1212", name: "grok-2-vision-1212 (官方视觉推理)", isVision: true },
      { id: "grok-4.6", name: "grok-4.6 (新一代多模态)", isVision: true },
      { id: "grok-2", name: "grok-2 (🚫 纯文本模型)", isVision: false }
    ]
  },
  minimax: {
    name: "MiniMax (稀宇科技)",
    baseUrl: "https://api.minimax.chat/v1",
    models: [
      { id: "MiniMax-M3", name: "MiniMax-M3 (全模态原生旗舰)", isVision: true },
      { id: "abab6.5s-chat", name: "abab6.5s-chat (多模态图纸支持)", isVision: true },
      { id: "abab6.5t", name: "abab6.5t (🚫 纯文本模型)", isVision: false }
    ]
  },
  custom: {
    name: "Custom (自定义兼容端点)",
    baseUrl: "https://your-custom-llm-proxy.internal/v1",
    models: [
      { id: "custom-vision-v1", name: "custom-vision-v1 (内部网关多模态模型)", isVision: true }
    ]
  }
};

const TEXT_ONLY_PATTERNS = [
  "deepseek-chat", "deepseek-reasoner", "gpt-3.5", "text-davinci", 
  "qwen-turbo", "qwen-max", "glm-4-air", "doubao-pro-4k", "claude-3-haiku-text",
  "abab6.5t", "grok-2"
];

let isCustomModelMode = false;
let currentSettings = null;

function checkIsVisionModel(modelName) {
  if (!modelName) return false;
  const lower = modelName.toLowerCase().trim();
  for (const pattern of TEXT_ONLY_PATTERNS) {
    if (lower === pattern || (pattern.length > 5 && lower.includes(pattern))) return false;
  }
  if (lower.includes("vl") || lower.includes("vision") || lower.includes("4o") || lower.includes("4v") ||
      lower.includes("gemini") || lower.includes("flash") || lower.includes("seed") || lower.includes("m3") ||
      lower.includes("luna") || lower.includes("custom")) {
    return true;
  }
  return false;
}

function applyValidationUI(isVision, modelName) {
  const banner = $('modelValidationBanner');
  const successBar = $('modelValidationSuccess');
  const saveBtn = $('btnSaveSettings');
  const bannerText = $('modelValidationText');
  if (!banner || !successBar || !saveBtn) return;

  if (!isVision) {
    banner.classList.remove('hidden');
    successBar.classList.add('hidden');
    if (bannerText) {
      bannerText.innerHTML = `已拦截纯文本模型！配电箱图纸拆量需要解析 DWG/PDF 蓝图元器件拓扑，当前配置的 <strong class="underline font-mono">[${esc(modelName) || '未命名'}]</strong> 仅支持纯文本，无法解析工程图纸。请切换至带有视觉识别能力的多模态模型（如 qwen-vl-max、glm-4.6v、gpt-4o、gemini-2.5-flash）。`;
    }
    saveBtn.disabled = true;
    saveBtn.classList.add('opacity-50', 'cursor-not-allowed');
  } else {
    banner.classList.add('hidden');
    successBar.classList.remove('hidden');
    saveBtn.disabled = false;
    saveBtn.classList.remove('opacity-50', 'cursor-not-allowed');
  }
}

function populateModelOptions(providerKey, selectedModelId) {
  const preset = PROVIDER_PRESETS[providerKey] || PROVIDER_PRESETS.qwen;
  const modelSelect = $('modelSelect');
  if (!modelSelect) return;
  modelSelect.innerHTML = '';
  let found = false;
  preset.models.forEach((m) => {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = m.name;
    opt.dataset.isVision = String(m.isVision);
    if (m.id === selectedModelId) {
      opt.selected = true;
      found = true;
    }
    modelSelect.appendChild(opt);
  });

  if (selectedModelId && !found) {
    setCustomModelMode(true, selectedModelId);
  } else {
    setCustomModelMode(false, selectedModelId || (preset.models[0] && preset.models[0].id) || '');
  }
}

function setCustomModelMode(isCustom, modelVal) {
  isCustomModelMode = !!isCustom;
  const selectBox = $('modelSelectContainer');
  const inputBox = $('customModelInput');
  const toggleText = $('customModelToggleText');
  if (!selectBox || !inputBox) return;

  if (isCustomModelMode) {
    selectBox.classList.add('hidden');
    inputBox.classList.remove('hidden');
    if (toggleText) toggleText.innerText = '选择预设';
    if (modelVal !== undefined) inputBox.value = modelVal;
    applyValidationUI(checkIsVisionModel(inputBox.value), inputBox.value);
  } else {
    selectBox.classList.remove('hidden');
    inputBox.classList.add('hidden');
    if (toggleText) toggleText.innerText = '手动输入';
    const curOpt = $('modelSelect')?.selectedOptions?.[0];
    const curVal = curOpt ? curOpt.value : (modelVal || '');
    const isVision = curOpt ? (curOpt.dataset.isVision === 'true') : checkIsVisionModel(curVal);
    applyValidationUI(isVision, curVal);
  }
}

function handleProviderChange() {
  const providerKey = $('providerSelect')?.value || 'qwen';
  const preset = PROVIDER_PRESETS[providerKey];
  if (!preset) return;
  if ($('baseUrlInput')) $('baseUrlInput').value = preset.baseUrl;
  populateModelOptions(providerKey);
}

function handleModelChange() {
  const modelSelect = $('modelSelect');
  const opt = modelSelect?.selectedOptions?.[0];
  const modelId = opt ? opt.value : '';
  const isVision = opt ? (opt.dataset.isVision === 'true') : checkIsVisionModel(modelId);
  if ($('customModelInput')) $('customModelInput').value = modelId;
  applyValidationUI(isVision, modelId);
}

function toggleApiKeyVisibility() {
  const input = $('apiKeyInput');
  const icon = $('eyeIcon');
  if (!input || !icon) return;
  if (input.type === 'password') {
    input.type = 'text';
    icon.innerText = 'visibility_off';
  } else {
    input.type = 'password';
    icon.innerText = 'visibility';
  }
}

async function testEndpointConnection() {
  const provider = $('providerSelect')?.value || 'qwen';
  const model = isCustomModelMode ? ($('customModelInput')?.value.trim() || '') : ($('modelSelect')?.value || '');
  const baseUrl = $('baseUrlInput')?.value.trim() || '';
  const apiKey = $('apiKeyInput')?.value.trim() || '';

  const testBtn = $('btnTestConnection');
  const testText = $('testText');
  const testIcon = $('testIcon');
  const statusBar = $('connectionStatusBar');
  const statusLed = $('statusLed');
  const statusMessage = $('statusMessage');

  if (testBtn) testBtn.disabled = true;
  if (testIcon) testIcon.classList.add('animate-spin');
  if (testText) testText.innerText = '测试中...';

  try {
    const res = await api('/api/settings/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider,
        vision_model: model,
        vision_base_url: baseUrl,
        vision_api_key: apiKey,
      }),
    });
    if (res.ok) {
      if (statusBar) statusBar.className = 'px-space-md py-1 bg-tertiary-fixed text-on-tertiary-fixed flex items-center justify-between font-label-sm text-label-sm border-b border-surface-container transition-all';
      if (statusLed) statusLed.className = 'w-1.5 h-1.5 rounded-full bg-tertiary animate-pulse shrink-0';
      if (statusMessage) statusMessage.innerText = `● 连通正常 · 延迟 ${res.latency_ms ? `${res.latency_ms}ms` : '—'} (多模态 Vision 图像张量通道就绪)`;
      toast('API 端点连通测试成功！');
    } else {
      if (statusBar) statusBar.className = 'px-space-md py-1 bg-error-container text-on-error-container flex items-center justify-between font-label-sm text-label-sm border-b border-surface-container transition-all';
      if (statusLed) statusLed.className = 'w-1.5 h-1.5 rounded-full bg-error shrink-0';
      if (statusMessage) statusMessage.innerText = `● 连接失败: ${res.error || '无法连通端点'}`;
      toast(`连接测试失败：${res.error || '请检查配置'}`, 4500);
    }
  } catch (err) {
    if (statusBar) statusBar.className = 'px-space-md py-1 bg-error-container text-on-error-container flex items-center justify-between font-label-sm text-label-sm border-b border-surface-container transition-all';
    if (statusLed) statusLed.className = 'w-1.5 h-1.5 rounded-full bg-error shrink-0';
    if (statusMessage) statusMessage.innerText = `● 连接失败: ${err.message}`;
    toast(`连接测试失败：${err.message}`, 4500);
  } finally {
    if (testBtn) testBtn.disabled = false;
    if (testIcon) testIcon.classList.remove('animate-spin');
    if (testText) testText.innerText = '测试连接';
  }
}

async function loadModelSettingsSummary() {
  try {
    const res = await api('/api/settings');
    currentSettings = res.settings || {};
    if (res.copper_reference_price) COPPER_REFERENCE = Number(res.copper_reference_price) || COPPER_REFERENCE;
    const configured = !!(res.vision_configured ?? currentSettings.vision_configured);
    const providerKey = res.vision_provider || currentSettings.vision_provider || 'qwen';
    const model = res.vision_model || currentSettings.vision_model || 'qwen-vl-max';
    const preset = PROVIDER_PRESETS[providerKey] || { name: providerKey };

    const dot = $('headerModelStatusDot');
    const text = $('headerModelText');
    const badge = $('headerModelBadge');

    if (dot && text && badge) {
      if (configured) {
        dot.className = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse shrink-0';
        text.innerText = `视觉模型: ${preset.name.split(' ')[0]} (${model})`;
        badge.innerText = 'BYOK已就绪';
        badge.className = 'font-label-sm text-label-sm px-1.5 py-0.5 rounded bg-tertiary-fixed text-on-tertiary-fixed font-mono';
      } else {
        dot.className = 'w-2 h-2 rounded-full bg-amber-500 animate-pulse shrink-0';
        text.innerText = '视觉模型: 未配置 (点击配置)';
        badge.innerText = '需配置KEY';
        badge.className = 'font-label-sm text-label-sm px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 font-mono';
      }
    }
    return configured;
  } catch (err) {
    console.warn('获取模型配置摘要失败:', err);
    return false;
  }
}

async function openSettingsModal() {
  if ($('user-menu-dropdown')) $('user-menu-dropdown').classList.add('hidden');
  $('modalSettings').hidden = false;

  // 重置状态条
  const statusBar = $('connectionStatusBar');
  const statusLed = $('statusLed');
  const statusMessage = $('statusMessage');
  if (statusBar) statusBar.className = 'px-space-md py-1 bg-surface-container text-on-surface-variant flex items-center justify-between font-label-sm text-label-sm border-b border-surface-container transition-all';
  if (statusLed) statusLed.className = 'w-1.5 h-1.5 rounded-full bg-slate-400 shrink-0';
  if (statusMessage) statusMessage.innerText = '● 尚未进行连通性测试 (点击右下角测试连接)';

  try {
    const data = await api('/api/settings');
    const s = data.settings || {};
    currentSettings = s;
    const providerKey = s.vision_provider || data.vision_provider || 'qwen';
    const model = s.vision_model || data.vision_model || 'qwen-vl-max';

    if ($('providerSelect')) $('providerSelect').value = providerKey;
    populateModelOptions(providerKey, model);

    if ($('baseUrlInput')) $('baseUrlInput').value = s.vision_base_url || (PROVIDER_PRESETS[providerKey]?.baseUrl || '');
    if ($('apiKeyInput')) {
      $('apiKeyInput').value = '';
      $('apiKeyInput').placeholder = s.vision_api_key_set ? '•••••••• (已保存密钥，如需修改请直接输入新Key)' : 'sk-...';
    }

    if ($('settingTileLargePages')) $('settingTileLargePages').checked = !!s.tile_large_pages;
  } catch (err) {
    toast(`加载系统设置失败：${err.message}`);
  }
}

const closeSettings = () => { $('modalSettings').hidden = true; };

async function saveSettingsConfiguration() {
  const provider = $('providerSelect')?.value || 'qwen';
  const model = isCustomModelMode ? ($('customModelInput')?.value.trim() || '') : ($('modelSelect')?.value || '');
  const baseUrl = $('baseUrlInput')?.value.trim() || '';
  const apiKey = $('apiKeyInput')?.value.trim() || '';

  if (!checkIsVisionModel(model)) {
    toast('当前选择的模型不支持 Vision 视觉识图，请更换后再保存', 4000);
    return;
  }

  const payload = {
    vision_provider: provider,
    vision_model: model || undefined,
    vision_base_url: baseUrl || undefined,
    tile_large_pages: !!$('settingTileLargePages')?.checked,
  };
  if (apiKey) {
    payload.vision_api_key = apiKey;
  }

  const saveBtn = $('btnSaveSettings');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.innerText = '保存中...'; }

  try {
    await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    toast('系统视觉模型与密钥配置已更新！');
    closeSettings();
    await loadModelSettingsSummary();
  } catch (err) {
    toast(`保存失败：${err.message}`);
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.innerHTML = '<span class="material-symbols-outlined text-[15px]">check</span><span>保存生效</span>';
    }
  }
}

// 绑定模型设置弹窗与表单事件
$('btnOpenModelSettings')?.addEventListener('click', openSettingsModal);
$('btnOpenSettings')?.addEventListener('click', openSettingsModal);
$('btnCloseSettings')?.addEventListener('click', closeSettings);
$('btnCancelSettings')?.addEventListener('click', closeSettings);
$('btnSaveSettings')?.addEventListener('click', saveSettingsConfiguration);
$('providerSelect')?.addEventListener('change', handleProviderChange);
$('modelSelect')?.addEventListener('change', handleModelChange);
$('btnToggleCustomModel')?.addEventListener('click', () => setCustomModelMode(!isCustomModelMode));
$('customModelInput')?.addEventListener('input', (e) => applyValidationUI(checkIsVisionModel(e.target.value), e.target.value));
$('btnToggleApiKeyVisibility')?.addEventListener('click', toggleApiKeyVisibility);
$('btnTestConnection')?.addEventListener('click', testEndpointConnection);

// AI 消耗与费用弹窗交互
$('btnOpenAiCosts').addEventListener('click', async () => {
  $('user-menu-dropdown').classList.add('hidden');
  $('modalAiCosts').hidden = false;
  try {
    const res = await api('/api/ai_logs');
    const sum = res.summary || {};
    const logs = res.ai_logs || res.logs || [];
    const totalCost = res.ai_cost_total != null ? res.ai_cost_total : (sum.total_cost || 0);
    const totalTokens = res.ai_tokens_total != null ? res.ai_tokens_total : (sum.total_tokens || 0);
    const callsCount = res.ai_logs_count || logs.length || sum.count || 0;

    $('aiTotalCost').textContent = `¥${Number(totalCost).toFixed(2)}`;
    $('aiTotalTokens').textContent = Number(totalTokens).toLocaleString();
    $('aiTotalCalls').textContent = `${Number(callsCount)} 次`;
    $('aiLogsCount').textContent = `${logs.length} 笔`;

    if (!logs.length) {
      $('aiLogsTbody').innerHTML = '';
      $('aiLogsEmpty').hidden = false;
    } else {
      $('aiLogsEmpty').hidden = true;
      $('aiLogsTbody').innerHTML = logs.map((l) => `
        <tr class="hover:bg-surface-container-low transition-colors">
          <td class="px-3 py-2 text-outline">${esc(fmtTime(l.created_at || l.timestamp))}</td>
          <td class="px-3 py-2 font-medium text-on-surface">${esc(l.filename || '—')}</td>
          <td class="px-3 py-2 text-outline">${esc(l.project_name || '—')}</td>
          <td class="px-3 py-2 text-outline">${esc(l.model || '—')}</td>
          <td class="px-3 py-2 text-right">${Number(l.total_tokens || 0).toLocaleString()}</td>
          <td class="px-3 py-2 text-right text-primary font-semibold">¥${Number(l.total_cost || 0).toFixed(4)}</td>
        </tr>
      `).join('');
    }
  } catch (err) {
    toast(`加载费用流水失败：${err.message}`);
  }
});
const closeAiCosts = () => { $('modalAiCosts').hidden = true; };
$('btnCloseAiCosts').addEventListener('click', closeAiCosts);
$('btnDoneAiCosts').addEventListener('click', closeAiCosts);

/* ================= ② 数据拆量与通用模型 ================= */

const ALL = '__all__';
const BOX_COMPONENT = '配电箱体';
/**
 * 每柜元器件直接吃后端拆分结果（backend/extractor/box_components.py），
 * 组价页、拆量页、导出共用同一份数量，前端不再自己按 used_in 算一套。
 */
function buildBoxes(data) {
  const circuits = data.circuits || [];
  const partsByBox = data.box_components || {};
  const counted = {};
  return (data.boxes || []).map((b, i) => {
    const code = b.code || `箱柜${i + 1}`;
    const first = !counted[code];
    counted[code] = true;
    const cir = first ? circuits.filter((c) => c.box === code) : [];
    const bbox = b.bbox || (cir.find((c) => c.bbox) || {}).bbox || null;
    const parts = first ? (partsByBox[code] || []) : [];
    return {
      key: `${i}`,
      code,
      name: b.name || '',   // 名称缺失就留空，不编造箱柜名
      raw: b,
      bbox,
      circuits: cir,
      parts,
    };
  });
}

/* box.size 在数据里同时承担柜型代号（GGD/JXF/XXM）与外形尺寸（800×600×2200）两种写法：
 * 带 × 的按尺寸展示，纯代号按柜型展示，两者都不猜、不补默认值。 */
function splitBoxSize(value) {
  const s = String(value || '').trim();
  if (!s) return { cabinetType: '', dims: '' };
  return /\d\s*[×xX*]\s*\d/.test(s) ? { cabinetType: '', dims: s } : { cabinetType: s, dims: '' };
}

/** 缺失的业务值统一显示为破折号，不显示编造内容。 */
const dashIfEmpty = (v) => (v === 0 || v ? String(v) : '—');

/** 费率小数→百分比字符串（保留小数，不四舍五入成整数，6.5% 不能显示成 7%） */
const fmtPct = (rate) => {
  if (rate == null || rate === '') return '';
  const n = Number(rate);
  if (!Number.isFinite(n)) return '';
  return String(Math.round(n * 10000) / 100);
};

/* ================= ③ 扒图工作台 (Blueprint Extraction & Inspection) ================= */

const CAD_ACI_COLORS = {
  1: '#FF0000', 2: '#FFFF00', 3: '#00FF00', 4: '#00FFFF',
  5: '#0000FF', 6: '#FF00FF', 7: '#FFFFFF', 8: '#808080', 9: '#C0C0C0'
};

const ExtractWorkbench = {
  id: '',
  job: null,
  boxes: [],
  selKey: '',
  selRowIdx: 0,
  filter: 'all', // 'all' | 'unverified' | 'doubt'
  sheetTab: 'components', // 'components' | 'circuits' | 'pending' | 'changes'
  verifiedBoxes: new Set(),
  changeLogs: [],
  page: 1,
  pages: 1,
  zoom: 1,
  panX: 0,
  panY: 0,
  isPanning: false,
  cadCollapsed: false,
  timer: 0,
  // 原生 DWG 矢量 CAD 交互引擎状态
  viewMode: 'vector', // 'vector' | 'raster'
  cadVectors: null,
  vectorState: { zoom: 0.001, panX: 0, panY: 0, isDragging: false, startX: 0, startY: 0 },
  cadLayersState: {},
  activeVectorTarget: null,
  eventsBound: false,
  resizersBound: false,

  stopPoll() { clearTimeout(this.timer); },

  async saveJobData(reason = "用户核对修改") {
    if (!this.job || !this.id) return;
    try {
      const allComps = [];
      this.boxes.forEach(b => {
        (b.parts || []).forEach(p => {
          allComps.push({
            name: p.name,
            spec: p.spec,
            unit: p.unit || '只',
            // 0 件就是 0 件（数量未标注行），不因为空值/0 就改写成 1 件
            quantity: Number.isFinite(Number(p.quantity)) && p.quantity !== '' ? Number(p.quantity) : 0,
            used_in: p.used_in || b.code,
            where: p.where || '主回路',
            note: p.note || ''
          });
        });
      });
      // 归不到任何柜的元器件行原样带回，用户改一次数据也不能把待核对项弄丢
      ((this.job.data && this.job.data.box_components && this.job.data.box_components['未归属']) || []).forEach(p => {
        allComps.push({
          name: p.name,
          spec: p.spec,
          unit: p.unit || '只',
          quantity: Number.isFinite(Number(p.quantity)) && p.quantity !== '' ? Number(p.quantity) : 0,
          used_in: p.used_in || '',
          note: p.note || ''
        });
      });
      const payload = {
        boxes: this.boxes.map(b => b.raw || { code: b.code, name: b.name }),
        circuits: (this.job.data && this.job.data.circuits) || [],
        components: allComps.length > 0 ? allComps : ((this.job.data && this.job.data.components) || []),
        requirements: (this.job.data && this.job.data.requirements) || [],
        uncertainties: (this.job.data && this.job.data.uncertainties) || [],
        changes: this.changeLogs || [],
        reason
      };
      await api(`/api/jobs/${this.id}/data`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } catch (e) {
      console.warn('saveJobData failed:', e);
    }
  },

  async open(id) {
    this.stopPoll();
    if ($('globalHeader')) $('globalHeader').hidden = true;
    $('viewList').hidden = true;
    $('viewPricing').hidden = true;
    $('viewExtract').hidden = false;
    const isFreshOpen = this.id !== id;
    this.id = id;
    // 数据到位前先摆真加载态，避免 HTML 静态内容闪现成假清单
    if (isFreshOpen) this.showLoadingState();

    let job;
    try {
      job = await api(`/api/jobs/${id}`);
    } catch (e) {
      this.setTopAlert(`加载图纸数据失败：${e.message}`, 'error');
      const cabTbody = $('extCabinetTbody');
      if (cabTbody) cabTbody.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-rose-500">加载图纸数据失败</td></tr>';
      const compTbody = $('extComponentsTbody');
      if (compTbody) compTbody.innerHTML = '<tr><td colspan="9" class="p-8 text-center text-rose-500">加载图纸数据失败</td></tr>';
      if ($('extCurBoxNameTitle')) $('extCurBoxNameTitle').textContent = '—';
      toast(`加载图纸失败：${e.message}`);
      return;
    }
    this.job = job;
    document.title = `${job.filename || '图纸'} · 电气拆量核对`;
    $('extFilename').textContent = job.filename || '未命名图纸';
    $('extBadge').textContent = (job.file_type || String(job.filename).split('.').pop() || 'CAD').toUpperCase();

    // 绑定进入组价与直接组价
    $('extGoPricingBtn').onclick = () => { location.hash = `#/job/${this.id}/pricing`; };
    $('extMainPriceBtn').onclick = () => { location.hash = `#/job/${this.id}/pricing`; };
    $('extExportBomBtn').onclick = () => { window.location.href = withToken(`/api/jobs/${id}/excel?stage=takeoff`); };

    if (!isDone(job.status)) {
      if (isFailed(job.status)) {
        this.setTopAlert(`识别失败：${job.error || '未知错误'}`, 'error');
      } else {
        this.setTopAlert('图纸识别中，请稍候…', 'info');
        this.timer = setTimeout(() => this.open(id), 2500);
      }
      return;
    }

    this.boxes = buildBoxes(job.data || {});
    await this.initCadViewer();
    // CAD 就绪后再统一切出加载态，避免各区域先后不一的半成品画面
    this.initStats();
    this.renderCabinetList();
    if (this.boxes.length > 0) {
      this.selectCabinet(this.boxes[0].key);
    }
    this.bindEvents();
    this.bindResizers();
  },

  openPricingModal() {
    const totalUnits = this.boxes.reduce((s, b) => s + (Number(b.raw.quantity) || 1), 0);
    const comps = ((this.job.data || {}).components || []).filter((c) => c.name !== BOX_COMPONENT);
    const totalPieces = comps.reduce((s, c) => s + (Number(c.quantity) || 0), 0);
    const doubts = ((this.job.data || {}).uncertainties || []).filter((u) => !u.resolved);

    if ($('pricingModalSummary')) {
      $('pricingModalSummary').textContent = `${this.boxes.length} 个箱柜（${totalUnits}台）/ ${comps.length} 种元器件（${Math.round(totalPieces)}件）`;
    }
    if ($('pricingModalWarning')) {
      if (doubts.length > 0) {
        $('pricingModalWarning').classList.remove('hidden');
        if ($('pricingModalWarningText')) $('pricingModalWarningText').textContent = `图纸尚有 ${doubts.length} 项存疑，建议核对后再执行组价`;
      } else {
        $('pricingModalWarning').classList.add('hidden');
      }
    }
    const modal = $('pricingModal');
    if (modal) modal.classList.remove('hidden');
  },

  closePricingModal() {
    const modal = $('pricingModal');
    if (modal) modal.classList.add('hidden');
  },

  initStats() {
    const d = this.job.data || {};
    const totalUnits = this.boxes.reduce((s, b) => s + (Number(b.raw.quantity) || 1), 0);
    const comps = (d.components || []).filter((c) => c.name !== BOX_COMPONENT);
    const totalPieces = comps.reduce((s, c) => s + (Number(c.quantity) || 0), 0);
    const circuits = d.circuits || [];
    const doubts = (d.uncertainties || []).filter((u) => !u.resolved);

    $('extStatBoxes').textContent = String(this.boxes.length);
    $('extStatBoxesUnit').textContent = `(${totalUnits}台)`;
    $('extStatComps').textContent = String(comps.length);
    $('extStatCompQty').textContent = String(Math.round(totalPieces));
    $('extStatCircuits').textContent = String(circuits.length);
    // 待核计数 = 未确认的不确定项 + 后端派生的待核对清单（存疑行/未归属行）
    $('extPendingCount').textContent = String(doubts.length + this.pendingChecks().length);
    $('extChangesCount').textContent = String(this.changeLogs.length);

    this.updateVerifiedBar();
    this.renderTopAlert();
  },

  /** 顶栏提醒条的样式与文案：info=中性信息，warn=真有不一致，error=任务失败 */
  setTopAlert(text, level = 'info') {
    const el = $('extTopAlert');
    const icon = $('extTopAlertIcon');
    const styles = {
      info: 'bg-sky-50 border-sky-300 text-sky-800',
      warn: 'bg-amber-50 border-amber-300 text-amber-800',
      error: 'bg-rose-50 border-rose-300 text-rose-800',
    };
    if (el) el.className = `flex items-center gap-1.5 border px-2.5 py-1 rounded text-xs font-medium ${styles[level] || styles.info}`;
    if (icon) {
      icon.textContent = level === 'warn' ? 'warning' : (level === 'error' ? 'error' : 'info');
      icon.className = `material-symbols-outlined text-[15px] ${level === 'warn' ? 'text-amber-600' : (level === 'error' ? 'text-rose-600' : 'text-sky-600')}`;
    }
    if ($('extTopAlertText')) $('extTopAlertText').textContent = text;
  },

  /**
   * 目录对账结论只来自后端真实结果：data.reconciliation（CatalogReconciliation）
   * 与 data.uncertainties 中 location=“图纸目录对账”的条目；没有就不编一句对账话。
   */
  renderTopAlert() {
    const d = (this.job && this.job.data) || {};
    const uncs = (d.uncertainties || []).filter((u) => !u.resolved);
    const errs = uncs.filter((u) => String(u.severity || '').toUpperCase() === 'ERROR');
    const reconUncs = uncs.filter((u) => String(u.location || '') === '图纸目录对账');
    const recon = d.reconciliation || (this.job.summary || {}).reconciliation || {};
    const circuits = (d.circuits || []).length;
    const base = `识别完成：${this.boxes.length} 台箱柜${circuits ? ` · ${circuits} 条回路` : ''}`;

    if (recon && recon.has_catalog) {
      const declared = Number(recon.total_declared_panels) || 0;
      const covered = Number(recon.covered_count) || 0;
      const missing = Number(recon.missing_count) || 0;
      const issues = Math.max(reconUncs.length, missing);
      if (issues > 0) {
        this.setTopAlert(`图纸目录对账：${issues} 处不一致（目录列出 ${declared} 台，图中找到 ${covered} 台），见“待核对”`, 'warn');
      } else {
        this.setTopAlert(`图纸目录对账：目录列出的 ${declared} 台在图中均已找到`, 'info');
      }
      return;
    }
    if (reconUncs.length > 0) {
      this.setTopAlert(`${base} · 图纸目录对账 ${reconUncs.length} 处不一致，见“待核对”`, 'warn');
      return;
    }
    if (errs.length > 0) {
      this.setTopAlert(`${base} · ${errs.length} 条阻断级待核对需处理`, 'warn');
      return;
    }
    this.setTopAlert(base, 'info');
  },

  updateVerifiedBar() {
    const total = this.boxes.length;
    const verified = this.verifiedBoxes.size;
    const pct = total > 0 ? Math.round((verified / total) * 100) : 0;
    $('extVerifiedBar').style.width = `${pct}%`;
    $('extVerifiedText').textContent = `${verified}/${total}`;
  },

  async initCadViewer() {
    this.pages = Math.max(1, Number(this.job.pages) || 1);
    const names = this.job.sheet_names || (this.job.summary || {}).sheet_names || {};
    $('extCadSheetSelect').innerHTML = Array.from({ length: this.pages }, (_, i) => {
      const n = i + 1;
      const nm = names[n] || names[String(n)] || '';
      return `<option value="${n}">${n}/${this.pages}${nm ? ` · ${esc(String(nm).slice(0, 20))}` : ''}</option>`;
    }).join('');

    this.page = 1;
    this.loadPageImage();

    // 检查是否为 CAD 图纸并拉取原生矢量流
    const ext = String(this.job.filename || '').split('.').pop().toLowerCase();
    const isCad = (ext === 'dwg' || ext === 'dxf' || this.job.file_type === 'dwg' || this.job.file_type === 'dxf');

    let vectorAvailable = false;
    if (isCad || !this.cadVectors) {
      try {
        const res = await api(`/api/jobs/${this.id}/cad_vectors`);
        if (res && res.available && res.vectors && res.vectors.extents) {
          this.cadVectors = res.vectors;
          vectorAvailable = true;
          if ($('extCadHudLayers')) $('extCadHudLayers').textContent = String(this.cadVectors.layers?.length || 0);
        }
      } catch (e) {
        console.warn('CAD vector loading fallback:', e);
      }
    } else if (this.cadVectors) {
      vectorAvailable = true;
    }

    if (vectorAvailable) {
      this.setViewMode('vector');
      this.resizeVectorCanvas();
      this.initVectorStateFromExtents();
      this.renderVectorCanvas();
    } else {
      this.setViewMode('raster');
      this.fitCad();
    }
  },

  setViewMode(mode) {
    this.viewMode = mode;
    const vCanvas = $('extVectorCanvas');
    const vHud = $('extVectorHud');
    const rCanvas = $('extBlueprintCanvas');
    const focusBox = $('extCadFocusBox');
    const btnVector = $('extBtnViewVector');
    const btnRaster = $('extBtnViewRaster');

    if (mode === 'vector') {
      if (!this.cadVectors) {
        toast('该图纸未解析到原生 CAD 矢量几何，请查看蓝图快照');
        return this.setViewMode('raster');
      }
      if (vCanvas) vCanvas.classList.remove('hidden');
      if (vHud) vHud.classList.remove('hidden');
      if (rCanvas) rCanvas.classList.add('hidden');
      if (focusBox) focusBox.classList.add('hidden');

      if (btnVector) {
        btnVector.className = 'px-2 py-0.5 rounded bg-blue-600 text-white font-bold cursor-pointer transition-colors shadow-2xs';
      }
      if (btnRaster) {
        btnRaster.className = 'px-2 py-0.5 rounded text-slate-400 hover:text-slate-200 cursor-pointer transition-colors';
      }

      this.resizeVectorCanvas();
      this.renderVectorCanvas();
    } else {
      if (vCanvas) vCanvas.classList.add('hidden');
      if (vHud) vHud.classList.add('hidden');
      if (rCanvas) rCanvas.classList.remove('hidden');
      if (focusBox && this.selKey) focusBox.classList.remove('hidden');

      if (btnVector) {
        btnVector.className = 'px-2 py-0.5 rounded text-slate-400 hover:text-slate-200 cursor-pointer transition-colors';
      }
      if (btnRaster) {
        btnRaster.className = 'px-2 py-0.5 rounded bg-blue-600 text-white font-bold cursor-pointer transition-colors shadow-2xs';
      }

      const curBox = this.boxes.find((x) => x.key === this.selKey);
      if (curBox) this.updateCadFocus(curBox);
    }
  },

  initVectorStateFromExtents() {
    if (!this.cadVectors || !this.cadVectors.extents) return;
    const canvas = $('extVectorCanvas');
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 600;
    const [[minX, minY], [maxX, maxY]] = this.cadVectors.extents;
    const dw = maxX - minX || 1;
    const dh = maxY - minY || 1;
    const scale = Math.min(w / dw, h / dh) * 0.95;
    this.vectorState.zoom = Math.max(scale, 0.0001);
    this.vectorState.panX = w / 2 - ((minX + maxX) / 2) * this.vectorState.zoom;
    this.vectorState.panY = h / 2 + ((minY + maxY) / 2) * this.vectorState.zoom;
    this.updateZoomDisplay();
  },

  resizeVectorCanvas() {
    const canvas = $('extVectorCanvas');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = rect.width || canvas.clientWidth || 800;
    const h = rect.height || canvas.clientHeight || 600;
    const tw = Math.round(w * dpr);
    const th = Math.round(h * dpr);
    if (canvas.width !== tw || canvas.height !== th) {
      canvas.width = tw;
      canvas.height = th;
    }
  },

  updateZoomDisplay() {
    let pct = '100%';
    if (this.viewMode === 'vector') {
      const z = this.vectorState.zoom;
      pct = `${Math.round(z * 1000)}%`;
    } else {
      pct = `${Math.round(this.zoom * 100)}%`;
    }
    if ($('extZoomLevelText')) $('extZoomLevelText').textContent = pct;
    if ($('extFooterZoomRatio')) $('extFooterZoomRatio').textContent = pct;
  },

  renderVectorCanvas() {
    const canvas = $('extVectorCanvas');
    if (!canvas || !this.cadVectors) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;

    ctx.save();
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 纯正 CAD 黑色背景
    ctx.fillStyle = '#141518';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.scale(dpr, dpr);
    ctx.translate(this.vectorState.panX, this.vectorState.panY);
    ctx.scale(this.vectorState.zoom, -this.vectorState.zoom);

    const layers = this.cadVectors.layers || [];
    const layerColors = layers.map(l => {
      if (typeof l.color === 'string') return l.color === 'White' ? '#FFFFFF' : l.color;
      return CAD_ACI_COLORS[l.color] || '#A0AEC0';
    });

    const isLayerVisible = (lIdx) => {
      const l = layers[lIdx];
      if (!l) return true;
      return this.cadLayersState[l.name] !== false;
    };

    // 1. 绘制 CAD 原生线段
    const lines = this.cadVectors.lines || [];
    for (let i = 0; i < lines.length; i++) {
      const [x1, y1, x2, y2, lIdx] = lines[i];
      if (!isLayerVisible(lIdx)) continue;
      ctx.strokeStyle = layerColors[lIdx] || '#A0AEC0';
      ctx.lineWidth = Math.max(0.5, 1 / this.vectorState.zoom);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }

    // 2. 绘制多段线
    const polylines = this.cadVectors.polylines || [];
    for (let i = 0; i < polylines.length; i++) {
      const [pts, lIdx] = polylines[i];
      if (!isLayerVisible(lIdx) || !pts || pts.length < 2) continue;
      ctx.strokeStyle = layerColors[lIdx] || '#A0AEC0';
      ctx.lineWidth = Math.max(0.5, 1 / this.vectorState.zoom);
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let j = 1; j < pts.length; j++) {
        ctx.lineTo(pts[j][0], pts[j][1]);
      }
      ctx.stroke();
    }

    // 3. 绘制圆与圆弧
    const circles = this.cadVectors.circles || [];
    for (let i = 0; i < circles.length; i++) {
      const [cx, cy, r, lIdx] = circles[i];
      if (!isLayerVisible(lIdx) || r <= 0) continue;
      ctx.strokeStyle = layerColors[lIdx] || '#A0AEC0';
      ctx.lineWidth = Math.max(0.5, 1 / this.vectorState.zoom);
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.stroke();
    }

    // 4. 绘制文字标注
    const texts = this.cadVectors.texts || [];
    for (let i = 0; i < texts.length; i++) {
      const [x, y, txt, height, lIdx] = texts[i];
      if (!isLayerVisible(lIdx) || !txt) continue;
      ctx.save();
      ctx.translate(x, y);
      ctx.scale(1, -1);
      ctx.fillStyle = layerColors[lIdx] || '#FFFFFF';
      const fontSize = Math.max(10, Number(height) || 14);
      ctx.font = `${fontSize}px "PingFang SC", "Microsoft YaHei", "SimHei", sans-serif`;
      ctx.fillText(txt, 0, 0);
      ctx.restore();
    }

    // 5. 绘制当前选中箱柜在 CAD 空间的高亮聚焦框与标牌
    if (this.activeVectorTarget) {
      const { x, y, label, w: bw = 35000, h: bh = 50000 } = this.activeVectorTarget;
      ctx.save();
      // 蓝色发光聚焦虚线框
      ctx.strokeStyle = '#3b82f6';
      ctx.lineWidth = Math.max(2, 2.5 / this.vectorState.zoom);
      ctx.setLineDash([8 / this.vectorState.zoom, 4 / this.vectorState.zoom]);
      ctx.strokeRect(x - bw * 0.15, y - bh * 0.85, bw, bh);

      // 十字寻迹星标
      ctx.strokeStyle = '#60a5fa';
      ctx.lineWidth = Math.max(1.5, 1.5 / this.vectorState.zoom);
      ctx.setLineDash([]);
      const cr = 4000;
      ctx.beginPath();
      ctx.moveTo(x - cr, y); ctx.lineTo(x + cr, y);
      ctx.moveTo(x, y - cr); ctx.lineTo(x, y + cr);
      ctx.stroke();

      // 标牌：在屏幕坐标系里画，字号恒为 11px（缩放不模糊也不拉大）、只显示箱柜号
      const z = this.vectorState.zoom;
      const tag = (String(label || '').match(/[^\s·\[\]]+/) || [''])[0];
      const sx = this.vectorState.panX + (x - bw * 0.15) * z;
      const sy = this.vectorState.panY - (y + bh * 0.15) * z;   // 聚焦框上沿（屏幕 Y）
      if (tag) {
        ctx.save();
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.font = 'bold 11px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.textBaseline = 'middle';
        const tw = ctx.measureText(tag).width + 8;
        ctx.fillStyle = 'rgba(37,99,235,0.92)';
        ctx.fillRect(sx, sy - 15, tw, 15);
        ctx.fillStyle = '#FFFFFF';
        ctx.fillText(tag, sx + 4, sy - 7);
        ctx.restore();
      }
      ctx.restore();
    }

    ctx.restore();
  },

  updateVectorHud(e) {
    const canvas = $('extVectorCanvas');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const cadX = Math.round((mx - this.vectorState.panX) / this.vectorState.zoom);
    const cadY = Math.round((this.vectorState.panY - my) / this.vectorState.zoom);

    if ($('extCadHudCoords')) $('extCadHudCoords').textContent = `X: ${cadX.toLocaleString()} Y: ${cadY.toLocaleString()}`;
    if ($('extFooterCoords')) $('extFooterCoords').textContent = `X: ${cadX.toLocaleString()} mm  Y: ${cadY.toLocaleString()} mm`;
  },

  loadPageImage() {
    const img = $('extPageImg');
    const svg = $('extSchematicSvg');
    const url = withToken(`/api/jobs/${this.id}/page/${this.page}`);

    img.onload = () => {
      img.classList.remove('hidden');
      svg.classList.add('hidden');
      this.fitCad();
    };
    img.onerror = () => {
      // 若没有转出图片，则显示高保真工程 SVG
      img.classList.add('hidden');
      svg.classList.remove('hidden');
      this.fitCad();
    };
    img.src = url;

    // 更新 SVG 标题栏（没有就不编）
    $('extSvgProj').textContent = this.job.project || '—';
    $('extSvgDrawing').textContent = this.job.filename || '—';
  },

  applyCadTransform() {
    const canvas = $('extBlueprintCanvas');
    canvas.style.transform = `translate(${this.panX}px, ${this.panY}px) scale(${this.zoom})`;
    $('extZoomLevelText').textContent = `${Math.round(this.zoom * 100)}%`;
    $('extFooterZoomRatio').textContent = `${Math.round(this.zoom * 100)}%`;
  },

  fitCad() {
    const container = $('extCadCanvasContainer');
    if (!container) return;
    this.zoom = 0.85;
    this.panX = 0;
    this.panY = 0;
    this.applyCadTransform();
  },

  updateCadFocus(box, comp = null) {
    if (!box) return;

    // 1. 矢量 CAD 空间精准对焦
    if (this.viewMode === 'vector' && this.cadVectors) {
      const canvas = $('extVectorCanvas');
      const w = canvas.clientWidth || 800;
      const h = canvas.clientHeight || 600;

      let targetX = null, targetY = null;
      const texts = this.cadVectors.texts || [];

      // 柜号精确检索
      const codeQuery = (box.code || '').toLowerCase();
      let match = null;
      if (codeQuery) {
        match = texts.find(t => t[2] && String(t[2]).toLowerCase().includes(codeQuery));
      }
      if (!match && box.name) {
        const nameKey = box.name.replace(/配电[箱柜总]+/g, '').trim().toLowerCase();
        if (nameKey) {
          match = texts.find(t => t[2] && String(t[2]).toLowerCase().includes(nameKey));
        }
      }

      if (match) {
        targetX = match[0];
        targetY = match[1];
      } else if (box.bbox && box.bbox.w && this.cadVectors.extents) {
        const [[minX, minY], [maxX, maxY]] = this.cadVectors.extents;
        targetX = minX + (box.bbox.x + box.bbox.w / 2) * (maxX - minX);
        targetY = maxY - (box.bbox.y + box.bbox.h / 2) * (maxY - minY);
      }

      if (targetX != null && targetY != null) {
        const targetZoom = Math.max(0.005, Math.min(0.04, w / 45000));
        this.vectorState.zoom = targetZoom;
        this.vectorState.panX = w / 2 - targetX * targetZoom;
        this.vectorState.panY = h / 2 + targetY * targetZoom;
        this.activeVectorTarget = {
          x: targetX,
          y: targetY,
          label: comp ? `[${box.code} · ${comp.name}]` : `[${box.code} ${box.name}]`
        };
        this.updateZoomDisplay();
        this.renderVectorCanvas();
      }
    }

    // 2. 位图快照模式标框更新
    const focusBox = $('extCadFocusBox');
    const focusLabel = $('extCadFocusLabel');
    if (!focusBox) return;
    focusBox.classList.toggle('hidden', this.viewMode === 'vector');   // 矢量模式由 canvas 自绘标框

    let x = 0.26, y = 0.22, w = 0.28, h = 0.58;
    if (box && box.bbox && box.bbox.w) {
      x = box.bbox.x;
      y = box.bbox.y;
      w = box.bbox.w;
      h = box.bbox.h;
    } else if (box) {
      const idx = this.boxes.indexOf(box);
      x = Math.max(0.08, Math.min(0.72, 0.08 + (idx % 4) * 0.22));
      y = 0.22;
      w = 0.22;
      h = 0.58;
    }

    if (comp) {
      h = 0.16;
      y = 0.22 + (this.selRowIdx % 5) * 0.08;
    }

    focusBox.style.left = `${Math.round(x * 100)}%`;
    focusBox.style.top = `${Math.round(y * 100)}%`;
    focusBox.style.width = `${Math.round(w * 100)}%`;
    focusBox.style.height = `${Math.round(h * 100)}%`;

    const label = comp
      ? `[${box.code} · ${comp.name}]`
      : `[${box.code} ${box.name}]`;
    if (focusLabel) focusLabel.textContent = label;
  },

  renderCabinetList() {
    const query = ($('extCabinetSearch').value || '').trim().toLowerCase();
    const filter = this.filter;
    const tbody = $('extCabinetTbody');

    const totalCount = this.boxes.length;
    let unverifiedCount = 0;
    let doubtCount = 0;

    const rows = [];
    this.boxes.forEach((b, idx) => {
      const isVerified = this.verifiedBoxes.has(b.code);
      const doubtNum = (b.raw.doubt_count != null) ? Number(b.raw.doubt_count) : (b.circuits.some(c => c.note && c.note.includes('存疑')) || b.parts.some(p => p.note && p.note.includes('存疑')) ? 1 : 0);
      if (!isVerified) unverifiedCount++;
      if (doubtNum > 0) doubtCount++;

      // 搜索过滤
      if (query && !b.code.toLowerCase().includes(query) && !b.name.toLowerCase().includes(query)) {
        return;
      }
      // Tab 过滤
      if (filter === 'unverified' && isVerified) return;
      if (filter === 'doubt' && doubtNum === 0) return;

      const active = (this.selKey === b.key);
      const activeCls = active
        ? 'active-cabinet bg-blue-50/90 border-l-2 border-l-blue-600 outline outline-1 outline-blue-600 cursor-pointer divide-x divide-blue-200 group'
        : 'hover:bg-slate-50 cursor-pointer transition-colors divide-x divide-slate-200 group';

      // 状态徽标：紧凑不折行（待核 / ✓ / ⚠N）
      let statusBadge = '';
      if (isVerified) {
        statusBadge = '<span class="inline-flex items-center justify-center min-w-[18px] px-1 rounded bg-emerald-100 text-emerald-700 font-bold text-[10px] leading-4 whitespace-nowrap" title="已核对">✓</span>';
      } else if (doubtNum > 0) {
        statusBadge = `<span class="inline-flex items-center justify-center min-w-[18px] px-1 rounded bg-amber-100 text-amber-800 font-bold font-mono text-[10px] leading-4 whitespace-nowrap" title="${doubtNum} 项存疑">⚠${doubtNum}</span>`;
      } else {
        statusBadge = '<span class="inline-flex items-center justify-center min-w-[18px] px-1 rounded bg-slate-100 text-slate-500 font-medium text-[10px] leading-4 whitespace-nowrap" title="待核对">待核</span>';
      }

      const seq = `1-${idx + 1}`;
      const size = splitBoxSize(b.raw.size);
      const subLine = [size.cabinetType, b.raw.install].filter(Boolean).join(' · ');
      rows.push(`
        <tr class="cabinet-row ${activeCls} h-8 select-none" data-key="${b.key}">
          <td class="h-8 px-0.5 text-center font-mono text-[10px] whitespace-nowrap ${active ? 'text-blue-700 font-semibold' : 'text-slate-500'}">${seq}</td>
          <td class="h-8 px-1 text-center overflow-hidden"><span class="block truncate font-mono font-bold text-[11px] whitespace-nowrap ${active ? 'text-blue-800' : 'text-slate-700'}" title="${esc(b.code)}">${esc(b.code || '—')}</span></td>
          <td class="h-8 px-1.5 overflow-hidden">
            <div class="truncate ${active ? 'font-bold text-blue-950' : 'font-medium text-slate-900'}" title="${esc(b.name || '名称未标注')}">${esc(b.name || '（名称未标注）')}</div>
            <div class="text-[10px] ${active ? 'text-blue-700' : 'text-slate-500'} font-mono leading-tight truncate" title="${esc(subLine || '柜型/安装未标注')}">${esc(subLine || '柜型/安装未标注')}</div>
          </td>
          <td class="h-8 px-0.5 text-center font-mono text-[11px] whitespace-nowrap ${active ? 'font-bold text-blue-800' : 'text-slate-600'}" title="${b.raw.quantity_confirmed === false ? '图面未标明台数，按单箱候选计' : '图面明示台数'}">×${esc(String(b.raw.quantity || 1))}</td>
          <td class="h-8 px-0.5 text-center whitespace-nowrap">${statusBadge}</td>
        </tr>
      `);
    });

    tbody.innerHTML = rows.join('') || '<tr><td colspan="5" class="p-4 text-center text-slate-400">无匹配箱柜</td></tr>';

    $('extFilterAllBtn').textContent = `全部 (${totalCount})`;
    $('extFilterUnverifiedBtn').textContent = `未核 (${unverifiedCount})`;
    $('extFilterDoubtBtn').textContent = `存疑 (${doubtCount})`;
  },

  selectCabinet(key) {
    this.selKey = key;
    this.selRowIdx = 0;
    const b = this.boxes.find((x) => x.key === key);
    if (!b) return;

    this.renderCabinetList();
    this.renderCabinetInspector(b);
    this.updateCadFocus(b);
  },

  /** 数据未到位前的真实加载态：不放任何演示/编造内容。 */
  showLoadingState() {
    this.boxes = [];
    this.selKey = '';
    this.activeVectorTarget = null;
    const cabTbody = $('extCabinetTbody');
    if (cabTbody) cabTbody.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-slate-400">正在加载图纸数据…</td></tr>';
    const compTbody = $('extComponentsTbody');
    if (compTbody) compTbody.innerHTML = '<tr><td colspan="9" class="p-8 text-center text-slate-400">正在加载图纸数据…</td></tr>';
    if ($('extCurBoxCodeBadge')) $('extCurBoxCodeBadge').textContent = '—';
    if ($('extCurBoxNameTitle')) $('extCurBoxNameTitle').textContent = '正在加载图纸数据…';
    if ($('extCurBoxSummarySub')) $('extCurBoxSummarySub').textContent = '—';
    ['extCurBoxTypePill', 'extCurBoxSizePill', 'extCurBoxInstallPill', 'extCurBoxLocationPill', 'extCurBoxQtyPill', 'extCurBoxIpPill']
      .forEach((id) => { if ($(id)) $(id).textContent = '—'; });
    ['extTabBadgeComps', 'extTabBadgeCircuits', 'extTabBadgePending', 'extTabBadgeChanges']
      .forEach((id) => { if ($(id)) $(id).textContent = '—'; });
    ['extFilterAllBtn', 'extFilterUnverifiedBtn', 'extFilterDoubtBtn'].forEach((id, i) => {
      if ($(id)) $(id).textContent = ['全部 (0)', '未核 (0)', '存疑 (0)'][i];
    });
    if ($('extFooterCountText')) $('extFooterCountText').textContent = '共 0 种元器件明细';
    if ($('extFooterSumQty')) $('extFooterSumQty').textContent = '0 件';
    if ($('extFooterSelection')) $('extFooterSelection').textContent = '—';
    const focusBox = $('extCadFocusBox');
    if (focusBox) focusBox.classList.add('hidden');
    if ($('extCadFocusLabel')) $('extCadFocusLabel').textContent = '';
    this.setTopAlert('正在加载图纸数据…', 'info');
  },

  /** 后端派生的待核对清单（存疑行 + 未归属行）：只随接口返回，不回写任务数据。 */
  pendingChecks() {
    return ((this.job && this.job.data && this.job.data.pending_checks) || []);
  },

  /** 当前柜要看得到的待核对项：本柜自己的 + 项目级“未归属”（归不到任何柜的行不能悄悄消失）。 */
  boxPendingChecks(code) {
    return this.pendingChecks().filter((p) => p.kind === 'unassigned' || p.box_code === code);
  },

  renderCabinetInspector(b) {
    const isVerified = this.verifiedBoxes.has(b.code);
    const size = splitBoxSize(b.raw.size);
    const qty = Number(b.raw.quantity) || 1;
    const qtyConfirmed = b.raw.quantity_confirmed !== false;

    // 输入框（表单兼容用）：没有值就留空，绝不写编造默认值进去
    if ($('extCurBoxCode')) $('extCurBoxCode').value = b.code || '';
    if ($('extCurBoxName')) $('extCurBoxName').value = b.name || '';
    if ($('extCurBoxType')) $('extCurBoxType').value = size.cabinetType;
    if ($('extCurBoxSize')) $('extCurBoxSize').value = size.dims;
    if ($('extCurBoxInstall')) $('extCurBoxInstall').value = b.raw.install || '';
    if ($('extCurBoxLocation')) $('extCurBoxLocation').value = b.raw.location || '';
    if ($('extCurBoxQty')) $('extCurBoxQty').value = qty;

    // 清爽两行概览头部，字段缺失一律显示“—”（对应后端 Box 字段）
    if ($('extCurBoxCodeBadge')) $('extCurBoxCodeBadge').textContent = dashIfEmpty(b.code);
    if ($('extCurBoxNameTitle')) $('extCurBoxNameTitle').textContent = b.name || '（名称未标注）';
    if ($('extCurBoxSummarySub')) $('extCurBoxSummarySub').textContent = `（共 ${qty} 台${qtyConfirmed ? '' : ' · 图面未标明台数'} · ${b.parts.length} 项元器件）`;
    if ($('extCurBoxTypePill')) $('extCurBoxTypePill').textContent = dashIfEmpty(size.cabinetType);
    if ($('extCurBoxSizePill')) $('extCurBoxSizePill').textContent = dashIfEmpty(size.dims);
    if ($('extCurBoxInstallPill')) $('extCurBoxInstallPill').textContent = dashIfEmpty(b.raw.install);
    if ($('extCurBoxLocationPill')) $('extCurBoxLocationPill').textContent = dashIfEmpty(b.raw.location);
    if ($('extCurBoxQtyPill')) $('extCurBoxQtyPill').textContent = qtyConfirmed ? `${qty} 台` : `${qty} 台 · 未明示`;
    if ($('extCurBoxIpPill')) $('extCurBoxIpPill').textContent = dashIfEmpty(b.raw.ip_rating);

    if ($('extMarkVerifiedText')) $('extMarkVerifiedText').textContent = isVerified ? '已完成核对 ✓' : '标记已核对';
    if ($('extMarkVerifiedBtn')) {
      $('extMarkVerifiedBtn').className = isVerified
        ? 'h-7 px-3 rounded bg-emerald-700 text-white font-medium flex items-center gap-1 transition-colors shadow-2xs cursor-pointer'
        : 'h-7 px-3 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-medium flex items-center gap-1 transition-colors shadow-2xs cursor-pointer';
    }

    if ($('extTabBadgeComps')) $('extTabBadgeComps').textContent = String(b.parts.length);
    if ($('extTabBadgeCircuits')) $('extTabBadgeCircuits').textContent = String(b.circuits.length);
    const doubtCount = b.parts.filter(p => p.note && p.note.includes('存疑')).length;
    const checks = this.boxPendingChecks(b.code);
    const pendingCount = checks.length || doubtCount;
    if ($('extTabBadgePending')) $('extTabBadgePending').textContent = `⚠ ${pendingCount}`;
    if ($('extTabBadgeChanges')) $('extTabBadgeChanges').textContent = String(this.changeLogs.filter(c => c.box === b.code).length);

    this.renderSheet();
  },

  renderSheet() {
    const b = this.boxes.find((x) => x.key === this.selKey);
    if (!b) return;

    document.querySelectorAll('.ext-sheet-tab').forEach(t => {
      const active = (t.dataset.target === this.sheetTab);
      t.classList.toggle('active', active);
      t.classList.toggle('bg-white', active);
      t.classList.toggle('border-t-2', active);
      t.classList.toggle('border-t-blue-600', active);
      t.classList.toggle('border-x', active);
      t.classList.toggle('border-slate-300', active);
      t.classList.toggle('text-blue-900', active);
      t.classList.toggle('font-bold', active);
    });

    if (this.sheetTab === 'components') {
      this.renderComponentsTable(b);
    } else if (this.sheetTab === 'circuits') {
      this.renderCircuitsTable(b);
    } else if (this.sheetTab === 'pending') {
      this.renderPendingTable(b);
    } else if (this.sheetTab === 'changes') {
      this.renderChangesTable();
    }
  },

  renderComponentsTable(b) {
    const tbody = $('extComponentsTbody');
    const parts = b.parts;

    if (!parts.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="p-8 text-center text-slate-400">该箱柜暂无识别元器件明细</td></tr>';
      if ($('extFooterCountText')) $('extFooterCountText').textContent = '共 0 种元器件明细';
      if ($('extFooterSumQty')) $('extFooterSumQty').textContent = '0 件';
      return;
    }

    const rows = parts.map((p, idx) => {
      const active = (this.selRowIdx === idx);
      const isModified = p.calibrated;
      const isDoubt = p.note && p.note.includes('存疑');

      const activeCls = active
        ? 'bg-blue-50/80 divide-x divide-blue-200 outline outline-2 outline-blue-600 relative h-[32px]'
        : (isDoubt
            ? 'bg-amber-50/70 hover:bg-amber-100/70 transition-colors divide-x divide-amber-200 h-[32px]'
            : 'hover:bg-slate-50 transition-colors divide-x divide-slate-300 h-[32px]');

      const cornerCls = isModified ? 'excel-modified-corner-tl' : (isDoubt ? 'excel-doubt-corner' : '');
      const statusHtml = isModified
        ? '<span class="px-1.5 py-0.2 rounded bg-blue-100 text-blue-800 font-medium text-[11px]">已改</span>'
        : (isDoubt
            ? '<span class="px-1.5 py-0.2 rounded bg-amber-100 text-amber-800 font-bold text-[10px] whitespace-nowrap">⚠ 存疑</span>'
            : '<span class="text-[11px] text-slate-400">已对齐</span>');

      return `
        <tr class="comp-row cursor-pointer select-none ${activeCls}" data-idx="${idx}">
          <td class="text-center font-mono text-[11px] ${active ? 'bg-blue-100 text-blue-800 font-bold' : (isDoubt ? 'bg-amber-100/80 text-amber-800 font-bold' : 'bg-[#f8fafc] text-slate-400')} select-none">${idx + 1}</td>
          <td class="px-1 text-center font-mono ${active ? 'text-blue-800 font-bold' : (isDoubt ? 'text-amber-800 font-bold' : 'text-slate-600')}">${idx + 1}</td>
          <td class="px-2.5 font-medium ${active ? 'font-bold text-blue-950' : (isDoubt ? 'text-amber-950' : 'text-slate-900')} truncate">${esc(p.name)}</td>
          <td class="px-2.5 font-mono ${active ? 'font-bold text-blue-900' : (isDoubt ? 'text-amber-900 font-semibold' : 'text-slate-800')} relative ${cornerCls} truncate" title="${esc(p.note || p.spec)}">
            <span>${esc(p.spec)}</span>
            ${p.oldSpec ? `<span class="text-[10px] font-sans font-normal text-slate-500 ml-1.5">(原: ${esc(p.oldSpec)})</span>` : ''}
          </td>
          <td class="px-2 text-right font-mono font-bold ${active ? 'text-blue-900' : (isDoubt ? 'text-amber-900' : 'text-slate-900')} tabular-nums">${p.quantity}</td>
          <td class="px-1 text-center text-slate-600">${esc(p.unit || '只')}</td>
          <td class="px-1 text-center text-emerald-600 font-semibold">✔</td>
          <td class="px-1 text-center">
            <button class="trace-link font-mono text-[13px] text-blue-600 hover:text-blue-800" title="定位图纸依据" type="button">⌖</button>
          </td>
          <td class="px-2 text-center">${statusHtml}</td>
        </tr>
      `;
    });

    tbody.innerHTML = rows.join('');

    const sumQty = parts.reduce((s, p) => s + Number(p.quantity || 0), 0);
    if ($('extFooterCountText')) $('extFooterCountText').textContent = `共 ${parts.length} 种元器件明细`;
    if ($('extFooterSumQty')) $('extFooterSumQty').textContent = `${Math.round(sumQty)} 件`;

    this.selectComponent(this.selRowIdx);
  },

  renderCircuitsTable(b) {
    const tbody = $('extComponentsTbody');
    const circuits = b.circuits;

    if (!circuits.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="p-8 text-center text-slate-400">该箱柜暂无回路分配明细</td></tr>';
      return;
    }

    const rows = circuits.map((c, idx) => `
      <tr class="hover:bg-slate-50 transition-colors divide-x divide-slate-300 h-[32px]" data-idx="${idx}">
        <td class="text-center font-mono text-[11px] bg-[#f8fafc] text-slate-400">${idx + 1}</td>
        <td class="px-1 text-center font-mono font-bold text-blue-700">${esc(c.circuit_no || `WL${idx + 1}`)}</td>
        <td class="px-2.5 font-medium truncate">${esc(c.load_name || '—')}</td>
        <td class="px-2.5 font-mono text-slate-700 truncate">${esc(c.breaker ? [c.breaker, c.cable].filter(Boolean).join(' · ') : (c.cable ? `控制电缆出线 · ${c.cable}` : '回路'))}</td>
        <td class="px-2 text-right font-mono font-bold text-slate-900 tabular-nums">${c.power_kw ? `${c.power_kw} kW` : '—'}</td>
        <td class="px-1 text-center text-slate-600">回路</td>
        <td class="px-1 text-center text-emerald-600 font-semibold">✔</td>
        <td class="px-1 text-center font-mono text-blue-600">${esc(c.phase || '3P')}</td>
        <td class="px-2 text-center"><span class="text-[11px] text-slate-400">已对齐</span></td>
      </tr>
    `);
    tbody.innerHTML = rows.join('');
    if ($('extFooterCountText')) $('extFooterCountText').textContent = `共 ${circuits.length} 条回路分配`;
    if ($('extFooterSumQty')) $('extFooterSumQty').textContent = `${circuits.length} 回路`;
  },

  renderPendingTable(b) {
    const tbody = $('extComponentsTbody');
    // 存疑行以拆量明细为准；另加后端派生的未归属行（项目级，每个柜都要看得到）
    const own = b.parts.filter(p => p.note && p.note.includes('存疑'));
    const derived = this.boxPendingChecks(b.code);
    const hasDerived = derived.length > 0;
    const items = own.map(p => ({
      name: p.name, spec: p.spec, quantity: p.quantity, unit: p.unit, where: p.where, note: p.note, tag: '⚠ 待核对',
    })).concat(hasDerived ? derived.filter(p => p.kind === 'unassigned').map(p => ({
      name: p.name, spec: p.spec, quantity: p.quantity, unit: p.unit,
      where: '未归属', note: p.note, tag: '⚠ 未归属',
    })) : []);

    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="p-8 text-center text-slate-400 font-medium">当前箱柜没有待核对项</td></tr>';
      return;
    }

    const rows = items.map((p, idx) => `
      <tr class="bg-amber-50/70 hover:bg-amber-100/70 transition-colors divide-x divide-amber-200 h-[32px]" data-idx="${idx}">
        <td class="text-center font-mono text-[11px] bg-amber-100/80 text-amber-800 font-bold select-none">${idx + 1}</td>
        <td class="px-1 text-center font-mono font-bold text-amber-800">${idx + 1}</td>
        <td class="px-2.5 font-bold text-amber-950 truncate">${esc(p.name)}</td>
        <td class="px-2.5 font-mono text-amber-900 font-semibold truncate relative excel-doubt-corner" title="${esc(p.note)}">${esc(p.spec)}</td>
        <td class="px-2 text-right font-mono font-bold text-amber-900 tabular-nums">${p.quantity}</td>
        <td class="px-1 text-center text-slate-700">${esc(p.unit || '只')}</td>
        <td class="px-1 text-center text-emerald-600 font-semibold">✔</td>
        <td class="px-1 text-center font-mono text-amber-700">${esc(p.where || '—')}</td>
        <td class="px-2 text-center"><span class="px-1.5 py-0.2 rounded bg-amber-100 text-amber-800 font-bold text-[10px] whitespace-nowrap">${p.tag}</span></td>
      </tr>
    `);
    tbody.innerHTML = rows.join('');
  },

  renderChangesTable() {
    const tbody = $('extComponentsTbody');
    if (!this.changeLogs.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="p-8 text-center text-slate-400">暂无人工修改与校准记录</td></tr>';
      return;
    }

    const rows = this.changeLogs.map((log, idx) => `
      <tr class="hover:bg-slate-50 transition-colors divide-x divide-slate-300 h-[32px]">
        <td class="text-center font-mono text-[11px] bg-blue-50 text-blue-800">${idx + 1}</td>
        <td class="px-1 text-center font-mono text-slate-500">${esc(log.time)}</td>
        <td class="px-2.5 font-semibold text-slate-900 truncate">${esc(log.box)} · ${esc(log.comp)}</td>
        <td class="px-2.5 font-mono text-blue-800 truncate">${esc(log.oldVal)} → <strong>${esc(log.newVal)}</strong></td>
        <td class="px-2 text-right font-mono text-slate-600">—</td>
        <td class="px-1 text-center text-slate-600">—</td>
        <td class="px-1 text-center text-emerald-600 font-semibold">✔</td>
        <td class="px-1 text-center text-blue-600">已生效</td>
        <td class="px-2 text-center"><span class="px-1.5 py-0.2 rounded bg-blue-100 text-blue-800 font-medium text-[11px]">人工校准</span></td>
      </tr>
    `);
    tbody.innerHTML = rows.join('');
  },

  selectComponent(idx) {
    const b = this.boxes.find((x) => x.key === this.selKey);
    if (!b || !b.parts[idx]) return;
    this.selRowIdx = idx;
    const comp = b.parts[idx];

    // 高亮行
    document.querySelectorAll('#extComponentsTbody tr.comp-row').forEach(tr => {
      const match = (tr.dataset.idx == idx);
      tr.classList.toggle('bg-blue-50/80', match);
      tr.classList.toggle('outline', match);
      tr.classList.toggle('outline-2', match);
      tr.classList.toggle('outline-blue-600', match);
    });

    if ($('extFooterSelection')) $('extFooterSelection').textContent = `${b.code} ${b.name} / 行${idx + 1} [${comp.name} ${comp.spec}]`;
    this.updateCadFocus(b, comp);
  },

  calibrateActiveCell() {
    const b = this.boxes.find((x) => x.key === this.selKey);
    if (!b || !b.parts[this.selRowIdx]) return;
    const comp = b.parts[this.selRowIdx];

    openFieldForm({
      title: `人工校准 · ${comp.name}`,
      submitText: '保存',
      fields: [{ key: 'spec', label: '设计型号规格', required: true, value: comp.spec, placeholder: '按图面实际型号填' }],
      onOk: ({ spec }) => {
        if (!spec || spec === comp.spec) return false;

        const oldSpec = comp.spec;
        comp.oldSpec = oldSpec;
        comp.spec = spec;
        comp.calibrated = true;

        this.changeLogs.push({
          time: new Date().toLocaleTimeString(),
          box: b.code,
          comp: comp.name,
          oldVal: oldSpec,
          newVal: comp.spec,
          note: '人工校准'
        });

        toast(`已校准 ${comp.name} 型号规格为：${comp.spec}`);
        this.initStats();
        this.renderSheet();
      }
    });
  },

  // 三栏宽度的统一约束：图纸栏 ≥ 360，箱柜栏 220~480，明细栏 ≥ 600（与 index.html 的 min-w 一致）
  LAYOUT: { cadMin: 360, cabMin: 220, cabMax: 480, detailMin: 600, gap: 8, key: 'extract.layout' },

  loadLayout() {
    try { return JSON.parse(localStorage.getItem(this.LAYOUT.key)) || {}; } catch (_) { return {}; }
  },

  saveLayout(patch) {
    try { localStorage.setItem(this.LAYOUT.key, JSON.stringify({ ...this.loadLayout(), ...patch })); } catch (_) {}
  },

  /** 按当前窗口宽度把两栏宽度收进合法范围；cad/cab 为期望宽度（px），不传则用当前值。 */
  applyLayout(cad, cab) {
    const L = this.LAYOUT;
    const cadPanel = $('extCadPanel');
    const cabPanel = $('extCabinetPanel');
    const row = cadPanel && cadPanel.parentElement;
    if (!cadPanel || !cabPanel || !row) return;
    const total = row.clientWidth;
    if (!total) return;                       // 页面还没显示，量不到宽度
    // 窗口窄到放不下三栏最小宽度：临时收起图纸栏（不记为用户收起），变宽后自动恢复
    const need = L.cadMin + L.cabMin + L.detailMin + L.gap;
    if (!this.cadCollapsed && total < need) {
      this.autoCollapsed = true;
      this.setCadCollapsed(true, false);
      return;
    }
    if (this.autoCollapsed && this.cadCollapsed && total >= need) {
      this.autoCollapsed = false;
      this.setCadCollapsed(false, false);
      return;
    }
    const cadShown = !this.cadCollapsed;
    let cabW = cab ?? cabPanel.getBoundingClientRect().width;
    cabW = Math.max(L.cabMin, Math.min(L.cabMax, cabW));
    if (cadShown) {
      let cadW = cad ?? cadPanel.getBoundingClientRect().width;
      const cadMax = Math.max(L.cadMin, total - cabW - L.detailMin - L.gap);
      cadW = Math.max(L.cadMin, Math.min(cadMax, cadW));
      // 窗口太窄放不下时，先压箱柜栏，再压图纸栏到下限
      const over = cadW + cabW + L.detailMin + L.gap - total;
      if (over > 0) cabW = Math.max(L.cabMin, cabW - over);
      cadPanel.style.flex = `0 0 ${Math.round(cadW)}px`;
    }
    cabPanel.style.width = `${Math.round(cabW)}px`;
    this.updateCadCompact();
  },

  /** 图纸栏窄时工具条只显示图标（.compact），避免按钮挤在一起。 */
  updateCadCompact() {
    const panel = $('extCadPanel');
    const bar = $('extCadToolbar');
    if (!panel || !bar) return;
    // 先按完整模式排一次，放不下（折成两行）再切到只显示图标
    panel.classList.remove('compact');
    if (bar.offsetHeight > 40) panel.classList.add('compact');
  },

  redrawCad() {
    if (this.cadCollapsed) return;
    if (this.viewMode === 'vector') {
      this.resizeVectorCanvas();
      this.renderVectorCanvas();
    }
  },

  bindResizers() {
    if (this.resizersBound) return;
    this.resizersBound = true;

    const resizer1 = $('extResizer1');
    const cadPanel = $('extCadPanel');
    const resizer2 = $('extResizer2');
    const cabPanel = $('extCabinetPanel');
    const saved = this.loadLayout();
    if (saved.collapsed) this.setCadCollapsed(true, false);
    requestAnimationFrame(() => this.applyLayout(saved.cad, saved.cab));

    const drag = (handle, onMove, onEnd) => {
      let active = false;
      handle.onpointerdown = (e) => {
        active = true;
        handle.classList.add('resizing');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        try { handle.setPointerCapture(e.pointerId); } catch (_) {}
        onMove.start?.(e);
      };
      window.addEventListener('pointermove', (e) => { if (active) onMove(e); });
      const end = () => {
        if (!active) return;
        active = false;
        handle.classList.remove('resizing');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        onEnd();
      };
      window.addEventListener('pointerup', end);
      window.addEventListener('pointercancel', end);
    };

    if (resizer1 && cadPanel) {
      drag(resizer1, (e) => {
        this.applyLayout(e.clientX - cadPanel.getBoundingClientRect().left);
        this.redrawCad();
      }, () => {
        this.saveLayout({ cad: Math.round(cadPanel.getBoundingClientRect().width) });
        this.redrawCad();
      });
      resizer1.ondblclick = () => {          // 双击恢复默认
        this.saveLayout({ cad: undefined });
        cadPanel.style.flex = '0 0 40%';
        requestAnimationFrame(() => { this.applyLayout(); this.redrawCad(); });
      };
    }

    if (resizer2 && cabPanel) {
      let startX = 0;
      let startW = 300;
      const move = (e) => this.applyLayout(undefined, startW + e.clientX - startX);
      move.start = (e) => { startX = e.clientX; startW = cabPanel.getBoundingClientRect().width; };
      drag(resizer2, move, () => {
        this.saveLayout({ cab: Math.round(cabPanel.getBoundingClientRect().width) });
        this.redrawCad();
      });
      resizer2.ondblclick = () => {
        this.saveLayout({ cab: undefined });
        this.applyLayout(undefined, 300);
        this.redrawCad();
      };
    }

    // 三栏容器宽度变化（窗口缩放等）时重新收进合法范围。监听容器本身，不依赖 window.resize
    let pending = 0;
    const relayout = () => {
      clearTimeout(pending);
      pending = setTimeout(() => {
        if ($('viewExtract') && $('viewExtract').hidden) return;
        this.applyLayout();
        this.redrawCad();
      }, 60);
    };
    const row = cadPanel && cadPanel.parentElement;
    if (row && typeof ResizeObserver !== 'undefined') {
      let lastW = 0;
      new ResizeObserver(() => {
        if (row.clientWidth === lastW) return;   // 只关心宽度变化，避免自身调整引发循环
        lastW = row.clientWidth;
        relayout();
      }).observe(row);
    } else {
      window.addEventListener('resize', relayout);
    }
  },

  /** 收起 / 展开图纸栏。收起时左侧留常驻展开条，展开回到收起前的宽度。 */
  setCadCollapsed(collapsed, save = true) {
    this.cadCollapsed = collapsed;
    const panel = $('extCadPanel');
    if (panel) panel.style.display = collapsed ? 'none' : 'flex';
    if ($('extResizer1')) $('extResizer1').style.display = collapsed ? 'none' : '';
    if ($('extExpandCadRail')) $('extExpandCadRail').hidden = !collapsed;
    if (save) this.saveLayout({ collapsed });
    if (!collapsed) {
      this.applyLayout(this.loadLayout().cad);   // 同步收进合法宽度，避免展开瞬间溢出
      requestAnimationFrame(() => {
        if (this.viewMode === 'vector') {
          this.resizeVectorCanvas();
          this.renderVectorCanvas();
        } else {
          this.fitCad();
        }
      });
    } else {
      this.applyLayout();
    }
  },

  bindEvents() {
    if (this.eventsBound) return;
    this.eventsBound = true;

    // 搜索
    $('extCabinetSearch').oninput = () => { this.renderCabinetList(); };

    // 筛选切换
    $('extFilterAllBtn').onclick = () => { this.filter = 'all'; this.renderCabinetList(); };
    $('extFilterUnverifiedBtn').onclick = () => { this.filter = 'unverified'; this.renderCabinetList(); };
    $('extFilterDoubtBtn').onclick = () => { this.filter = 'doubt'; this.renderCabinetList(); };
    if ($('extFilterPendingTopBtn')) $('extFilterPendingTopBtn').onclick = () => { this.sheetTab = 'pending'; this.renderSheet(); };
    if ($('extOpenChangeLogTopBtn')) $('extOpenChangeLogTopBtn').onclick = () => { this.sheetTab = 'changes'; this.renderSheet(); };

    // 模式切换（📐 矢量 CAD vs 🖼 高清图）
    if ($('extBtnViewVector')) $('extBtnViewVector').onclick = () => { this.setViewMode('vector'); };
    if ($('extBtnViewRaster')) $('extBtnViewRaster').onclick = () => { this.setViewMode('raster'); };

    // 箱柜点击
    $('extCabinetTbody').onclick = (e) => {
      const tr = e.target.closest('tr.cabinet-row');
      if (tr && tr.dataset.key) this.selectCabinet(tr.dataset.key);
    };

    // 元器件表格行点击与双击编辑
    $('extComponentsTbody').onclick = (e) => {
      const tr = e.target.closest('tr.comp-row');
      if (tr && tr.dataset.idx != null) this.selectComponent(Number(tr.dataset.idx));
    };
    $('extComponentsTbody').ondblclick = (e) => {
      const tr = e.target.closest('tr.comp-row');
      if (tr) this.calibrateActiveCell();
    };

    // Sheet 选项卡切换
    $('extTabComponents').onclick = () => { this.sheetTab = 'components'; this.renderSheet(); };
    $('extTabCircuits').onclick = () => { this.sheetTab = 'circuits'; this.renderSheet(); };
    $('extTabPending').onclick = () => { this.sheetTab = 'pending'; this.renderSheet(); };
    $('extTabChanges').onclick = () => { this.sheetTab = 'changes'; this.renderSheet(); };

    // 定位与标记已核对
    $('extLocateInCadBtn').onclick = () => {
      const b = this.boxes.find((x) => x.key === this.selKey);
      if (b) {
        this.updateCadFocus(b);
        toast(`已在图纸中重新对焦高亮：${b.code} ${b.name}`);
      }
    };
    $('extMarkVerifiedBtn').onclick = () => {
      const b = this.boxes.find((x) => x.key === this.selKey);
      if (!b) return;
      if (this.verifiedBoxes.has(b.code)) {
        this.verifiedBoxes.delete(b.code);
        toast(`已取消核对：${b.code}`);
      } else {
        this.verifiedBoxes.add(b.code);
        toast(`已核对确认：${b.code} ${b.name}`);
      }
      this.initStats();
      this.renderCabinetList();
      this.renderCabinetInspector(b);
      this.saveJobData(`核对状态变更: ${b.code}`);
    };

    // 添加元器件与添加箱柜
    $('extAddComponentBtn').onclick = () => {
      const b = this.boxes.find((x) => x.key === this.selKey);
      if (!b) return;
      openFieldForm({
        title: `新增元器件 · ${b.code}`,
        submitText: '添加',
        fields: [
          { key: 'name', label: '元器件名称', required: true, placeholder: '如 微型断路器' },
          { key: 'spec', label: '型号规格', placeholder: '如 MCB-16A/1P' },
          { key: 'qty', label: '数量', type: 'number', required: true, placeholder: '如 2' },
        ],
        onOk: ({ name, spec, qty }) => {
          if (!Number.isFinite(qty) || qty <= 0) { toast('数量需为大于 0 的整数'); return false; }
          b.parts.push({ name, spec, unit: '只', quantity: Math.round(qty), where: '主回路', note: '手动新增', calibrated: true });
          this.changeLogs.push({ time: new Date().toLocaleTimeString(), box: b.code, comp: name, oldVal: '无', newVal: spec, note: '新增器件' });
          toast(`已向 ${b.code} 添加元器件：${name} (${Math.round(qty)}只)`);
          this.initStats();
          this.renderSheet();
          this.saveJobData(`向 ${b.code} 添加元器件 ${name} (${spec})`);
        }
      });
    };

    $('extAddCabinetBtn').onclick = () => {
      openFieldForm({
        title: '新增箱柜',
        submitText: '添加',
        fields: [
          { key: 'code', label: '箱柜编号', required: true, placeholder: '如 02AP3' },
          { key: 'name', label: '箱柜名称', placeholder: '如 照明配电箱' },
        ],
        onOk: ({ code, name }) => {
          if (this.boxes.some((x) => x.code === code)) { toast(`箱柜编号 ${code} 已存在`); return false; }
          const newBox = {
            key: String(this.boxes.length),
            code,
            name,
            // 只记用户明确填的编号/名称：柜型、尺寸、位置等图面未给就不填
            raw: { code, name, quantity: 1, quantity_confirmed: false },
            bbox: null,
            circuits: [],
            parts: []
          };
          this.boxes.push(newBox);
          toast(`已添加箱柜：${code}${name ? ` ${name}` : ''}（柜型/尺寸/安装待核对时补全）`);
          this.initStats();
          this.renderCabinetList();
          this.selectCabinet(newBox.key);
          this.saveJobData(`新增箱柜 ${code} ${name}`);
        }
      });
    };

    // 缩放控制（适配矢量与位图）
    if ($('extZoomInBtn')) $('extZoomInBtn').onclick = () => {
      if (this.viewMode === 'vector') {
        this.vectorState.zoom = Math.min(10, this.vectorState.zoom * 1.25);
        this.updateZoomDisplay();
        this.renderVectorCanvas();
      } else {
        this.zoom = Math.min(6, this.zoom * 1.25);
        this.applyCadTransform();
      }
    };
    if ($('extZoomOutBtn')) $('extZoomOutBtn').onclick = () => {
      if (this.viewMode === 'vector') {
        this.vectorState.zoom = Math.max(0.000001, this.vectorState.zoom / 1.25);
        this.updateZoomDisplay();
        this.renderVectorCanvas();
      } else {
        this.zoom = Math.max(0.15, this.zoom / 1.25);
        this.applyCadTransform();
      }
    };
    if ($('extFitWindowBtn')) $('extFitWindowBtn').onclick = () => {
      if (this.viewMode === 'vector') {
        this.initVectorStateFromExtents();
        this.renderVectorCanvas();
      } else {
        this.fitCad();
      }
    };
    const hdBtn = $('extHdRenderBtn') || $('extHdBtn');
    if (hdBtn) hdBtn.onclick = () => {
      if (this.viewMode === 'vector') {
        this.vectorState.zoom = 0.02;
        this.updateZoomDisplay();
        this.renderVectorCanvas();
      } else {
        this.zoom = 1; this.panX = 0; this.panY = 0;
        this.applyCadTransform();
      }
    };

    // 图纸翻页
    if ($('extCadSheetSelect')) {
      $('extCadSheetSelect').onchange = (e) => {
        this.page = Number(e.target.value);
        this.loadPageImage();
      };
    }
    if ($('extPgPrev')) {
      $('extPgPrev').onclick = () => {
        if (this.page > 1) {
          this.page--;
          $('extCadSheetSelect').value = this.page;
          this.loadPageImage();
        }
      };
    }
    if ($('extPgNext')) {
      $('extPgNext').onclick = () => {
        if (this.page < this.pages) {
          this.page++;
          $('extCadSheetSelect').value = this.page;
          this.loadPageImage();
        }
      };
    }

    // 收起/展开图纸视口（展开入口：图纸栏外的常驻展开条）
    if ($('extCollapseCadBtn')) $('extCollapseCadBtn').onclick = () => { this.autoCollapsed = false; this.setCadCollapsed(true); };
    if ($('extExpandCadRail')) $('extExpandCadRail').onclick = () => { this.autoCollapsed = false; this.setCadCollapsed(false); };

    // 画布滚轮缩放与平移（支持原生矢量 CAD 与位图）
    const canvasWrap = $('extCadCanvasContainer');
    if (canvasWrap) {
      canvasWrap.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (this.viewMode === 'vector') {
          const rect = canvasWrap.getBoundingClientRect();
          const mouseX = e.clientX - rect.left;
          const mouseY = e.clientY - rect.top;
          const factor = e.deltaY < 0 ? 1.18 : 0.82;
          const oldZoom = this.vectorState.zoom;
          const newZoom = Math.min(Math.max(oldZoom * factor, 0.000001), 10);
          this.vectorState.panX = mouseX - (mouseX - this.vectorState.panX) * (newZoom / oldZoom);
          this.vectorState.panY = mouseY - (mouseY - this.vectorState.panY) * (newZoom / oldZoom);
          this.vectorState.zoom = newZoom;
          this.updateZoomDisplay();
          this.renderVectorCanvas();
        } else {
          const rect = canvasWrap.getBoundingClientRect();
          const mouseX = e.clientX - rect.left - rect.width / 2;
          const mouseY = e.clientY - rect.top - rect.height / 2;
          const oldZoom = this.zoom;
          const factor = Math.exp(-e.deltaY * 0.0018);
          const newZoom = Math.max(0.15, Math.min(6, oldZoom * factor));
          this.panX = mouseX - (mouseX - this.panX) * (newZoom / oldZoom);
          this.panY = mouseY - (mouseY - this.panY) * (newZoom / oldZoom);
          this.zoom = newZoom;
          this.applyCadTransform();
        }
      }, { passive: false });

      let dragStart = null;
      canvasWrap.onpointerdown = (e) => {
        if (e.button !== 0) return;
        if (this.viewMode === 'vector') {
          dragStart = { x: e.clientX, y: e.clientY, ox: this.vectorState.panX, oy: this.vectorState.panY };
        } else {
          dragStart = { x: e.clientX, y: e.clientY, ox: this.panX, oy: this.panY };
        }
        try { canvasWrap.setPointerCapture(e.pointerId); } catch (_) {}
        canvasWrap.classList.remove('cursor-grab');
        canvasWrap.classList.add('cursor-grabbing');
      };
      canvasWrap.onpointermove = (e) => {
        if (dragStart) {
          if (this.viewMode === 'vector') {
            this.vectorState.panX = dragStart.ox + (e.clientX - dragStart.x);
            this.vectorState.panY = dragStart.oy + (e.clientY - dragStart.y);
            this.renderVectorCanvas();
          } else {
            this.panX = dragStart.ox + (e.clientX - dragStart.x);
            this.panY = dragStart.oy + (e.clientY - dragStart.y);
            this.applyCadTransform();
          }
        }
        if (this.viewMode === 'vector') {
          this.updateVectorHud(e);
        }
      };
      const endDrag = (e) => {
        if (dragStart && e && e.pointerId) {
          try { canvasWrap.releasePointerCapture(e.pointerId); } catch (_) {}
        }
        dragStart = null;
        canvasWrap.classList.remove('cursor-grabbing');
        canvasWrap.classList.add('cursor-grab');
      };
      canvasWrap.onpointerup = endDrag;
      canvasWrap.onpointercancel = endDrag;
      canvasWrap.ondblclick = () => {
        if (this.viewMode === 'vector') {
          this.initVectorStateFromExtents();
          this.renderVectorCanvas();
        } else {
          this.fitCad();
        }
      };
    }

    // 窗口尺寸变化重调矢量画布
    window.addEventListener('resize', () => {
      if (this.viewMode === 'vector') {
        this.resizeVectorCanvas();
        this.renderVectorCanvas();
      }
    });

    // 组价确认弹窗操作
    if ($('closePricingModalBtn')) {
      $('closePricingModalBtn').onclick = () => { this.closePricingModal(); };
    }
    if ($('confirmPricingBtn')) {
      $('confirmPricingBtn').onclick = () => {
        this.closePricingModal();
        location.hash = `#/job/${this.id}/pricing`;
      };
    }

    // AI Drawer
    $('extAiToggleBtn').onclick = () => {
      $('extAiDrawer').classList.toggle('translate-x-full');
    };
    $('extAiCloseBtn').onclick = () => {
      $('extAiDrawer').classList.add('translate-x-full');
    };
    $('extAiForm').onsubmit = async (e) => {
      e.preventDefault();
      const input = $('extAiInput');
      const val = (input.value || '').trim();
      if (!val) return;
      input.value = '';

      const msgs = $('extAiMsgs');
      msgs.innerHTML += `
        <div class="p-2.5 rounded bg-blue-50 border border-blue-200 text-blue-900 text-[11px] self-end">
          <strong>你:</strong> ${esc(val)}
        </div>
      `;
      msgs.scrollTop = msgs.scrollHeight;

      try {
        const res = await api(`/api/jobs/${this.id}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: val, history: [] })
        });
        msgs.innerHTML += `
          <div class="p-2.5 rounded bg-white border border-slate-200 text-slate-800 text-[11px] leading-relaxed shadow-2xs">
            <div class="flex items-center gap-1 font-bold text-blue-700 mb-0.5">
              <span class="material-symbols-outlined text-[13px]">smart_toy</span>
              <span>AI 诊断助手</span>
            </div>
            ${esc(res.reply || '（AI 未返回内容）')}
          </div>
        `;
        msgs.scrollTop = msgs.scrollHeight;
      } catch (err) {
        toast(`AI 诊断请求失败：${err.message}`);
      }
    };
  }
};

/* ================= ④ 组价核算工作台 (Cost Pricing & Quotation) ================= */

const PricingWorkbench = {
  id: '',
  job: null,
  quotation: null,
  activeCompRow: null,
  collapsedBoxes: new Set(),
  filterMissing: false,
  filterManual: false,
  filterUnpriced: false,
  searchKeyword: '',
  rates: { aux: null, mfg: null, tax: null },   // 空 = 未加载，后端按配置默认费率算；不编 5%/9%/6%
  brand: '正泰',
  lastSavedTime: '—',
  timer: 0,

  stopPoll() { clearTimeout(this.timer); },

  async open(id) {
    this.stopPoll();
    if ($('globalHeader')) $('globalHeader').hidden = true;
    $('viewList').hidden = true;
    $('viewExtract').hidden = true;
    $('viewPricing').hidden = false;
    this.id = id;

    try {
      this.job = await api(`/api/jobs/${id}`);
    } catch (_) {}

    const filename = (this.job && this.job.filename) || '工程图纸.dwg';
    if ($('pricingFilename')) $('pricingFilename').textContent = filename;
    const extMatch = filename.match(/\.([a-zA-Z0-9]+)$/);
    if ($('pricingFileExtTag')) $('pricingFileExtTag').textContent = extMatch ? extMatch[1].toUpperCase() : 'DWG';
    document.title = `${filename} · 组价套定额核算`;

    // 路由跳转与顶部操作
    if ($('pricingGoBackBtn')) $('pricingGoBackBtn').onclick = () => { location.hash = '#/'; };
    if ($('pricingGoExtractBtn')) $('pricingGoExtractBtn').onclick = () => { location.hash = `#/job/${id}/extract`; };
    if ($('pricingExportBtn')) $('pricingExportBtn').onclick = () => {
      const activeBrand = this.brand || '正泰';
      const rates = ['aux_rate', 'mfg_rate', 'tax_rate']
        .map((k, i) => { const v = this.rates[['aux', 'mfg', 'tax'][i]]; return v == null ? '' : `${k}=${v}`; })
        .filter(Boolean).join('&');
      window.location.href = withToken(`/api/jobs/${id}/excel?stage=pricing&target_brand=${encodeURIComponent(activeBrand)}${rates ? `&${rates}` : ''}`);
    };
    if ($('pricingSyncBtn')) $('pricingSyncBtn').onclick = () => {
      this.fetchQuotation();
      toast('已成功将拆量最新回路数据同步至报价表！');
    };

    await this.fetchQuotation();
    this.bindEvents();
  },

  setRecalcBusy(busy) {
    let hint = $('pricingRecalcHint');
    if (!hint && $('pricingRecalcBtn')) {
      hint = document.createElement('span');
      hint.id = 'pricingRecalcHint';
      hint.className = 'ml-2 hidden text-blue-600 text-[11px] font-medium animate-pulse';
      hint.textContent = '正在重新核算…';
      $('pricingRecalcBtn').insertAdjacentElement('afterend', hint);
    }
    if (hint) hint.classList.toggle('hidden', !busy);
  },

  async fetchQuotation(customParams = {}) {
    if (customParams.brand) this.brand = customParams.brand;
    const brand = this.brand || '正泰';
    const query = new URLSearchParams({
      brand,
      copper_price: String(COPPER_REFERENCE),
    });
    // 费率只有在用户/后端明确给出时才传，否则让后端用配置默认值
    if (this.rates.aux != null) query.set('aux_rate', String(this.rates.aux));
    if (this.rates.mfg != null) query.set('mfg_rate', String(this.rates.mfg));
    if (this.rates.tax != null) query.set('tax_rate', String(this.rates.tax));

    this.setRecalcBusy(true);
    try {
      const res = await api(`/api/jobs/${this.id}/quotation?${query.toString()}`);
      this.quotation = res;
      const s = res.summary || {};
      if (s.aux_rate != null) this.rates.aux = Number(s.aux_rate);
      if (s.mfg_rate != null) this.rates.mfg = Number(s.mfg_rate);
      if (s.tax_rate != null) this.rates.tax = Number(s.tax_rate);
      const now = new Date();
      this.lastSavedTime = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
      this.render();
    } catch (e) {
      toast(`组价数据加载失败：${e.message}`);
    } finally {
      this.setRecalcBusy(false);
    }
  },

  render() {
    if (!this.quotation) return;
    this.renderBasisHeader();
    this.renderTable();
  },

  renderBasisHeader() {
    const s = this.quotation.summary || {};
    const boxes = this.quotation.boxes || [];
    const rows = this.quotation.rows || [];

    // 费率显示（未加载时留空，输入框的 placeholder 会提示默认）
    const ratePct = fmtPct;
    if ($('pricingAuxInput')) $('pricingAuxInput').value = ratePct(this.rates.aux);
    if ($('pricingLaborInput')) $('pricingLaborInput').value = ratePct(this.rates.mfg);
    if ($('pricingTaxInput')) $('pricingTaxInput').value = ratePct(this.rates.tax);

    // 合计（2 位小数）与报价总计（取整到元）
    const boxesCount = s.boxes_count || boxes.length || 0;
    const unitsCount = boxes.reduce((acc, b) => acc + (Number(b.quantity) || 1), 0) || boxesCount;
    const projectTotal = Number(s.total_price || 0);
    const grandTotal = Number(s.grand_total != null ? s.grand_total : Math.round(projectTotal));

    if ($('pricingBoxesSummaryText')) $('pricingBoxesSummaryText').textContent = `共 ${boxesCount} 个箱柜（${unitsCount} 台）`;
    if ($('pricingCostTotalText')) $('pricingCostTotalText').textContent = `¥${projectTotal.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}`;
    if ($('pricingQuoteTotalText')) $('pricingQuoteTotalText').textContent = `¥${grandTotal.toLocaleString('zh-CN')}`;
    if ($('pricingChineseCurrencyText')) $('pricingChineseCurrencyText').textContent = `（${s.total_upper || '零元整'}）`;

    // 状态过滤标签
    const missingCount = s.missing_count || 0;
    const manualCount = s.manual_count || 0;
    let unpricedCount = 0;
    rows.forEach(r => {
      if (r.type === 'component' && (r.is_unpriced || (r.unit_price === 0 && r.source !== '缺价'))) {
        unpricedCount++;
      }
    });

    if ($('pricingMissingCountLabel')) $('pricingMissingCountLabel').textContent = `缺价 ${missingCount} 项`;
    if ($('pricingManualCountLabel')) $('pricingManualCountLabel').textContent = `手填 ${manualCount} 项`;
    if ($('pricingUnpricedCountLabel')) $('pricingUnpricedCountLabel').textContent = `不计价 ${unpricedCount} 项`;

    // 底部状态栏
    if ($('pricingFooterCount')) $('pricingFooterCount').textContent = `${boxesCount} 箱 / ${rows.length} 行`;
    if ($('pricingFooterSum')) $('pricingFooterSum').textContent = `¥${Number(s.grand_total != null ? s.grand_total : s.total_price || 0).toLocaleString('zh-CN')}`;
    if ($('pricingFooterSavedTime')) $('pricingFooterSavedTime').textContent = `已保存 ${this.lastSavedTime}`;
    if ($('pricingGridRowSummary')) $('pricingGridRowSummary').textContent = `共 ${boxesCount} 箱 / ${rows.length} 行`;
  },

  renderTable() {
    const rows = this.quotation.rows || [];
    const boxes = this.quotation.boxes || [];
    const tbody = $('pricingExcelTbody');
    if (!tbody) return;

    const s = this.quotation.summary || {};
    const boxesCount = s.boxes_count || boxes.length || 0;
    const unitsCount = boxes.reduce((acc, b) => acc + (Number(b.quantity) || 1), 0) || boxesCount;
    const quoteTotal = Number(s.total_price || 0);
    const grandTotal = Number(s.grand_total != null ? s.grand_total : Math.round(quoteTotal));
    const totalUpper = s.total_upper || '零元整';

    const html = [];

    // 1. 分类行：一、配电箱（N 个箱柜 / M 台） 合计 ¥...
    html.push(`
      <tr class="h-8 bg-slate-200/80 font-bold text-slate-900 border-b border-slate-300">
        <td class="excel-cell px-2 text-center font-mono" colspan="2">一</td>
        <td class="excel-cell px-2" colspan="2">配电箱（${boxesCount} 个箱柜 / ${unitsCount} 台）</td>
        <td class="excel-cell px-2" colspan="4"></td>
        <td class="excel-cell px-2 text-right font-mono-nums text-slate-900 font-bold">合计 ¥${quoteTotal.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
        <td class="excel-cell px-2" colspan="3"></td>
      </tr>
    `);

    // 2. 按箱柜渲染
    // 先把行数据按箱柜分组
    const boxGroups = [];
    let currentGroup = null;

    rows.forEach((r) => {
      if (r.type === 'box_header') {
        if (currentGroup) boxGroups.push(currentGroup);
        currentGroup = { header: r, items: [] };
      } else if (r.type === 'component' || r.type === 'material') {
        if (currentGroup) currentGroup.items.push(r);
      }
    });
    if (currentGroup) boxGroups.push(currentGroup);

    boxGroups.forEach((group, bIdx) => {
      const bh = group.header;
      const isCollapsed = this.collapsedBoxes.has(bh.box_code);
      const compCount = group.items.filter(it => it.type === 'component').length;

      // 箱柜行
      html.push(`
        <tr class="h-8 bg-slate-100 hover:bg-slate-150 transition-colors font-semibold border-b border-slate-300 cursor-pointer" data-box-code="${esc(bh.box_code)}">
          <td class="excel-cell text-center font-mono text-blue-700 font-bold">${isCollapsed ? '▸' : '▾'} 1-${bIdx + 1}</td>
          <td class="excel-cell px-2 font-mono font-bold text-blue-800">${esc(bh.box_code)}</td>
          <td class="excel-cell px-2 text-slate-900 font-bold">${esc(bh.name)}（${compCount} 项）</td>
          <td class="excel-cell px-2 text-slate-700">${esc(bh.design_spec || '—')}</td>
          <td class="excel-cell px-2 text-slate-400"></td>
          <td class="excel-cell px-1 text-center text-slate-600">${esc(bh.unit || '台')}</td>
          <td class="excel-cell px-2 text-right font-mono-nums">${bh.quantity || 1}</td>
          <td class="excel-cell px-2 text-right font-mono-nums text-slate-800">${Number(bh.unit_price || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
          <td class="excel-cell px-2 text-right font-mono-nums font-bold text-blue-900">${Number(bh.total_price || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
          <td class="excel-cell px-2 text-slate-600">${esc(bh.brand || '')}</td>
          <td class="excel-cell text-center text-slate-400 text-[11px]"></td>
          <td class="excel-cell px-2 text-slate-500 text-[11px]">${esc(bh.location || '—')}</td>
        </tr>
      `);

      // 如果未折叠，渲染元器件和构成行
      if (!isCollapsed) {
        group.items.forEach((r) => {
          // 壳体/铜材是普通明细行，不参与缺价筛选与手改价
          if (r.type === 'material') {
            html.push(`
              <tr class="h-8 bg-slate-50/70 text-slate-700">
                <td class="excel-cell text-center font-mono text-slate-400 text-[11px]">${r.seq || '-'}</td>
                <td class="excel-cell px-2 font-mono text-slate-300 text-center"></td>
                <td class="excel-cell px-2 pl-4 flex items-center gap-1.5 h-8">${esc(r.name)}</td>
                <td class="excel-cell px-2 font-mono-nums text-slate-600 text-[12px]">${esc(r.design_spec || '—')}</td>
                <td class="excel-cell px-2 font-mono-nums text-slate-800"></td>
                <td class="excel-cell px-1 text-center text-slate-500">${esc(r.unit || '台')}</td>
                <td class="excel-cell px-2 text-right font-mono-nums">${r.quantity || 1}</td>
                <td class="excel-cell px-2 text-right font-mono-nums text-slate-800">${Number(r.unit_price || 0).toFixed(2)}</td>
                <td class="excel-cell px-2 text-right font-mono-nums text-slate-800">${Number(r.total_price || 0).toFixed(2)}</td>
                <td class="excel-cell px-2 text-slate-700">${esc(r.brand || '—')}</td>
                <td class="excel-cell text-center text-slate-500 text-[11px]">${esc(r.source || '')}</td>
                <td class="excel-cell px-2 text-slate-500 text-[11px]"></td>
              </tr>
            `);
            return;
          }

          // 过滤条件
          const isMissing = (r.source === '缺价' || Number(r.unit_price) <= 0);
          const isManual = (r.source === '手填');
          const isUnpriced = (r.is_unpriced || (r.unit_price === 0 && r.source !== '缺价'));

          if (this.filterMissing && !isMissing) return;
          if (this.filterManual && !isManual) return;
          if (this.filterUnpriced && !isUnpriced) return;

          if (this.searchKeyword) {
            const kw = this.searchKeyword.toLowerCase();
            const textMatch = (
              (r.box_code || '').toLowerCase().includes(kw) ||
              (r.name || '').toLowerCase().includes(kw) ||
              (r.design_spec || '').toLowerCase().includes(kw) ||
              (r.replacement_model || '').toLowerCase().includes(kw)
            );
            if (!textMatch) return;
          }

          const isSelected = (this.activeCompRow && (this.activeCompRow.key === r.key || (this.activeCompRow.box_code === r.box_code && this.activeCompRow.seq === r.seq)));

          let rowBg = 'hover:bg-blue-50/30 transition-colors';
          if (isUnpriced) {
            rowBg = 'bg-slate-50 text-slate-400 hover:bg-slate-100/60 transition-colors';
          } else if (isMissing) {
            rowBg = isSelected ? 'bg-amber-50/90' : 'bg-amber-50/60 transition-colors';
          } else if (isSelected) {
            rowBg = 'bg-blue-50/50';
          }

          // 单价单元格呈现
          let priceCellHtml = '';
          if (isMissing) {
            priceCellHtml = `
              <td class="excel-cell p-0 relative ${isSelected ? 'ring-2 ring-blue-600 ring-inset bg-white' : 'bg-rose-50/40'} text-right">
                <div class="w-full h-full flex items-center justify-end px-2">
                  <span class="text-xs text-rose-500 font-mono-nums font-semibold">[ 缺价 ]</span>
                </div>
              </td>
            `;
          } else if (isManual) {
            priceCellHtml = `
              <td class="excel-cell px-2 text-right font-mono-nums font-semibold text-blue-700 ${isSelected ? 'ring-2 ring-blue-600 ring-inset bg-white' : ''}">
                ◤${Number(r.unit_price).toFixed(2)}
              </td>
            `;
          } else if (isUnpriced) {
            priceCellHtml = `
              <td class="excel-cell px-2 text-right font-mono-nums ${isSelected ? 'ring-2 ring-blue-600 ring-inset bg-white' : ''}">
                0.00
              </td>
            `;
          } else {
            priceCellHtml = `
              <td class="excel-cell px-2 text-right font-mono-nums text-slate-800 ${isSelected ? 'ring-2 ring-blue-600 ring-inset bg-white' : ''}">
                ${Number(r.unit_price).toFixed(2)}
              </td>
            `;
          }

          // 来源徽标
          let sourceBadgeHtml = `<span class="text-slate-600 text-[11px]">${esc(r.source || '未标注')}</span>`;
          if (isMissing) {
            sourceBadgeHtml = `<span class="inline-block px-1.5 py-0.2 text-[10px] font-semibold text-rose-700 bg-rose-100 border border-rose-300 rounded">缺价</span>`;
          } else if (isManual) {
            sourceBadgeHtml = `<span class="inline-block px-1.5 py-0.2 text-[10px] font-semibold text-blue-700 bg-blue-100 border border-blue-200 rounded">手填</span>`;
          } else if (isUnpriced) {
            sourceBadgeHtml = `<span class="text-slate-400 text-[11px]">不计价</span>`;
          }

          html.push(`
            <tr class="h-8 cursor-pointer ${rowBg}" data-comp-key="${esc(r.key || '')}" data-box-code="${esc(r.box_code)}" data-seq="${r.seq}">
              <td class="excel-cell text-center font-mono ${isMissing ? 'font-bold text-rose-600' : 'text-slate-400'} text-[11px]">${r.seq}</td>
              <td class="excel-cell px-2 font-mono text-slate-300 text-center"></td>
              <td class="excel-cell px-2 pl-4 ${isMissing ? 'text-rose-700 font-semibold' : 'text-slate-800'} flex items-center gap-1.5 h-8">
                <span>${esc(r.name)}</span>
                ${isMissing ? '<span class="w-1.5 h-1.5 rounded-full bg-rose-600"></span>' : ''}
              </td>
              <td class="excel-cell px-2 font-mono-nums text-slate-600 text-[12px]">${esc(r.design_spec || '—')}</td>
              <td class="excel-cell px-2 font-mono-nums text-slate-800 ${isMissing ? 'text-slate-400 italic text-xs' : ''}">
                ${esc(r.replacement_model || (isMissing ? '待指定报价型号' : ''))}
              </td>
              <td class="excel-cell px-1 text-center text-slate-500">${esc(r.unit || '只')}</td>
              <td class="excel-cell px-2 text-right font-mono-nums ${isMissing ? 'font-bold' : ''}">${r.quantity}</td>
              ${priceCellHtml}
              <td class="excel-cell px-2 text-right font-mono-nums ${isMissing ? 'text-slate-400' : 'text-slate-800'}">${isMissing ? '—' : Number(r.total_price).toFixed(2)}</td>
              <td class="excel-cell px-2 text-slate-700">${esc(r.brand || '—')}</td>
              <td class="excel-cell text-center">${sourceBadgeHtml}</td>
              <td class="excel-cell px-2 text-slate-500 text-[11px]">${esc(isManual ? (r.replacement_model || '') : (isUnpriced ? '甲供' : (r.location || '')))}</td>
            </tr>
          `);
        });

        // 构成行：直接取后端同一套计费链结果，屏幕与导出 Excel 同口径
        const bd = (boxes.find(b => b.box_code === bh.box_code) || {}).breakdown || {};
        const rates = bd.rates || this.rates;
        const subtotal = Number(bd.subtotal || 0);
        const auxCost = Number(bd.auxiliary || 0);
        const mfgCost = Number(bd.manufacture || 0);
        const taxCost = Number(bd.tax || 0);
        const singleBoxTotal = Number(bd.unit_total || 0);

        html.push(`
          <!-- 构成行：小计 -->
          <tr class="h-7 bg-slate-50/60 text-slate-500 text-[11px]">
            <td class="excel-cell text-center font-mono">-</td>
            <td class="excel-cell" colspan="7"><span class="pl-6">小计</span></td>
            <td class="excel-cell px-2 text-right font-mono-nums text-slate-700 font-medium">${subtotal.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
            <td class="excel-cell" colspan="3"></td>
          </tr>
          <!-- 辅料 -->
          <tr class="h-7 bg-slate-50/60 text-slate-500 text-[11px]">
            <td class="excel-cell text-center font-mono">-</td>
            <td class="excel-cell" colspan="7"><span class="pl-6">辅料（小计 × ${fmtPct(rates.aux)}%）</span></td>
            <td class="excel-cell px-2 text-right font-mono-nums text-slate-700 font-medium">${auxCost.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
            <td class="excel-cell px-2 text-slate-400"></td>
            <td class="excel-cell text-center text-slate-500">费率</td>
            <td class="excel-cell"></td>
          </tr>
          <!-- 成套制作费 -->
          <tr class="h-7 bg-slate-50/60 text-slate-500 text-[11px]">
            <td class="excel-cell text-center font-mono">-</td>
            <td class="excel-cell" colspan="7"><span class="pl-6">成套制作费（(小计+辅料) × ${fmtPct(rates.mfg)}%）</span></td>
            <td class="excel-cell px-2 text-right font-mono-nums text-slate-700 font-medium">${mfgCost.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
            <td class="excel-cell px-2 text-slate-400"></td>
            <td class="excel-cell text-center text-slate-500">费率</td>
            <td class="excel-cell"></td>
          </tr>
          <!-- 税费 -->
          <tr class="h-7 bg-slate-50/60 text-slate-500 text-[11px]">
            <td class="excel-cell text-center font-mono">-</td>
            <td class="excel-cell" colspan="7"><span class="pl-6">税费（前三项 × ${fmtPct(rates.tax)}%）</span></td>
            <td class="excel-cell px-2 text-right font-mono-nums text-slate-700 font-medium">${taxCost.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
            <td class="excel-cell px-2 text-slate-400"></td>
            <td class="excel-cell text-center text-slate-500">费率</td>
            <td class="excel-cell"></td>
          </tr>
          <!-- 单台合计 -->
          <tr class="h-7 bg-slate-100 text-slate-700 text-[11px] font-semibold border-b border-slate-300">
            <td class="excel-cell text-center font-mono">-</td>
            <td class="excel-cell" colspan="7"><span class="pl-6 text-slate-800">单台合计</span></td>
            <td class="excel-cell px-2 text-right font-mono-nums text-slate-900 font-bold">${singleBoxTotal.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
            <td class="excel-cell" colspan="3"></td>
          </tr>
        `);
      }
    });

    // 3. 底部冻结合计行：合计（2 位小数）与报价总计（取整到元）+ 大写
    html.push(`
      <tr class="h-8 bg-slate-200 text-slate-900 font-bold border-t-2 border-slate-400 sticky bottom-0 z-10 shadow-xs">
        <td class="excel-cell text-center font-mono">合计</td>
        <td class="excel-cell px-2" colspan="5">${boxesCount} 个箱柜 / ${unitsCount} 台</td>
        <td class="excel-cell px-2 text-right font-mono-nums">数量 ${unitsCount}</td>
        <td class="excel-cell px-2 text-right font-mono-nums" colspan="2">合计 ¥${quoteTotal.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</td>
        <td class="excel-cell px-2 text-right font-mono-nums text-blue-900" colspan="3">报价总计 ¥${grandTotal.toLocaleString('zh-CN')}（${esc(totalUpper)}）</td>
      </tr>
    `);

    tbody.innerHTML = html.join('') || '<tr><td colspan="12" class="p-8 text-center text-slate-400">暂无组价行数据</td></tr>';

    // 默认高亮首个元器件行（优先选缺价项）
    if (!this.activeCompRow) {
      const allComps = rows.filter(r => r.type === 'component');
      const missingComp = allComps.find(r => r.source === '缺价' || Number(r.unit_price) <= 0);
      const targetComp = missingComp || allComps[0];
      if (targetComp) this.selectRow(targetComp);
    }
  },

  selectRow(row) {
    this.activeCompRow = row;
    const coord = `H${row.row_num || row.seq || 6}`;
    if ($('pricingCellCoordinate')) $('pricingCellCoordinate').textContent = coord;

    if ($('pricingFormulaBox')) $('pricingFormulaBox').textContent = `${row.box_code || '1-1'}`;
    if ($('pricingFormulaName')) $('pricingFormulaName').textContent = `${row.name} ${row.design_spec || ''}`;

    const isMissing = (row.source === '缺价' || Number(row.unit_price) <= 0);
    const isManual = (row.source === '手填');
    const isUnpriced = (row.is_unpriced || (row.unit_price === 0 && row.source !== '缺价'));

    let sourceText = `来源：${row.source || '未标注'}，可在右侧选价或替换`;
    if (isMissing) {
      sourceText = `来源：缺价，可在右侧选价`;
      if ($('pricingFormulaSource')) {
        $('pricingFormulaSource').className = 'text-rose-600 font-medium';
        $('pricingFormulaSource').textContent = sourceText;
      }
    } else if (isManual) {
      sourceText = `来源：手填 (${row.replacement_model || ''})`;
      if ($('pricingFormulaSource')) {
        $('pricingFormulaSource').className = 'text-blue-700 font-medium';
        $('pricingFormulaSource').textContent = sourceText;
      }
    } else if (isUnpriced) {
      sourceText = `来源：不计价 (甲供)`;
      if ($('pricingFormulaSource')) {
        $('pricingFormulaSource').className = 'text-slate-500 font-medium';
        $('pricingFormulaSource').textContent = sourceText;
      }
    } else {
      if ($('pricingFormulaSource')) {
        $('pricingFormulaSource').className = 'text-slate-700 font-medium';
        $('pricingFormulaSource').textContent = sourceText;
      }
    }

    if ($('pricingFooterSel')) $('pricingFooterSel').textContent = `${row.box_code} / ${row.seq} ${row.name}`;
    this.renderSidebar(row);
  },

  renderSidebar(row) {
    if ($('pricingSidebarMeta')) $('pricingSidebarMeta').textContent = `${row.box_code} ${row.location || ''}`.trim();
    if ($('pricingSidebarTitle')) $('pricingSidebarTitle').textContent = `${row.name} ${row.design_spec || ''} × ${row.quantity || 1} ${row.unit || '只'}`;
    if ($('pricingSidebarSub')) $('pricingSidebarSub').textContent = [row.location, '设定整定及选型规格'].filter(Boolean).join(' · ');

    const isMissing = (row.source === '缺价' || Number(row.unit_price) <= 0);
    const isManual = (row.source === '手填');
    const isUnpriced = (row.is_unpriced || (row.unit_price === 0 && row.source !== '缺价'));

    // 状态胶囊
    const badge = $('pricingSidebarBadge');
    const badgeText = $('pricingSidebarBadgeText');
    const badgeIcon = $('pricingSidebarBadgeIcon');

    if (badge && badgeText) {
      if (isMissing) {
        badge.className = 'inline-flex items-center gap-1 text-[11px] font-semibold text-rose-700 bg-rose-100 border border-rose-300 px-1.5 py-0.2 rounded';
        if (badgeIcon) badgeIcon.textContent = 'warning';
        badgeText.textContent = '缺价';
      } else if (isManual) {
        badge.className = 'inline-flex items-center gap-1 text-[11px] font-semibold text-blue-700 bg-blue-100 border border-blue-200 px-1.5 py-0.2 rounded';
        if (badgeIcon) badgeIcon.textContent = 'edit';
        badgeText.textContent = '手填';
      } else if (isUnpriced) {
        badge.className = 'inline-flex items-center gap-1 text-[11px] font-semibold text-slate-600 bg-slate-100 border border-slate-300 px-1.5 py-0.2 rounded';
        if (badgeIcon) badgeIcon.textContent = 'remove_circle_outline';
        badgeText.textContent = '不计价';
      } else {
        badge.className = 'inline-flex items-center gap-1 text-[11px] font-semibold text-blue-700 bg-blue-100 border border-blue-200 px-1.5 py-0.2 rounded';
        if (badgeIcon) badgeIcon.textContent = 'verified';
        badgeText.textContent = row.source || '未标注';
      }
    }

    const cands = row.candidates || {};
    const infoCand = cands.info;
    const marketCand = cands.market;
    const aiCand = cands.ai_suggested || null;

    // 1. 价库卡片（品牌优先顺序一）：有命中就显示真实品牌，没有就显示“—”，不得用当前单价冒充
    if (infoCand && Number(infoCand.price) > 0) {
      if ($('pricingCardInfoTitle')) $('pricingCardInfoTitle').textContent = `价库·${infoCand.brand || '未知品牌'}`;
      if ($('pricingCardInfoModel')) $('pricingCardInfoModel').textContent = `${infoCand.brand || ''} ${infoCand.model || ''}`.trim();
      if ($('pricingCardInfoPrice')) $('pricingCardInfoPrice').textContent = `¥ ${Number(infoCand.price).toFixed(2)}`;
      if ($('pricingAdoptInfoBtn')) {
        $('pricingAdoptInfoBtn').disabled = false;
        $('pricingAdoptInfoBtn').textContent = '采用';
        $('pricingAdoptInfoBtn').className = 'mt-1 px-2.5 py-0.5 bg-blue-700 hover:bg-blue-800 text-white rounded text-[11px] font-medium transition-colors cursor-pointer';
      }
    } else {
      if ($('pricingCardInfoTitle')) $('pricingCardInfoTitle').textContent = '价库';
      if ($('pricingCardInfoModel')) $('pricingCardInfoModel').textContent = '内置库暂无收录';
      if ($('pricingCardInfoPrice')) $('pricingCardInfoPrice').textContent = '—';
      if ($('pricingAdoptInfoBtn')) {
        $('pricingAdoptInfoBtn').disabled = true;
        $('pricingAdoptInfoBtn').textContent = '库无收录';
        $('pricingAdoptInfoBtn').className = 'mt-1 px-2.5 py-0.5 bg-slate-200 text-slate-400 rounded text-[11px] cursor-not-allowed';
      }
    }

    // 2. 价库卡片（品牌优先顺序二）
    if (marketCand && Number(marketCand.price) > 0) {
      if ($('pricingCardMarketTitle')) $('pricingCardMarketTitle').textContent = `价库·${marketCand.brand || '未知品牌'}`;
      if ($('pricingCardMarketModel')) $('pricingCardMarketModel').textContent = `${marketCand.brand || ''} ${marketCand.model || ''}`.trim();
      if ($('pricingCardMarketPrice')) $('pricingCardMarketPrice').textContent = `¥ ${Number(marketCand.price).toFixed(2)}`;
      if ($('pricingAdoptMarketBtn')) {
        $('pricingAdoptMarketBtn').disabled = false;
        $('pricingAdoptMarketBtn').textContent = '采用';
        $('pricingAdoptMarketBtn').className = 'mt-1 px-2.5 py-0.5 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 rounded text-[11px] transition-colors cursor-pointer';
      }
    } else {
      if ($('pricingCardMarketTitle')) $('pricingCardMarketTitle').textContent = '价库';
      if ($('pricingCardMarketModel')) $('pricingCardMarketModel').textContent = '内置库暂无收录';
      if ($('pricingCardMarketPrice')) $('pricingCardMarketPrice').textContent = '—';
      if ($('pricingAdoptMarketBtn')) {
        $('pricingAdoptMarketBtn').disabled = true;
        $('pricingAdoptMarketBtn').textContent = '暂无报价';
        $('pricingAdoptMarketBtn').className = 'mt-1 px-2.5 py-0.5 bg-slate-200 text-slate-400 border border-slate-300 rounded text-[11px] cursor-not-allowed';
      }
    }

    // 3. 定额估算参考价卡片（estimate_reference_price 的经验定额，不是联网行情）
    const aiPrice = aiCand && Number(aiCand.price) > 0 ? Number(aiCand.price) : 0;
    if (aiPrice > 0) {
      if ($('pricingCardAiModel')) $('pricingCardAiModel').textContent = `${aiCand.brand || ''} ${aiCand.model || ''}`.trim() || '暂无估算';
      if ($('pricingCardAiDesc')) $('pricingCardAiDesc').textContent = aiCand.reference || '内置经验定额，未联网核价，采用前请人工确认';
      if ($('pricingCardAiPrice')) $('pricingCardAiPrice').textContent = `¥ ${aiPrice.toFixed(2)}`;
      if ($('pricingAdoptAiBtn')) {
        $('pricingAdoptAiBtn').disabled = false;
        $('pricingAdoptAiBtn').textContent = '采用';
        $('pricingAdoptAiBtn').className = 'mt-1 px-2.5 py-0.5 bg-blue-600 hover:bg-blue-700 text-white rounded text-[11px] font-medium transition-colors shadow-2xs cursor-pointer';
        $('pricingAdoptAiBtn').title = `${aiCand.source || '定额估算'} · ${aiCand.reference || '内置经验定额，未联网核价'}`;
      }
    } else {
      if ($('pricingCardAiModel')) $('pricingCardAiModel').textContent = '暂无估算';
      if ($('pricingCardAiDesc')) $('pricingCardAiDesc').textContent = '—';
      if ($('pricingCardAiPrice')) $('pricingCardAiPrice').textContent = '—';
      if ($('pricingAdoptAiBtn')) {
        $('pricingAdoptAiBtn').disabled = true;
        $('pricingAdoptAiBtn').textContent = '暂无估算';
        $('pricingAdoptAiBtn').className = 'mt-1 px-2.5 py-0.5 bg-slate-200 text-slate-400 rounded text-[11px] cursor-not-allowed';
        $('pricingAdoptAiBtn').title = '后端未提供定额估算';
      }
    }

    // 4. 手填
    if ($('pricingCustomPriceInput')) {
      $('pricingCustomPriceInput').value = isManual ? row.unit_price : (isMissing ? '' : row.unit_price);
    }
    if ($('pricingCustomNoteInput')) {
      $('pricingCustomNoteInput').value = isManual ? (row.replacement_model || '') : (row.replacement_model || '');
    }
  },

  async adoptCandidate(candidateData) {
    if (!this.activeCompRow) return;
    const price = Number(candidateData.price || 0);
    if (!Number.isFinite(price) || price <= 0) {
      toast('该价格项暂无有效数值，无法采用');
      return;
    }
    const model = candidateData.model || this.activeCompRow.replacement_model || '';
    const source = candidateData.source || '未标注';
    const brand = candidateData.brand || '';
    const boxCode = this.activeCompRow.box_code;
    const seq = this.activeCompRow.seq;
    const designSpec = this.activeCompRow.design_spec;
    const key = this.activeCompRow.key || `${boxCode}_${seq}`;
    const quoteKey = `${boxCode}_${seq}`;

    // 价格落盘后直接以后端返回的核算结果重绘，屏幕与导出保持同一套计费链
    const payload = {
      brand: this.brand || '正泰',
      copper_price: COPPER_REFERENCE,
      aux_rate: this.rates.aux,
      mfg_rate: this.rates.mfg,
      tax_rate: this.rates.tax,
      overrides: {
        [key]: { unit_price: price, price, model, quote_spec: model, brand, source },
        [quoteKey]: { unit_price: price, price, model, quote_spec: model, brand, source },
        [designSpec]: { unit_price: price, price, model, quote_spec: model, brand, source }
      }
    };

    this.setRecalcBusy(true);
    try {
      const res = await api(`/api/jobs/${this.id}/quotation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      this.quotation = res;
      this.render();
      const refreshed = (res.rows || []).find(r => r.key === key || (r.box_code === boxCode && r.seq === seq));
      if (refreshed) this.selectRow(refreshed);
      toast(`已成功采用【${source}】：¥${price.toFixed(2)} (${model})`);
    } catch (e) {
      toast(`后端同步失败：${e.message}`);
    } finally {
      this.setRecalcBusy(false);
    }
  },

  async confirmCustomPrice() {
    if (!this.activeCompRow) return;
    const input = $('pricingCustomPriceInput');
    const price = Number(input ? input.value : 0);
    if (!Number.isFinite(price) || price < 0) {
      toast('请输入有效的手填单价');
      return;
    }
    const note = ($('pricingCustomNoteInput') && $('pricingCustomNoteInput').value.trim()) || '询价人工填报';
    const boxCode = this.activeCompRow.box_code;
    const seq = this.activeCompRow.seq;
    const designSpec = this.activeCompRow.design_spec;
    const key = this.activeCompRow.key || `${boxCode}_${seq}`;
    const quoteKey = `${boxCode}_${seq}`;

    const payload = {
      brand: this.brand || '正泰',
      copper_price: COPPER_REFERENCE,
      aux_rate: this.rates.aux,
      mfg_rate: this.rates.mfg,
      tax_rate: this.rates.tax,
      overrides: {
        [key]: { unit_price: price, price, model: note, quote_spec: note, source: '手填' },
        [quoteKey]: { unit_price: price, price, model: note, quote_spec: note, source: '手填' },
        [designSpec]: { unit_price: price, price, model: note, quote_spec: note, source: '手填' }
      }
    };

    this.setRecalcBusy(true);
    try {
      const res = await api(`/api/jobs/${this.id}/quotation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      this.quotation = res;
      this.render();
      const refreshed = (res.rows || []).find(r => r.key === key || (r.box_code === boxCode && r.seq === seq));
      if (refreshed) this.selectRow(refreshed);
      toast(`已成功保存手填单价：¥${price.toFixed(2)} (${note})`);
    } catch (e) {
      toast(`后端同步失败：${e.message}`);
    } finally {
      this.setRecalcBusy(false);
    }
  },

  bindEvents() {
    // 品牌切换
    if ($('pricingBrandSel')) {
      $('pricingBrandSel').onchange = (e) => {
        this.brand = e.target.value;
        this.fetchQuotation({ brand: this.brand });
        toast(`已切换报价品牌基准：${e.target.options[e.target.selectedIndex].text}`);
      };
    }

    // 费率：行内数字输入，回车或失焦生效，校验 0–100
    const bindRateInput = (id, key, label) => {
      const el = $(id);
      if (!el) return;
      const restore = () => {
        el.value = fmtPct(this.rates[key]);
      };
      // 用属性赋值而不是 addEventListener：bindEvents 可能被重复调用，避免监听器叠加导致重复核算
      el.onkeydown = (e) => {
        if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
        if (e.key === 'Escape') { restore(); el.blur(); }
      };
      el.onchange = () => {
        const raw = String(el.value || '').trim();
        if (raw === '') { restore(); return; }
        const num = parseFloat(raw);
        if (isNaN(num) || num < 0 || num > 100) { toast(`${label}需为 0–100 的百分比`); restore(); return; }
        if (this.rates[key] === num / 100) return;
        this.rates[key] = num / 100;
        this.fetchQuotation();
        toast(`${label}已更新为 ${num}% 并已重新核算`);
      };
    };
    bindRateInput('pricingAuxInput', 'aux', '辅料费率');
    bindRateInput('pricingLaborInput', 'mfg', '成套制作费率');
    bindRateInput('pricingTaxInput', 'tax', '税费率');

    // 重算
    if ($('pricingRecalcBtn')) {
      $('pricingRecalcBtn').onclick = () => {
        this.fetchQuotation();
        toast('已根据当前口径参数重新核算！');
      };
    }

    // 全部展开 / 收起
    if ($('pricingExpandAllBtn')) {
      $('pricingExpandAllBtn').onclick = () => {
        this.collapsedBoxes.clear();
        this.renderTable();
        toast('已展开所有配电箱柜明细');
      };
    }

    if ($('pricingCollapseAllBtn')) {
      $('pricingCollapseAllBtn').onclick = () => {
        const boxes = this.quotation.boxes || [];
        boxes.forEach(b => this.collapsedBoxes.add(b.box_code));
        this.renderTable();
        toast('已收起所有配电箱柜明细');
      };
    }

    // 过滤按钮
    const updateFilterStyles = () => {
      if ($('pricingOnlyMissingBtn')) {
        $('pricingOnlyMissingBtn').classList.toggle('bg-rose-100', this.filterMissing);
        $('pricingOnlyMissingBtn').classList.toggle('border-rose-400', this.filterMissing);
      }
      if ($('pricingFilterMissingBtn')) {
        $('pricingFilterMissingBtn').classList.toggle('ring-2', this.filterMissing);
        $('pricingFilterMissingBtn').classList.toggle('ring-rose-500', this.filterMissing);
      }
      if ($('pricingFilterManualBtn')) {
        $('pricingFilterManualBtn').classList.toggle('ring-2', this.filterManual);
        $('pricingFilterManualBtn').classList.toggle('ring-blue-500', this.filterManual);
      }
      if ($('pricingOnlyUnpricedBtn')) {
        $('pricingOnlyUnpricedBtn').classList.toggle('bg-slate-200', this.filterUnpriced);
        $('pricingOnlyUnpricedBtn').classList.toggle('border-slate-400', this.filterUnpriced);
      }
      if ($('pricingFilterUnpricedBtn')) {
        $('pricingFilterUnpricedBtn').classList.toggle('ring-2', this.filterUnpriced);
        $('pricingFilterUnpricedBtn').classList.toggle('ring-slate-500', this.filterUnpriced);
      }
    };

    const toggleMissing = () => {
      this.filterMissing = !this.filterMissing;
      if (this.filterMissing) {
        this.filterManual = false;
        this.filterUnpriced = false;
      }
      updateFilterStyles();
      this.renderTable();
    };

    const toggleManual = () => {
      this.filterManual = !this.filterManual;
      if (this.filterManual) {
        this.filterMissing = false;
        this.filterUnpriced = false;
      }
      updateFilterStyles();
      this.renderTable();
    };

    const toggleUnpriced = () => {
      this.filterUnpriced = !this.filterUnpriced;
      if (this.filterUnpriced) {
        this.filterMissing = false;
        this.filterManual = false;
      }
      updateFilterStyles();
      this.renderTable();
    };

    if ($('pricingOnlyMissingBtn')) $('pricingOnlyMissingBtn').onclick = toggleMissing;
    if ($('pricingFilterMissingBtn')) $('pricingFilterMissingBtn').onclick = toggleMissing;
    if ($('pricingFilterManualBtn')) $('pricingFilterManualBtn').onclick = toggleManual;
    if ($('pricingOnlyUnpricedBtn')) $('pricingOnlyUnpricedBtn').onclick = toggleUnpriced;
    if ($('pricingFilterUnpricedBtn')) $('pricingFilterUnpricedBtn').onclick = toggleUnpriced;

    // 搜索输入
    if ($('pricingSearchInput')) {
      $('pricingSearchInput').oninput = (e) => {
        this.searchKeyword = (e.target.value || '').trim();
        this.renderTable();
      };
    }

    // 表格委托点击
    if ($('pricingExcelTbody')) {
      $('pricingExcelTbody').onclick = (e) => {
        // 折叠 / 展开箱柜
        const boxTr = e.target.closest('tr[data-box-code]');
        if (boxTr && !boxTr.dataset.compKey) {
          const code = boxTr.dataset.boxCode;
          if (this.collapsedBoxes.has(code)) {
            this.collapsedBoxes.delete(code);
          } else {
            this.collapsedBoxes.add(code);
          }
          this.renderTable();
          return;
        }

        // 选中元器件行
        const compTr = e.target.closest('tr[data-comp-key]');
        if (compTr) {
          const key = compTr.dataset.compKey;
          const boxCode = compTr.dataset.boxCode;
          const seq = Number(compTr.dataset.seq);
          const rows = this.quotation.rows || [];
          const found = rows.find(r => r.key === key || (r.box_code === boxCode && r.seq === seq));
          if (found) {
            this.selectRow(found);
            this.renderTable();
          }
        }
      };
    }

    // 采纳价格按钮
    if ($('pricingAdoptInfoBtn')) {
      $('pricingAdoptInfoBtn').onclick = () => {
        if (!this.activeCompRow) return;
        const info = (this.activeCompRow.candidates || {}).info;
        const p = Number(info && info.price);
        if (!Number.isFinite(p) || p <= 0) {
          toast('该来源没有价格');
          return;
        }
        this.adoptCandidate({ price: p, model: `${info.brand || ''} ${info.model || ''}`.trim(), source: info.source || `价库·${info.brand || ''}`, brand: info.brand || '' });
      };
    }

    if ($('pricingAdoptMarketBtn')) {
      $('pricingAdoptMarketBtn').onclick = () => {
        if (!this.activeCompRow) return;
        const market = (this.activeCompRow.candidates || {}).market;
        const p = Number(market && market.price);
        if (!Number.isFinite(p) || p <= 0) {
          toast('该来源没有价格');
          return;
        }
        this.adoptCandidate({ price: p, model: `${market.brand || ''} ${market.model || ''}`.trim(), source: market.source || `价库·${market.brand || ''}`, brand: market.brand || '' });
      };
    }

    if ($('pricingAdoptAiBtn')) {
      $('pricingAdoptAiBtn').onclick = () => {
        if (!this.activeCompRow) return;
        const ai = (this.activeCompRow.candidates || {}).ai_suggested;
        const p = Number(ai && ai.price);
        if (!Number.isFinite(p) || p <= 0) {
          toast('该来源没有价格');
          return;
        }
        const model = ai.model || this.activeCompRow.design_spec || '';
        this.adoptCandidate({ price: p, model, source: ai.source || '定额估算', brand: ai.brand || '' });
      };
    }

    if ($('pricingConfirmCustomBtn')) {
      $('pricingConfirmCustomBtn').onclick = () => {
        this.confirmCustomPrice();
      };
    }

    // 快捷键 F9 重算
    document.onkeydown = (e) => {
      if (!$('viewPricing').hidden && e.key === 'F9') {
        e.preventDefault();
        this.fetchQuotation();
        toast('F9 快捷重算已触发');
      }
    };
  }
};

/* 兼容老版本 openJob 接口 */
function openJob(id) {
  openSmartJob(id);
}

/* ================= 启动 ================= */

(async () => {
  if (typeof applyLayout === 'function') applyLayout();
  Auth.init();
  const hadToken = !!TOKEN;
  const ok = await Auth.restore();
  if (!ok) {
    Auth.show('login', hadToken ? '登录状态已失效，请重新登录' : '');
    if (!hadToken) {
      $('authModal')?.classList.add('hidden');
    }
    return;
  }
  Auth.hide();
  await loadModelSettingsSummary();
  route();
})();
