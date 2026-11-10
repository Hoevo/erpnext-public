"""Exercise combined invoicing on the disposable clone through the real UI."""
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[3]
BASE='http://127.0.0.1:8261'

def main():
    raw=subprocess.check_output(['wsl','-d','ERPNextDev','-u','root','--exec','cat','/var/lib/erpnext/v17-native-contract/test.env']).decode('utf-8')
    config=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    evidence=ROOT/'.local/browser';evidence.mkdir(exist_ok=True,parents=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(BASE+'/login',wait_until='networkidle')
            page.locator('#login_email').fill('Administrator');page.locator('#login_password').fill(config['ADMIN_PASSWORD'])
            page.locator('.form-login button[type=submit]').click()
            page.wait_for_url(lambda u:'/login' not in str(u),timeout=60000)
            page.goto(BASE+'/desk/contract-operations/vat-invoice-record',wait_until='networkidle')
            page.get_by_role('button',name='按客户合并开票',exact=True).click()
            dialog=page.locator('.modal:visible')
            for field,value in [('company','Contract Ops Test Company'),('customer','Contract Ops Test Customer')]:
                dialog.locator(f'[data-fieldname="{field}"] input').fill(value)
                dialog.locator(f'[data-fieldname="{field}"] input').press('Tab')
            dialog.get_by_role('button',name='加载可开票期次',exact=True).click()
            page.wait_for_function("cur_dialog?.fields_dict.plans.df.data?.length > 0")
            rows=dialog.locator('.grid-body .grid-row')
            count=rows.count();assert count>=2,count
            for row in rows.all():
                row.locator('[data-fieldname="include"]').first.click()
                (ROOT/'.local/merged-grid.html').write_text(row.inner_html(),encoding='utf-8')
                check=row.locator('input[type="checkbox"]:enabled').filter(visible=True)
                if check.count():check.last.check()
                else:raise AssertionError('No editable merge checkbox')
            page.screenshot(path=str(evidence/'merged-vat-select.png'),full_page=True)
            dialog.get_by_role('button',name='生成一张税票草稿',exact=True).click()
            page.wait_for_function("cur_frm?.doc?.doctype === 'VAT Invoice Record' && cur_frm.doc.__islocal && cur_frm.doc.plan_allocations?.length >= 2")
            data=page.evaluate("({plans:cur_frm.doc.plan_allocations.length,payments:cur_frm.doc.payment_allocations.length,gross:cur_frm.doc.gross_amount,contract:cur_frm.doc.sales_contract,group:cur_frm.doc.contract_group})")
            assert data['payments']>=4,data
            assert not data['contract'] and not data['group'],data
            number='TEST-MERGE-UI-'+page.evaluate('Date.now().toString()')
            page.locator('[data-fieldname="invoice_number"] input').fill(number)
            page.locator('[data-fieldname="tax_amount"] input').fill('0')
            page.locator('[data-fieldname="tax_amount"] input').press('Tab')
            page.get_by_role('button',name='保存',exact=True).click()
            page.wait_for_function("cur_frm.doc.name.startsWith('VAT-') && !cur_frm.is_dirty()")
            page.get_by_role('button',name='提交',exact=True).click()
            page.get_by_role('button',name='是',exact=True).click()
            page.wait_for_function('cur_frm.doc.docstatus === 1')
            page.wait_for_function("cur_frm.fields_dict.relationship_html.$wrapper.text().includes('本票资金分配')")
            name=page.evaluate('cur_frm.doc.name')
            page.screenshot(path=str(evidence/'merged-vat-submitted.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(evidence/'merged-vat-mobile.png'),full_page=True)
            assert not errors,errors
            result=dict(status='passed',vat=name,**data,js_errors=errors)
            (ROOT/'.local/merged-vat-ui-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result))
        except Exception:
            page.screenshot(path=str(evidence/'merged-vat-failure.png'),full_page=True)
            (ROOT/'.local/merged-vat-failure.txt').write_text(page.locator('body').inner_text(),encoding='utf-8')
            raise
        finally:browser.close()

if __name__=='__main__':main()
