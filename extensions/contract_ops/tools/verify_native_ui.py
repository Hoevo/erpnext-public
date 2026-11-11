"""Native form, sales navigation and legacy evidence through one real browser."""
import json
import subprocess
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
BASE = 'http://127.0.0.1:8261'


def main():
    raw = subprocess.check_output(['wsl', '-d', 'ERPNextDev', '-u', 'root', '--exec', 'cat',
        '/var/lib/erpnext/v17-native-contract/test.env']).decode('utf-8')
    config = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    suites = json.loads((ROOT / '.local/v17-suites.json').read_text(encoding='utf-8'))
    native = suites['test_native_contract']['browser_contract']
    artifacts = ROOT / '.local/native-browser'
    artifacts.mkdir(exist_ok=True, parents=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        errors = []
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(BASE + '/login', wait_until='networkidle')
            page.locator('#login_email').fill('Administrator')
            page.locator('#login_password').fill(config['ADMIN_PASSWORD'])
            page.locator('.form-login button[type=submit]').click()
            page.wait_for_url(lambda url: '/login' not in str(url), timeout=60000)

            def open_contract(name):
                page.goto(BASE + '/desk/contract-operations/contract/' + quote(name), wait_until='networkidle')
                page.wait_for_function("name => cur_frm?.doctype === 'Contract' && cur_frm.doc.name === name", arg=name)
                page.locator('.renewal-panel').wait_for()

            open_contract(native)
            page.wait_for_function("frappe.realtime?.socket?.connected === true", timeout=30000)
            state = page.evaluate("({signed:cur_frm.doc.is_signed, fulfilment:cur_frm.doc.fulfilment_status, template:cur_frm.doc.contract_template, fields:['party_name','contract_template','signee','signed_on','fulfilment_terms','contract_amount'].every(f=>!!cur_frm.fields_dict[f])})")
            assert state['signed'] == 1 and state['fulfilment'] == 'Fulfilled' and state['template'] and state['fields'], state
            page.screenshot(path=str(artifacts / 'native-contract-desktop.png'), full_page=True)
            page.get_by_role('button', name='新增开票计划', exact=True).click()
            page.wait_for_function("cur_frm?.doctype === 'Contract Billing Plan' && cur_frm.is_new()")
            assert page.evaluate('cur_frm.doc.sales_contract') == native
            response = page.request.get(BASE + '/api/resource/Contract', params=dict(
                fields=json.dumps(['name']), filters=json.dumps({'legacy_sales_contract': ['is', 'set']}), limit_page_length=1))
            assert response.ok, response.status
            legacy = response.json()['data'][0]['name']
            open_contract(legacy)
            old = page.evaluate("({name:cur_frm.doc.name, source:cur_frm.doc.legacy_sales_contract, signed:cur_frm.doc.is_signed, snapshot:!!cur_frm.doc.legacy_source_snapshot, customer:cur_frm.doc.party_name})")
            assert old['name'] == old['source'] and old['snapshot'] and old['customer'] and not old['signed'], old
            page.screenshot(path=str(artifacts / 'migrated-contract-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.reload(wait_until='networkidle')
            page.locator('.renewal-panel').wait_for()
            page.screenshot(path=str(artifacts / 'migrated-contract-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.goto(BASE + '/desk/contract-operations/contract', wait_until='networkidle')
            page.wait_for_function("cur_list?.doctype === 'Contract'")
            page.get_by_role('button', name='新增销售合同', exact=True).click()
            page.wait_for_function("cur_frm?.doctype === 'Contract' && cur_frm.is_new()")
            assert page.evaluate("cur_frm.doc.party_type === 'Customer' && !!cur_frm.doc.contract_ops_enabled")
            assert not errors, errors
            result = dict(status='passed', native_contract=native, migrated_contract=legacy, errors=errors,
                checks=['native signature and template fields', 'native fulfilment state', 'sales plan navigation',
                        'legacy identity and evidence', 'no invented signature', 'desktop and mobile', 'sales list entry'])
            (artifacts / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=True))
        except Exception:
            page.screenshot(path=str(artifacts / 'failure.png'), full_page=True)
            (artifacts / 'failure.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
            raise
        finally:
            browser.close()


if __name__ == '__main__':
    main()
