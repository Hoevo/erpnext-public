"""Read-only real-browser acceptance of the imported register and contract navigation."""
import argparse,json,subprocess
from pathlib import Path
from urllib.parse import quote
from playwright.sync_api import sync_playwright

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1:8261');args=parser.parse_args()
    root=Path(__file__).resolve().parents[3];artifacts=root/'.local/browser';artifacts.mkdir(exist_ok=True)
    raw=subprocess.check_output(['wsl','-d','ERPNextDev','-u','root','--exec','cat','/var/lib/erpnext/v17-native-contract/test.env']).decode('utf-8')
    config=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(args.base+'/login',wait_until='networkidle');page.locator('#login_email').fill('Administrator')
            page.locator('#login_password').fill(config['ADMIN_PASSWORD']);page.locator('.form-login button[type=submit]').click()
            page.wait_for_url(lambda u:'/login' not in str(u))
            response=page.request.get(args.base+'/api/resource/Public%20Income%20Project',params={'fields':json.dumps(['name','source_row','annual_reference','amount_status']),'limit_page_length':1000})
            assert response.ok,response.text();projects=response.json()['data'];assert len(projects)==52,len(projects)
            project=next(x for x in projects if x['source_row']==24)
            page.goto(args.base+'/desk/contract-operations/public-income-project',wait_until='networkidle')
            page.wait_for_function("cur_list?.doctype === 'Public Income Project'")
            page.get_by_text('公益性收入项目台账',exact=True).first.wait_for()
            print(json.dumps(dict(language=page.evaluate('frappe.boot.lang'),sidebar=page.locator('.desk-sidebar').inner_text() if page.locator('.desk-sidebar').count() else ''),ensure_ascii=False),flush=True)
            page.screenshot(path=str(artifacts/'income-project-list.png'),full_page=True)
            page.goto(args.base+'/desk/contract-operations/public-income-project/'+quote(project['name']),wait_until='networkidle')
            page.get_by_role('button',name='关联年度合同',exact=True).wait_for()
            assert page.evaluate('cur_frm.doc.annual_reference') in (None,''),project
            assert page.evaluate('cur_frm.doc.amount_status')=='非年度计价'
            assert page.locator('[data-fieldname="pricing_text"] input').input_value()=='50块钱一小时'
            page.locator('[data-fieldname="contracts_html"]').scroll_into_view_if_needed()
            page.screenshot(path=str(artifacts/'income-project-contracts.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(artifacts/'income-project-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            page.get_by_role('button',name='关联年度合同',exact=True).click()
            page.wait_for_function("cur_frm?.doc?.doctype === 'Public Income Contract Link' && cur_frm.doc.__islocal")
            assert page.evaluate('cur_frm.doc.income_project')==project['name']
            assert not errors,errors
            result=dict(status='passed',projects=52,nonannual_amount_preserved=True,contract_link_entry=True,js_errors=errors,base=args.base)
            (root/'.local'/('income-ui-'+args.base.rsplit(':',1)[-1]+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result))
        except Exception:
            page.screenshot(path=str(artifacts/'income-project-failure.png'),full_page=True)
            (root/'.local/income-ui-failure.txt').write_text(page.locator('body').inner_text(),encoding='utf-8');raise
        finally:browser.close()

if __name__=='__main__':main()
