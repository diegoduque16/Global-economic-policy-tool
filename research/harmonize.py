"""Auditable descriptive research layer for versioned economic warehouse snapshots.

Makes no cross-provider merges, imputations, causal assignments, policy estimates or
comparability assertions. Original warehouse stays untouched. Standard library only.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile

# (family, provider-marker, series) -> (concept, population/definition, comparison group)
# Comparison groups ALWAYS distinguish source and definition, including apparently
# identical GDP growth or payroll data from different publishers.
RULES = {
 ('wdi','*','NY.GDP.MKTP.KD.ZG'):('real_gdp_growth','annual percent change in real GDP','wdi_real_gdp_growth'),
 ('wdi','*','NY.GDP.PCAP.KD.ZG'):('real_gdp_per_capita_growth','annual percent change','wdi_real_gdp_pc_growth'),
 ('wdi','*','SP.POP.TOTL'):('population','total population; WB definition','wdi_population'),
 ('wdi','*','SL.UEM.TOTL.ZS'):('unemployment_rate','ILO modeled, national 15+ population definition; verify series methodology','wdi_ilo_modelled_unemployment'),
 ('wdi','*','SL.EMP.TOTL.SP.ZS'):('employment_population_ratio','ILO modeled employment/population ages 15+','wdi_ilo_modelled_employment_ratio'),
 ('wdi','*','FP.CPI.TOTL.ZG'):('inflation_cpi','consumer prices annual percent','wdi_cpi_inflation'),
 ('wdi','*','NE.CON.GOVT.ZS'):('government_final_consumption_gdp','government final consumption, percent of GDP; not total expenditure','wdi_govt_final_consumption'),
 ('wdi','*','GC.TAX.TOTL.GD.ZS'):('tax_revenue_gdp','central government tax revenue / GDP; social contributions mostly excluded','wdi_central_govt_tax_ratio'),
 ('wdi','*','NE.GDI.FTOT.ZS'):('investment_gdp','gross fixed capital formation, percent GDP','wdi_fixed_capital_formation'),
 ('wdi','*','IT.NET.USER.ZS'):('internet_user_share','individuals using internet, percent','wdi_internet_use'),
 ('imf_oecd','IMF DataMapper','NGDP_RPCH'):('real_gdp_growth','IMF WEO annual real GDP growth; vintage and projections require review','imf_weo_real_gdp_growth'),
 ('imf_oecd','IMF DataMapper','LUR'):('unemployment_rate','IMF WEO unemployment, may not equal modeled WDI series','imf_weo_unemployment'),
 ('imf_oecd','IMF DataMapper','PCPIPCH'):('inflation_cpi','IMF WEO CPI average, annual percent','imf_weo_cpi_average'),
 ('imf_oecd','IMF DataMapper','GGXWDG_NGDP'):('government_debt_gdp','IMF WEO gross government debt percent GDP','imf_weo_gross_debt'),
 ('imf_oecd','IMF DataMapper','GGXCNL_NGDP'):('government_balance_gdp','IMF WEO general government net lending/borrowing percent GDP','imf_weo_net_lending'),
 ('imf_oecd','OECD Global Revenue Statistics','TAX_TOTAL_PCT_GDP'):('tax_revenue_gdp','OECD general government tax revenue percent GDP, including applicable social contributions','oecd_general_govt_tax_ratio'),
 ('us_europe','FRED','PAYEMS'):('payroll_jobs','US nonfarm payroll positions, thousands; seasonality/revisions unverified','fred_us_payroll_jobs'),
 ('us_europe','FRED','USPRIV'):('payroll_jobs_private','US private nonfarm payroll positions, thousands','fred_us_private_jobs'),
 ('us_europe','FRED','UNRATE'):('unemployment_rate','US CPS headline unemployment rate, percent; seasonally adjusted','fred_us_unemployment'),
 ('us_europe','FRED','CIVPART'):('labor_force_participation','US labor force participation rate, percent','fred_us_labor_participation'),
 ('us_europe','FRED','GDPC1'):('real_gdp_level','US real GDP quarterly; FRED metadata needed for price/base vintage','fred_us_real_gdp_level'),
 ('us_europe','Eurostat','unemployment_rate'):('unemployment_rate','Eurostat unemployment, ages 15-74; annual; source flags require review','eurostat_unemployment_15_74'),
 ('us_europe','Eurostat','gdp_real_growth'):('real_gdp_growth','Eurostat real GDP volume growth annual percent','eurostat_real_gdp_growth'),
 ('labor','US BLS public API v1','CES0000000001'):('payroll_jobs','US nonfarm payroll jobs, thousands; not persons employed','bls_us_nonfarm_payroll'),
 ('labor','US BLS public API v1','CES0500000001'):('payroll_jobs_private','US private payroll jobs, thousands','bls_us_private_payroll'),
 ('labor','US BLS public API v1','LNS14000000'):('unemployment_rate','US CPS unemployment rate percent; monthly','bls_us_unemployment'),
 ('labor','US BLS public API v1','CES0500000002'):('hours_weekly','US private weekly hours; monthly','bls_private_weekly_hours'),
 ('labor','US BLS public API v1','CES0500000003'):('nominal_hourly_earnings','US private nominal hourly earnings USD/hour','bls_private_hourly_earnings'),
 ('labor','Eurostat EU LFS','EMP_RATE_20_64'):('employment_population_ratio','Eurostat employment rate ages 20-64, not WDI 15+','eurostat_employment_20_64'),
}

FIELDS=['snapshot_id','observation_id','country_iso3','year','period','frequency','concept','comparison_group',
        'definition','family','provider','original_series','value','source_unit','source_status',
        'descriptive_use','causal_use','review_flags']


def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def iso_codes(path):
    with open(path,encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    valid={r['iso3'] for r in rows if len(r.get('iso3',''))==3 and r.get('classification')=='ISO_3166_1_country_or_territory'}
    if len(valid)<240:raise ValueError('ISO country/territory code reference unexpectedly incomplete')
    return valid


def find_rule(family,provider,series):
    direct=RULES.get((family,'*',series))
    if direct:return direct
    if family=='us_europe':provider='FRED' if provider.startswith('FRED') else provider
    return RULES.get((family,provider,series))


def classify(row,valid_geos,*,snapshot_id):
    """Return (CSV record or None, reason); never silently guess missing units."""
    oid,family,provider,geo,year,period,series,value,unit,frequency,disagg,status=(
        row['observation_id'],row['family'],row['provider'],row['country_or_polity_code'],
        row['year'],row['period'],row['series'],row['value'],row['unit'],row['frequency'],
        row['disaggregation_json'],row['source_status'])
    if family=='historical':return None,'historical_polity_or_estimate_review'
    if geo not in valid_geos:return None,'non_iso3_or_aggregate_geography'
    if family=='labor' and provider=='ILOSTAT':
        return None,'ilostat_disaggregation_and_unit_metadata_not_verified'
    rule=find_rule(family,provider,series)
    if not rule:return None,'unmapped_or_unverified_measure'
    concept,definition,group=rule
    flags=[]
    try:source=json.loads(status) if status else {}
    except json.JSONDecodeError:source={'unparseable_source_status':status};flags.append('invalid_source_status')
    if not isinstance(source,dict):source={'source_metadata':source}
    # IMF projection-year metadata occasionally does not fully identify forecast vintage.
    if family=='imf_oecd' and provider=='IMF DataMapper':
        flags.append('imf_release_vintage_needs_review')
        if source.get('data_status')=='projection':return None,'imf_projection_not_realized'
        if year>=datetime.now(timezone.utc).year:
            flags.append('current_year_may_be_preliminary_or_forecast')
    if family=='us_europe' and provider.startswith('FRED'):
        flags.append('fred_revision_and_seasonality_metadata_needed')
    if family=='labor' and provider=='Eurostat EU LFS':
        flags.append('eurostat_survey_break_flags_check')
    if group in ('wdi_central_govt_tax_ratio','oecd_general_govt_tax_ratio'):
        flags.append('tax_base_government_level_not_cross_source_comparable')
    if family=='wdi':flags.append('world_bank_indicator_metadata_validate')
    try:d=json.loads(disagg) if disagg else {}
    except json.JSONDecodeError:return None,'invalid_disaggregation_metadata'
    if isinstance(d,dict) and d and (family=='labor'):
        flags.append('labor_disaggregation_requires_population_crosswalk')
    return dict(snapshot_id=snapshot_id,observation_id=oid,country_iso3=geo,year=year,period=period,
        frequency=frequency,concept=concept,comparison_group=group,definition=definition,
        family=family,provider=provider,original_series=series,value=value,source_unit=unit,
        source_status=status,descriptive_use='candidate_with_metadata_review',causal_use='not_ready',
        review_flags=';'.join(sorted(set(flags)))),None


def audit_policy_sources(path,out_path,valid_geos):
    """Records are research-study placeholders, NOT individual coded policy treatments."""
    result={'source_studies':0,'candidate_source_studies':0,'usable_dated_numeric_policy_shocks':0,'errors':[]}
    out=[]
    if not path:return result
    with open(path,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        required={'event_id','country','policy_type','status','shock_value','shock_unit','source_url'}
        if not required.issubset(reader.fieldnames or []):raise ValueError('Policy source registry missing required columns')
        for record in reader:
            result['source_studies']+=1
            geo=(record.get('country') or '').strip()
            if geo not in valid_geos:result['errors'].append(f"Invalid geography in source: {record.get('event_id')}")
            # Existing registry supplies study COVERAGE dates, not individual event dates.
            # Thus even numeric values here must not be assumed to represent dated shocks.
            out.append({'study_id':record.get('event_id') or '', 'country_iso3':geo,
                        'status':record.get('status') or '', 'registry_type':'source_study_not_policy_event',
                        'has_numeric_shock':str(bool((record.get('shock_value') or '').strip())),
                        'policy_treatment_ready':'false','official_source_url':record.get('source_url') or '',
                        'required_next_step':'retrieve replication series and independent dated-event codebook'})
            result['candidate_source_studies']+=1
    with open(out_path,'w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['study_id','country_iso3','status','registry_type','has_numeric_shock',
          'policy_treatment_ready','official_source_url','required_next_step']);writer.writeheader();writer.writerows(out)
    return result


def verified_archive_db(archive,tmpdir):
    """Read only declared archive entries; never extract arbitrary tar paths."""
    with tarfile.open(archive,'r:gz') as t:
        regular={x.name:x for x in t.getmembers() if x.isfile() and not x.issym() and not x.islnk()}
        manifests=[x for x in regular if x.endswith('/manifest.json') and x.count('/')==1]
        if len(manifests)!=1:raise ValueError('Expected exactly one top-level snapshot manifest')
        mn=manifests[0];manifest=json.load(t.extractfile(regular[mn]))
        snapshot_id=manifest.get('snapshot_id')
        if mn!=snapshot_id+'/manifest.json':raise ValueError('Snapshot manifest path mismatch')
        records=manifest.get('files')
        if not isinstance(records,list) or not records:raise ValueError('Missing checksum inventory')
        sqlite_entry=next((x for x in records if x.get('path')=='warehouse.sqlite'),None)
        if not sqlite_entry:raise ValueError('Warehouse DB missing in checksum inventory')
        out=Path(tmpdir)/'warehouse.sqlite'
        expected=set()
        for meta in records:
            name=snapshot_id+'/'+meta['path']
            if name in expected:raise ValueError('Duplicate manifest inventory path')
            expected.add(name)
            item=regular.get(name)
            if not item or item.size != meta['bytes']:
                raise ValueError(f'Archive inventory size/path mismatch: {meta["path"]}')
            handle=t.extractfile(item)
            h=hashlib.sha256()
            target=out.open('wb') if meta['path']=='warehouse.sqlite' else None
            try:
                for block in iter(lambda:handle.read(1024*1024),b''):
                    h.update(block)
                    if target:target.write(block)
            finally:
                if target:target.close()
                handle.close()
            if h.hexdigest()!=meta['sha256']:raise ValueError(f'Checksum mismatch for {meta["path"]}')
    if not out.exists():raise ValueError('Missing database')
    return out,manifest


def build(archive,output,iso_file,policy_file=None):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise FileExistsError('Output directory must be empty; do not overwrite prior snapshots')
    output.mkdir(parents=True,exist_ok=True)
    try:
        codes=iso_codes(iso_file)
        with tempfile.TemporaryDirectory() as temp:
            db,source_manifest=verified_archive_db(archive,temp)
            con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
            con.row_factory=sqlite3.Row
            try:
                integrity=con.execute('PRAGMA integrity_check').fetchone()[0]
                if integrity!='ok':raise ValueError(f'SQLite integrity check failed: {integrity}')
                count=con.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
                if count!=source_manifest['total_source_observations']:raise ValueError('Manifest row count mismatch')
                for fam,coverage in source_manifest['source_families'].items():
                    actual=con.execute('SELECT COUNT(*) FROM observations WHERE family=?',(fam,)).fetchone()[0]
                    if actual!=coverage['rows']:raise ValueError(f'Family count mismatch: {fam}')
                kept=Counter();excluded=Counter();reject_by_series=Counter();flags=Counter()
                candidates=output/'descriptive_candidates.csv'
                with candidates.open('w',encoding='utf-8',newline='') as f:
                    writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader()
                    cursor=con.execute('SELECT observation_id,family,provider,country_or_polity_code,year,period,series,value,unit,frequency,disaggregation_json,source_status FROM observations ORDER BY observation_id')
                    for row in cursor:
                        formatted,reason=classify(row,codes,snapshot_id=source_manifest['snapshot_id'])
                        if reason:
                            excluded[(row['family'],reason)]+=1
                            reject_by_series[(row['family'],row['provider'],row['series'],reason)]+=1
                        else:
                            writer.writerow(formatted)
                            kept[(row['family'],formatted['comparison_group'])]+=1
                            for flag in formatted['review_flags'].split(';'):
                                if flag:flags[flag]+=1
                total_kept=sum(kept.values());total_excluded=sum(excluded.values())
                if total_kept+total_excluded!=count:raise ValueError('Incomplete audit accounting')
            finally:con.close()
        with (output/'review_summary.csv').open('w',encoding='utf-8',newline='') as f:
            wr=csv.writer(f);wr.writerow(['family','provider','series','reason','rows'])
            for (family,provider,series,reason),n in sorted(reject_by_series.items()):wr.writerow([family,provider,series,reason,n])
        policy=audit_policy_sources(policy_file,output/'policy_source_studies.csv',codes)
        report={'source_snapshot_id':source_manifest['snapshot_id'],'source_observations':count,
            'descriptive_candidates':total_kept,'excluded_from_descriptive_panel':total_excluded,
            'candidate_counts':{'|'.join(x):n for x,n in sorted(kept.items())},
            'exclusion_counts':{'|'.join(x):n for x,n in sorted(excluded.items())},
            'metadata_flags':dict(flags),'policy_registry':policy,
            'claims':{'statistically_harmonized':False,'cross_provider_equivalent':False,'causal_estimation_ready':False,
                      'policy_treatments_imported':0},
            'limitations':['ISO 3166-1 code acceptance includes dependent territories and does not establish sovereignty.',
              'Historical observations and unverified ILOSTAT classifications excluded from descriptive candidates but remain in original warehouse.',
              'GDP, tax, labor and other similarly named concepts from different providers are NOT automatically comparable.',
              'No deflation, annualization, temporal joining, imputation, counterfactuals, causal attribution or AI outcomes are estimated.',
              'The policy registry currently lists research STUDIES, not validated treatment events.']}
        (output/'harmonization_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        (output/'sha256_manifest.json').write_text(json.dumps({p.name:sha256_file(p) for p in output.iterdir() if p.is_file() and p.name!='sha256_manifest.json'},indent=2)+'\n')
        return report
    except Exception:
        shutil.rmtree(output,ignore_errors=True)
        raise


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',required=True,help='Versioned economic warehouse tar.gz from GitHub Actions artifact')
    parser.add_argument('--out',default='research_output')
    parser.add_argument('--iso-codes',default='research/iso3_country_codes.csv')
    parser.add_argument('--policy-sources',default='data/policy_event_sources.csv')
    args=parser.parse_args()
    report=build(args.archive,args.out,args.iso_codes,args.policy_sources)
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
