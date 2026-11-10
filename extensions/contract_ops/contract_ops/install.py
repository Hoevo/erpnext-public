import frappe
import json
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def has_app_permission():
    return frappe.has_permission("Sales Contract", ptype="read")


def setup():
    create_custom_fields({
        "Sales Invoice": [
            {
                "fieldname": "custom_sales_contract", "label": "销售合同",
                "fieldtype": "Link", "options": "Sales Contract", "insert_after": "customer",
                "no_copy": 1, "allow_on_submit": 1,
            },
            {
                "fieldname": "custom_billing_period_start", "label": "计费开始日期",
                "fieldtype": "Date", "insert_after": "custom_sales_contract", "allow_on_submit": 1,
            },
            {
                "fieldname": "custom_billing_period_end", "label": "计费结束日期",
                "fieldtype": "Date", "insert_after": "custom_billing_period_start", "allow_on_submit": 1,
            },
        ],
        "Payment Entry": [{
            "fieldname": "custom_contract_group", "label": "合同组（仅未分配预收款）",
            "fieldtype": "Link", "options": "Contract Group", "insert_after": "party",
            "allow_on_submit": 1,
        },{
            "fieldname":"custom_actual_receipt_date","label":"银行实际到账日期（先款后票）",
            "fieldtype":"Date","insert_after":"custom_contract_group",
            "description":"先款后票分配时必填，按银行证据确认，不以记账日期代替。",
        },{
            "fieldname":"custom_collection_allocations","label":"先款后票收款期次分配",
            "fieldtype":"Table","options":"Contract Collection Allocation","insert_after":"custom_actual_receipt_date",
            "description":"分配本合同已核销款或本笔未分配预收款；本表只描述到账归属，不重复记账，不能占用其他合同款项。",
        }],
    }, update=True)
    # The public workspace only contains links; underlying DocType permissions apply.
    if not frappe.db.exists("Workspace", "合同与票款"):
        frappe.get_doc({
            "doctype": "Workspace", "name": "合同与票款", "label": "合同与票款",
            "title": "合同与票款", "module": "Contract Operations", "public": 1,
            "icon": "file", "content": json.dumps([{
                "id": "contract-ops-card", "type": "card",
                "data": {"card_name": "第一期 · 销售合同与票款", "col": 4},
            }], ensure_ascii=False),
            "links": [
                {"type": "Card Break", "label": "第一期 · 销售合同与票款"},
                *[{"type": "Link", "link_type": "DocType", "label": label, "link_to": target}
                  for label, target in (("合同组", "Contract Group"), ("销售合同", "Sales Contract"), ("税票登记", "VAT Invoice Record"),
                                        ("应收发票", "Sales Invoice"), ("收款与核销", "Payment Entry"))],
            ],
        }).insert(ignore_permissions=True)
    else:
        workspace = frappe.get_doc("Workspace", "合同与票款")
        if not any(link.link_to == "Contract Group" for link in workspace.links):
            workspace.append("links", {"type": "Link", "link_type": "DocType", "label": "合同组", "link_to": "Contract Group"})
            workspace.save(ignore_permissions=True)
    if not frappe.db.exists('Notification Type', 'Contract Renewal'):
        frappe.get_doc(dict(doctype='Notification Type', type_name='Contract Renewal', enabled=1)).insert(ignore_permissions=True)
    for row in frappe.get_all('Sales Contract', fields=['name','renewal_mode','renewal_health']):
        updates = {}
        if not row.renewal_mode:
            updates['renewal_mode'] = '待核实'
        if not row.renewal_health:
            updates['renewal_health'] = '续签方式待核实'
        if updates:
            frappe.db.set_value('Sales Contract', row.name, updates, update_modified=False)
    income_links=(("公益性收入项目台账","Public Income Project"),("项目年度合同关联","Public Income Contract Link"),("收入清单来源与缺项","Public Income Import"),
                  ("续签与到期跟进", "Contract Renewal Cycle"), ("续签提醒发送记录", "Contract Renewal Notice"))
    for label,target in income_links:
        if not frappe.db.exists('Translation',{'language':'zh','source_text':target}):
            frappe.get_doc(dict(doctype='Translation',language='zh',source_text=target,translated_text=label)).insert(ignore_permissions=True)
    workspace=frappe.get_doc('Workspace','合同与票款')
    changed=False
    for label,target in income_links:
        if not any(link.link_to==target for link in workspace.links):
            workspace.append('links',dict(type='Link',link_type='DocType',label=label,link_to=target));changed=True
    if changed:workspace.save(ignore_permissions=True)
    for sidebar_type in ('Sidebar','Workspace Sidebar'):
        if not frappe.db.exists('DocType',sidebar_type):continue
        for name in frappe.get_all(sidebar_type,pluck='name'):
            sidebar=frappe.get_doc(sidebar_type,name)
            if 'contract' not in name.lower() and '合同' not in name:continue
            changed=False
            for label,target in income_links:
                if not any(row.get('link_to')==target for row in sidebar.items):
                    sidebar.append('items',dict(label=label,type='Link',link_type='DocType',link_to=target,icon='folder'));changed=True
            if changed:sidebar.save(ignore_permissions=True)
