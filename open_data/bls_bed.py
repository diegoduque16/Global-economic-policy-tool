import csv, datetime, hashlib, io, json, math, pathlib, urllib.request
output=pathlib.Path('research/bls_bed');output.mkdir(parents=True,exist_ok=True)
url='https://www.bls.gov/web/cewbd/bd_data_ann.txt'
report={'source':'BLS annual Business Employment Dynamics','source_url':url,'status':'NOT_VALIDATED','research_ready':False,
        'notes':['Annual March-to-March gross flows differ from the sum of quarterly gross flows',
                 'Values are thousands of jobs; establishments and rates are excluded',
                 'National and state values overlap; never sum across both',
                 'Annual quarter reference is retained to prevent accidental mixing']}
try:
    req=urllib.request.Request(url,headers={'User-Agent':'EconomicPolicyResearch/1.0 (source audit)','Accept':'text/plain,*/*'})
    with urllib.request.urlopen(req,timeout=100) as response:
        raw=response.read(80_000_001)
    if len(raw)>80_000_000: raise ValueError('BLS annual file exceeds 80MB; refusing unbounded download')
    if raw.lstrip().lower().startswith((b'<html',b'<!doctype')): raise ValueError('BLS returned HTML instead of the annual data file')
    report['source_sha256']=hashlib.sha256(raw).hexdigest();report['download_bytes']=len(raw)
    reader=csv.reader(io.StringIO(raw.decode('utf-8-sig')),delimiter='\t')
    rows=[];dup=set();skipped=0
    for fields in reader:
        if len(fields)<4:continue
        sid=fields[0].strip();year_text=fields[1].strip();quarter=fields[2].strip()
        if len(sid)<28 or not year_text.isdecimal():continue
        # Official BLS BED positions: seasonal, geography, industry, unit,
        # data-element, establishment-size, data-class, level, annual, ownership.
        if not (sid[:2]=='BD' and sid[2]=='U' and sid[3:8]=='00000' and
            sid[10:13]=='000' and sid[13:19]=='000000' and sid[19:21]=='11' and
            sid[21:23]=='00' and sid[23:25] in ('01','04') and
            sid[25:28]=='LA5' and quarter=='Q01'):continue
        year=int(year_text)
        if not 1992<=year<=2026:continue
        value_text=fields[3].strip().replace(',','')
        try:value=float(value_text)
        except ValueError:skipped+=1;continue
        if not math.isfinite(value) or value<0:raise ValueError('Invalid BLS job-flow value')
        state=sid[8:10];kind='gross_job_gains' if sid[23:25]=='01' else 'gross_job_losses'
        key=(state,year,quarter,kind)
        if key in dup:raise ValueError('Duplicate BED observations: '+str(key))
        dup.add(key)
        rows.append({'provider':'US BLS','dataset':'Business Employment Dynamics annual',
           'geography_level':'national' if state=='00' else 'state','state_fips':state,
           'year':year,'reference_quarter':quarter,'series':kind,
           'value':value,'unit':'thousands_of_jobs','series_id':sid,'source_url':url})
    rows.sort(key=lambda r:(r['state_fips'],r['series'],r['year']))
    if len(rows)<200 or len({r['year'] for r in rows})<10 or not {'gross_job_gains','gross_job_losses'}<=set(r['series'] for r in rows):
        raise ValueError('Coverage insufficient or source structure changed: '+str(len(rows)))
    if not any(r['state_fips']=='00' for r in rows):raise ValueError('National series missing')
    panel=output/'job_flows.csv'
    with panel.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    report.update({'status':'VALIDATED_EXTRACT','observations':len(rows),
         'geographies':len({r['state_fips'] for r in rows}),
         'earliest_year':min(r['year'] for r in rows),'latest_year':max(r['year'] for r in rows),
         'non_numeric_rows_skipped':skipped,'panel_sha256':hashlib.sha256(panel.read_bytes()).hexdigest()})
except Exception as exc:
    report['error']=f'{type(exc).__name__}: {str(exc)[:350]}'
(output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
if report['status']!='VALIDATED_EXTRACT':raise SystemExit('BLS BED validation failed; see diagnostic artifact')

# Adapt validated BED rows into the versioned warehouse's native observation schema.
import csv, json, pathlib
source_root = pathlib.Path('research/bls_bed')
report = json.loads((source_root/'report.json').read_text(encoding='utf-8'))
assert report['status'] == 'VALIDATED_EXTRACT'
with (source_root/'job_flows.csv').open(newline='',encoding='utf-8') as f:
    records = list(csv.DictReader(f))
assert len(records) == report['observations'] >= 200
root = pathlib.Path('data/bls_bed'); (root/'processed').mkdir(parents=True,exist_ok=True)
out = []
for r in records:
    extra = {k:r[k] for k in ('state_fips','geography_level','reference_quarter','series_id')}
    out.append(dict(country_iso3='USA',year=r['year'],period=r['year'],
       series='BLS_BED.'+r['series']+'.annual_march_to_march',value=r['value'],
       unit='thousands_of_jobs',frequency='annual',provider='US Bureau of Labor Statistics BED',
       disaggregation=json.dumps(extra,sort_keys=True),
       obs_status='source_reported',source_url=r['source_url']))
path = root/'processed'/'bls_bed_panel.csv'
with path.open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
report.update({'errors':[],'conflicts':[],'observations':len(out),
    'warehouse_adapter':'bls_bed','research_ready':False})
(root/'run_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('BLS BED warehouse rows:',len(out))

