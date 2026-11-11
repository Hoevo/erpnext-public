frappe.listview_settings['Contract Renewal Cycle'] = {
    add_fields:['status','deadline','health'],
    get_indicator(doc) {
        if (['待处理','洽谈中'].includes(doc.status)) {
            const overdue = doc.deadline < frappe.datetime.get_today();
            return [overdue ? '已逾期' : doc.status, overdue ? 'red' : 'orange', `status,=,${doc.status}`];
        }
        return [doc.status, ['已停用','规则已替换'].includes(doc.status) ? 'gray' : 'green', `status,=,${doc.status}`];
    },
    onload(listview) {
        listview.page.add_inner_button('待处理及洽谈中',()=>listview.filter_area.add([[listview.doctype,'status','in',['待处理','洽谈中']]]));
        listview.page.add_inner_button('已逾期未处理',()=>listview.filter_area.add([[listview.doctype,'status','in',['待处理','洽谈中']],[listview.doctype,'deadline','<',frappe.datetime.get_today()]]));
    },
};
