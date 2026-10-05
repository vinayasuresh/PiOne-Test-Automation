// frontend/js/scan_status.js
// UI logic for live scan counts and toast notifications

function updateScanCounts(auto, manual, merged) {
  document.getElementById('count-auto').textContent = auto;
  document.getElementById('count-manual').textContent = manual;
  document.getElementById('count-merged').textContent = merged;
}

function showToast(message, type = 'info') {
  let toast = document.createElement('div');
  toast.className = `fixed top-6 right-6 z-50 px-5 py-3 rounded-lg shadow-lg text-white font-semibold text-sm bg-${type === 'success' ? 'emerald' : type === 'error' ? 'red' : 'blue'}-600 animate-fadein`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => { toast.remove(); }, 3500);
}

// Listen for backend events (pseudo, replace with real event source)
window.addEventListener('scan:logout-resume', () => {
  showToast('Logout detected. Scan auto-resumed.', 'info');
});

window.addEventListener('scan:counts-update', (e) => {
  const { auto, manual, merged } = e.detail;
  updateScanCounts(auto, manual, merged);
});

// Export for use in other scripts
window.updateScanCounts = updateScanCounts;
window.showToast = showToast;

(function connectScanWebSocket() {
  let ws = new WebSocket(`ws://${location.host}/ws/scan-events`);
  ws.onmessage = function(event) {
    const msg = JSON.parse(event.data);
    if (msg.type === 'counts-update') {
      window.updateScanCounts(msg.data.auto, msg.data.manual, msg.data.merged);
      window.dispatchEvent(new CustomEvent('scan:counts-update', { detail: msg.data }));
    }
    if (msg.type === 'logout-resume') {
      window.showToast('Logout detected. Scan auto-resumed.', 'info');
      window.dispatchEvent(new Event('scan:logout-resume'));
    }
  };
  ws.onclose = function() {
    setTimeout(connectScanWebSocket, 2000); // Auto-reconnect
  };
})();
