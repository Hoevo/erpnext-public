frappe.ui.form.on("Contract", {
    refresh(frm) {
        if (frm.doc.party_type !== 'Customer' || !frm.doc.contract_ops_enabled) return;
        if (!frm.is_new()) render_renewal_cycles(frm);
        if (!frm.is_new()) render_contract_plans(frm);
        if (frm.doc.docstatus !== 1) return;
        const pay_first = frm.doc.billing_flow === '先款后票';
        frm.add_custom_button(__(pay_first ? "新增先款后票收款计划" : "新增开票计划"), () => frappe.new_doc(pay_first ? 'Contract Collection Plan' : 'Contract Billing Plan', {
            sales_contract: frm.doc.name, period_start: frm.doc.start_date,
            period_end: frm.doc.end_date,
        }));
        frm.add_custom_button(__("刷新计划"), () => render_contract_plans(frm));
        frm.add_custom_button(__("登记税票"), () => frappe.new_doc("VAT Invoice Record", {
            sales_contract: frm.doc.name, company: frm.doc.company,
            customer: frm.doc.party_name, currency: frm.doc.currency,
        }));
        frm.add_custom_button(__("新建应收发票"), () => frappe.new_doc("Sales Invoice", {
            custom_sales_contract: frm.doc.name, company: frm.doc.company,
            customer: frm.doc.party_name, currency: frm.doc.currency,
        }));
        frm.add_custom_button(__("查看应收发票"), () => frappe.set_route("List", "Sales Invoice", {
            custom_sales_contract: frm.doc.name,
        }));
        frm.add_custom_button(__("查看税票"), () => frappe.set_route("List", "VAT Invoice Record", {
            sales_contract: frm.doc.name,
        }));
    },
});

async function render_renewal_cycles(frm) {
    const {message: data} = await frappe.call('contract_ops.renewal.get_cycles', {sales_contract: frm.doc.name});
    const esc = x => frappe.utils.escape_html(String(x ?? ''));
    const link = x => `<a href="/desk/contract-operations/contract-renewal-cycle/${encodeURIComponent(x.name)}">${esc(x.status)}</a>`;
    frm.fields_dict.renewal_html.$wrapper.html(`<div class="renewal-panel" style="border:1px solid var(--border-color);border-radius:8px;padding:16px;margin-top:12px">
      <p><strong>提醒配置：${esc(data.health)}</strong></p>
      <p class="text-muted">根据合同条款计算办理截止日；提醒提前量相对此截止日计算。确认办理后保留本周期记录，按条款续期会建立下一周期。</p>
      <div class="table-responsive"><table class="table"><thead><tr><th>本周期</th><th>续签方式</th><th>办理截止</th><th>负责人</th><th>下一提醒</th><th>状态／办理入口</th></tr></thead>
      <tbody>${data.cycles.length ? data.cycles.map(x => `<tr><td>${esc(x.period_start || '无固定期限')}<br>${esc(x.period_end)}</td><td>${esc(x.mode)}</td><td>${esc(x.deadline)}</td><td>${esc(x.responsible_user)}</td><td>${esc(x.next_reminder)}<br>${esc(x.health)}</td><td>${link(x)}</td></tr>`).join('') : '<tr><td colspan="6">核实续签条款并启用提醒，提交合同后自动建立跟进周期。</td></tr>'}</tbody></table></div></div>`);
}

async function render_contract_plans(frm) {
    const {message: data} = await frappe.call('contract_ops.planning.get_contract_planning', {sales_contract: frm.doc.name});
    const esc = (x) => frappe.utils.escape_html(String(x ?? ''));
    const money = (x) => format_currency(x || 0, data.currency);
    const link = (dt, name, title = name) => name ? `<a href="/desk/${frappe.router.slug(dt)}/${encodeURIComponent(name)}">${esc(title)}</a>` : '待关联';
    const table = (title, heads, rows) => `<h4 style="margin-top:20px">${title}</h4><div class="table-responsive"><table class="table table-bordered"><thead><tr>${heads.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.map(cols=>`<tr>${cols.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${heads.length}">暂无计划；历史单据不会自动补造计划。</td></tr>`}</tbody></table></div>`;
    const html = `<p class="text-muted">${esc(data.billing_flow)}：${data.billing_flow === '先款后票' ? '先维护约定收款期次，在客户收款单填写银行实际到账日期并分配本期款项；提交后按每笔到账或本期收齐规则生成开票计划。' : '税票提交后生成收款计划；按约定开票日或确认收票日计算到期日。'}计划本身不产生会计分录。</p>` +
        (data.billing_flow === '先款后票' ? table('先款后票：约定收款计划',['期次','约定收款日','金额','实际到账分配','未收','开票起算','状态'],data.collection_plans.map(p=>[
            link('Contract Collection Plan',p.name,p.plan_title),esc(p.receipt_due_date),money(p.planned_amount),money(p.received_amount),money(p.outstanding_amount),esc(`${p.invoice_trigger}后${p.invoice_days}${p.day_type}`),esc(p.status)])) : '') +
        table('开票计划', ['期次','计费期间','计划开票日','计划金额','已开票','已收款','状态'], data.billing_plans.map(p=>[
            link('Contract Billing Plan',p.name,p.plan_title), `${esc(p.period_start)} 至 ${esc(p.period_end)}`,esc(p.invoice_due_date),money(p.planned_amount),money(p.billed_amount),money(p.received_amount),esc(p.status)])) +
        (data.billing_flow === '先款后票' ? '' : table('税票后自动生成的收款计划', ['来源税票','开票日期','收票确认日','计划收款日','金额','已核销','未收','状态／确认收票'], data.receipt_plans.map(p=>[
            link('VAT Invoice Record',p.source_vat),esc(p.invoice_date),esc(p.received_on || '待确认'),esc(p.due_date || '待确认'),money(p.expected_amount),money(p.received_amount),money(p.outstanding_amount),link('Contract Receipt Plan',p.name,p.status)])));
    frm.set_df_property('planning_html','options',html);
    frm.refresh_field('planning_html');
}
