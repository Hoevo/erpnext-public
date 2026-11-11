"""Single-browser acceptance against the isolated planning site, never business data."""
import json
from datetime import datetime
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright


def main():
    root=Path(__file__).resolve().parents[3]
    raw=__import__('subprocess').check_output(['wsl','-d','ERPNextDev','-u','root','--exec','cat','/var/lib/erpnext/v17-native-contract/test.env']).decode('utf-8')
    environment=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    config={'User':'Administrator','Password':environment['ADMIN_PASSWORD']}
    base='http://127.0.0.1:8261'
    session=requests.Session()
    session.post(base+'/api/method/login',data={'usr':config['User'],'pwd':config['Password']}).raise_for_status()
    def get(path, **params):
        r=session.get(base+path,params=params);r.raise_for_status();return r.json()
    contract=get('/api/resource/Contract',fields=json.dumps(['name']),filters=json.dumps({'contract_title':'TEST Planning Lifecycle'}),order_by='creation desc',limit_page_length=1)['data'][0]['name']
    plans=get('/api/method/contract_ops.planning.get_contract_planning',sales_contract=contract)['message']
    first=plans['billing_plans'][0]
    invoice=next(r['sales_invoice'] for r in plans['receipt_plans'] if r['billing_plan']==first['name'] and not r['cancelled'])
    artifacts=root/'.local/planning-browser';artifacts.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            console_errors=[]
            page.on('console',lambda m: console_errors.append(m.text) if m.type=='error' else None)
            page.goto(base+'/login',wait_until='networkidle')
            page.locator('#login_email').fill(config['User']);page.locator('#login_password').fill(config['Password'])
            page.locator('.form-login button[type=submit]').click()
            page.wait_for_url(lambda u:'/login' not in str(u))
            page.goto(base+'/desk/contract-operations/contract/'+contract,wait_until='networkidle')
            page.get_by_role('link',name='TEST Quarter 1',exact=True).wait_for()
            page.screenshot(path=str(artifacts/'contract-plans.png'),full_page=True)
            page.get_by_role('link',name='TEST Quarter 1',exact=True).click()
            page.wait_for_function("window.cur_frm?.doctype === 'Contract Billing Plan'")
            page.get_by_role('button',name='登记本期开票',exact=True).click()
            page.wait_for_function("window.cur_frm?.doctype === 'VAT Invoice Record' && window.cur_frm.is_new()")
            assert page.evaluate('cur_frm.doc.plan_allocations.length')==1
            name='TEST-UI-'+datetime.now().strftime('%Y%m%d%H%M%S')
            page.locator('[data-fieldname="invoice_number"] input').fill(name)
            page.locator('[data-fieldname="gross_amount"] input').fill('100')
            page.locator('[data-fieldname="tax_amount"] input').fill('0')
            page.locator('[data-fieldname="notes"] textarea:visible').fill('ISOLATED UI ACCEPTANCE')
            page.locator('[data-fieldname="notes"] textarea:visible').press('Tab')
            page.wait_for_function("cur_frm.doc.notes === 'ISOLATED UI ACCEPTANCE'")
            page.get_by_role('button',name='保存',exact=True).click()
            page.wait_for_function("window.cur_frm?.doctype === 'VAT Invoice Record' && !window.cur_frm.is_new() && !window.cur_frm.is_dirty()")
            page.get_by_role('button',name='提交',exact=True).click()
            page.get_by_role('dialog').get_by_role('button',name='是',exact=True).click()
            page.wait_for_function("window.cur_frm?.doc?.docstatus===1")
            source=page.evaluate('cur_frm.doc.name')
            receipts=get('/api/resource/Contract Receipt Plan',filters=json.dumps({'source_vat':source}),fields=json.dumps(['name','status','expected_amount','due_date']))['data']
            assert len(receipts)==1 and receipts[0]['expected_amount']==100
            assert not receipts[0]['due_date']
            receipt=receipts[0]['name']
            page.goto(base+'/desk/contract-operations/contract-receipt-plan/'+receipt,wait_until='networkidle')
            page.wait_for_function("window.cur_frm?.doctype==='Contract Receipt Plan'")
            page.locator('[data-fieldname="sales_invoice"] input').fill(invoice)
            page.locator('[data-fieldname="sales_invoice"] input').press('Tab')
            page.locator('[data-fieldname="received_on"] input').fill(datetime.now().strftime('%Y-%m-%d'))
            page.locator('[data-fieldname="received_on"] input').press('Tab')
            page.get_by_role('button',name='保存',exact=True).click()
            page.wait_for_function("!cur_frm.is_dirty() && !!cur_frm.doc.due_date")
            assert page.evaluate('cur_frm.doc.status')=='待收款'
            page.screenshot(path=str(artifacts/'receipt-confirmed.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.goto(base+'/desk/contract-operations/contract/'+contract,wait_until='networkidle')
            page.get_by_role('link',name='TEST Quarter 1',exact=True).wait_for()
            page.screenshot(path=str(artifacts/'contract-mobile.png'),full_page=True)
            assert not errors,errors
            report=dict(status='passed',contract=contract,vat=source,receipt_plan=receipt,errors=errors,
                checks=['contract plan table','plan to VAT prefill','save and submit tax invoice','automatic receipt plan',
                        'await receipt confirmation','link receivable and confirm receipt','working due date','mobile render'])
            (artifacts/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=True))
        except Exception:
            page.screenshot(path=str(artifacts/'failure.png'),full_page=True)
            print(json.dumps({'failure_url':page.url,'errors':errors,'console_errors':console_errors,'doc':page.evaluate('cur_frm.doc'),'body':page.locator('body').inner_text()},ensure_ascii=True,default=str),flush=True)
            raise
        finally:
            browser.close()


if __name__=='__main__':
    main()
