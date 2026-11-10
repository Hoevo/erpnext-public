frappe.ui.form.on('Contract Collection Plan', {
    refresh(frm) {
        frm.set_query('sales_contract',()=>({filters:{docstatus:1,billing_flow:'先款后票'}}));
        if (frm.is_new()) return;
        frm.add_custom_button(__('登记本期到账'),()=>{
            frappe.db.get_doc('Sales Contract',frm.doc.sales_contract).then(contract=>{
                frappe.new_doc('Payment Entry',{
                    payment_type:'Receive',party_type:'Customer',company:contract.company,party:contract.customer,
                },doc=>{
                    const row=frappe.model.add_child(doc,'Contract Collection Allocation','custom_collection_allocations');
                    row.collection_plan=frm.doc.name;
                    row.currency=contract.currency;
                    row.allocated_amount=Math.max(0,frm.doc.planned_amount-frm.doc.received_amount);
                });
            });
        });
        frm.add_custom_button(__('查看到账后开票计划'),()=>frappe.set_route('List','Contract Billing Plan',{source_collection_plan:frm.doc.name}));
    },
});
