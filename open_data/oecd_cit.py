import csv, datetime, hashlib, io, json, math, pathlib, urllib.error, urllib.request, time
output=pathlib.Path('research/oecd_cit');output.mkdir(parents=True,exist_ok=True)
url=('https://sdmx.oecd.org/public/rest/data/'
     'OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,2.0/.A..ST..S13...?'
     'startPeriod=1990&endPeriod=2026&dimensionAtObservation=AllDimensions&format=csvfile')
report={'source':'OECD Corporate Tax Statistics','dataset':'DSD_TAX_CIT@DF_CIT version 2.0',
        'source_url':url,'status':'NOT_VALIDATED','research_ready':False,
        'notes':['Statutory tax rates are distinct from effective tax rates and tax revenues',
                 'Legal tax rates do not independently identify causal policy shocks',
                 'Preserve targeting, jurisdiction, sector and original unit codes']}
try:
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'EconomicPolicyResearch/1.0 (source audit)',
                   'Accept':'text/csv,application/vnd.sdmx.csv,*/*'})
            with urllib.request.urlopen(req,timeout=95) as response: raw=response.read(15_000_001)
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429,500,502,503,504) or attempt==2:raise
            time.sleep(3*(attempt+1))
    if len(raw)>15_000_000:raise ValueError('OECD response exceeds 15MB')
    if raw.lstrip().lower().startswith((b'<html',b'<!doctype',b'<?xml')):raise ValueError('Expected SDMX CSV, received markup')
    report['source_sha256']=hashlib.sha256(raw).hexdigest();report['download_bytes']=len(raw)
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    columns=reader.fieldnames or []
    if not {'REF_AREA','TIME_PERIOD','OBS_VALUE'}.issubset(columns):
        raise ValueError('OECD CSV missing essential columns: '+repr(columns[:20]))
    report['columns']=columns
    # Never assert the meaning of an unknown SDMX dimension.
    target_col=next((c for c in ('TARGETING','TAX_TARGETING') if c in columns),None)
    sector_col=next((c for c in ('INSTITUTIONAL_SECTOR','SECTOR') if c in columns),None)
    if not target_col or not sector_col:raise ValueError('Unable to confirm statutory-rate and government-sector dimensions')
    rows=[];keys=set()
    for entry in reader:
        if entry.get(target_col)!='ST' or entry.get(sector_col)!='S13':continue
        try:year=int(entry.get('TIME_PERIOD',''));value=float(entry.get('OBS_VALUE',''))
        except (TypeError,ValueError):continue
        if not (1990<=year<=2026 and math.isfinite(value) and 0<=value<=100):continue
        geo=(entry.get('REF_AREA') or '').strip()
        if not geo:continue
        dimensions={k:(v or '') for k,v in entry.items() if k not in ('OBS_VALUE','TIME_PERIOD','REF_AREA')}
        series='DSD_TAX_CIT@DF_CIT:'+':'.join((dimensions.get(k,'') for k in columns if k not in ('REF_AREA','TIME_PERIOD','OBS_VALUE','DATAFLOW','OBS_STATUS','DECIMALS','UNIT_MULT')))
        unique=(geo,year,series)
        if unique in keys:raise ValueError('Duplicate OECD corporate tax observation '+str(unique))
        keys.add(unique)
        rows.append({'provider':'OECD','dataset':'Corporate income tax statutory rates',
            'geo':geo,'year':year,'series':series,'value':value,
            'source_unit':entry.get('UNIT_MEASURE') or entry.get('UNIT') or 'source_defined',
            'obs_status':entry.get('OBS_STATUS') or '',
            'dimensions_json':json.dumps(dimensions,sort_keys=True),'source_url':url})
    rows.sort(key=lambda r:(r['geo'],r['year'],r['series']))
    if len(rows)<100 or len({r['geo'] for r in rows})<10:
        raise ValueError('Insufficient country-rate observations; schema or query needs revision: '+str(len(rows)))
    panel=output/'corporate_tax_rates.csv'
    with panel.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    report.update({'status':'VALIDATED_EXTRACT','observations':len(rows),
        'countries_or_areas':len({r['geo'] for r in rows}),
        'earliest_year':min(r['year'] for r in rows),'latest_year':max(r['year'] for r in rows),
        'panel_sha256':hashlib.sha256(panel.read_bytes()).hexdigest()})
except Exception as exc:
    report['error']=f'{type(exc).__name__}: {str(exc)[:350]}'
(output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
if report['status']!='VALIDATED_EXTRACT':raise SystemExit('OECD CIT validation failed; see diagnostic artifact')

# Preserve every original OECD SDMX code without inferring legal/causal effects.
import csv, json, pathlib
source_root = pathlib.Path('research/oecd_cit')
report = json.loads((source_root/'report.json').read_text(encoding='utf-8'))
assert report['status'] == 'VALIDATED_EXTRACT'
with (source_root/'corporate_tax_rates.csv').open(newline='',encoding='utf-8') as f:
    records = list(csv.DictReader(f))
assert len(records)==report['observations'] >= 100
root=pathlib.Path('data/oecd_cit');(root/'processed').mkdir(parents=True,exist_ok=True)
out=[]
for r in records:
    out.append(dict(country_iso3=r['geo'],year=r['year'],period=r['year'],
       series=r['series'],value=r['value'],unit=r['source_unit'],frequency='annual',
       provider='OECD Corporate Tax Statistics',disaggregation=r['dimensions_json'],
       obs_status=r['obs_status'],source_url=r['source_url']))
path=root/'processed'/'oecd_cit_panel.csv'
with path.open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
report.update({'errors':[],'conflicts':[],'observations':len(out),
    'warehouse_adapter':'oecd_cit','research_ready':False})
(root/'run_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('OECD CIT warehouse rows:',len(out))

