frappe.ui.form.on('Public Income Project',{
    refresh(frm){
        if(frm.is_new())return;
        frm.add_custom_button(__('关联年度合同'),()=>frappe.new_doc('Public Income Contract Link',{income_project:frm.doc.name}));
        frappe.call('contract_ops.income.get_project_contracts',{income_project:frm.doc.name}).then(({message:rows})=>{
            const esc=x=>frappe.utils.escape_html(String(x??''));
            const link=(type,name,label=name)=>name?`<a href="/desk/${frappe.router.slug(type)}/${encodeURIComponent(name)}">${esc(label)}</a>`:'未填写';
            const html='<p class="text-muted">通常每年续约不代表已经签约。请确认覆盖范围后关联已有合同，保留往年合同；本台账不自动生成合同、应收或收款。</p>'+
                '<style>.income-contract-table{min-width:1040px}.income-contract-table th{white-space:nowrap}.income-contract-table td{vertical-align:top}.income-contract-table .income-nowrap{white-space:nowrap}.income-contract-evidence{max-width:440px;min-width:260px;white-space:normal;overflow-wrap:anywhere;margin-top:8px}</style>'+
                '<div class="table-responsive"><table class="table table-bordered income-contract-table"><thead><tr><th>年度合同</th><th>所属合同组</th><th>客户</th><th>期限</th><th>状态</th><th>上一份合同</th><th>关联依据</th></tr></thead><tbody>'+
                (rows.length?rows.map(r=>`<tr><td>${link('Contract',r.sales_contract,r.external_number||r.contract_title)}</td><td class="income-nowrap" title="${esc(r.contract_group_name)}">${r.contract_group?link('Contract Group',r.contract_group):'独立／旧来源记录'}</td><td class="income-nowrap">${esc(r.customer)}</td><td class="income-nowrap">${esc(r.valid_from)}<br>至 ${esc(r.valid_to)}</td><td class="income-nowrap">${link('Public Income Contract Link',r.name,r.status)}</td><td>${link('Contract',r.previous_contract)}</td><td><details><summary class="income-nowrap">查看依据</summary><div class="income-contract-evidence">${esc(r.evidence)}</div></details></td></tr>`).join(''):'<tr><td colspan="7">尚未确认年度合同，请核实合同编号、位置及覆盖范围后关联。</td></tr>')+'</tbody></table></div>';
            frm.set_df_property('contracts_html','options',html);frm.refresh_field('contracts_html');
        });
    }
});
