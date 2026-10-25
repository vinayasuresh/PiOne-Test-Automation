import sys
sys.path.insert(0, '.')
from api.services.security_scan_service import fetch_page, analyze_response, RULES

res = fetch_page('https://www.saucedemo.com/')
items, parser = analyze_response('https://www.saucedemo.com/', res, RULES)
print(f"Findings on SauceDemo root ({len(items)}):")
for item in items:
    print(f" - {item['rule']} : {item['title']} [{item.get('severity')}] (OWASP: {item.get('owasp')})")

print("Links observed:", parser.links)
print("Forms observed:", parser.forms)
