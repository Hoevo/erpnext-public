frappe.ui.form.on('Contract Renewal Cycle', {
    refresh(frm) {
        if (!['待处理', '洽谈中'].includes(frm.doc.status) || !frappe.model.can_write('Contract')) return;
        frm.add_custom_button('登记续签办理', () => {
            const dialog = new frappe.ui.Dialog({
                title: '登记续签／到期办理',
                fields: [
                    {fieldname:'outcome', label:'办理结果', fieldtype:'Select', options:'洽谈中\n已签新合同\n已确认按条款续期\n已发出不续签通知\n已终止', reqd:1},
                    {fieldname:'handled_on', label:'处理日期', fieldtype:'Date', default:frappe.datetime.get_today(), reqd:1},
                    {fieldname:'new_contract', label:'续签新合同', fieldtype:'Link', options:'Contract', depends_on:'eval:doc.outcome === "已签新合同"', mandatory_depends_on:'eval:doc.outcome === "已签新合同"', get_query:()=>({filters:{docstatus:1, party_type:'Customer', contract_ops_enabled:1, company:frm.doc.company, party_name:frm.doc.customer}})},
                    {fieldname:'evidence', label:'处理说明及依据', fieldtype:'Small Text', reqd:1},
                    {fieldname:'evidence_file', label:'处理附件', fieldtype:'Attach'},
                ],
                primary_action_label:'保存办理结果',
                async primary_action(values) {
                    await frappe.call({method:'contract_ops.renewal.handle_cycle', args:{cycle_name:frm.doc.name, ...values}, freeze:true});
                    dialog.hide(); frm.reload_doc();
                },
            });
            dialog.show();
        });
        frm.add_custom_button('查看原合同', () => frappe.set_route('Form', 'Contract', frm.doc.sales_contract));
    },
});
