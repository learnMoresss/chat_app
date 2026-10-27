/* global fetch, localStorage, FormData, FileReader */
(() => {
  const $ = (id) => document.getElementById(id);
  const LS = {
    access: 'chat_access',
    refresh: 'chat_refresh',
    base: 'chat_base',
    user: 'chat_user',
    remember: 'chat_remember',
    username: 'chat_username',
  };
  const state = {
    token: localStorage.getItem(LS.access) || '',
    refresh: localStorage.getItem(LS.refresh) || '',
    base: localStorage.getItem(LS.base) || '',
    user: JSON.parse(localStorage.getItem(LS.user) || '{}'),
    remember: localStorage.getItem(LS.remember) !== '0',
    page: 'overview',
    personas: [],
    channels: [],
    prefs: {},
    selectedPersonaId: '',
  };

  function persistSession() {
    localStorage.setItem(LS.base, state.base || '');
    localStorage.setItem(LS.remember, state.remember ? '1' : '0');
    if (!state.remember) {
      localStorage.removeItem(LS.access);
      localStorage.removeItem(LS.refresh);
      localStorage.removeItem(LS.user);
      return;
    }
    localStorage.setItem(LS.access, state.token || '');
    localStorage.setItem(LS.refresh, state.refresh || '');
    localStorage.setItem(LS.user, JSON.stringify(state.user || {}));
  }

  function clearSession(keepUsername = true) {
    localStorage.removeItem(LS.access);
    localStorage.removeItem(LS.refresh);
    localStorage.removeItem(LS.user);
    if (!keepUsername) localStorage.removeItem(LS.username);
    state.token = '';
    state.refresh = '';
    state.user = {};
  }

  const titles = {
    overview: ['总览', '服务器状态与快捷入口'],
    partner: ['聊天对象', '选定当前默认聊天人设（App/网页共用偏好）'],
    personas: ['人设', '编辑 bio、渠道、问候与朋友圈开关'],
    memories: ['记忆', '按人设浏览与增删记忆（Mem0 风格隔离）'],
    channels: ['渠道 / Key', '配置模型接口；Key 可明文核对'],
    extract: ['提取', '上传聊天文本抽习惯；上传语音存声音特征（对齐 OpenSelf / Resemblyzer）'],
    usage: ['用量', '近 30 天调用与 Token（对齐 Open WebUI Usage）'],
  };

  function apiBase() {
    const v = ($('baseUrl')?.value || state.base || '').replace(/\/$/, '');
    return v || location.origin;
  }

  async function api(path, opts = {}, { retry = true } = {}) {
    const headers = { ...(opts.headers || {}) };
    if (!(opts.body instanceof FormData)) headers['Content-Type'] = headers['Content-Type'] || 'application/json';
    if (state.token) headers.Authorization = 'Bearer ' + state.token;
    const res = await fetch(apiBase() + path, { ...opts, headers });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = text; }
    if (res.status === 401 && retry && state.refresh && !path.includes('/auth/')) {
      const ok = await tryRefresh();
      if (ok) return api(path, opts, { retry: false });
    }
    if (!res.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === 'string' ? detail : ('请求失败 ' + res.status));
    }
    return data;
  }

  async function tryRefresh() {
    if (!state.refresh) return false;
    try {
      const res = await fetch(apiBase() + '/api/v1/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: state.refresh }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return false;
      state.token = data.access_token || '';
      state.refresh = data.refresh_token || state.refresh;
      if (data.user) state.user = data.user;
      persistSession();
      return !!state.token;
    } catch (_) {
      return false;
    }
  }

  function showAdmin() {
    $('loginPage').classList.add('hidden');
    $('adminPage').classList.remove('hidden');
    $('sideUser').textContent = (state.user.display_name || state.user.username || '用户') + (state.user.is_admin ? ' · 管理员' : '');
    go(state.page || 'overview');
  }

  function go(page) {
    state.page = page;
    document.querySelectorAll('.nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === page));
    const [t, h] = titles[page] || [page, ''];
    $('pageTitle').textContent = t;
    $('pageHint').textContent = h;
    render();
  }

  async function loadCore() {
    const [info, personas, channels, prefs] = await Promise.all([
      api('/api/info'),
      api('/api/v1/personas'),
      api('/api/v1/channels'),
      api('/api/v1/prefs'),
    ]);
    state.personas = personas || [];
    state.channels = channels || [];
    state.prefs = prefs || {};
    state.selectedPersonaId = state.prefs.active_persona_id || state.personas[0]?.id || '';
    $('sideVer').textContent = `${info.name} · v${info.version}`;
  }

  function personaOptions(selected) {
    return state.personas.map((p) =>
      `<option value="${p.id}" ${p.id === selected ? 'selected' : ''}>${escapeHtml(p.remark || p.name)}</option>`
    ).join('');
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  async function render() {
    const root = $('pageRoot');
    root.innerHTML = '<div class="muted">加载中…</div>';
    try {
      if (!state.personas.length) await loadCore();
      if (state.page === 'overview') return renderOverview(root);
      if (state.page === 'partner') return renderPartner(root);
      if (state.page === 'personas') return renderPersonas(root);
      if (state.page === 'memories') return renderMemories(root);
      if (state.page === 'channels') return renderChannels(root);
      if (state.page === 'extract') return renderExtract(root);
      if (state.page === 'usage') return renderUsage(root);
    } catch (e) {
      root.innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
    }
  }

  async function renderOverview(root) {
    const sum = await api('/api/v1/usage/summary?days=30');
    root.innerHTML = `
      <div class="grid grid-3">
        <div class="card metric"><span>近 30 天调用</span><b>${sum.calls || 0}</b></div>
        <div class="card metric"><span>成功</span><b>${sum.ok_calls || 0}</b></div>
        <div class="card metric"><span>Token</span><b>${sum.total_tokens || 0}</b></div>
      </div>
      <div class="card" style="margin-top:16px">
        <h3>快捷</h3>
        <div class="row">
          <button class="btn secondary" data-go="partner">配置聊天对象</button>
          <button class="btn secondary" data-go="personas">编辑人设</button>
          <button class="btn secondary" data-go="extract">提取习惯 / 声音</button>
          <button class="btn secondary" data-go="channels">模型 Key</button>
        </div>
        <p class="muted" style="margin-top:12px">当前默认聊天对象：
          <strong>${escapeHtml((state.personas.find(p => p.id === state.prefs.active_persona_id) || {}).name || '未设置')}</strong>
          · 人设 ${state.personas.length} 个 · 渠道 ${state.channels.length} 条
        </p>
      </div>`;
    root.querySelectorAll('[data-go]').forEach((b) => b.onclick = () => go(b.dataset.go));
  }

  function renderPartner(root) {
    root.innerHTML = `
      <div class="card">
        <h3>默认聊天人</h3>
        <p class="muted">选定后，App / 网页都以此为人设偏好（存 user_prefs）。</p>
        <label>人设</label>
        <select id="partnerSelect">${personaOptions(state.prefs.active_persona_id)}</select>
        <div class="row" style="margin-top:14px">
          <button class="btn" id="savePartner">保存</button>
        </div>
        <div id="partnerMsg"></div>
      </div>`;
    $('savePartner').onclick = async () => {
      try {
        const id = $('partnerSelect').value || null;
        state.prefs = await api('/api/v1/prefs', { method: 'PUT', body: JSON.stringify({ active_persona_id: id }) });
        state.selectedPersonaId = id || '';
        $('partnerMsg').innerHTML = '<div class="ok">已保存</div>';
      } catch (e) {
        $('partnerMsg').innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
      }
    };
  }

  function renderPersonas(root) {
    const pid = state.selectedPersonaId || state.personas[0]?.id;
    const p = state.personas.find((x) => x.id === pid) || state.personas[0];
    if (!p) {
      root.innerHTML = '<div class="card muted">还没有人设。注册时会自动创建「深深」。</div>';
      return;
    }
    state.selectedPersonaId = p.id;
    root.innerHTML = `
      <div class="grid grid-2">
        <div class="card">
          <h3>选择人设</h3>
          <select id="personaPick">${personaOptions(p.id)}</select>
          <div style="margin-top:14px" id="personaList">
            ${state.personas.map((x) => `
              <div class="list-item">
                <div>
                  <strong>${escapeHtml(x.name)}</strong>
                  ${x.is_bot ? '<span class="pill">人设</span>' : ''}
                  <div class="muted">${escapeHtml((x.bio || '').slice(0, 60))}</div>
                </div>
              </div>`).join('')}
          </div>
        </div>
        <div class="card">
          <h3>编辑 · ${escapeHtml(p.name)}</h3>
          <label>名字</label>
          <input id="pName" value="${escapeHtml(p.name)}" />
          <label>备注</label>
          <input id="pRemark" value="${escapeHtml(p.remark || '')}" />
          <label>人设说明（bio）</label>
          <textarea id="pBio">${escapeHtml(p.bio || '')}</textarea>
          <label>渠道</label>
          <select id="pChannel">
            <option value="">（自动选有 Key 的）</option>
            ${state.channels.map((c) => `<option value="${c.id}" ${c.id === p.channel_id ? 'selected' : ''}>${escapeHtml(c.name)}${c.key_filled ? '' : ' · 无Key'}</option>`).join('')}
          </select>
          <div class="row" style="margin-top:10px">
            <label style="display:flex;gap:8px;align-items:center;margin:0"><input type="checkbox" id="pGreet" ${p.greet ? 'checked' : ''}/> 自己问候</label>
            <label style="display:flex;gap:8px;align-items:center;margin:0"><input type="checkbox" id="pMoments" ${p.moments_on ? 'checked' : ''}/> 朋友圈</label>
          </div>
          <div class="row" style="margin-top:14px">
            <button class="btn" id="savePersona">保存人设</button>
          </div>
          <div id="personaMsg"></div>
        </div>
      </div>`;
    $('personaPick').onchange = () => {
      state.selectedPersonaId = $('personaPick').value;
      renderPersonas(root);
    };
    $('savePersona').onclick = async () => {
      try {
        const body = {
          name: $('pName').value.trim(),
          remark: $('pRemark').value.trim(),
          bio: $('pBio').value,
          channel_id: $('pChannel').value,
          greet: $('pGreet').checked,
          moments_on: $('pMoments').checked,
        };
        const updated = await api(`/api/v1/personas/${p.id}`, { method: 'PATCH', body: JSON.stringify(body) });
        const i = state.personas.findIndex((x) => x.id === p.id);
        if (i >= 0) state.personas[i] = updated;
        $('personaMsg').innerHTML = '<div class="ok">已保存</div>';
      } catch (e) {
        $('personaMsg').innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
      }
    };
  }

  async function renderMemories(root) {
    const pid = state.selectedPersonaId || state.personas[0]?.id;
    if (!pid) {
      root.innerHTML = '<div class="card muted">无人设</div>';
      return;
    }
    const memories = await api(`/api/v1/personas/${pid}/memories`);
    root.innerHTML = `
      <div class="card">
        <div class="row" style="justify-content:space-between">
          <h3 style="margin:0">记忆</h3>
          <select id="memPersona">${personaOptions(pid)}</select>
        </div>
        <p class="muted">隔离键 = 用户 + 人设（Mem0）。可手改；聊天后也会自动抽取。</p>
        <label>新增一条</label>
        <div class="row">
          <input id="memText" style="flex:1" placeholder="例如：主人喜欢安静" />
          <button class="btn" id="memAdd">添加</button>
        </div>
        <div id="memMsg"></div>
        <div style="margin-top:14px">
          ${(memories || []).map((m) => `
            <div class="list-item">
              <div>${escapeHtml(m.text)}<div class="muted">${escapeHtml((m.updated_at || '').replace('T', ' ').slice(0, 19))}</div></div>
              <button class="btn danger" data-del="${m.id}">删除</button>
            </div>`).join('') || '<div class="muted">暂无记忆</div>'}
        </div>
      </div>`;
    $('memPersona').onchange = () => {
      state.selectedPersonaId = $('memPersona').value;
      renderMemories(root);
    };
    $('memAdd').onclick = async () => {
      try {
        await api(`/api/v1/personas/${pid}/memories`, { method: 'POST', body: JSON.stringify({ text: $('memText').value.trim() }) });
        renderMemories(root);
      } catch (e) {
        $('memMsg').innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
      }
    };
    root.querySelectorAll('[data-del]').forEach((b) => {
      b.onclick = async () => {
        await api(`/api/v1/personas/${pid}/memories/${b.dataset.del}`, { method: 'DELETE' });
        renderMemories(root);
      };
    });
  }

  async function renderChannels(root) {
    const channels = await api('/api/v1/channels');
    state.channels = channels;
    root.innerHTML = `
      <div class="card">
        <h3>渠道列表</h3>
        <p class="muted">点「编辑」可明文查看 / 修改 Key。</p>
        ${(channels || []).map((c) => `
          <div class="list-item">
            <div>
              <strong>${escapeHtml(c.name)}</strong>
              ${c.shared ? '<span class="pill">公共</span>' : '<span class="pill warn">我的</span>'}
              ${c.key_filled ? '<span class="pill">Key 已填</span>' : '<span class="pill warn">未配置</span>'}
              <div class="muted">${escapeHtml(c.model)} · ${escapeHtml(c.base_url)}</div>
            </div>
            <button class="btn secondary" data-edit="${c.id}">编辑</button>
          </div>`).join('')}
      </div>
      <div class="card hidden" id="chEdit">
        <h3>编辑渠道</h3>
        <input type="hidden" id="chId" />
        <label>名称</label><input id="chName" />
        <label>模型</label><input id="chModel" />
        <label>接口地址</label><input id="chUrl" />
        <label>API Key（明文）</label><input id="chKey" />
        <div class="row" style="margin-top:14px">
          <button class="btn" id="chSave">保存</button>
          <button class="btn secondary" id="chCancel">取消</button>
        </div>
        <div id="chMsg"></div>
      </div>`;
    root.querySelectorAll('[data-edit]').forEach((b) => {
      b.onclick = async () => {
        const detail = await api(`/api/v1/channels/${b.dataset.edit}`);
        $('chEdit').classList.remove('hidden');
        $('chId').value = detail.id;
        $('chName').value = detail.name || '';
        $('chModel').value = detail.model || '';
        $('chUrl').value = detail.base_url || '';
        $('chKey').value = detail.api_key || '';
      };
    });
    $('chCancel').onclick = () => $('chEdit').classList.add('hidden');
    $('chSave').onclick = async () => {
      try {
        const id = $('chId').value;
        await api(`/api/v1/channels/${id}`, {
          method: 'PATCH',
          body: JSON.stringify({
            name: $('chName').value.trim(),
            model: $('chModel').value.trim(),
            base_url: $('chUrl').value.trim(),
            api_key: $('chKey').value,
          }),
        });
        $('chMsg').innerHTML = '<div class="ok">已保存</div>';
        renderChannels(root);
      } catch (e) {
        $('chMsg').innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
      }
    };
  }

  function renderExtract(root) {
    const pid = state.selectedPersonaId || state.personas[0]?.id || '';
    root.innerHTML = `
      <div class="card" style="margin-bottom:16px">
        <h3>应用到哪个人设</h3>
        <select id="exPersona">${personaOptions(pid)}</select>
        <p class="muted" style="margin-top:8px">
          习惯提取对齐 <a href="https://github.com/Open-Self/Open-Self" target="_blank">OpenSelf</a> /
          <a href="https://github.com/AFan4724/clone-chat" target="_blank">clone-chat</a>；
          声音特征对齐 <a href="https://github.com/resemble-ai/Resemblyzer" target="_blank">Resemblyzer</a> /
          <a href="https://github.com/myshell-ai/OpenVoice" target="_blank">OpenVoice</a>（已装则写 embedding，否则先存参考音频）。
        </p>
      </div>
      <div class="grid grid-2">
        <div class="card">
          <h3>① 聊天习惯</h3>
          <label>粘贴聊天文本</label>
          <textarea id="habitText" placeholder="导出的微信/QQ 聊天记录文本…"></textarea>
          <label>或上传 .txt</label>
          <input type="file" id="habitFile" accept=".txt,text/plain" />
          <label style="display:flex;gap:8px;align-items:center;margin-top:10px">
            <input type="checkbox" id="habitApply" checked /> 提取后写回人设 bio
          </label>
          <button class="btn" id="habitRun" style="margin-top:12px">开始提取习惯</button>
          <div id="habitMsg"></div>
          <pre class="out hidden" id="habitOut"></pre>
        </div>
        <div class="card">
          <h3>② 声音特征</h3>
          <label>上传参考语音（建议 5–30 秒清晰人声）</label>
          <input type="file" id="voiceFile" accept="audio/*,.wav,.mp3,.m4a,.ogg,.flac,.webm" />
          <button class="btn" id="voiceRun" style="margin-top:12px">上传并提取</button>
          <div id="voiceMsg"></div>
          <div id="voiceList" style="margin-top:12px" class="muted">加载中…</div>
        </div>
      </div>`;

    const refreshVoice = async () => {
      const id = $('exPersona').value;
      state.selectedPersonaId = id;
      try {
        const list = await api(`/api/v1/extract/voice/${id}`);
        $('voiceList').innerHTML = (list || []).map((v) => `
          <div class="list-item">
            <div>
              <strong>${escapeHtml(v.filename)}</strong>
              <span class="pill ${v.embed_status === 'ready' ? '' : 'warn'}">${escapeHtml(v.embed_status)}</span>
              <div class="muted">${v.duration_sec}s · ${escapeHtml(v.embed_note)}</div>
            </div>
          </div>`).join('') || '还没有声音档案';
      } catch (e) {
        $('voiceList').textContent = e.message;
      }
    };
    $('exPersona').onchange = refreshVoice;
    refreshVoice();

    $('habitRun').onclick = async () => {
      const fd = new FormData();
      fd.append('persona_id', $('exPersona').value);
      fd.append('text', $('habitText').value);
      fd.append('apply_to_bio', $('habitApply').checked ? 'true' : 'false');
      const f = $('habitFile').files[0];
      if (f) fd.append('file', f);
      $('habitMsg').textContent = '提取中…';
      try {
        const res = await api('/api/v1/extract/habits', { method: 'POST', body: fd, headers: {} });
        $('habitMsg').innerHTML = '<div class="ok">完成</div>';
        $('habitOut').classList.remove('hidden');
        $('habitOut').textContent = JSON.stringify(res.profile, null, 2);
        await loadCore();
      } catch (e) {
        $('habitMsg').innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
      }
    };

    $('voiceRun').onclick = async () => {
      const f = $('voiceFile').files[0];
      if (!f) {
        $('voiceMsg').innerHTML = '<div class="err">请选择音频文件</div>';
        return;
      }
      const fd = new FormData();
      fd.append('persona_id', $('exPersona').value);
      fd.append('file', f);
      $('voiceMsg').textContent = '上传中…';
      try {
        const res = await api('/api/v1/extract/voice', { method: 'POST', body: fd, headers: {} });
        $('voiceMsg').innerHTML = `<div class="ok">${escapeHtml(res.embed_note)}</div>`;
        refreshVoice();
      } catch (e) {
        $('voiceMsg').innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
      }
    };
  }

  async function renderUsage(root) {
    const [sum, logs] = await Promise.all([
      api('/api/v1/usage/summary?days=30'),
      api('/api/v1/usage/logs?limit=40'),
    ]);
    let adminHtml = '';
    if (state.user.is_admin) {
      try {
        const adm = await api('/api/v1/usage/admin/summary?days=30');
        adminHtml = `
          <div class="card" style="margin-top:16px">
            <h3>管理员 · 全站</h3>
            <div class="muted">调用 ${adm.calls || 0} · Token ${adm.total_tokens || 0}</div>
            <table style="margin-top:10px">
              <thead><tr><th>用户</th><th>调用</th><th>Token</th></tr></thead>
              <tbody>
                ${(adm.by_user || []).map((u) => `<tr><td>${escapeHtml(u.username)}</td><td>${u.calls}</td><td>${u.tokens}</td></tr>`).join('') || '<tr><td colspan="3">暂无</td></tr>'}
              </tbody>
            </table>
          </div>`;
      } catch (_) {}
    }
    root.innerHTML = `
      <div class="grid grid-3">
        <div class="card metric"><span>调用</span><b>${sum.calls || 0}</b></div>
        <div class="card metric"><span>成功</span><b>${sum.ok_calls || 0}</b></div>
        <div class="card metric"><span>Token</span><b>${sum.total_tokens || 0}</b></div>
      </div>
      <div class="card" style="margin-top:16px">
        <h3>明细</h3>
        <table>
          <thead><tr><th>时间</th><th>模型</th><th>Token</th><th>端</th><th>状态</th></tr></thead>
          <tbody>
            ${(logs || []).map((r) => `
              <tr>
                <td>${escapeHtml((r.created_at || '').replace('T', ' ').slice(0, 19))}</td>
                <td>${escapeHtml(r.model || '-')}</td>
                <td>${r.total_tokens || 0}</td>
                <td>${escapeHtml(r.client || '-')}</td>
                <td>${r.status === 'ok' ? '成功' : '失败'}</td>
              </tr>`).join('') || '<tr><td colspan="5">暂无记录</td></tr>'}
          </tbody>
        </table>
      </div>
      ${adminHtml}`;
  }

  // events
  document.querySelectorAll('.nav button').forEach((b) => b.onclick = () => go(b.dataset.page));
  $('logoutBtn').onclick = async () => {
    try {
      if (state.refresh) {
        await fetch(apiBase() + '/api/v1/auth/logout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: state.refresh }),
        });
      }
    } catch (_) { /* ignore */ }
    clearSession(true);
    $('adminPage').classList.add('hidden');
    $('loginPage').classList.remove('hidden');
    if ($('rememberMe')) $('rememberMe').checked = state.remember;
    if ($('password')) $('password').value = '';
  };

  async function doLogin(ev) {
    if (ev) ev.preventDefault();
    $('loginErr').textContent = '';
    $('loginBtn').disabled = true;
    try {
      state.remember = !!$('rememberMe')?.checked;
      localStorage.setItem(LS.remember, state.remember ? '1' : '0');
      const username = $('username').value.trim();
      const data = await api('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          username,
          password: $('password').value,
          device_name: 'WebAdmin',
        }),
      }, { retry: false });
      state.token = data.access_token;
      state.refresh = data.refresh_token || '';
      state.user = data.user || {};
      state.base = apiBase();
      localStorage.setItem(LS.username, username);
      persistSession();
      await loadCore();
      showAdmin();
    } catch (e) {
      $('loginErr').textContent = e.message || String(e);
    } finally {
      $('loginBtn').disabled = false;
    }
  }

  $('loginForm').onsubmit = doLogin;

  if (state.base && $('baseUrl')) $('baseUrl').value = state.base;
  const savedUser = localStorage.getItem(LS.username) || '';
  if (savedUser && $('username')) $('username').value = savedUser;
  if ($('rememberMe')) $('rememberMe').checked = state.remember;

  async function boot() {
    if (!state.token && !state.refresh) return;
    try {
      await loadCore();
      showAdmin();
    } catch (_) {
      const ok = await tryRefresh();
      if (!ok) {
        clearSession(true);
        return;
      }
      try {
        await loadCore();
        showAdmin();
      } catch (__) {
        clearSession(true);
      }
    }
  }
  boot();
})();
