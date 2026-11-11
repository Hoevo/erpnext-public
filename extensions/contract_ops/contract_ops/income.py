"""Public-income inventory, immutable source imports and dated contract relationships."""
import base64
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
import frappe
from frappe.utils import now_datetime, getdate
from contract_ops.income_source import parse_workbook, dump

MODEL=dict(version='1.1',objects={
    'Public Income Project':dict(key='name',identity='One source-scoped inventory row initially; grouped equipment is not split without evidence',
        source_key='source_key',source='source_import + source_sheet + source_row',annual_reference='CNY/year source observation; NULL/blank unknown, not receivable or cash'),
    'Public Income Import':dict(key='name',identity='SHA256 of original XLSX bytes',raw_bytes='original_b64',
        original_content_hash='source_sha256',facts='workbook_snapshot contains source cell, formula, cached value and comment; gaps kept in source_gaps',
        time='imported_at is record time, not effective year'),
    'Contract':dict(key='name',meaning='Executed annual or multi-year agreement; financial documents remain under existing contracts')},
    relationships={
        'project_contract':dict(storage='Public Income Contract Link',key='relation_key',from_key='income_project -> Public Income Project.name',
            to_key='sales_contract -> Contract.name',valid_time='valid_from / valid_to copied from contract, not import date',
            status='待核实=unconfirmed; 已确认=confirmed; 已撤销=superseded',evidence='evidence; exact coverage must be confirmed, name equality alone is insufficient'),
        'project_source':dict(storage='Public Income Project',key='name',from_key='name',to_key='source_import -> Public Income Import.name',
            facts='source_sheet + source_row + source_row_json',valid_time='Source observation; effective year unknown'),
        'contract_renewal':dict(storage='Public Income Contract Link',key='relation_key',from_key='sales_contract',to_key='previous_contract',
            rule='Both agreements must be confirmed for the same project; previous agreement must end before new agreement starts')},
    operation=dict(name='public-income-xlsx-import',version='1.0',identity='Exact file SHA256 is idempotent; changed existing source rows require reviewed reconciliation',
        calculation='Known annual references from detail rows only; preserve blanks and nonannual pricing; never add summary sheet or infer share/bonus/tax',
        renewal='Usually annual by user instruction; no future executed contract, invoice or receivable is generated'))

def validate_project(doc):
    if not doc.source_key:doc.source_key='manual:'+frappe.generate_hash(length=32)
    if doc.amount_status=='原表年参考金额':
        try:
            if doc.annual_reference is None or not str(doc.annual_reference).strip():raise InvalidOperation
            amount=Decimal(str(doc.annual_reference))
            if not amount.is_finite() or amount<0:raise InvalidOperation
            doc.annual_reference=str(amount.quantize(Decimal('.01')))
        except (InvalidOperation,ValueError):frappe.throw('年参考金额必须是非负数；未提供时请选择“金额未提供”并留空')
    elif doc.annual_reference not in (None,''):
        frappe.throw('非年度计价或金额未提供时，年参考金额必须留空，不能填零代替未知')
    if not doc.is_new():
        before=doc.get_doc_before_save()
        for field in ('source_key','source_import','source_sheet','source_row','source_row_json'):
            if before and before.get(field)!=doc.get(field):frappe.throw('原始来源不可改写，请保留原记录并建立有依据的新版本')
    confirmed=frappe.db.exists('Public Income Contract Link',{'income_project':doc.name,'status':'已确认'}) if not doc.is_new() else False
    if doc.evidence_status=='合同已核实' and not confirmed:frappe.throw('请先建立有明确依据的已确认合同关系')

def validate_contract_link(doc):
    project=frappe.get_doc('Public Income Project',doc.income_project);project.check_permission('read')
    contract=frappe.get_doc('Contract',doc.sales_contract);contract.check_permission('read')
    from contract_ops.native_contract import require_sales_contract
    require_sales_contract(contract)
    if contract.docstatus!=1:frappe.throw('只能关联已提交销售合同，清单不代表已经签约')
    doc.relation_key=hashlib.sha256((project.name+'|'+contract.name).encode()).hexdigest()
    existing=frappe.db.get_value('Public Income Contract Link',{'relation_key':doc.relation_key},'name')
    if existing and existing!=doc.name:frappe.throw('本项目与该合同的关系已存在，请查看原关联，不能重复建立')
    doc.valid_from=contract.start_date;doc.valid_to=contract.end_date
    if doc.status=='已确认' and (not doc.evidence or not doc.valid_from or not doc.valid_to):
        frappe.throw('确认关联需要明确的覆盖范围依据和完整合同期限')
    if doc.status=='已撤销' and frappe.db.exists('Public Income Contract Link',{'income_project':project.name,'previous_contract':contract.name,'status':'已确认'}):
        frappe.throw('仍有已确认的后续续约关系，请先处理后续关系，再撤销本关系')
    if doc.previous_contract and doc.status!='已撤销':
        if doc.previous_contract==doc.sales_contract:frappe.throw('上一份合同不能是本合同')
        previous=frappe.get_doc('Contract',doc.previous_contract);previous.check_permission('read')
        if not frappe.db.exists('Public Income Contract Link',{'income_project':project.name,'sales_contract':previous.name,'status':'已确认'}):
            frappe.throw('上一份合同必须先确认为同一收入项目的合同')
        if not previous.end_date or not doc.valid_from or getdate(previous.end_date)>=getdate(doc.valid_from):
            frappe.throw('续约合同日期重叠或期限不完整，请核实，不能自动推断续约')
    if not doc.is_new():
        before=doc.get_doc_before_save()
        if before and any(before.get(f)!=doc.get(f) for f in ('income_project','sales_contract')):
            frappe.throw('关系两端不可改写，请撤销旧关系并新增正确关联')

@frappe.whitelist(methods=['POST'])
def import_register(filename,file_b64,expected_sha=None,preview=0):
    frappe.only_for('System Manager')
    if '/' in filename or '\\' in filename or not filename.lower().endswith('.xlsx'):frappe.throw('请提供原始XLSX文件名')
    if len(file_b64)>14_000_000:frappe.throw('清单不得超过10MB')
    try:raw=base64.b64decode(file_b64,validate=True);parsed=parse_workbook(raw,filename)
    except Exception as exc:frappe.throw('清单读取失败：'+str(exc))
    if expected_sha and expected_sha!=parsed['sha256']:frappe.throw('原文件哈希校验不一致')
    summary=dict(project_count=len(parsed['projects']),community_count=len(parsed['communities']),
        known_annual_total=parsed['known_annual_total'],gaps=parsed['gaps'],sha256=parsed['sha256'])
    if int(preview):return summary
    existing=frappe.db.get_value('Public Income Import',{'source_sha256':parsed['sha256']},'name')
    if existing:
        if frappe.db.count('Public Income Project',{'source_import':existing})!=len(parsed['projects']):frappe.throw('已有导入批次项目不完整，请核实后修复，不能重复导入')
        return dict(summary,source_import=existing,created=0,idempotent=True)
    if any(frappe.db.exists('Public Income Project',{'source_key':p['source_key']}) for p in parsed['projects']):
        frappe.throw('已有同来源行的其他版本，请先逐行核对变化；不会覆盖历史台账')
    batch=frappe.get_doc(dict(doctype='Public Income Import',source_filename=filename,source_sha256=parsed['sha256'],
        original_b64=parsed['original_b64'],workbook_snapshot=dump(parsed['workbook_snapshot']),source_gaps=dump(parsed['gaps']),
        model_definition=dump(MODEL),review_notes=parsed['review_notes'],imported_at=now_datetime(),
        project_count=summary['project_count'],community_count=summary['community_count'],known_annual_total=summary['known_annual_total']))
    batch.flags.from_income_import=True;batch.insert(ignore_permissions=True)
    from frappe.utils.file_manager import save_file
    attachment=save_file(filename,raw,batch.doctype,batch.name,is_private=1)
    frappe.db.set_value(batch.doctype,batch.name,'source_file',attachment.file_url,update_modified=False)
    for project in parsed['projects']:
        frappe.get_doc(dict(doctype='Public Income Project',source_import=batch.name,**project)).insert()
    return dict(summary,source_import=batch.name,created=len(parsed['projects']),idempotent=False)

def import_from_path(path):
    if not str(path).startswith('/tmp/public-income-'):frappe.throw('仅允许指定的临时清单文件')
    source=Path(path)
    result=import_register(source.name.removeprefix('public-income-'),base64.b64encode(source.read_bytes()).decode())
    frappe.db.commit();return result

@frappe.whitelist()
def get_project_contracts(income_project):
    project=frappe.get_doc('Public Income Project',income_project);project.check_permission('read')
    rows=frappe.get_list('Public Income Contract Link',filters={'income_project':project.name},
        fields=['name','sales_contract','status','valid_from','valid_to','previous_contract','evidence'],limit_page_length=0,order_by='valid_from asc,creation asc')
    for row in rows:
        contract=frappe.get_doc('Contract',row.sales_contract);contract.check_permission('read')
        group_name=None
        if contract.contract_group:
            group=frappe.get_doc('Contract Group',contract.contract_group);group.check_permission('read');group_name=group.group_name
        row.update(contract_title=contract.contract_title,external_number=contract.external_number,customer=contract.party_name,
            contract_amount=contract.contract_amount,contract_group=contract.contract_group,contract_group_name=group_name)
    return rows

def refresh_project_status(doc):
    if frappe.db.get_value('Public Income Project',doc.income_project,'evidence_status')=='停用':return
    confirmed=frappe.db.exists('Public Income Contract Link',{'income_project':doc.income_project,'status':'已确认'})
    frappe.db.set_value('Public Income Project',doc.income_project,'evidence_status','合同已核实' if confirmed else '清单观察／合同待核实',update_modified=False)

def audit_database():
    """Reconstruct from stored bytes and rows only; never read the source XLSX path."""
    checks=[]
    for row in frappe.get_all('Public Income Import',pluck='name'):
        batch=frappe.get_doc('Public Income Import',row)
        raw=base64.b64decode(batch.original_b64);assert hashlib.sha256(raw).hexdigest()==batch.source_sha256
        source=json.loads(batch.workbook_snapshot);assert source and json.loads(batch.model_definition)['version'] in ('1.0','1.1')
        projects=frappe.get_all('Public Income Project',filters={'source_import':row},fields=['name','source_key','source_sheet','source_row','source_row_json','amount_status','annual_reference'])
        assert len(projects)==batch.project_count
        total=Decimal(0)
        for p in projects:
            snapshot=json.loads(p.source_row_json);original=next(r for r in source[p.source_sheet]['rows'] if r['row']==p.source_row)
            cells=original['cells'];assert any(v['value']==snapshot['original']['公共收益项目'] for v in cells.values())
            value=snapshot['cached']['金额(元/年)']
            if isinstance(value,(int,float)) and not isinstance(value,bool):total+=Decimal(str(value)).quantize(Decimal('.01'))
        assert total==Decimal(batch.known_annual_total)
        checks.append(dict(batch=row,projects=len(projects),source_sha256=batch.source_sha256,known_annual_total=str(total),gaps=json.loads(batch.source_gaps)))
    for link in frappe.get_all('Public Income Contract Link',fields=['income_project','sales_contract','valid_from','valid_to','status','evidence']):
        assert frappe.db.exists('Public Income Project',link.income_project)
        contract=frappe.get_doc('Contract',link.sales_contract)
        assert link.valid_from==contract.start_date and link.valid_to==contract.end_date
        if link.status=='已确认':assert link.evidence
    return dict(status='passed',inputs='database bytes, model definitions, source rows and explicit relationships only',imports=checks,
        business_coverage='incomplete: source effective year and contract identities not supplied; no automatic confirmed contract links')

def audit_database_read_only():
    frappe.db.rollback()
    frappe.db.sql('START TRANSACTION READ ONLY')
    try:return audit_database()
    finally:frappe.db.rollback()
