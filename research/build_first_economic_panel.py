#!/usr/bin/env python3
"""Combine *real* existing WID SQLite and canonical WDI SQLite values in a
source-audited, descriptive country-year research panel. No causal estimation.

All inputs remain unchanged; outputs are new and versioned by caller.
Stdlib only. Does NOT use catalog/metadata as data values.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, hashlib, json, math, sqlite3
from collections import Counter, defaultdict
from pathlib import Path

WID_EXPECTED=21430
WID_COUNTRIES=23
CANONICAL_EXPECTED=2780117
WDI_TARGETS={
 'NY.GDP.MKTP.KD.ZG': ('gdp_growth_pct', 'annual_pct'),
 'NY.GDP.PCAP.KD.ZG': ('gdp_per_capita_growth_pct', 'annual_pct'),
 'NY.GDP.PCAP.PP.KD': ('gdp_per_capita_ppp_constant', 'constant_international_dollar_source_vintage'),
 'SL.UEM.TOTL.ZS': ('unemployment_modelled_ilo_pct', 'pct_labor_force'),
 'SL.EMP.TOTL.SP.ZS': ('employment_population_ratio_pct', 'pct_age_15plus'),
 'GC.TAX.TOTL.GD.ZS': ('tax_revenue_pct_gdp', 'pct_gdp_source_defined_scope'),
 'GC.XPN.TOTL.GD.ZS': ('government_expense_pct_gdp', 'pct_gdp_source_defined_scope'),
 'SI.POV.GINI': ('world_bank_survey_gini', 'index_0_100'),
}
WID_TARGETS={
 ('p99p100','pretax_national_income_share'): 'top1_pretax_income_share',
 ('p99p100','personal_wealth_share'): 'top1_wealth_share',
 ('p0p50','pretax_national_income_share'): 'bottom50_pretax_income_share',
 ('p0p50','posttax_national_income_share'): 'bottom50_posttax_income_share',
 ('p0p50','posttax_disposable_income_share'): 'bottom50_disposable_income_share',
 ('p90p100','pretax_national_income_share'): 'top10_pretax_income_share',
}
WID_NAMES=tuple(WID_TARGETS.values())
WDI_NAMES=tuple(v[0] for v in WDI_TARGETS.values())
FIELDS=['iso3','year']+list(WID_NAMES)+list(WDI_NAMES)


def digest(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()


def check_whitelist(p):
 obj=json.loads(Path(p).read_text())
 codes=set()
 for group in obj.values():
  if isinstance(group,dict): codes.update(group)
 if not set(WDI_TARGETS).issubset(codes):raise ValueError('Selected WDI source indicator definitions missing from reviewed catalog')


def load_wid(con):
 n=con.execute('SELECT COUNT(*) FROM wid_numeric_observations').fetchone()[0]
 if n!=WID_EXPECTED:raise ValueError(f'WID numeric row-count regression: {n}')
 valid=con.execute("SELECT COUNT(*) FROM wid_numeric_observations WHERE typeof(value_fraction)='real' AND value_fraction IS NOT NULL").fetchone()[0]
 if valid!=n:raise ValueError('WID contains non-REAL numeric records')
 countries=con.execute('SELECT COUNT(*) FROM source_files').fetchone()[0]
 if countries!=WID_COUNTRIES:raise ValueError('WID source country count regression')
 if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('WID SQLite integrity failure')
 out=defaultdict(dict); coverage=Counter();seen=set()
 for iso,y,pct,concept,v in con.execute('SELECT iso3,year,percentile,concept,value_fraction FROM wid_numeric_observations ORDER BY iso3,year'):
  key=(pct,concept)
  if key not in WID_TARGETS:continue
  if not iso or len(iso)!=3 or not 1700<=y<=2100 or not math.isfinite(v):raise ValueError('Invalid WID source keys/values')
  name=WID_TARGETS[key];k=(iso,y,name)
  if k in seen:raise ValueError('Duplicate WID key')
  seen.add(k);out[(iso,y)][name]=v*100 # percent, explicitly converted from source fraction
  coverage[name]+=1
 if not any(out.values()):raise ValueError('No real WID measures found')
 return out,coverage,n,countries


def load_wdi(con,iso_codes):
 base=con.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
 if base!=CANONICAL_EXPECTED:raise ValueError(f'Canonical warehouse count regression: {base}')
 families=con.execute('SELECT COUNT(*) FROM source_files').fetchone()[0]
 if families<24:raise ValueError('Canonical warehouse source family regression')
 if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Canonical SQLite integrity failed')
 selected=tuple(WDI_TARGETS)
 q=('SELECT country_or_polity_code,year,series,value,provider,period,unit,source_status '
    'FROM observations WHERE family=? AND series IN ('+','.join('?'*len(selected))+') AND country_or_polity_code IN ('+','.join('?'*len(iso_codes))+')')
 out=defaultdict(dict);coverage=Counter();repeated=Counter();seen={}
 rows=con.execute(q,('wdi',*selected,*sorted(iso_codes)))
 for iso,year,series,v,provider,period,unit,status in rows:
  if not math.isfinite(v):raise ValueError('Non-finite WDI source data')
  if str(period)!=str(year):raise ValueError(f'Unexpected WDI frequency/period: {iso},{series},{year},{period}')
  if not provider or not unit:raise ValueError('WDI missing provenance/units')
  name=WDI_TARGETS[series][0];key=(iso,year,name)
  if key in seen:
   if abs(seen[key]-v)>1e-10:raise ValueError(f'Conflicting WDI numeric duplicates: {key}')
   repeated[name]+=1
   continue
  seen[key]=v;out[(iso,year)][name]=v;coverage[name]+=1
 if not out:raise ValueError('No numeric WDI values for WID countries found in canonical snapshot')
 return out,coverage,repeated,base,families


def pearson(vals):
 if len(vals)<6:return None
 x=[v[0] for v in vals];y=[v[1] for v in vals]
 mx=sum(x)/len(x);my=sum(y)/len(y)
 xx=sum((a-mx)**2 for a in x);yy=sum((b-my)**2 for b in y)
 if xx<1e-20 or yy<1e-20:return None
 return sum((a-mx)*(b-my) for a,b in vals)/math.sqrt(xx*yy)


def compute_exploration(rows):
 # These are exploratory correlations between values at the SAME observation year;
 # nothing is imputed, aggregated over missing years or interpreted as causal.
 out=[]
 for y in (2000,2010,2020,2024):
  subset=[r for r in rows if r['year']==y]
  for w in ('top1_pretax_income_share','top1_wealth_share','bottom50_posttax_income_share'):
   for m in ('gdp_per_capita_growth_pct','gdp_per_capita_ppp_constant','unemployment_modelled_ilo_pct','tax_revenue_pct_gdp'):
    pairs=[(r[w],r[m]) for r in subset if r[w] is not None and r[m] is not None]
    if len(pairs)>=6:
     out.append({'year':y,'inequality_measure':w,'macroeconomic_measure':m,
                 'matched_countries':len(pairs),'pearson_r_exploratory':pearson(pairs),
                 'interpretation':'DESCRIPTIVE_CORRELATION_NOT_CAUSAL'})
 return out


def write_csv(p,rows,fields):
 with Path(p).open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
  for r in rows:w.writerow({k:('' if r.get(k) is None else r.get(k)) for k in fields})


def build(wid_db,canonical_db,wdi_dictionary,outdir):
 outdir=Path(outdir)
 if outdir.exists() and any(outdir.iterdir()):raise FileExistsError('Refuse overwrite of existing research outputs')
 outdir.mkdir(parents=True,exist_ok=True)
 check_whitelist(wdi_dictionary)
 with sqlite3.connect(f'file:{Path(wid_db).resolve()}?mode=ro',uri=True) as w:
  wid,wcov,wtotal,wcount=load_wid(w)
 with sqlite3.connect(f'file:{Path(canonical_db).resolve()}?mode=ro',uri=True) as c:
  wdi,mcov,duplicates,total,families=load_wdi(c,{k[0] for k in wid})
 paired_keys=sorted(set(wid)&set(wdi))
 if not paired_keys:raise ValueError('No country-year WID/WDI overlap: stop rather than mark as integrated')
 countries={k[0] for k in paired_keys}
 rows=[]
 for iso,yr in paired_keys:
  a=wid[(iso,yr)];b=wdi[(iso,yr)]
  row={'iso3':iso,'year':yr,**{name:a.get(name) for name in WID_NAMES},**{name:b.get(name) for name in WDI_NAMES}}
  if not any(row[n] is not None for n in WID_NAMES) or not any(row[n] is not None for n in WDI_NAMES):raise ValueError('A joined row lacks actual data values')
  rows.append(row)
 if len(rows)<100 or len(countries)<10:raise ValueError('Insufficient actual joined numeric coverage: investigate source completeness')
 # Metadata stays in JSON; analytical table is actual measured values only.
 write_csv(outdir/'wid_wdi_country_year_numeric.csv',rows,FIELDS)
 coverage=[]
 for name in (*WID_NAMES,*WDI_NAMES):
  coverage.append({'measure':name,'source':'WID' if name in WID_NAMES else 'WorldBank_WDI',
                   'original_numeric_values_in_23countries':wcov[name] if name in WID_NAMES else mcov[name],
                   'paired_country_year_values':sum(r[name] is not None for r in rows),
                   'unit':'percentage_points_of_total' if name in WID_NAMES else next(v[1] for v in WDI_TARGETS.values() if v[0]==name),
                   'data_status':'REAL_NUMERIC_OBSERVATIONS_NOT_CAUSAL'})
 write_csv(outdir/'joined_measure_coverage.csv',coverage,['measure','source','original_numeric_values_in_23countries','paired_country_year_values','unit','data_status'])
 exp=compute_exploration(rows)
 write_csv(outdir/'exploratory_country_correlations.csv',exp,['year','inequality_measure','macroeconomic_measure','matched_countries','pearson_r_exploratory','interpretation'])
 report={'status':'REAL_WID_WDI_NUMERIC_COUNTRY_YEAR_PANEL_DESCRIPTIVE_ONLY',
         'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
         'canonical_observations_verified':total,'canonical_families_verified':families,
         'wid_numeric_observations_verified':wtotal,'wid_countries_verified':wcount,
         'country_year_rows_with_both_sources':len(rows),
         'countries_with_both_sources':len(countries),'observed_year_min':min(r['year'] for r in rows),
         'observed_year_max':max(r['year'] for r in rows),
         'wdi_exact_duplicate_rows_skipped':dict(duplicates),
         'total_matched_numeric_cells':sum(r[n] is not None for r in rows for n in (*WID_NAMES,*WDI_NAMES)),
         'exploratory_correlations_with_at_least_six_countries':len(exp),
         'wdi_unit_basis':'Whitelisted source indicator codes from repository open_data/wdi_indicators.json; source native unit column retained in original warehouse',
         'wid_unit_conversion':'Source fraction x100 => percentage points of total; comparison of distinct variables is descriptive',
         'read_only_sources':True,'policy_reforms_joined':0,'causal_effects_estimated':0,
         'provenance':{'wid_sqlite_sha256':digest(wid_db),'canonical_sqlite_sha256':digest(canonical_db),'wdi_dictionary_sha256':digest(wdi_dictionary)},
         'limitations':['Data panel includes only observed WID and WDI values from pinned source snapshots; no imputation or interpolation.',
            'Observations joined only on matching ISO3 and calendar year; this DOES NOT establish equivalence of statistical concepts.',
            'WDI unemployment is ILO modeled; tax-revenue coverage depends on reporting definition.',
            'WID pretax vs posttax shares are not identified tax treatment effects.',
            'No policy events, experimental treatment, counterfactual or causal attribution are included.',
            'Do not pool country correlations into causal regressions or treat the sample as representative.']}
 (outdir/'analysis_audit.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 files=['wid_wdi_country_year_numeric.csv','joined_measure_coverage.csv','exploratory_country_correlations.csv','analysis_audit.json']
 (outdir/'sha256_manifest.json').write_text(json.dumps({name:digest(outdir/name) for name in files},indent=2)+'\n',encoding='utf-8')
 return report


def main():
 p=argparse.ArgumentParser();p.add_argument('--wid-db',type=Path,required=True);p.add_argument('--warehouse-db',type=Path,required=True)
 p.add_argument('--wdi-definitions',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
 a=p.parse_args();r=build(a.wid_db,a.warehouse_db,a.wdi_definitions,a.out)
 print(json.dumps({k:r[k] for k in ('status','wid_numeric_observations_verified','country_year_rows_with_both_sources','countries_with_both_sources','total_matched_numeric_cells','exploratory_correlations_with_at_least_six_countries')},indent=2))

if __name__=='__main__':main()
