#!/usr/bin/env python3
"""Phase 8: privately align original RR 2010 quarterly tax measures to source-reported US outcomes.

This is an alignment and data-coverage audit, not a causal estimate or reproduction.
Historical PAYEMS/UNRATE monthly data become unweighted quarterly arithmetic means;
GDPC1/GPDIC1 are read at original quarterly frequency, without annualization changes.
No numeric private series or aligned observations are printed or published.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
from collections import defaultdict
import hashlib
import io
import json
import math
from pathlib import Path
import re
import tempfile
import urllib.request
from urllib.parse import urlsplit

SOURCE_STUDY='romer_romer_2010'
RR_SHA='e0803df2b6a541041ef80c9dee050fc65bd75ea5ac57926f67483d1ee94dadd3'
RR_VARS=('nonretroactive','retroactive','present_value')
PERIODS=tuple(f'{y}Q{q}' for y in range(1945,2008) for q in range(1,5))
OUTCOMES={
 'PAYEMS': {'freq':'monthly','first':'1945Q1','last':'2007Q4','n':252,
            'label':'All Employees, Total Nonfarm','units':'thousands of payroll jobs; seasonally adjusted; quarterly arithmetic mean of 3 monthly stocks',
            'provider':'BLS via FRED','quarter_column':'PAYEMS_qmean_thousands'},
 'UNRATE': {'freq':'monthly','first':'1948Q1','last':'2007Q4','n':240,
            'label':'Civilian Unemployment Rate','units':'percent of civilian labor force; seasonally adjusted; quarterly arithmetic mean of 3 monthly rates',
            'provider':'BLS via FRED','quarter_column':'UNRATE_qmean_percent'},
 'GDPC1': {'freq':'quarterly','first':'1947Q1','last':'2007Q4','n':244,
            'label':'Real Gross Domestic Product','units':'billions of chained 2017 dollars; seasonally adjusted annual rate; original quarterly observation',
            'provider':'BEA via FRED','quarter_column':'GDPC1_billions_chained2017_SAAR'},
 'GPDIC1': {'freq':'quarterly','first':'1947Q1','last':'2007Q4','n':244,
            'label':'Real Gross Private Domestic Investment','units':'billions of chained 2017 dollars; seasonally adjusted annual rate; original quarterly observation',
            'provider':'BEA via FRED','quarter_column':'GPDIC1_billions_chained2017_SAAR'}
}
ORIGINAL_FIELDS=('period','source_variant','deficit_motivation_original_value',
                 'long_run_growth_motivation_original_value','exogenous_combined_original_value',
                 'source_sheet','source_excel_row','deficit_source_cell','growth_source_cell',
                 'exogenous_source_cell','source_unit','source_workbook_sha256')


def sha(buf):return hashlib.sha256(buf).hexdigest()


def positive_float(raw, desc, allow_zero=False):
    if raw in (None, '', '.'):raise ValueError(f'Missing observation {desc}')
    try:x=float(raw)
    except (ValueError,TypeError) as e:raise ValueError(f'Not numeric {desc}') from e
    if not math.isfinite(x) or x<0 or (x==0 and not allow_zero):raise ValueError(f'Invalid observation {desc}')
    return x


def q_index(date):
    m=re.fullmatch(r'(\d{4})-(\d\d)-(\d\d)',date)
    if not m:raise ValueError('Malformed FRED observation date')
    yr,mo,day=map(int,m.groups())
    if not(1<=mo<=12) or day!=1:raise ValueError('Unsupported FRED date convention')
    return f'{yr}Q{(mo-1)//3+1}',mo


def parse_fred(blob:bytes,series:str):
    """Strict raw source CSV parser. Missing outside study window may be ignored; inside cannot."""
    if series not in OUTCOMES or len(blob)>4_000_000:raise ValueError('Unexpected source series or input size')
    try:txt=blob.decode('utf-8-sig')
    except UnicodeDecodeError as e:raise ValueError('Bad FRED response encoding') from e
    reader=csv.DictReader(io.StringIO(txt))
    if not reader.fieldnames or len(reader.fieldnames)!=2 or reader.fieldnames[1]!=series or reader.fieldnames[0] not in ('DATE','observation_date'):
        raise ValueError('FRED returned unexpected columns or an HTML error')
    freq=OUTCOMES[series]['freq'];rows=defaultdict(dict);dates=set()
    for r in reader:
        date=r[reader.fieldnames[0]]
        if date in dates:raise ValueError('Duplicate FRED source date')
        dates.add(date)
        q,mo=q_index(date)
        if not('1945Q1'<=q<='2007Q4'):continue
        if freq=='quarterly' and mo not in (1,4,7,10):raise ValueError('Unexpected quarterly observation month')
        x=positive_float(r[series],f'{series} {date}',allow_zero=series=='UNRATE')
        if mo in rows[q]:raise ValueError('Duplicate FRED calendar month')
        rows[q][mo]=x
    start,end=OUTCOMES[series]['first'],OUTCOMES[series]['last']
    expected=[q for q in PERIODS if start<=q<=end]
    out={}
    for q in expected:
        if q not in rows:raise ValueError(f'Missing FRED {series} quarter {q}')
        months=rows[q]
        if freq=='monthly':
            qnum=int(q[-1]);expected_mons=set(range((qnum-1)*3+1,qnum*3+1))
            if set(months)!=expected_mons:raise ValueError(f'Incomplete quarter for {series} {q}')
            out[q]=sum(months.values())/3
        else:
            expected_month=(int(q[-1])-1)*3+1
            if set(months)!={expected_month}:raise ValueError('Nonstandard quarter or duplicate '+q)
            out[q]=months[expected_month]
    if len(out)!=OUTCOMES[series]['n']:raise ValueError('FRED coverage not equal to documented minimum')
    return out


def read_rr(folder:Path):
    manifest_path=folder/'private_file_manifest.json'
    if not manifest_path.is_file():raise ValueError('Private Romer manifest is missing')
    manifest=json.loads(manifest_path.read_text())
    if manifest.get('study_id')!=SOURCE_STUDY or manifest.get('workbook_sha256')!=RR_SHA:
        raise ValueError('Wrong Romer study or source vintage')
    files=manifest.get('files',{})
    if set(files)!={f'romer_tax_{v}_quarterly.csv' for v in RR_VARS}:raise ValueError('Source files differ from approved set')
    allrows={}
    for variant in RR_VARS:
        name=f'romer_tax_{variant}_quarterly.csv';blob=(folder/name).read_bytes()
        if sha(blob)!=files[name]['sha256'] or len(blob)!=files[name]['bytes'] or files[name]['rows']!=252:
            raise ValueError('Private Romer source checksum/count mismatch')
        reader=csv.DictReader(io.StringIO(blob.decode('utf-8-sig')))
        if tuple(reader.fieldnames or [])!=ORIGINAL_FIELDS:raise ValueError('Private Romer source columns changed')
        rows=list(reader)
        if len(rows)!=252 or tuple(r['period'] for r in rows)!=PERIODS:raise ValueError('Private Romer source dates shifted')
        for i,r in enumerate(rows):
            if r['source_variant']!=variant or r['source_workbook_sha256']!=RR_SHA:
                raise ValueError('Private Romer wrong variant or vintage')
            if r['source_excel_row']!=str(i+13):raise ValueError('Source Excel row shifted')
            for k in ('deficit_motivation_original_value','long_run_growth_motivation_original_value','exogenous_combined_original_value'):
                try: v=float(r[k])
                except (ValueError, TypeError) as exc:raise ValueError('Nonnumeric source shock') from exc
                if not math.isfinite(v):raise ValueError('Nonfinite source shock')
            a,b,c=(float(r[x]) for x in ('deficit_motivation_original_value','long_run_growth_motivation_original_value','exogenous_combined_original_value'))
            if abs(a+b-c)>0.001:raise ValueError('Exogenous components fail identity check')
        nonzero=sum(float(r['exogenous_combined_original_value'])!=0 for r in rows)
        if nonzero!={'nonretroactive':45,'retroactive':53,'present_value':29}[variant]:raise ValueError('Source nonzero counts have changed')
        allrows[variant]=rows
    return allrows,sha(manifest_path.read_bytes())


def fetch_fred(series):
    if series not in OUTCOMES:raise ValueError('Unapproved FRED series')
    url=f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}'
    req=urllib.request.Request(url,headers={'User-Agent':'ResearchDataAudit/1.0 (public academic economic research)', 'Accept':'text/csv'})
    with urllib.request.urlopen(req,timeout=50) as resp:
        target=resp.geturl();p=urlsplit(target)
        if p.scheme!='https' or p.hostname!='fred.stlouisfed.org':raise ValueError('Unexpected FRED redirect destination')
        blob=resp.read(4_000_001)
    if len(blob)>4_000_000 or not blob:raise ValueError('FRED input size invalid')
    return blob,url


def build(private_source_dir:Path, private_out:Path, public_out:Path, blobs=None):
    if private_out.exists() and any(private_out.iterdir()):raise FileExistsError('Private output not empty')
    if public_out.exists() and any(public_out.iterdir()):raise FileExistsError('Public output not empty')
    private_out.mkdir(parents=True,exist_ok=True);public_out.mkdir(parents=True,exist_ok=True)
    rr,manifest_sha=read_rr(private_source_dir)
    data={};rawmeta={}
    for name in OUTCOMES:
        if blobs is None:blob,url=fetch_fred(name)
        else:blob,url=blobs[name],f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={name}'
        data[name]=parse_fred(blob,name)
        out=private_out/f'FRED_{name}_raw.csv';out.write_bytes(blob)
        rawmeta[name]={'provider':OUTCOMES[name]['provider'],'frequency':OUTCOMES[name]['freq'],
            'source_url':url,'raw_file':out.name,'raw_sha256':sha(blob),'source_unit':OUTCOMES[name]['units'],
            'quarters_aligned':len(data[name]),'first_quarter':OUTCOMES[name]['first'],'last_quarter':OUTCOMES[name]['last']}
    columns=list(ORIGINAL_FIELDS)+[OUTCOMES[s]['quarter_column'] for s in OUTCOMES]
    aligned=private_out/'romer_policy_outcomes_quarterly_aligned_PRIVATE.csv'
    missing={k:0 for k in OUTCOMES}
    with aligned.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for variant in RR_VARS:
            for r in rr[variant]:
                row=dict(r);q=r['period']
                for s,meta in OUTCOMES.items():
                    v=data[s].get(q)
                    if v is None:missing[s]+=1
                    row[meta['quarter_column']]='' if v is None else format(v,'.17g')
                writer.writerow(row)
    if missing!={'PAYEMS':0,'UNRATE':36,'GDPC1':24,'GPDIC1':24}:
        raise ValueError('Quarterly data alignment misses unexpected number of observations')
    manifest={'study':SOURCE_STUDY,'phase7_private_manifest_sha256':manifest_sha,'outcome_sources':rawmeta,
              'joined_private_csv':aligned.name,'joined_sha256':sha(aligned.read_bytes()),
              'original_policy_rows':756,'outcome_measurements_present':sum(756-x for x in missing.values()),
              'outcome_missing_cells':missing,'joined_policy_outcome_rows':756,
              'join_definition':'USA, exact YYYYQquarter matching; 3 RR variants kept separate',
              'source_rows_replicated_by_variant':True,'missing_values_are_blank_not_zero':True,
              'fiscal_policy_identification_independently_verified':False,
              'causal_inference_ready':False,'is_replication_of_original_paper':False,
              'FRED_vintage_policy':'current FRED values at retrieval; not historical real-time vintages',
              'numeric_series_publication_allowed':False,'created_utc':datetime.now(timezone.utc).isoformat()}
    private_manifest=private_out/'private_alignment_manifest.json';private_manifest.write_text(json.dumps(manifest,indent=2)+'\n')
    public={'status':'HISTORICAL_SOURCE_ALIGNMENT_VALIDATED_NOT_CAUSAL',
            'original_policy_rows':756,'joined_policy_outcome_rows':756,
            'outcome_series':{k:{q:v for q,v in rawmeta[k].items() if q not in ('raw_file',)} for k in rawmeta},
            'outcome_measurements_present':manifest['outcome_measurements_present'],
            'outcome_missing_cells':missing,'private_alignment_manifest_sha256':sha(private_manifest.read_bytes()),
            'numeric_source_values_in_public_report':False,'causal_inference_ready':False,
            'remarks':['Only quarterly geographic/time alignment, no causal model or policy attribution.',
                       'FRED series reflect later revisions; vintages not fixed as-of the historical quarter.',
                       'Gross private domestic investment is not corporate investment alone.',
                       'PAYEMS is payroll jobs, not distinct employed people; quarterly mean of monthly stocks.',
                       'The three Romer variants are alternative definitions, not independent policy shocks.',
                       'Original Romer tax-change units remain as stored, not silently normalized.',
                       'No assurance of independent identification or research replication.']}
    (public_out/'historical_outcome_alignment_audit.json').write_text(json.dumps(public,indent=2)+'\n')
    print(json.dumps({'status':public['status'],'policy_rows':756,
           'quarterly_outcome_measurements_present':public['outcome_measurements_present'],
           'quarterly_outcome_missing_cells':public['outcome_missing_cells'],
           'private_outputs':len(list(private_out.iterdir())),
           'numeric_values_published':False,'causal_inference_ready':False},indent=2))
    return public


def self_test():
    header='observation_date,UNRATE\n'
    ok=header+'1948-01-01,4\n1948-02-01,5\n1948-03-01,6\n'
    try:parse_fred(ok.encode(),'UNRATE')
    except ValueError as e:
        assert 'Missing FRED UNRATE quarter' in str(e)
    else:raise AssertionError('Incomplete series accepted')
    bad='observation_date,PAYEMS\n1945-01-01,100\n1945-02-01,110\n1945-01-01,110\n'
    try:parse_fred(bad.encode(),'PAYEMS')
    except ValueError as e:assert 'Duplicate' in str(e)
    else:raise AssertionError('Duplicate source dates accepted')
    valid_months=[]
    for yr in range(1945,2008):
        for mo in range(1,13):valid_months.append((f'{yr}-{mo:02d}-01', 100+yr-1945+mo))
    pay=('DATE,PAYEMS\n'+'\n'.join(f'{d},{n}' for d,n in valid_months)+'\n').encode()
    got=parse_fred(pay,'PAYEMS')
    assert len(got)==252 and abs(got['1945Q1']-102)<1e-9
    un=('DATE,UNRATE\n'+'\n'.join(f'{d},5' for d,n in valid_months if d[:4]>='1948')+'\n').encode()
    assert len(parse_fred(un,'UNRATE'))==240
    gd=('DATE,GDPC1\n'+'\n'.join(f'{yr}-{m:02d}-01,{100+yr-1947}' for yr in range(1947,2008) for m in (1,4,7,10))+'\n').encode()
    gp=gd.replace(b'GDPC1',b'GPDIC1')
    assert len(parse_fred(gd,'GDPC1'))==244
    assert len(parse_fred(gp,'GPDIC1'))==244
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);src=root/'rr';src.mkdir();files={}
        for variant in RR_VARS:
            f=src/f'romer_tax_{variant}_quarterly.csv'
            with f.open('w',newline='') as stream:
                w=csv.DictWriter(stream,fieldnames=ORIGINAL_FIELDS);w.writeheader()
                count={'nonretroactive':45,'retroactive':53,'present_value':29}[variant]
                for i,q in enumerate(PERIODS):
                    w.writerow({'period':q,'source_variant':variant,
                        'deficit_motivation_original_value':'.1' if i<count else '0',
                        'long_run_growth_motivation_original_value':'0',
                        'exogenous_combined_original_value':'.1' if i<count else '0',
                        'source_sheet':'Tax Measures','source_excel_row':i+13,
                        'source_workbook_sha256':RR_SHA})
            files[f.name]={'sha256':sha(f.read_bytes()),'bytes':f.stat().st_size,'rows':252}
        (src/'private_file_manifest.json').write_text(json.dumps({'study_id':SOURCE_STUDY,'workbook_sha256':RR_SHA,'files':files}))
        public=build(src,root/'private',root/'public',blobs={'PAYEMS':pay,'UNRATE':un,'GDPC1':gd,'GPDIC1':gp})
        assert public['outcome_measurements_present']==2940
        assert public['outcome_missing_cells']=={'PAYEMS':0,'UNRATE':36,'GDPC1':24,'GPDIC1':24}
        assert not public['numeric_source_values_in_public_report']
        assert len(list((root/'private').iterdir()))==6
        assert len(list((root/'public').iterdir()))==1
    print('PASS: no missing-to-zero, 3-month quarter, source fingerprint and private/public output guards')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--self-test',action='store_true')
    ap.add_argument('--private-source-dir',type=Path)
    ap.add_argument('--private-out',type=Path)
    ap.add_argument('--public-out',type=Path)
    a=ap.parse_args()
    if a.self_test:self_test();return
    if not(a.private_source_dir and a.private_out and a.public_out):
        ap.error('Require --private-source-dir, --private-out, --public-out')
    build(a.private_source_dir,a.private_out,a.public_out)

if __name__=='__main__':main()
