/* Scoped review UI. Target response content is always rendered as text. */
(() => {
  let timer, displayedRun, busy = false, currentStatus = 'not_started';
  const el = id => document.getElementById(`security-${id}`);
  const active = status => ['running', 'paused', 'stopping'].includes(status);
  function clear() {
    currentStatus = 'not_started';
    clearTimeout(timer);
    el('findings').replaceChildren();
    el('status').textContent = 'Not started for this run.';
    ['pause', 'resume', 'stop', 'json', 'markdown', 'excel'].forEach(x => el(x).disabled = true);
    el('start').disabled = false;
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
    const status = data.status;
    currentStatus = status;
    el('start').disabled = active(status) || busy;
    el('pause').disabled = status !== 'running';
    el('resume').disabled = status !== 'paused';
    el('stop').disabled = !['running', 'paused'].includes(status);
    ['json', 'markdown', 'excel'].forEach(x => el(x).disabled = status === 'not_started');
    el('status').textContent = `Run ${data.run_id || ''} | ${status} | ${data.requests || 0} requests | ${data.completed_pages || 0} pages reviewed | ${data.pending_pages || 0} pending | ${(data.findings || []).length} findings`;
    el('findings').replaceChildren();
    const counts = {};
    for (const f of data.findings || []) {
      counts[f.severity] = (counts[f.severity] || 0) + 1;
      counts[f.owasp] = (counts[f.owasp] || 0) + 1;
    }
    const totals = document.createElement('p');
    totals.textContent = Object.entries(counts).map(([key, count]) => `${key}: ${count}`).join(' | ');
    el('findings').append(totals);
    const inventory = document.createElement('details');
    const title = document.createElement('summary');
    title.textContent = `Endpoint inventory (${(data.endpoints || []).length} retrieved)`;
    inventory.append(title);
    for (const endpoint of data.endpoints || []) {
      const p = document.createElement('p');
      p.textContent = `${endpoint.method} ${endpoint.url} — HTTP ${endpoint.status}; forms observed: ${endpoint.forms_observed || 0}`;
      inventory.append(p);
    }
    el('findings').append(inventory);
    for (const warning of data.warnings || []) {
      const p = document.createElement('p'); p.textContent = warning; el('findings').append(p);
    }
    if (data.not_tested) {
      const p = document.createElement('p'); p.textContent = `Not tested: ${data.not_tested.join(', ')}`; el('findings').append(p);
    }
    for (const f of data.findings || []) {
      const details = document.createElement('details');
      details.className = 'bg-white border rounded p-2';
      const summary = document.createElement('summary');
      summary.textContent = `${f.severity.toUpperCase()} · ${f.status || 'Observation'} · ${f.title} · ${f.owasp_mapping || f.owasp}`;
      details.append(summary);
      for (const key of ['finding_id', 'category', 'url', 'confidence', 'verification', 'evidence', 'business_impact', 'remediation']) {
        const p = document.createElement('p'); p.textContent = `${key}: ${f[key] || f.impact || ''}`; details.append(p);
      }
      el('findings').append(details);
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
    if (!document.getElementById('panel-5').classList.contains('hidden')) timer = setTimeout(refresh, 2000);
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
      if (panel.classList.contains('hidden')) { clearTimeout(timer); return; }
      if (displayedRun !== state.runId) {
        displayedRun = state.runId; clear(); el('authorized').checked = false; el('active').checked = false;
        if (mode) mode.value = 'both';
      }
      applyMode();
      refresh();
    }).observe(document.getElementById('panel-5'), {attributes: true, attributeFilter: ['class']});
  });
})();
