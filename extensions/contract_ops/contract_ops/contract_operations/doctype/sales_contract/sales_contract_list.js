frappe.listview_settings['Sales Contract'] = {
    add_fields:['renewal_health','renewal_enabled','renewal_mode'],
    onload(listview) {
        listview.page.add_inner_button('续签规则待核实',()=>listview.filter_area.add([[listview.doctype,'renewal_mode','=','待核实']]));
        listview.page.add_inner_button('已启用续签提醒',()=>listview.filter_area.add([[listview.doctype,'renewal_enabled','=',1]]));
    },
};
