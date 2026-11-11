"""Read/write acceptance is restricted to the isolated flows-test frontend on 8261."""
import json
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright


def main():
    root=Path(__file__).resolve().parents[3]
    raw=__import__('subprocess').check_output(['wsl','-d','ERPNextDev','-u','root','--exec','cat','/var/lib/erpnext/v17-native-contract/test.env']).decode('utf-8')
    config=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    c={'User':'Administrator','Password':config['ADMIN_PASSWORD']}
    base='http://127.0.0.1:8261';s=requests.Session()
    s.post(base+'/api/method/login',data={'usr':c['User'],'pwd':c['Password']}).raise_for_status()
    def get(path,**params):
        r=s.get(base+path,params=params);r.raise_for_status();return r.json()
    cp=get('/api/resource/Contract Collection Plan',fields=json.dumps(['*']),filters=json.dumps({'invoice_trigger':'每笔到账'}),order_by='creation desc',limit_page_length=1)['data'][0]
    contract=get('/api/resource/Contract/'+cp['sales_contract'])['data']
    company=get('/api/resource/Company/'+contract['company'])['data']
    amount=cp['outstanding_amount'];assert amount>0
    payload=dict(doctype='Payment Entry',payment_type='Receive',company=company['name'],posting_date='2026-11-09',
        party_type='Customer',party=contract['party_name'],paid_from=company['default_receivable_account'],paid_to=company['default_bank_account'],
        paid_from_account_currency='CNY',paid_to_account_currency='CNY',paid_amount=amount,received_amount=amount,
        source_exchange_rate=1,target_exchange_rate=1,reference_no='TEST-UI-FLOW-'+datetime.now().strftime('%H%M%S'),
        reference_date='2026-11-09',custom_actual_receipt_date='2026-11-09',
        custom_collection_allocations=[dict(collection_plan=cp['name'],allocated_amount=amount)])
    r=s.post(base+'/api/resource/Payment Entry',json=payload);r.raise_for_status();payment=r.json()['data']['name']
    artifacts=root/'.local/two-flows-browser';artifacts.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1050})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
            page.goto(base+'/login',wait_until='networkidle')
            page.locator('#login_email').fill(c['User']);page.locator('#login_password').fill(c['Password'])
            page.locator('.form-login button[type=submit]').click();page.wait_for_url(lambda u:'/login' not in str(u))
            page.goto(base+'/desk/contract-operations/contract/'+contract['name'],wait_until='networkidle')
            page.get_by_role('button',name='新增先款后票收款计划',exact=True).wait_for()
            page.get_by_role('link',name=cp['plan_title'],exact=True).wait_for()
            page.screenshot(path=str(artifacts/'payment-first-contract.png'),full_page=True)
            page.get_by_role('link',name=cp['plan_title'],exact=True).click()
            page.wait_for_function("cur_frm.doctype==='Contract Collection Plan'")
            page.get_by_role('button',name='登记本期到账',exact=True).click()
            page.wait_for_function("cur_frm.doctype==='Payment Entry' && cur_frm.is_new()")
            assert page.evaluate('cur_frm.doc.custom_collection_allocations.length')==1
            page.goto(base+'/desk/contract-operations/payment-entry/'+payment,wait_until='networkidle')
            page.wait_for_function("cur_frm.doctype==='Payment Entry' && cur_frm.doc.docstatus===0")
            page.get_by_role('button',name='提交',exact=True).click()
            page.get_by_role('dialog').get_by_role('button',name='是',exact=True).click()
            page.wait_for_function('cur_frm.doc.docstatus===1')
            generated=get('/api/resource/Contract Billing Plan',fields=json.dumps(['name','invoice_due_date','planned_amount']),filters=json.dumps({'source_payment':payment,'source_cancelled':0}))['data']
            assert len(generated)==1 and generated[0]['invoice_due_date']=='2026-11-11'
            assert generated[0]['planned_amount']==amount
            bp=generated[0]['name']
            page.goto(base+'/desk/contract-operations/contract-billing-plan/'+bp,wait_until='networkidle')
            page.wait_for_function("cur_frm.doctype==='Contract Billing Plan'")
            page.screenshot(path=str(artifacts/'cash-generated-invoice-plan.png'),full_page=True)
            page.get_by_role('button',name='登记本期开票',exact=True).click()
            page.wait_for_function("cur_frm.doctype==='VAT Invoice Record' && cur_frm.is_new()")
            page.locator('[data-fieldname="invoice_number"] input:visible').fill('TEST-UI-AFTER-CASH-'+datetime.now().strftime('%H%M%S'))
            page.locator('[data-fieldname="tax_amount"] input:visible').fill('0')
            page.locator('[data-fieldname="tax_amount"] input:visible').press('Tab')
            page.get_by_role('button',name='保存',exact=True).click()
            page.wait_for_function("cur_frm.doctype==='VAT Invoice Record' && !cur_frm.is_new() && !cur_frm.is_dirty()")
            page.get_by_role('button',name='提交',exact=True).click();page.get_by_role('dialog').get_by_role('button',name='是',exact=True).click()
            page.wait_for_function('cur_frm.doc.docstatus===1');vat=page.evaluate('cur_frm.doc.name')
            assert get('/api/resource/Contract Receipt Plan',filters=json.dumps({'source_vat':vat}))['data']==[]
            final=get('/api/resource/Contract Billing Plan/'+bp)['data'];assert final['status']=='已开齐'
            page.set_viewport_size({'width':390,'height':844})
            page.goto(base+'/desk/contract-operations/contract/'+contract['name'],wait_until='networkidle')
            page.get_by_role('link',name=cp['plan_title'],exact=True).wait_for()
            page.screenshot(path=str(artifacts/'payment-first-mobile.png'),full_page=True)
            assert not errors,errors
            result=dict(status='passed',contract=contract['name'],payment=payment,billing_plan=bp,vat=vat,errors=errors,
                checks=['payment-first contract table','period to payment prefill','actual cash submit',
                    'automatic invoice obligation date/amount','generated source evidence','VAT submit',
                    'no circular receipt plan','invoice completion status','mobile render'])
            (artifacts/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=True))
        except Exception:
            page.screenshot(path=str(artifacts/'failure.png'),full_page=True)
            print(json.dumps({'url':page.url,'body':page.locator('body').inner_text(),'errors':errors},ensure_ascii=True),flush=True)
            raise
        finally:browser.close()


if __name__=='__main__':main()
