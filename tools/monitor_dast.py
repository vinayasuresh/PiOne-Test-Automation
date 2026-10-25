import urllib.request, urllib.error, json, time

run_id = '828b65f112134aa792b097c5ddfe1e15'

# 1. Start review via HTTP API
payload = {
    'assessment_mode': 'review',
    'authorized': True,
    'active_opt_in': True,
    'checks': ['headers', 'cookies', 'mixed_content', 'disclosure', 'api_inventory', 'auth_boundary', 'error_disclosure', 'cors_probe', 'reflection_canary'],
    'max_requests': 20,
    'max_seconds': 120
}

req = urllib.request.Request(
    f'http://127.0.0.1:8000/runs/{run_id}/security/start',
    data=json.dumps(payload).encode(),
    headers={'Content-Type': 'application/json'}
)

try:
    with urllib.request.urlopen(req) as resp:
        print('HTTP Start Status:', resp.status)
        start_data = json.loads(resp.read().decode())
        print('Review ID:', start_data.get('review_id'))
        print('Status:', start_data.get('status'))
        print('Pending pages:', start_data.get('pending_pages'))
except urllib.error.HTTPError as e:
    print('HTTP Error on start:', e.code, e.read().decode())
    exit(1)

# 2. Monitor execution
for i in range(50):
    time.sleep(2)
    poll_req = urllib.request.Request(f'http://127.0.0.1:8000/runs/{run_id}/security')
    with urllib.request.urlopen(poll_req) as resp:
        data = json.loads(resp.read().decode())
    status = data.get('status')
    completed = data.get('completed_pages', 0)
    pending = data.get('pending_pages', 0)
    findings = len(data.get('findings', []))
    print(f'Poll {i+1}: status={status}, completed={completed}, pending={pending}, findings={findings}')
    if status not in {'running', 'paused', 'stopping'}:
        print('\n=== DAST SCAN COMPLETE ===')
        print('Terminal status reached:', status)
        print('Summary:', json.dumps(data.get('summary'), indent=2))
        print('Endpoints assessed:', len(data.get('endpoints', [])))
        for ep in data.get('endpoints', []):
            print(f"  {ep.get('method')} {ep.get('url')} [HTTP {ep.get('status')}]")
        print('Warnings:', data.get('warnings'))
        print(f'\nTotal Findings Recorded: {len(data.get("findings", []))}')
        for f in data.get('findings', []):
            print(f" - [{f.get('severity', '').upper()}] {f.get('title')} ({f.get('rule')})")
            print(f"   URL: {f.get('url')}")
            print(f"   OWASP: {f.get('owasp_mapping', f.get('owasp'))} | CWE: {f.get('cwe')}")
            print(f"   Evidence: {f.get('evidence')}")
            print(f"   Remediation: {f.get('remediation')}\n")
        break
