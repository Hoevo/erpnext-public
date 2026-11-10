frappe.ui.form.on("Contract Group", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__("刷新汇总"), () => render_summary(frm));
        render_summary(frm);
    },
});

const money = (value) => format_currency(value || 0, "CNY");
const escape_html = (value) => frappe.utils.escape_html(String(value || ""));
const link = (doctype, name, label = name) =>
    `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${escape_html(label)}</a>`;

function rows(items, columns) {
    if (!items.length) return `<tr><td colspan="${columns.length}" class="text-muted">暂无记录</td></tr>`;
    return items.map((item) => `<tr>${columns.map((column) => `<td>${column(item)}</td>`).join("")}</tr>`).join("");
}

async function render_summary(frm) {
    const response = await frappe.call({
        method: "contract_ops.api.get_contract_group_summary",
        args: { contract_group: frm.doc.name },
        freeze: false,
    });
    const data = response.message;
    const metrics = [
        ["合同金额", data.totals.contract_amount, "contracts", "组内有效协议两年推算金额；不等于应收或欠款"],
        ["已开税票", data.totals.vat_invoices, "vat-invoices", "已提交且未取消，合并税票仅计入本组分配金额"],
        ["已确认应收", data.totals.receivables, "receivables", "已提交且未取消的销售发票"],
        ["实际收款", data.totals.receipts, "receipts", "仅按收款核销分配金额计入"],
        ["未收应收", data.totals.outstanding, "receivables", "关联应收发票未结清余额"],
    ];
    const cards = metrics.map(([label, value, target, note]) =>
        `<a class="contract-group-metric" href="#${target}"><span>${label}</span><strong>${money(value)}</strong><small>${note}</small></a>`).join("");
    const table = (id, title, headers, body) => `
        <section id="${id}" class="contract-group-table">
          <h4>${title}</h4><div class="table-responsive"><table class="table table-bordered">
          <thead><tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>
        </section>`;
    const html = `
      <style>
        .contract-group-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:12px 0 20px}
        .contract-group-metric{display:block;padding:14px;border:1px solid var(--border-color);border-radius:var(--border-radius-md);color:var(--text-color);background:var(--card-bg);text-decoration:none}
        .contract-group-metric:hover{border-color:var(--primary);text-decoration:none}.contract-group-metric span,.contract-group-metric small{display:block}.contract-group-metric span,.contract-group-metric small{color:var(--text-muted);font-size:12px}.contract-group-metric strong{display:block;font-size:22px;margin:7px 0}.contract-group-table{margin-top:24px}.contract-group-table h4{margin-bottom:10px}
      </style>
      <p class="text-muted">金额按不同口径分别展示，不能相加。点击金额或单据名称可追溯。</p>
      <div class="contract-group-metrics">${cards}</div>
      ${table("contracts", "协议清单", ["销售合同", "协议号", "期限", "两年推算金额"], rows(data.contracts, [
          (x) => link("Sales Contract", x.name, x.name), (x) => escape_html(x.external_number),
          (x) => `${escape_html(x.start_date)} 至 ${escape_html(x.end_date)}`, (x) => money(x.contract_amount),
      ]))}
      ${table("vat-invoices", "税票登记", ["税票", "销售合同", "本组分配金额"], rows(data.vat_invoices, [
          (x) => link("VAT Invoice Record", x.name, x.invoice_number || x.name),
          (x) => x.sales_contract ? link("Sales Contract", x.sales_contract) : "多合同合并（点击税票查看）", (x) => money(x.gross_amount),
      ]))}
      ${table("receivables", "应收发票", ["应收发票", "销售合同", "计费期间", "金额", "未收"], rows(data.receivables, [
          (x) => link("Sales Invoice", x.name), (x) => link("Sales Contract", x.custom_sales_contract),
          (x) => `${escape_html(x.custom_billing_period_start || "未填")} 至 ${escape_html(x.custom_billing_period_end || "未填")}`,
          (x) => money(x.grand_total), (x) => money(x.outstanding_amount),
      ]))}
      ${table("receipts", "收款核销分配", ["收款单", "应收发票", "核销分配金额"], rows(data.receipts, [
          (x) => link("Payment Entry", x.payment_entry), (x) => link("Sales Invoice", x.sales_invoice), (x) => money(x.allocated_amount),
      ]))}
      ${table("unallocated-advances", "未分配预收款", ["收款单", "未分配金额", "说明"], rows(data.unallocated_advances, [
          (x) => link("Payment Entry", x.name), (x) => money(x.unallocated_amount), () => "不计入已核销收款",
      ]))}`;
    frm.set_df_property("summary_html", "options", html);
    frm.refresh_field("summary_html");
}
