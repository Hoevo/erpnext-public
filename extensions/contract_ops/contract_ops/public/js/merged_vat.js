frappe.provide('frappe.contract_ops');
frappe.contract_ops.open_merge = function(defaults={}) {
    const dialog=new frappe.ui.Dialog({
        title:__('按客户合并登记税票'),size:'extra-large',
        fields:[
            {fieldname:'company',label:__('开票公司'),fieldtype:'Link',options:'Company',reqd:1,default:defaults.company},
            {fieldname:'customer',label:__('客户'),fieldtype:'Link',options:'Customer',reqd:1,default:defaults.customer},
            {fieldname:'currency',label:__('币种'),fieldtype:'Link',options:'Currency',reqd:1,default:defaults.currency||'CNY'},
            {fieldname:'invoice_date',label:__('实际开票日期'),fieldtype:'Date',reqd:1,default:frappe.datetime.get_today()},
            {fieldname:'load',label:__('加载可开票期次'),fieldtype:'Button',click:async()=>{
                const v=dialog.get_values();if(!v)return;
                const {message:rows}=await frappe.call('contract_ops.vat_links.get_merge_candidates',{company:v.company,customer:v.customer,currency:v.currency});
                dialog.fields_dict.plans.df.data=rows.map(x=>({...x,include:x.billing_plan===defaults.billing_plan?1:0}));
                dialog.fields_dict.plans.grid.refresh();
            }},
            {fieldname:'scope_note',fieldtype:'HTML',options:'<p class="text-muted">勾选实际合并范围，可修改本次金额。同客户不代表同一合同或同一笔款；不同开票主体、客户或币种不能合并。合并不会改变原各期约定日期。</p>'},
            {fieldname:'plans',label:__('选择期次及本次金额'),fieldtype:'Table',cannot_add_rows:true,cannot_delete_rows:true,in_place_edit:true,data:[],fields:[
                {fieldname:'include',label:__('合并'),fieldtype:'Check',in_list_view:1,columns:1},
                {fieldname:'billing_plan',label:__('开票计划'),fieldtype:'Link',options:'Contract Billing Plan',read_only:1,in_list_view:1,columns:3},
                {fieldname:'external_number',label:__('协议号'),fieldtype:'Data',read_only:1,in_list_view:1,columns:2},
                {fieldname:'available_amount',label:__('剩余可开'),fieldtype:'Currency',read_only:1,in_list_view:1,columns:2},
                {fieldname:'allocated_amount',label:__('本次金额'),fieldtype:'Currency',in_list_view:1,columns:2},
                {fieldname:'invoice_due_date',label:__('原计划开票日'),fieldtype:'Date',read_only:1},
            ]},
        ],primary_action_label:__('生成一张税票草稿'),
        primary_action:async values=>{
            const allocations=(values.plans||[]).filter(r=>r.include).map(r=>({billing_plan:r.billing_plan,allocated_amount:r.allocated_amount}));
            if(!allocations.length){frappe.msgprint(__('请勾选至少一个期次'));return;}
            const {message:data}=await frappe.call('contract_ops.vat_links.prepare_merged_vat',{
                company:values.company,customer:values.customer,currency:values.currency,invoice_date:values.invoice_date,allocations,
            });
            dialog.hide();
            const {plan_allocations,payment_allocations,...header}=data;
            frappe.new_doc('VAT Invoice Record',header,doc=>{
                for(const row of plan_allocations)Object.assign(frappe.model.add_child(doc,'VAT Plan Allocation','plan_allocations'),row);
                for(const row of payment_allocations)Object.assign(frappe.model.add_child(doc,'VAT Payment Allocation','payment_allocations'),row);
            });
        },
    });
    dialog.show();
    return dialog;
};
