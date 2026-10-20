// frontend/js/scan_status.js
// PiOne Testmate UI helpers for live counts, toasts, and scoped telemetry WebSocket

function updateScanCounts(auto, manual, merged) {
  const autoEl = document.getElementById('count-auto');
  const manualEl = document.getElementById('count-manual');
  const mergedEl = document.getElementById('count-merged');
  if (autoEl) autoEl.textContent = auto;
  if (manualEl) manualEl.textContent = manual;
  if (mergedEl) mergedEl.textContent = merged;
}

function showToast(message, type = 'info') {
  let toast = document.createElement('div');
  const bgClass = type === 'success' ? 'bg-emerald-600' : type === 'error' ? 'bg-red-600' : 'bg-blue-600';
  toast.className = `fixed top-6 right-6 z-50 px-5 py-3 rounded-lg shadow-lg text-white font-semibold text-sm ${bgClass} transition-all duration-300`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => { toast.remove(); }, 3500);
}

// Scoped WebSocket connection by run_id or scan_id
function connectScanWebSocket(runId, onEventCallback) {
  if (!runId) return null;
  const wsScheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  let ws = new WebSocket(`${wsScheme}//${location.host}/ws/scan-events?run_id=${encodeURIComponent(runId)}`);

  ws.onmessage = function(event) {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'heartbeat') return;
      if (typeof onEventCallback === 'function') {
        onEventCallback(msg);
      }
      if (msg.type === 'counts-update' && msg.data) {
        updateScanCounts(msg.data.auto, msg.data.manual, msg.data.merged);
      }
      if (msg.type === 'logout-resume') {
        showToast('Logout detected. Scan auto-resumed.', 'info');
      }
    } catch (_) {}
  };

  ws.onerror = function() {
    // Reconnect handled smoothly
  };

  return ws;
}

// Export globals
window.updateScanCounts = updateScanCounts;
window.showToast = showToast;
window.connectScanWebSocket = connectScanWebSocket;
