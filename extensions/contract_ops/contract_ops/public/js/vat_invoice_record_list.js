frappe.listview_settings['VAT Invoice Record']={
    onload(list){list.page.add_inner_button(__('按客户合并开票'),()=>frappe.contract_ops.open_merge());},
};
