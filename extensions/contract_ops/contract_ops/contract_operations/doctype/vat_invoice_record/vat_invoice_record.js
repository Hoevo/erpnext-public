frappe.ui.form.on('VAT Invoice Record', {
    refresh(frm) {
        if(frm.is_new())frm.add_custom_button(__('按客户合并开票'),()=>frappe.contract_ops.open_merge({company:frm.doc.company,customer:frm.doc.customer,currency:frm.doc.currency}));
        if(!frm.is_new())render_vat_links(frm);
        frm.set_query('billing_plan','plan_allocations', () => ({filters: {
            ...(frm.doc.sales_contract ? {sales_contract:frm.doc.sales_contract} : {}), disabled:0,
        }}));
        frm.set_query('sales_invoice', () => ({filters: {docstatus:1,
            ...(frm.doc.sales_contract ? {custom_sales_contract:frm.doc.sales_contract} : {})}}));
        if (frm.doc.docstatus===1) frm.add_custom_button(__('查看收款计划'), () =>
            frappe.set_route('List','Contract Receipt Plan',{source_vat:frm.doc.name}));
    },
});

async function render_vat_links(frm){
    const {message:data}=await frappe.call('contract_ops.vat_links.get_vat_relationships',{vat_invoice:frm.doc.name});
    const esc=x=>frappe.utils.escape_html(String(x??''));
    const link=(type,name,label=name)=>name?`<a href="/desk/${frappe.router.slug(type)}/${encodeURIComponent(name)}">${esc(label)}</a>`:'未确认';
    const amount=x=>format_currency(x||0,data.currency);
    const table=(title,heads,rows)=>`<h4>${title}</h4><div class="table-responsive"><table class="table table-bordered"><thead><tr>${heads.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(cols=>`<tr>${cols.map(x=>`<td>${x}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${heads.length}">暂无关联记录</td></tr>`}</tbody></table></div>`;
    const html=table('本票对应合同',['合同／协议号','合同名称','本票分配金额'],data.contracts.map(x=>[link('Sales Contract',x.name,x.external_number||x.name),esc(x.title),amount(x.allocated_amount)]))+
        table('本票付款来源（先款后票）',['收款单','合同','期次','银行到账日','本票使用金额','依据'],data.payment_allocations.map(x=>[link('Payment Entry',x.payment_entry),link('Sales Contract',x.sales_contract),link('Contract Billing Plan',x.billing_plan),esc(x.actual_receipt_date||'未确认'),amount(x.allocated_amount),esc(x.basis)]))+
        '<p class="text-muted">以下为对应应收的收款核销依据；同一应收可能对应多张税票，不能把整笔核销金额重复当作每张税票实收。</p>'+
        table('对应应收的核销记录（共享依据）',['收款单','应收发票','本票应收份额','应收核销金额','银行到账日／记账日'],data.invoice_settlements.map(x=>[link('Payment Entry',x.payment_entry),link('Sales Invoice',x.sales_invoice),amount(x.invoice_share),amount(x.allocated_to_invoice),esc(x.actual_receipt_date||'到账日未确认')+' / '+esc(x.posting_date)]));
    frm.set_df_property('relationship_html','options',html);frm.refresh_field('relationship_html');
}
