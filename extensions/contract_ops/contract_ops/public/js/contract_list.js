// Add the sales entry point without replacing native status indicators or list behavior.
(() => {
    const settings = frappe.listview_settings.Contract || {};
    const previous = settings.onload;
    settings.onload = function(listview) {
        if (previous) previous.call(this, listview);
        if (frappe.model.can_create('Contract')) {
            listview.page.add_inner_button(__('新增销售合同'), () => frappe.new_doc('Contract', {
                party_type: 'Customer', contract_ops_enabled: 1,
            }));
        }
    };
    frappe.listview_settings.Contract = settings;
})();
