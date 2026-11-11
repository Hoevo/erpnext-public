frappe.ui.form.on('Contract Receipt Plan', {
    refresh(frm) {
        frm.set_query('sales_invoice', () => ({filters:{custom_sales_contract:frm.doc.sales_contract,docstatus:1}}));
        frm.set_intro(__('这是跟进计划，不是收款凭证。确认收票后才计算工作日到期日；实际收款请通过 Payment Entry 核销。'));
    },
});
