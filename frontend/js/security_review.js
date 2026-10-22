/* Scoped review UI. Target response content is always rendered as text. */
(() => {
  let timer, displayedRun, busy = false, currentStatus = 'not_started';
  let severityFilter = 'all', searchQuery = '';
  let cachedData = null;

  const el = id => document.getElementById(`security-${id}`);
  const active = status => ['running', 'paused', 'stopping'].includes(status);

  function clear() {
    currentStatus = 'not_started';
    cachedData = null;
    severityFilter = 'all';
    searchQuery = '';
    clearTimeout(timer); timer = null;
    el('findings').replaceChildren();
    el('status').textContent = 'Not started for this run.';
    ['pause', 'resume', 'stop', 'json', 'markdown', 'excel'].forEach(x => {
      const btn = el(x);
      if (btn) btn.disabled = true;
    });
    if (el('start')) el('start').disabled = false;
  }

  function scheduleRefresh() {
    clearTimeout(timer); timer = null;
    const panel = document.getElementById('panel-5');
    if (panel && !panel.classList.contains('hidden') && active(currentStatus)) {
      timer = setTimeout(refresh, 2000);
    }
  }

  function applyMode() {
    const mode = document.getElementById('assessment-mode')?.value || 'both';
    const securityOnly = mode === 'security_only' || mode === 'vapt_only';
    const vaptOnly = mode === 'vapt_only';
    const functionalOnly = mode === 'functional_only';
    const help = document.getElementById('assessment-mode-help');
    const proceed = document.getElementById('btn-analyze-proceed');
    const start = el('start');
    if (help) {
      help.textContent = securityOnly
        ? (vaptOnly ? 'VAPT assessment is enabled. Safe, bounded security checks run after the crawl; functional test planning and execution remain skipped.' : 'Security review is enabled. Test planning and execution remain skipped for this run.')
        : functionalOnly
          ? 'Security review is disabled. Continue with the normal test-planning and execution flow.'
          : 'Run the normal test-planning/execution flow and start the security review separately when ready.';
    }
    if (start) {
      start.disabled = functionalOnly || busy || active(currentStatus);
      start.title = functionalOnly ? 'Select Security review only or both to enable this scan.' : '';
    }
    if (proceed) {
      proceed.disabled = securityOnly;
      proceed.classList.toggle('opacity-50', securityOnly);
      proceed.title = securityOnly ? 'Security-review-only mode selected.' : '';
    }
  }

  function render(data) {
    cachedData = data;
    const status = data.status;
    currentStatus = status;
    el('start').disabled = active(status) || busy;
    el('pause').disabled = status !== 'running';
    el('resume').disabled = status !== 'paused';
    el('stop').disabled = !['running', 'paused'].includes(status);
    ['json', 'markdown', 'excel'].forEach(x => el(x).disabled = status === 'not_started');

    const mode = data.assessment_mode === 'vapt' ? 'VAPT' : 'Security review';
    el('status').textContent = `${mode} · Run ${data.run_id || ''} | ${status} | ${data.requests || 0} requests | ${data.completed_pages || 0} pages reviewed | ${data.pending_pages || 0} pending | ${(data.findings || []).length} findings`;
    renderFindingsList();
    scheduleRefresh();
  }

  function renderFindingsList() {
    if (!cachedData) return;
    const data = cachedData;
    const container = el('findings');
    container.replaceChildren();

    // Disclaimer banner
    const disclaimer = document.createElement('div');
    disclaimer.className = 'bg-blue-100/70 border border-blue-200 text-blue-900 px-3 py-1.5 rounded text-xs flex items-center justify-between font-medium';
    disclaimer.innerHTML = '<span>Authorized passive security assessment only. Destructive testing is disabled.</span><span class="text-[11px] font-mono text-blue-800">' + (data.requests || 0) + ' requests / ' + (data.completed_pages || 0) + ' pages</span>';
    container.append(disclaimer);

    // Summary counts by severity
    const counts = { high: 0, medium: 0, low: 0, info: 0 };
    for (const f of data.findings || []) {
      const s = String(f.severity || '').toLowerCase();
      if (counts[s] !== undefined) counts[s]++;
      else counts.info++;
    }

    // Text totals representation for automated assertions
    const totals = document.createElement('p');
    totals.className = 'text-[11px] text-slate-500 hidden';
    totals.textContent = Object.entries(counts).map(([k, c]) => `${k}: ${c}`).join(' | ');
    container.append(totals);

    // Summary cards grid
    const cards = document.createElement('div');
    cards.className = 'grid grid-cols-4 gap-2 text-center text-xs font-semibold';
    const cardDefs = [
      { key: 'high', label: 'High', count: counts.high, color: 'bg-red-50 text-red-700 border-red-200' },
      { key: 'medium', label: 'Medium', count: counts.medium, color: 'bg-amber-50 text-amber-700 border-amber-200' },
      { key: 'low', label: 'Low', count: counts.low, color: 'bg-yellow-50 text-yellow-700 border-yellow-200' },
      { key: 'info', label: 'Informational', count: counts.info, color: 'bg-blue-50 text-blue-700 border-blue-200' },
    ];
    cardDefs.forEach(cd => {
      const c = document.createElement('button');
      c.type = 'button';
      c.className = `p-2 rounded border cursor-pointer transition ${cd.color} ${severityFilter === cd.key ? 'ring-2 ring-blue-500 font-bold' : 'hover:opacity-90'}`;
      c.innerHTML = `<div class="text-base font-bold">${cd.count}</div><div class="text-[10px] uppercase tracking-wider">${cd.label}</div>`;
      c.onclick = () => {
        severityFilter = severityFilter === cd.key ? 'all' : cd.key;
        renderFindingsList();
      };
      cards.append(c);
    });
    container.append(cards);

    // Filter controls
    const filterRow = document.createElement('div');
    filterRow.className = 'flex items-center gap-2 pt-1 pb-1';
    
    const filterSelect = document.createElement('select');
    filterSelect.className = 'text-xs border border-slate-300 rounded px-2 py-1 bg-white font-medium text-slate-700';
    [
      { val: 'all', txt: 'All Severities' },
      { val: 'high', txt: 'High' },
      { val: 'medium', txt: 'Medium' },
      { val: 'low', txt: 'Low' },
      { val: 'info', txt: 'Informational' },
    ].forEach(opt => {
      const o = document.createElement('option');
      o.value = opt.val;
      o.textContent = opt.txt;
      if (severityFilter === opt.val) o.selected = true;
      filterSelect.append(o);
    });
    filterSelect.onchange = (e) => {
      severityFilter = e.target.value;
      renderFindingsList();
    };
    filterRow.append(filterSelect);

    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = 'Filter findings by title or URL...';
    searchInput.value = searchQuery;
    searchInput.className = 'text-xs border border-slate-300 rounded px-2.5 py-1 flex-1 bg-white text-slate-800 placeholder-slate-400';
    searchInput.oninput = (e) => {
      searchQuery = e.target.value.toLowerCase();
      renderFindingsList();
    };
    filterRow.append(searchInput);
    container.append(filterRow);

    // Discovered endpoint inventory
    const inventory = document.createElement('details');
    inventory.className = 'bg-white border border-slate-200 rounded p-2 text-xs';
    const title = document.createElement('summary');
    title.className = 'cursor-pointer font-semibold text-slate-700 hover:text-blue-600';
    title.textContent = `Discovered Endpoint Inventory (${(data.endpoints || []).length} endpoints cataloged)`;
    inventory.append(title);
    const endpList = document.createElement('div');
    endpList.className = 'mt-2 space-y-1 max-h-40 overflow-y-auto pl-1 font-mono text-[11px] text-slate-600';
    for (const endpoint of data.endpoints || []) {
      const p = document.createElement('p');
      p.textContent = `${endpoint.method || 'GET'} ${endpoint.url} — HTTP ${endpoint.status}; forms: ${endpoint.forms_observed || 0}`;
      endpList.append(p);
    }
    inventory.append(endpList);
    container.append(inventory);

    // Warnings and limitations
    for (const warning of data.warnings || []) {
      const p = document.createElement('p');
      p.className = 'text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2.5 py-1';
      p.textContent = warning;
      container.append(p);
    }
    if (data.not_tested && data.not_tested.length) {
      const p = document.createElement('p');
      p.className = 'text-[11px] text-slate-500 italic pl-1';
      p.textContent = `Not tested: ${data.not_tested.join(', ')}`;
      container.append(p);
    }

    // Filtered findings list
    const filtered = (data.findings || []).filter(f => {
      const sevMatch = severityFilter === 'all' || String(f.severity || '').toLowerCase() === severityFilter;
      const textMatch = !searchQuery ||
        String(f.title || '').toLowerCase().includes(searchQuery) ||
        String(f.url || '').toLowerCase().includes(searchQuery) ||
        String(f.owasp_mapping || f.owasp || '').toLowerCase().includes(searchQuery);
      return sevMatch && textMatch;
    });

    if (filtered.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'text-xs text-slate-500 italic text-center py-4';
      empty.textContent = (data.findings || []).length === 0 ? 'No findings recorded.' : 'No findings match the active filter.';
      container.append(empty);
    }

    for (const f of filtered) {
      const details = document.createElement('details');
      details.className = 'bg-white border border-slate-200 rounded-lg p-2.5 shadow-xs space-y-1.5 transition hover:border-slate-300';
      const summary = document.createElement('summary');
      summary.className = 'cursor-pointer font-medium text-slate-800 flex items-center justify-between gap-2';

      const sev = String(f.severity || 'low').toLowerCase();
      const sevBadgeColor = sev === 'high' ? 'bg-red-100 text-red-800' :
                            sev === 'medium' ? 'bg-amber-100 text-amber-800' :
                            sev === 'low' ? 'bg-yellow-100 text-yellow-800' : 'bg-blue-100 text-blue-800';

      summary.innerHTML = `<div class="flex items-center gap-2 overflow-hidden text-ellipsis">
        <span class="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${sevBadgeColor}">${f.severity || 'info'}</span>
        <span class="font-semibold text-slate-900">${f.title}</span>
      </div>
      <span class="text-[10px] font-mono text-slate-500 whitespace-nowrap">${f.owasp_mapping || f.owasp || 'A05:2021'}</span>`;
      details.append(summary);

      const content = document.createElement('div');
      content.className = 'pt-2 space-y-1 text-xs border-t border-slate-100 text-slate-700';

      const row = (label, val, mono = false) => {
        if (!val) return;
        const p = document.createElement('p');
        p.className = 'leading-relaxed';
        const strong = document.createElement('strong');
        strong.className = 'text-slate-900 mr-1';
        strong.textContent = `${label}:`;
        p.append(strong);
        if (mono) {
          const span = document.createElement('span');
          span.className = 'font-mono text-[11px] bg-slate-100 px-1 py-0.5 rounded break-all';
          span.textContent = val;
          p.append(span);
        } else {
          p.append(document.createTextNode(val));
        }
        content.append(p);
      };

      row('Finding ID', f.finding_id, true);
      row('Category', f.category || f.owasp);
      row('Endpoint', `${f.method || 'GET'} ${f.url}`, true);
      row('Confidence', f.confidence);
      row('Verification', f.verification);
      row('Evidence', f.evidence);
      row('Impact', f.business_impact || f.impact);
      row('Remediation', f.remediation);

      if (f.request_metadata) {
        const meta = f.request_metadata;
        const p = document.createElement('p');
        p.className = 'text-[11px] font-mono text-slate-500 bg-slate-50 p-1.5 rounded border border-slate-200';
        p.textContent = `Safe Repro: ${meta.method || 'GET'} ${meta.url}`;
        content.append(p);
      }

      details.append(content);
      container.append(details);
    }
  }

  async function request(action, body) {
    const run = state.runId;
    if (!run) throw new Error('Complete a crawl first.');
    const response = await fetch(`${API_BASE}/runs/${encodeURIComponent(run)}/security${action}`, body ? {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    } : {});
    let data;
    try { data = await response.json(); } catch (_) { data = {}; }
    if (response.status === 404) {
      throw new Error('Security scan backend is not loaded. Restart the project server and hard-refresh the browser.');
    }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Security request failed.');
    if (run === state.runId) render(data);
    return data;
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      if (!state.runId) { clear(); return; }
      await request('');
    } catch (e) { el('status').textContent = e.message; }
    scheduleRefresh();
  }

  document.addEventListener('DOMContentLoaded', () => {
    const mode = document.getElementById('assessment-mode');
    if (mode) mode.onchange = applyMode;
    el('start').onclick = async () => {
      const checks = [...document.querySelectorAll('[name="security-check"]:checked')].map(x => x.value);
      if (el('active').checked) checks.push('cors_probe', 'reflection_canary');
      busy = true; el('start').disabled = true;
      let started = false;
      try {
        const selectedMode = document.getElementById('assessment-mode')?.value || 'both';
        await request('/start', {assessment_mode: selectedMode === 'vapt_only' ? 'vapt' : 'review', authorized: el('authorized').checked, active_opt_in: el('active').checked, checks});
        started = true;
      } catch (e) { el('status').textContent = e.message; }
      finally { busy = false; el('start').disabled = started; }
      if (started) refresh();
    };
    ['pause', 'resume', 'stop'].forEach(action => el(action).onclick = async () => {
      try { await request(`/${action}`, {}); } catch (e) { el('status').textContent = e.message; }
    });
    el('refresh').onclick = refresh;
    ['json', 'markdown', 'excel'].forEach(format => el(format).onclick = async () => {
      const run = state.runId;
      if (!run) return;
      try {
        const response = await fetch(`${API_BASE}/runs/${encodeURIComponent(run)}/security/export/${format}`);
        if (!response.ok) throw new Error('Security report is unavailable.');
        const url = URL.createObjectURL(await response.blob());
        const a = document.createElement('a'); a.href = url; a.download = `security_${run}.${{json: 'json', markdown: 'md', excel: 'xlsx'}[format]}`;
        a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (e) { el('status').textContent = e.message; }
    });
    new MutationObserver(() => {
      const panel = document.getElementById('panel-5');
      if (panel.classList.contains('hidden')) { clearTimeout(timer); timer = null; return; }
      if (displayedRun !== state.runId) {
        displayedRun = state.runId; clear(); el('authorized').checked = false; el('active').checked = false;
        if (mode) mode.value = 'both';
      }
      applyMode();
      if (currentStatus === 'not_started') refresh();
    }).observe(document.getElementById('panel-5'), {attributes: true, attributeFilter: ['class']});
  });
})();
