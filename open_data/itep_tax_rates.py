"""Fail-closed ITEP 2026.2 historical state tax-rate workbook importer.

Requires explicitly identifiable state/year/rate columns or state-year matrix by
single-tax worksheets. A schema audit is written even if import fails.
"""
from __future__ import annotations
import collections,csv,datetime as dt,hashlib,io,json,math,re,urllib.request,urllib.parse,zipfile
from pathlib import Path
from openpyxl import load_workbook

SOURCE='https://itep.org/wp-content/uploads/2026.2_ITEP-State-Tax-Rate-Historical-Data.xlsx'
SOURCE_PAGE='https://itep.org/historical-state-tax-rate-data/'
VERSION='2026.2 (June 2026)'
STATES='AL Alabama|AK Alaska|AZ Arizona|AR Arkansas|CA California|CO Colorado|CT Connecticut|DE Delaware|FL Florida|GA Georgia|HI Hawaii|ID Idaho|IL Illinois|IN Indiana|IA Iowa|KS Kansas|KY Kentucky|LA Louisiana|ME Maine|MD Maryland|MA Massachusetts|MI Michigan|MN Minnesota|MS Mississippi|MO Missouri|MT Montana|NE Nebraska|NV Nevada|NH New Hampshire|NJ New Jersey|NM New Mexico|NY New York|NC North Carolina|ND North Dakota|OH Ohio|OK Oklahoma|OR Oregon|PA Pennsylvania|RI Rhode Island|SC South Carolina|SD South Dakota|TN Tennessee|TX Texas|UT Utah|VT Vermont|VA Virginia|WA Washington|WV West Virginia|WI Wisconsin|WY Wyoming|DC District of Columbia'
STATE_ALIASES={}
for s in STATES.split('|'):
    code,name=s.split(' ',1)
    STATE_ALIASES[code.lower()]=code
    STATE_ALIASES[name.lower()]=code
STATE_ALIASES.update({'washington dc':'DC','washington d.c.':'DC','district of columbia':'DC'})
KINDS={
 'pit':r'(?<![a-z])pit(?![a-z])|personal income|individual income',
 'cit':r'(?<![a-z])cit(?![a-z])|corporate income|corporation income',
 'sales':r'(?<![a-z])gst(?![a-z])|general sales|sales tax|sales taxes',
}
FORBIDDEN=r'deduct|adjust|supplement|summary|instruction|readme|footnote|citations|change|federal|effective|historical notes|data sources|description'
FIELDS=['provider','country_iso3','year','period','series','value','unit','frequency','disaggregation','obs_status','source_url','retrieved_utc']

def norm(x):return ' '.join(str(x or '').strip().lower().replace('_',' ').replace('\n',' ').split())
def state(x):return STATE_ALIASES.get(norm(x).strip('.'))
def year(x):
    if isinstance(x,dt.datetime):x=x.year
    if isinstance(x,(int,float)) and int(x)==x:x=int(x)
    s=str(x or '').strip()
    if re.fullmatch(r'(?:18|19|20)\d{2}',s):
        val=int(s)
        return val if 1900<=val<=2025 else None
    return None

def kind_from_text(s):
    s=norm(s)
    kinds=[k for k,rx in KINDS.items() if re.search(rx,s)]
    return kinds[0] if len(kinds)==1 and not re.search(FORBIDDEN,s) else None

def rate(cell):
    raw=cell.value
    if raw is None or raw=='' or isinstance(raw,bool):return None
    if isinstance(raw,str):
        s=raw.strip()
        if s in ('-','N/A','NA','n/a','—','*','...'):return None
        if s.endswith('%'):
            try:value=float(s[:-1].replace(',',''))
            except ValueError:return None
            fmt='string_percentage'
        else:return None # formulas need evaluated cached numeric values
    elif isinstance(raw,(int,float)):
        value=float(raw)
        fmt=str(cell.number_format or '')
        if '%' in fmt:value*=100
        elif 0<abs(value)<0.30:raise ValueError(f'Ambiguous fraction-or-percent rate at {cell.coordinate}: {raw} format={fmt}')
    else:return None
    if not math.isfinite(value) or not 0<=value<=50:
        raise ValueError(f'Out-of-range rate at {cell.coordinate}: {raw}')
    return round(value,9)

def detect_matrix(ws,kind,audit):
    """Parse when one axis explicitly enumerates states, the other tax years.

    Never infer tax type from numeric data or silently turn missing into zero.
    """
    results=[]; seen=set(); proposals=[]
    max_r=min(ws.max_row or 0,180)
    max_c=min(ws.max_column or 0,220)
    for hr in range(1,min(max_r,65)+1):
        header=[ws.cell(hr,c).value for c in range(1,max_c+1)]
        years=[(c,year(v)) for c,v in enumerate(header,1) if year(v) is not None]
        statecols=[(c,state(v)) for c,v in enumerate(header,1) if state(v) is not None]
        if len(years)>=12 and len({y for _,y in years})==len(years):
            for sc in range(1,min(max_c,8)+1):
                state_rows=[]
                for r in range(hr+1,min(ws.max_row,hr+100)+1):
                    st=state(ws.cell(r,sc).value)
                    if st:state_rows.append((r,st))
                if len(set(x[1] for x in state_rows))>=35:
                    proposals.append(('state_row',hr,sc,years,state_rows))
        if len(set(s for _,s in statecols))>=35:
            for yc in range(1,min(max_c,8)+1):
                yearrows=[(r,year(ws.cell(r,yc).value)) for r in range(hr+1,(ws.max_row or hr)+1)]
                yearrows=[x for x in yearrows if x[1] is not None]
                if len(yearrows)>=12 and len(set(y for _,y in yearrows))==len(yearrows):
                    proposals.append(('year_row',hr,yc,statecols,yearrows))
    if len(proposals)>1:
        # Overlapping repeated headers may be detected more than once; reject unknown layouts.
        raise ValueError(f'Ambiguous state-year matrices in {ws.title}: {len(proposals)} candidates')
    if not proposals:return []
    orient,hr,axis,first,second=proposals[0]
    audit.setdefault('accepted_layouts',[]).append({'sheet':ws.title,'kind':kind,'orientation':orient,'header_row':hr,'axis_col':axis})
    for row,st in (second if orient=='state_row' else []):
        for col,yr in first:
            if col==axis:continue
            v=rate(ws.cell(row,col))
            if v is not None:results.append((st,yr,kind,v,ws.title,ws.cell(row,col).coordinate))
    for row,yr in (second if orient=='year_row' else []):
        for col,st in first:
            if col==axis:continue
            v=rate(ws.cell(row,col))
            if v is not None:results.append((st,yr,kind,v,ws.title,ws.cell(row,col).coordinate))
    return results

def detect_long_form(ws,audit):
    results=[]
    # Explicit state/year columns and columns that name rate *and* tax type.
    for hr in range(1,min(ws.max_row or 0,35)+1):
        header=[norm(ws.cell(hr,c).value) for c in range(1,min(ws.max_column or 0,90)+1)]
        sc=[c for c,v in enumerate(header,1) if v in ('state','state name','state abbreviation','state abbr','jurisdiction')]
        yc=[c for c,v in enumerate(header,1) if v in ('year','tax year','calendar year')]
        taxcols=[(c,kind_from_text(v)) for c,v in enumerate(header,1) if kind_from_text(v) and ('rate' in v or v in ('pit','cit'))]
        taxcols=[(c,k) for c,k in taxcols if 'federal' not in header[c-1]]
        if len(sc)!=1 or len(yc)!=1 or len(taxcols)<2:continue
        if len(set(k for _,k in taxcols))!=len(taxcols):raise ValueError('Ambiguous duplicate tax-type columns')
        for r in range(hr+1,(ws.max_row or hr)+1):
            st=state(ws.cell(r,sc[0]).value);yr=year(ws.cell(r,yc[0]).value)
            if not st or yr is None:continue
            for c,k in taxcols:
                value=rate(ws.cell(r,c))
                if value is not None:results.append((st,yr,k,value,ws.title,ws.cell(r,c).coordinate))
        if results:
            audit.setdefault('accepted_layouts',[]).append({'sheet':ws.title,'layout':'explicit_long_form','header_row':hr,'rate_columns':[(header[c-1],k) for c,k in taxcols]})
            return results
    return []

def load_workbook_with_verified_font_repair(raw,audit):
    """Repair invalid Excel *font family metadata only*, never source cells.

    Some ITEP workbook font-family values exceed openpyxl's 0..14 range.
    Audit the exact replacement and verify every non-style ZIP member is byte-identical.
    Unknown style errors are not suppressed.
    """
    try:
        return load_workbook(io.BytesIO(raw),read_only=False,data_only=True)
    except ValueError as initial:
        if 'could not read stylesheet' not in str(initial):
            raise
        audit['initial_excel_style_error']=str(initial)
    with zipfile.ZipFile(io.BytesIO(raw)) as original:
        if 'xl/styles.xml' not in original.namelist():
            raise ValueError('Missing XLSX stylesheet; cannot repair')
        styles=original.read('xl/styles.xml')
        # The font's family is presentation metadata (not a tax unit or numeric format).
        # Only remove objectively invalid family tags inside the <fonts> element.
        font_section=re.search(rb'<(?:[a-zA-Z0-9_]+:)?fonts\b[^>]*>.*?</(?:[a-zA-Z0-9_]+:)?fonts>',styles,re.S)
        if not font_section:
            raise ValueError('Cannot isolate font metadata safely')
        removed=[]
        def repair_family(match):
            tag=match.group(0)
            m=re.search(rb'\bval=[\"\'](?P<val>[0-9]+)[\"\']',tag)
            if not m:
                return tag
            value=int(m.group('val'))
            if 0<=value<=14:
                return tag
            removed.append(value)
            return b''
        updated_section=re.sub(rb'<(?:[a-zA-Z0-9_]+:)?family\b[^<>]*?/\s*>',repair_family,font_section.group(0))
        if not removed:
            raise ValueError('Style error was not caused by invalid font-family values; refusing broad modifications')
        patched=styles[:font_section.start()]+updated_section+styles[font_section.end():]
        # XML must remain structurally well-formed after a pure font-metadata change.
        import xml.etree.ElementTree as ET
        ET.fromstring(patched)
        # The official workbook also has invalid font-family metadata in
        # rich-text comments. Remove only invalid <family val="N"/> tags
        # in comment XML; preserve all comment text and every source cell.
        comment_repairs={}
        comment_families={}
        for info in original.infolist():
            name=info.filename
            if not (name.startswith('xl/comments') and name.endswith('.xml')):
                continue
            comment_bytes=original.read(name)
            bad=[]
            def comment_font_family(match):
                tag=match.group(0)
                m=re.search(rb"\bval=[\"\'](?P<val>[0-9]+)[\"\']",tag)
                if m and int(m.group('val'))>14:
                    bad.append(int(m.group('val')))
                    return b''
                return tag
            repaired=re.sub(rb'<(?:[a-zA-Z0-9_]+:)?family\b[^<>]*?/\s*>',comment_font_family,comment_bytes)
            if bad:
                ET.fromstring(repaired)
                comment_repairs[name]=repaired
                comment_families[name]=bad
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as target:
            for info in original.infolist():
                content=(patched if info.filename=='xl/styles.xml'
                         else comment_repairs.get(info.filename,original.read(info.filename)))
                import copy
                target.writestr(copy.copy(info),content)
        fixed=out.getvalue()
        with zipfile.ZipFile(io.BytesIO(fixed)) as check:
            for info in original.infolist():
                actual=check.read(info.filename)
                expected=(patched if info.filename=='xl/styles.xml'
                          else comment_repairs.get(info.filename,original.read(info.filename)))
                if actual!=expected:
                    raise ValueError('Unexpected source ZIP content modification: '+info.filename)
        audit['font_presentation_repair']={
            'invalid_family_values_removed_from_styles':removed,
            'invalid_family_values_removed_from_comment_fonts':comment_families,
            'original_styles_sha256':hashlib.sha256(styles).hexdigest(),
            'repaired_styles_sha256':hashlib.sha256(patched).hexdigest(),
            'all_worksheet_sharedstring_and_nonfont_source_content_byte_identical':True,
            'numeric_formats_untouched':True,
            'original_source_sha256':hashlib.sha256(raw).hexdigest()}
        return load_workbook(io.BytesIO(fixed),read_only=False,data_only=True)

def parse(raw,audit_path:Path):
    audit={'source_url':SOURCE,'version':VERSION,'source_sha256':hashlib.sha256(raw).hexdigest(),'sheets':[],'accepted_layouts':[]}
    try:
        wb=load_workbook_with_verified_font_repair(raw,audit)
        results=[]
        for ws in wb.worksheets:
            sample=[]
            for r in ws.iter_rows(min_row=1,max_row=min(ws.max_row or 0,12),max_col=min(ws.max_column or 0,13)):
                sample.append([str(c.value)[:110] if isinstance(c.value,str) else ('numeric' if isinstance(c.value,(float,int)) else '') for c in r])
            kind=kind_from_text(ws.title)
            audit['sheets'].append({'title':ws.title,'rows':ws.max_row,'columns':ws.max_column,'tax_type_in_title':kind,'first_rows_label_audit':sample})
            if re.search(FORBIDDEN,norm(ws.title)) and not kind:continue
            if kind:
                results.extend(detect_matrix(ws,kind,audit))
            else:
                results.extend(detect_long_form(ws,audit))
        dedup={}
        for st,yr,k,value,sheet,cell in results:
            key=(st,yr,k)
            if key in dedup:
                if dedup[key][0]!=value:raise ValueError(f'Conflicting ITEP rates for {key}: {dedup[key][0]} vs {value}')
                raise ValueError(f'Duplicate identical rate for {key} across workbook; review layout')
            dedup[key]=(value,sheet,cell)
        by_kind=collections.Counter(k for _,_,k in dedup)
        states_by_kind={k:{st for st,yr,kind in dedup if kind==k} for k in KINDS}
        years_by_kind={k:{yr for st,yr,kind in dedup if kind==k} for k in KINDS}
        audit.update({'parsed_rate_observations':len(dedup),'rates_by_tax_type':dict(by_kind),
                      'state_count_by_type':{k:len(v) for k,v in states_by_kind.items()},
                      'year_count_by_type':{k:len(v) for k,v in years_by_kind.items()}})
        if not all(by_kind[k]>=500 and len(states_by_kind[k])>=35 and len(years_by_kind[k])>=20 for k in KINDS):
            raise ValueError('RATE_SCHEMA_NOT_VERIFIED: expected >=500 numeric points, >=35 states and >=20 years for each of PIT,CIT,sales; inspect audit before importing')
        return dedup,audit
    except Exception as exc:
        audit['status']='SOURCE_SCHEMA_REVIEW_REQUIRED'
        audit['error']=f'{type(exc).__name__}: {str(exc)}'
        raise
    finally:
        audit_path.parent.mkdir(parents=True,exist_ok=True)
        audit_path.write_text(json.dumps(audit,indent=2,default=str)+'\n',encoding='utf-8')

def download():
    req=urllib.request.Request(SOURCE,headers={'User-Agent':'GlobalEconomicPolicyTool/1.0 (academic source data audit)'})
    with urllib.request.urlopen(req,timeout=120) as response:
        if response.geturl()!=SOURCE:raise ValueError(f'ITEP source redirect changed: {response.geturl()}')
        blob=response.read(25_000_001)
    if not 10000<len(blob)<=25_000_000 or not zipfile.is_zipfile(io.BytesIO(blob)):
        raise ValueError('Unexpected ITEP XLSX download type/size')
    return blob

def run(out='merged_data/itep_tax_rates',audit='itep_workbook_schema_audit.json',fetch=download):
    out=Path(out);audit=Path(audit);out.mkdir(parents=True,exist_ok=True)
    blob=fetch()
    dedup,audit_result=parse(blob,audit)
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    rows=[];lookup=collections.defaultdict(dict)
    for (st,yr,k),(value,sheet,cell) in sorted(dedup.items()):
        lookup[(st,k)][yr]=value
        dims={'state_abbr':st,'tax_type':k,'rate_definition':{'pit':'top_statutory_personal_income','cit':'top_statutory_corporate_income','sales':'state_general_sales'}[k],
              'year_end_rate_as_of':f'{yr}-12-31','original_sheet':sheet,'original_cell':cell,
              'source_version':VERSION,'tax_base_changes_excluded':True,'local_taxes_excluded':True,
              'legal_effective_date_not_identified':True,'not_causal_policy_shock':True}
        rows.append({'provider':'ITEP Historical State Tax Rate Data','country_iso3':'USA','year':yr,'period':str(yr),
                     'series':f'ITEP.STATE_TAX_RATE.{k.upper()}.STATUTORY','value':format(value,'.9g'),
                     'unit':'percent_points','frequency':'annual_december_31','disaggregation':json.dumps(dims,sort_keys=True),
                     'obs_status':'source_reported_year_end_statutory_rate','source_url':SOURCE_PAGE,'retrieved_utc':stamp})
    path=out/'processed'/'itep_tax_rates_panel.csv';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as stream:
        w=csv.DictWriter(stream,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
    changes=[]
    for (st,k),hist in lookup.items():
        for yr in sorted(hist):
            if yr-1 not in hist:continue
            previous,current=hist[yr-1],hist[yr]
            if abs(current-previous)>1e-9:
                changes.append({'state_abbr':st,'year':yr,'tax_type':k,'rate_previous_dec_31_pp':previous,
                                'rate_current_dec_31_pp':current,'delta_percentage_points':round(current-previous,9),
                                'direction':'increase' if current>previous else 'decrease',
                                'enactment_date':'','effective_date':'',
                                'timing_status':'year_end_comparison_only_not_verified_legal_effective_date',
                                'source_url':SOURCE_PAGE})
    changepath=out/'processed'/'derived_year_end_changes_not_legal_events.csv'
    with changepath.open('w',newline='',encoding='utf-8') as f:
        cols=['state_abbr','year','tax_type','rate_previous_dec_31_pp','rate_current_dec_31_pp','delta_percentage_points','direction','enactment_date','effective_date','timing_status','source_url']
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(changes)
    report={'source_url':SOURCE,'source_page':SOURCE_PAGE,'version':VERSION,
            'source_sha256':hashlib.sha256(blob).hexdigest(),'panel_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'observations':len(rows),'derived_year_end_change_comparisons':len(changes),
            'rates_by_tax_type':audit_result['rates_by_tax_type'],
            'errors':[],'conflicts':[],'research_ready':False,'is_causal_policy_shock':False,
            'source_as_of':'december_31','last_included_year':2025,
            'limitations':['ITEP workbook is version 2026.2 (June 2026); only years <=2025 included, excludes potentially changing 2026/2027 future rates.',
                           'Source only reports year-end values, not the exact legal implementation or enactment date.',
                           'Top marginal rates do not measure effective average tax burdens or tax base changes.',
                           'Changes are derived differences; cannot identify motive, surprise, or causality.',
                           'No source missing cell is imputed to zero; rate formats are interpreted conservatively.']}
    (out/'run_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'observations':len(rows),'by_tax_type':report['rates_by_tax_type'],'derived_year_end_changes':len(changes)},indent=2))
    return report

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',default='merged_data/itep_tax_rates');p.add_argument('--audit',default='itep_workbook_schema_audit.json')
    a=p.parse_args();run(a.out,a.audit)
