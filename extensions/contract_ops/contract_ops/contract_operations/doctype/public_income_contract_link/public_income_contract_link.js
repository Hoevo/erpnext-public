frappe.ui.form.on('Public Income Contract Link',{
    setup(frm){frm.set_query('sales_contract',()=>({filters:{docstatus:1}}));frm.set_query('previous_contract',()=>({filters:{docstatus:1}}));}
});
