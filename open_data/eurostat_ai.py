import csv, datetime as dt, hashlib, json, math, pathlib, urllib.parse, urllib.request
out=pathlib.Path('research/eurostat_ai_adoption');out.mkdir(parents=True,exist_ok=True)
dataset='isoc_eb_ai'
params={'freq':'A','nace_r2':'C10-S951_X_K','size_emp':'GE10',
        'indic_is':'E_AI_TANY','unit':'PC_ENT','lang':'EN','format':'JSON'}
url='https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/'+dataset+'?'+urllib.parse.urlencode(params)
req=urllib.request.Request(url,headers={'User-Agent':'GlobalEconomicPolicyResearch/1.0','Accept':'application/json'})
with urllib.request.urlopen(req, timeout=80) as resp:
    raw=resp.read()
obj=json.loads(raw)
dims=obj['id']; sizes=obj['size']; values=obj['value']
assert len(dims)==len(sizes) and 'geo' in dims and 'time' in dims
assert math.prod(sizes)<1000000 and isinstance(values,(dict,list))
decoded=[]
for dimension,size in zip(dims,sizes):
    index=obj['dimension'][dimension]['category']['index']
    if isinstance(index,dict):
        labels=[None]*size
        for key,pos in index.items():
            assert isinstance(pos,int) and 0<=pos<size and labels[pos] is None
            labels[pos]=key
    else:
        labels=index
    assert len(labels)==size and all(x is not None for x in labels)
    decoded.append(labels)
strides=[math.prod(sizes[i+1:]) for i in range(len(sizes))]
pairs=values.items() if isinstance(values,dict) else enumerate(values)
rows=[]; statuses=obj.get('status') or {}
for flat,value in pairs:
    if value is None:continue
    index=int(flat)
    assert 0<=index<math.prod(sizes)
    tags={d:labels[(index//stride)%size] for d,labels,stride,size in zip(dims,decoded,strides,sizes)}
    if any(tags.get(k)!=v for k,v in [('freq','A'),('nace_r2','C10-S951_X_K'),('size_emp','GE10'),('indic_is','E_AI_TANY'),('unit','PC_ENT')]):continue
    year=int(tags['time']);number=float(value)
    assert 2021<=year<=2026 and math.isfinite(number) and 0<=number<=100
    geo=tags['geo']
    flag=statuses.get(str(index),'') if isinstance(statuses,dict) else ''
    rows.append({'geo':geo,'year':year,'indicator':'E_AI_TANY',
                 'value_percent':number,'status_flag':str(flag),
                 'provider':'Eurostat','dataset':dataset,'source_url':url})
rows.sort(key=lambda r:(r['geo'],r['year']))
assert len(rows)>=50, f'Insufficient observations: {len(rows)}'
assert len({(r['geo'],r['year']) for r in rows})==len(rows), 'Duplicate country-year'
eu2025=[r['value_percent'] for r in rows if r['geo']=='EU27_2020' and r['year']==2025]
assert len(eu2025)==1 and 19.0<eu2025[0]<21.0, f'EU 2025 reference mismatch: {eu2025}'
(out/'official_response.json').write_bytes(raw)
with (out/'ai_adoption_panel.csv').open('w',newline='',encoding='utf-8') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
report={'dataset':dataset,'retrieved_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'source_url':url,'observations':len(rows),'countries_or_areas':len({r['geo'] for r in rows}),
        'years':sorted({r['year'] for r in rows}),
        'eu27_2025_percent':eu2025[0],
        'dimensions_selected':params,'raw_sha256':digest(out/'official_response.json'),
        'panel_sha256':digest(out/'ai_adoption_panel.csv'),
        'coverage_status':'PARTIAL_DATASET_INGESTION',
        'research_ready':False,
        'notes':['Eurostat enterprise survey, firms with 10+ persons; not all firms.',
                 'Adoption is not causal employment or productivity effect.',
                 'Country codes may include aggregates; check flags and break-in-series metadata.',
                 'Source metadata and historical revisions need deeper verification.']}
(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))

# Preserve the source extract in the standard economic warehouse layout.
import pathlib, csv, json
rawdir=pathlib.Path('research/eurostat_ai_adoption')
root=pathlib.Path('data/eurostat_ai');(root/'processed').mkdir(parents=True,exist_ok=True)
with (rawdir/'ai_adoption_panel.csv').open(newline='',encoding='utf-8') as f:
    rows=list(csv.DictReader(f))
assert len(rows)>=50
output=[]
for x in rows:
    output.append({'country_iso3':x['geo'],'year':x['year'],'period':x['year'],
      'series':'isoc_eb_ai.E_AI_TANY.C10-S951_X_K.GE10',
      'value':x['value_percent'],'unit':'percent_of_enterprises_10plus',
      'frequency':'annual','provider':'Eurostat enterprise AI survey',
      'status_flag':x['status_flag'],'source_url':x['source_url'],
      'observation_kind':'survey_estimate'})
panel=root/'processed'/'eurostat_ai_panel.csv'
with panel.open('w',newline='',encoding='utf-8') as f:
    writer=csv.DictWriter(f,fieldnames=list(output[0]));writer.writeheader();writer.writerows(output)
manifest=json.loads((rawdir/'manifest.json').read_text())
manifest.update({'observations':len(output),'errors':[],'conflicts':[]})
(root/'run_report.json').write_text(json.dumps(manifest,indent=2)+'\n')
