"""Real-browser renewal workflow; mutations restricted to the disposable 8261 site."""
import argparse
import json
import subprocess
from uuid import uuid4
from pathlib import Path
from urllib.parse import quote
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8261')
    parser.add_argument('--read-only', action='store_true')
    args = parser.parse_args()
    assert args.read_only or args.base == 'http://127.0.0.1:8261'
    root = Path(__file__).resolve().parents[3]
    artifacts = root / '.local/browser'
    artifacts.mkdir(exist_ok=True)
    raw = subprocess.check_output(['wsl','-d','ERPNextDev','-u','root','--exec','cat','/var/lib/erpnext/v17-native-contract/test.env']).decode('utf-8')
    config = dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors = []
        page.on('pageerror', lambda err: errors.append(str(err)))
        try:
            page.goto(args.base+'/login',wait_until='networkidle')
            page.locator('#login_email').fill('Administrator')
            page.locator('#login_password').fill(config['ADMIN_PASSWORD'])
            page.locator('.form-login button[type=submit]').click()
            page.wait_for_url(lambda u:'/login' not in str(u))
            def api(dt, **params):
                response = page.request.get(args.base+'/api/resource/'+quote(dt), params=params)
                assert response.ok, response.text()
                return response.json()['data']
            params = dict(fields=json.dumps(['name']), limit_page_length=1, order_by='creation desc')
            if not args.read_only:
                params['filters'] = json.dumps([['contract_title','like','TEST Renewal %'],['renewal_mode','=','到期协商续签'],['renewal_enabled','=',1]])
            contract = api('Contract', **params)[0]['name']
            page.goto(args.base+'/desk/contract-operations/contract/'+quote(contract),wait_until='networkidle')
            page.wait_for_function("cur_frm?.doc?.name === " + json.dumps(contract))
            page.locator('.renewal-panel').wait_for()
            assert '提醒配置' in page.locator('.renewal-panel').inner_text()
            page.locator('[data-fieldname="renewal_section"]').scroll_into_view_if_needed()
            page.screenshot(path=str(artifacts/('renewal-contract-'+('live' if args.read_only else 'test')+'.png')),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.reload(wait_until='networkidle')
            page.locator('.renewal-panel').wait_for()
            page.locator('[data-fieldname="renewal_section"]').scroll_into_view_if_needed()
            page.screenshot(path=str(artifacts/'renewal-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            page.reload(wait_until='networkidle')
            page.locator('.renewal-panel').wait_for()
            if not args.read_only:
                cycle = api('Contract Renewal Cycle', fields=json.dumps(['name']), filters=json.dumps({'sales_contract':contract,'status':['in',['待处理','洽谈中']]}))[0]['name']
                page.locator('.renewal-panel a').filter(has_text='待处理').or_(page.locator('.renewal-panel a').filter(has_text='洽谈中')).first.click()
                page.get_by_role('button',name='登记续签办理',exact=True).wait_for()
                page.get_by_role('button',name='登记续签办理',exact=True).click()
                dialog=page.get_by_role('dialog')
                dialog.locator('[data-fieldname="outcome"] select').select_option(label='洽谈中')
                evidence = 'ISOLATED BROWSER TEST: customer contacted; negotiation '+uuid4().hex[:8]
                dialog.locator('[data-fieldname="evidence"] textarea').fill(evidence)
                dialog.locator('[data-fieldname="evidence"] textarea').press('Tab')
                page.wait_for_function("cur_dialog?.get_value('evidence')?.includes('ISOLATED BROWSER TEST')")
                with page.expect_response(lambda response: 'contract_ops.renewal.handle_cycle' in response.url) as saved:
                    dialog.get_by_role('button',name='保存办理结果',exact=True).click()
                assert saved.value.ok, saved.value.text()
                page.wait_for_function("cur_frm?.doc?.status === '洽谈中'")
                page.screenshot(path=str(artifacts/'renewal-handled.png'),full_page=True)
                check=api('Contract Renewal Cycle/'+cycle)
                assert check['status']=='洽谈中'
                assert check['evidence'] == evidence
                notices=api('Contract Renewal Notice',filters=json.dumps({'cycle':cycle}),fields=json.dumps(['notification']))
                assert notices and notices[0]['notification']
            page.goto(args.base+'/desk/contract-operations/contract-renewal-cycle',wait_until='networkidle')
            page.wait_for_function("cur_list?.doctype === 'Contract Renewal Cycle'")
            page.get_by_role('button',name='已逾期未处理',exact=True).wait_for()
            page.screenshot(path=str(artifacts/'renewal-list.png'),full_page=True)
            jobs=api('Scheduled Job Type',filters=json.dumps({'method':'contract_ops.renewal.daily'}),fields=json.dumps(['name','frequency','stopped','last_execution']))
            assert len(jobs)==1 and jobs[0]['frequency']=='Daily' and not jobs[0]['stopped'],jobs
            settings=api('System Settings/System Settings')
            assert settings['time_zone']=='Asia/Shanghai',settings['time_zone']
            assert not errors, errors
            result=dict(status='passed',base=args.base,read_only=args.read_only,contract=contract,js_errors=errors,scheduler=jobs[0],time_zone=settings['time_zone'])
            (root/'.local'/('renewal-ui-'+args.base.rsplit(':',1)[-1]+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result))
        except Exception:
            page.screenshot(path=str(artifacts/'renewal-failure.png'),full_page=True)
            (root/'.local/renewal-ui-failure.txt').write_text(page.locator('body').inner_text(),encoding='utf-8')
            raise
        finally:
            browser.close()


if __name__ == '__main__':
    main()
