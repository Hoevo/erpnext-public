"""Parse a public-income inventory without interpreting it as executed contracts."""
import base64
import hashlib
import io
import json
from decimal import Decimal
import openpyxl

HEADERS=['小区','公共收益项目','公司名称','数量','金额(元/年)','分成比例','奖金比例']

def dump(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,default=str,separators=(',',':'))

def text(value):
    return '' if value is None else str(value).strip()

def parse_workbook(raw,filename):
    formulas=openpyxl.load_workbook(io.BytesIO(raw),data_only=False)
    values=openpyxl.load_workbook(io.BytesIO(raw),data_only=True)
    matches=[s for s in formulas if set(HEADERS).issubset({c.value for c in s[1]})]
    if len(matches)!=1:raise ValueError('必须且只能有一张包含完整明细表头的工作表')
    sheet=matches[0];cached=values[sheet.title]
    columns={c.value:c.column for c in sheet[1] if c.value in HEADERS}
    projects=[];gaps=[];total=Decimal(0);communities=set();snapshots={}
    for ws in formulas:
        snapshots[ws.title]=dict(merged_ranges=[str(r) for r in ws.merged_cells.ranges],rows=[
            dict(row=i,cells={c.coordinate:dict(value=c.value,cached=values[ws.title][c.coordinate].value,
                data_type=c.data_type,comment=c.comment.text if c.comment else None) for c in row if c.value is not None})
            for i,row in enumerate(ws,1) if any(c.value is not None for c in row)])
    for row in range(2,sheet.max_row+1):
        observed={h:cached.cell(row,columns[h]).value for h in HEADERS}
        original={h:sheet.cell(row,columns[h]).value for h in HEADERS}
        community=text(observed[HEADERS[0]]);kind=text(observed[HEADERS[1]])
        if not community:continue
        communities.add(community)
        if not kind:
            gaps.append(dict(sheet=sheet.title,row=row,community=community,issue='仅有小区名称，未提供具体收入项目',original=original));continue
        amount=observed[HEADERS[4]];known=isinstance(amount,(int,float)) and not isinstance(amount,bool)
        annual=str(Decimal(str(amount)).quantize(Decimal('.01'))) if known else ''
        if known:total+=Decimal(annual)
        issues=['原表未提供合同编号、有效年度和签约期限，合同关系待核实']
        if observed[HEADERS[3]] is None:issues.append('数量未提供，不按零处理')
        if not observed[HEADERS[2]]:issues.append('往来单位未提供')
        if not known:issues.append('原表为非年度计价说明或未提供金额，不计入已知年参考金额')
        key=hashlib.sha256((filename+'|'+sheet.title+'|'+str(row)).encode()).hexdigest()
        share=observed[HEADERS[5]]
        projects.append(dict(source_key=key,project_title=' · '.join(filter(None,[community,kind,text(observed[HEADERS[2]])]))+f'（原表第{row}行）',
            community=community,income_type=kind,counterparty_text=text(observed[HEADERS[2]]),quantity_text=text(observed[HEADERS[3]]),
            annual_reference=annual,amount_status='原表年参考金额' if known else ('非年度计价' if amount is not None else '金额未提供'),
            pricing_text=text(original[HEADERS[4]]),sharing_rule=(str(Decimal(str(share))*100).rstrip('0').rstrip('.')+'%' if isinstance(share,(int,float)) else text(share)),
            bonus_rule=text(observed[HEADERS[6]]),source_sheet=sheet.title,source_row=row,source_row_json=dump(dict(original=original,cached=observed)),
            evidence_status='清单观察／合同待核实',renewal_rule='通常每年续约，实际期限以签署合同为准',issues='；'.join(issues)))
    return dict(filename=filename,sha256=hashlib.sha256(raw).hexdigest(),original_b64=base64.b64encode(raw).decode(),
        projects=projects,gaps=gaps,workbook_snapshot=snapshots,communities=sorted(communities),known_annual_total=str(total),
        review_notes='明细表与汇总表口径不一致，不合并相加；以明细建立项目，汇总表仅保留为独立来源。金桥十九打印机明细1500元、汇总500元；金巷高层快递柜与电梯广告的汇总金额疑似互换。原表没有明确适用年度，年参考金额不等于应收或已收，不计算分成及奖金。')
