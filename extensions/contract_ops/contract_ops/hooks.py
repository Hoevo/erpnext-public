app_name = "contract_ops"
app_title = "合同与票款"
app_publisher = "Contract Ops Contributors"
app_description = "销售合同、税票登记及标准应收收款关联"
app_email = "admin@example.invalid"
app_license = "GPL-3.0-or-later"
app_home = "/desk/contract-operations/sales-contract"
add_to_apps_screen = [{
    "name": app_name,
    "title": app_title,
    "logo": "/assets/erpnext/images/erpnext-logo.svg",
    "route": app_home,
    "has_permission": "contract_ops.install.has_app_permission",
}]
required_apps = ["erpnext"]
app_include_js = ["/assets/contract_ops/js/merged_vat.js"]
doctype_list_js = {"VAT Invoice Record": "public/js/vat_invoice_record_list.js"}
after_install = "contract_ops.install.setup"
after_migrate = "contract_ops.install.setup"
scheduler_events = {"daily": ["contract_ops.renewal.daily"]}
notification_skip_email_types = ["Contract Renewal"]
has_permission = {"Contract Renewal Cycle": "contract_ops.renewal.cycle_permission", "Contract Renewal Notice": "contract_ops.renewal.notice_permission"}
permission_query_conditions = {"Contract Renewal Cycle": "contract_ops.renewal.cycle_query", "Contract Renewal Notice": "contract_ops.renewal.notice_query"}
doc_events = {
    "Payment Entry": {
        "validate": "contract_ops.prepayment.validate_payment_allocations",
        "before_cancel": "contract_ops.prepayment.before_payment_cancel",
        "on_submit": "contract_ops.planning.payment_changed",
        "on_cancel": "contract_ops.planning.payment_changed",
        "on_update_after_submit": "contract_ops.planning.payment_changed",
    },
    "Sales Invoice": {
        "validate": "contract_ops.events.validate_sales_invoice",
        "before_submit": "contract_ops.events.validate_sales_invoice",
    },
}
