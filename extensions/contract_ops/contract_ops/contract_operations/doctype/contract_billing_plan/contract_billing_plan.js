frappe.ui.form.on('Contract Billing Plan', {
    refresh(frm) {
        frm.set_query('sales_contract', () => ({filters: {docstatus: 1,...(frm.doc.source_collection_plan ? {} : {billing_flow:'先票后款'})}}));
        if (frm.doc.source_collection_plan) {
            frm.set_intro(__('本期开票计划由实际到账生成；取消来源收款前必须先处理已开税票。资金依据保留在下方快照中。'));
            ['sales_contract','plan_title','period_start','period_end','invoice_due_date','planned_amount','disabled'].forEach(f=>frm.set_df_property(f,'read_only',1));
        }
        if (!frm.is_new()) frm.add_custom_button(__('登记本期开票'), () => {
            frappe.db.get_doc('Contract',frm.doc.sales_contract).then(contract => {
                frappe.new_doc('VAT Invoice Record',{
                    sales_contract:contract.name, company:contract.company, customer:contract.party_name,
                    currency:contract.currency, gross_amount:Math.max(0, frm.doc.planned_amount-frm.doc.billed_amount),
                }, (doc) => {
                    const row = frappe.model.add_child(doc, 'VAT Plan Allocation', 'plan_allocations');
                    row.billing_plan = frm.doc.name;
                    row.currency = contract.currency;
                    row.allocated_amount = Math.max(0, frm.doc.planned_amount-frm.doc.billed_amount);
                });
            });
        });
        if(!frm.is_new())frm.add_custom_button(__('合并登记税票'),()=>{
            frappe.db.get_doc('Contract',frm.doc.sales_contract).then(contract=>
                frappe.contract_ops.open_merge({company:contract.company,customer:contract.party_name,currency:contract.currency,billing_plan:frm.doc.name}));
        });
        if (!frm.is_new() && !frm.doc.source_collection_plan) frm.add_custom_button(__('回填历史已开票'), () => {
            const d = new frappe.ui.Dialog({title: __('历史计划初始化'), fields: [
                {fieldname:'vat_invoice',label:__('已提交税票'),fieldtype:'Link',options:'VAT Invoice Record',reqd:1},
                {fieldname:'sales_invoice',label:__('对应已提交应收发票'),fieldtype:'Link',options:'Sales Invoice',reqd:1},
                {fieldname:'allocated_amount',label:__('本期回填金额'),fieldtype:'Currency',default:Math.max(0,frm.doc.planned_amount-frm.doc.billed_amount),reqd:1},
            ], primary_action_label: __('建立关联'), primary_action(values) {
                frappe.call('contract_ops.planning.backfill_historical_vat',{billing_plan:frm.doc.name,...values}).then(()=>{d.hide();frm.reload_doc();});
            }}); d.show();
        });
    },
});
