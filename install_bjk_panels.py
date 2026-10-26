#!/usr/bin/env python3
"""Install independently sourced BJK descriptive tax panels into an economic warehouse.
Run in the repository root with the ZIP from this package present: python install_bjk_panels.py --archive bjk_descriptive_panels_and_audit.zip
This changes snapshot.py only to register two optional, noncausal source families.
"""
import argparse,csv,hashlib,io,json,math,re,zipfile
from pathlib import Path
from datetime import datetime,timezone

STATE=['corp_min_rate_state','corp_max_rate_state','sales_rate_state','prop_rate_state','corp_rate_state','gas_rate_state','spirit_rate_state','wine_rate_state','beer_rate_state','median_inc_rate_state','top_inc_rate_state']
COUNTY=['prop_rate','sales_rate','mean_inc_rate']
FIELDS=['provider','country_iso3','year','period','series','value','unit','frequency','disaggregation','obs_status','source_url','retrieved_utc']
SOURCE='https://www.openicpsr.org/openicpsr/project/208462/version/V2/view'
PAPER='Baker, Janas and Kueng, Correlation in State and Local Tax Changes, NBER WP 32786'
NAMES={'state':('bjk_state_tax_panel_descriptive.csv','bjk_state_rates','bjk_state_rates_panel.csv',STATE),
       'county':('bjk_county_tax_panel_descriptive.csv','bjk_county_rates','bjk_county_rates_panel.csv',COUNTY)}

def digest(b): return hashlib.sha256(b).hexdigest()

def convert(archive,data_dir):
    z=zipfile.ZipFile(archive)
    audit=json.loads(z.read('bjk_source_audit.json'))
    assert audit['status']=='VALIDATED_DESCRIPTIVE_IMPORT'
    prepared=[]
    for typ,(filename,family,output,variables) in NAMES.items():
        raw=z.read(filename)
        assert digest(raw)==audit['sources'][typ]['output_sha256'], f'SHA mismatch for {filename}'
        reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
        assert set(variables).issubset(reader.fieldnames)
        rows=list(reader); assert len(rows)==audit['sources'][typ]['rows']
        source_keys=set(); output_rows=[];missing={v:0 for v in variables}; zeros={v:0 for v in variables};timestamp=datetime.now(timezone.utc).isoformat()
        geos=set(); years=set()
        for r in rows:
            yr=int(r['year']); assert 1900<=yr<=2026
            if typ=='state':
                state=r['stateabbr']; assert re.fullmatch('[A-Z]{2}',state)
                key=(state,yr);dim={'state_abbr':state,'state_fips':r['fipstate'].zfill(2),'state_name':r['state'],'geography':'state'}
            else:
                state=r['state']; fips=r['fips'].zfill(5)
                assert re.fullmatch('[A-Z]{2}',state) and re.fullmatch(r'\d{5}',fips)
                key=(fips,yr);dim={'state_abbr':state,'state_fips':r['state_fips'].zfill(2),'county_fips':r['county_fips'].zfill(3),'county_geoid':fips,'geography':'county'}
            assert key not in source_keys, f'duplicate key {key}'
            source_keys.add(key);geos.add(key[0]);years.add(yr)
            for v in variables:
                value=r[v].strip()
                if not value: missing[v]+=1;continue
                x=float(value);assert math.isfinite(x),f'nonfinite source value {key} {v}'
                if x==0:zeros[v]+=1
                output_rows.append(dict(provider='Baker-Janas-Kueng public V2 tax rates',country_iso3='USA',year=yr,period=str(yr),series='BJK.'+v,value=value,unit='source_native_tax_rate_or_excise_measure',frequency='annual',disaggregation=json.dumps({**dim,'original_variable':v,'source_vintage':'V2'},sort_keys=True),obs_status='source_observed_descriptive_not_causal',source_url=SOURCE,retrieved_utc=timestamp))
        root=Path(data_dir)/family; (root/'processed').mkdir(parents=True,exist_ok=True)
        target=root/'processed'/output
        with target.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(output_rows)
        report={'status':'VALIDATED_DESCRIPTIVE_NOT_CAUSAL','errors':[],'conflicts':[],'research_ready':False,'is_causal_policy_shock':False,'source':PAPER,'source_url':SOURCE,'source_file_sha256':digest(raw),'output_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'observations':len(output_rows),'state_or_county_years':len(rows),'geographies':len(geos),'year_min':min(years),'year_max':max(years),'missing_by_variable':missing,'zero_by_variable':zeros,'limitations':['Source units and meanings retained without generic percent conversion; verify each rate or excise measure against author codebook before substantive interpretation.','Different tax types may change together, tax-rate changes are not identified exogenous shocks.','Missing numeric source cells are omitted, not zero-filled.','County GEOIDs maintained as padded 5-character strings.','No fiscal or policy-effective date reconstructed from annual rates.']}
        (root/'run_report.json').write_text(json.dumps(report,indent=2)+'\n')
        prepared.append((family,output,len(output_rows),len(rows)))
    return prepared

def patch_snapshot(path):
    p=Path(path);s=p.read_text(); anchor="    'us_state_tax': ('us_state_tax', 'us_state_tax_panel.csv', 5000),";assert s.count(anchor)==1,'Expected warehouse source family anchor missing or ambiguous'
    entries="    'bjk_state_rates': ('bjk_state_rates', 'bjk_state_rates_panel.csv', 10000),\n    'bjk_county_rates': ('bjk_county_rates', 'bjk_county_rates_panel.csv', 100000),"
    if "'bjk_state_rates':" in s or "'bjk_county_rates':" in s:
        assert entries in s,'BJK family definitions exist but differ; review manually'
        return 'already installed'
    edited=s.replace(anchor,anchor+'\n'+entries)
    compile(edited,str(p),'exec');p.write_text(edited)
    return 'patched'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--archive',default='bjk_descriptive_panels_and_audit.zip');ap.add_argument('--data-dir',default='data');ap.add_argument('--snapshot-script',default='warehouse/snapshot.py');a=ap.parse_args()
    done=convert(a.archive,a.data_dir)
    outcome=patch_snapshot(a.snapshot_script)
    print(json.dumps({'prepared':done,'snapshot_patch':outcome,'note':'Descriptive only; do not treat as causal data.'},indent=2))
if __name__=='__main__':main()
