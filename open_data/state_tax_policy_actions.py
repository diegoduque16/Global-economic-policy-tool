"""Research-grade ingestion of NCSL 2015-2018 historical State Tax Actions.

Capture jurisdiction and effective date as stated, without claiming treatment
exogeneity, reducing tax changes to tax-rate deltas, or inventing enactment dates.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, hashlib, json, re, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from bs4 import BeautifulSoup

SOURCE='https://www.ncsl.org/fiscal/state-tax-actions-database'
STATES=dict(x.split('|') for x in '''Alabama|AL;Alaska|AK;Arizona|AZ;Arkansas|AR;California|CA;Colorado|CO;Connecticut|CT;Delaware|DE;District of Columbia|DC;Florida|FL;Georgia|GA;Hawaii|HI;Idaho|ID;Illinois|IL;Indiana|IN;Iowa|IA;Kansas|KS;Kentucky|KY;Louisiana|LA;Maine|ME;Maryland|MD;Massachusetts|MA;Michigan|MI;Minnesota|MN;Mississippi|MS;Missouri|MO;Montana|MT;Nebraska|NE;Nevada|NV;New Hampshire|NH;New Jersey|NJ;New Mexico|NM;New York|NY;North Carolina|NC;North Dakota|ND;Ohio|OH;Oklahoma|OK;Oregon|OR;Pennsylvania|PA;Rhode Island|RI;South Carolina|SC;South Dakota|SD;Tennessee|TN;Texas|TX;Utah|UT;Vermont|VT;Virginia|VA;Washington|WA;West Virginia|WV;Wisconsin|WI;Wyoming|WY'''.split(';'))
CSV_FIELDS=['provider','country_iso3','year','period','series','value','unit','frequency','disaggregation','obs_status','source_url']
DATE_RE=re.compile(r'(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})(?!\d)')
MONTHS={name.lower():index for index,name in enumerate('January February March April May June July August September October November December'.split(),1)}
MONTHS.update({name[:3].lower():n for name,n in MONTHS.items()})
MONTH_RE=re.compile(r'(?i)\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(\d{4})\b')
MONTHS['sept']=9

def parse_date(raw):
    """Return exact ISO date only if there is exactly one explicit complete date."""
    dates=set()
    for m,d,y in DATE_RE.findall(raw):
        year=int(y);year=2000+year if len(y)==2 and year<40 else (1900+year if len(y)==2 else year)
        try: dates.add(dt.date(year,int(m),int(d)).isoformat())
        except ValueError: return None
    for m,d,y in MONTH_RE.findall(raw):
        try: dates.add(dt.date(int(y),MONTHS[m.lower()[:3]],int(d)).isoformat())
        except ValueError: return None
    if len(dates)!=1:return None
    return next(iter(dates))

def clean(s):return re.sub(r'\s+',' ',s).strip()

def parse_html(blob):
    soup=BeautifulSoup(blob,'html.parser')
    current_year=None; panel=[];undated=[];skipped=collections.Counter(); year_table_count=collections.Counter(); states=set(); seen=set()
    for elem in soup.find_all(['h2','h3','h4','h5','table']):
        if elem.name!='table':
            heading=clean(elem.get_text(' ',strip=True))
            if heading in {'2015','2016','2017','2018'}:current_year=int(heading)
            elif re.fullmatch(r'20\d\d',heading):current_year=None
            continue
        if current_year is None:continue
        trs=elem.find_all('tr')
        if not trs:continue
        columns=[clean(c.get_text(' ',strip=True)) for c in trs[0].find_all(['th','td'])]
        if len(columns)<6 or 'description' not in ' '.join(columns).lower() or not any('effective date' in c.lower() for c in columns):continue
        year_table_count[current_year]+=1
        for index,tr in enumerate(trs[1:],start=2):
            cells=[clean(c.get_text(' ',strip=True)) for c in tr.find_all(['th','td'],recursive=False)]
            if len(cells)<6:skipped['wrong_cell_count']+=1;continue
            state,description,tax_type,rawdate,fy1,fy2=cells[:6]
            if state not in STATES:
                skipped['unknown_or_blank_jurisdiction']+=1;continue
            if not description or not tax_type:
                skipped['blank_description_or_type']+=1;continue
            # Fiscal and regulatory non-tax entries in NCSL's combined action table
            # must not be mislabeled as tax-policy observations.
            if tax_type.casefold().strip() not in {
                'personal income','corporate income','sales','sales and use',
                'property','tobacco','motor fuel','alcohol','alcoholic beverages',
                'excise','estate','severance','gasoline','cigarette','cigarette and tobacco'
            }:
                skipped['outside_explicit_tax_categories']+=1;continue
            exact=parse_date(rawdate)
            raw={'state':state,'state_abbr':STATES[state],'action_report_year':current_year,
                 'description':description,'tax_type_original':tax_type,
                 'effective_date_original':rawdate,'effective_date_iso':exact,
                 'fiscal_period_1':columns[4],'fiscal_impact_1_original':fy1,
                 'fiscal_period_2':columns[5],'fiscal_impact_2_original':fy2,
                 'date_is_retroactive':bool(re.search(r'\bretroactive\b',rawdate,re.I)),
                 'is_enactment_date_known':False,'causal_policy_shock_identified':False,
                 'source_row':index,'source_report_section':current_year}
            key=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()[:24]
            raw['source_record_id']=key
            if key in seen: raise ValueError(f'Unexpected duplicate source record: {key}')
            seen.add(key)
            states.add(STATES[state])
            if exact is None:
                undated.append(raw);continue
            yr=int(exact[:4]);series='NCSL.STATE_TAX_ACTION.'+re.sub(r'[^A-Z0-9]+','_',tax_type.upper()).strip('_')
            panel.append({'provider':'National Conference of State Legislatures',
                'country_iso3':'USA','year':yr,'period':exact,'series':series,
                'value':1,'unit':'documented_action_count','frequency':'irregular_exact_effective_date',
                'disaggregation':json.dumps(raw,ensure_ascii=False,sort_keys=True),
                'obs_status':'officially_reported_effective_date_not_causal_shock',
                'source_url':SOURCE})
    if len(year_table_count)<4:
        raise ValueError(f'NCSL historical table coverage incomplete: {dict(year_table_count)}')
    if len(panel)<200 or len(states)<35:
        raise ValueError(f'Unexpectedly small NCSL extraction: {len(panel)} dated actions, {len(states)} states')
    panel.sort(key=lambda r:(r['period'],json.loads(r['disaggregation'])['state_abbr'],r['series'],json.loads(r['disaggregation'])['source_record_id']))
    return panel,undated,{'reports_included':sorted(year_table_count),'tables_by_report_year':dict(year_table_count),
           'dated_actions':len(panel),'undated_or_ambiguous_actions':len(undated),
           'jurisdictions_all_rows':len(states),'skipped_rows':dict(skipped),
           'effective_date_range':[panel[0]['period'],panel[-1]['period']]}

def fetch(url=SOURCE):
    if url!=SOURCE:raise ValueError('Only official source is allowed')
    for attempt in range(3):
        try:
            request=Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; EconomicPolicyResearchBot/1.0; academic research)', 'Accept':'text/html'})
            with urlopen(request,timeout=75) as response:
                if response.geturl().split('/')[2].lower() not in {'www.ncsl.org','ncsl.org'}:raise ValueError('Source redirected to unexpected host')
                body=response.read(15_000_001)
            if len(body)>15_000_000 or not body.startswith((b'<!',b'<h',b'\xef\xbb\xbf<')) and b'<html' not in body[:3000].lower():
                raise ValueError('Unexpected HTML source response')
            return body
        except (HTTPError,URLError,TimeoutError):
            if attempt==2:raise
            time.sleep(5*(attempt+1))

def run(out='data/state_tax_policy_actions',getter=fetch):
    out=Path(out);(out/'processed').mkdir(parents=True,exist_ok=True)
    blob=getter();rows,undated,stats=parse_html(blob)
    panel=out/'processed'/'state_tax_policy_actions_panel.csv'
    with panel.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=CSV_FIELDS);writer.writeheader();writer.writerows(rows)
    audit=out/'undated_actions_audit.json'
    audit.write_text(json.dumps(undated,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    report={'source_url':SOURCE,'source_sha256':hashlib.sha256(blob).hexdigest(),
        'panel_sha256':hashlib.sha256(panel.read_bytes()).hexdigest(),
        'retrieved_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'observations':len(rows),'errors':[],'conflicts':[],**stats,
        'research_ready':False,'is_causal_policy_shock':False,
        'definition_harmonized':False,'excludes_interactive_2019_to_2025_data':True,
        'limitations':['Source historical page sections cover reported 2015–2018 actions, not full reform universe.',
          'Only rows with exactly one explicit source effective date entered SQLite; undated/ambiguous kept in audit.',
          'Action report year is not enactment date or effective date.',
          'Non-tax fees, healthcare assessments and miscellaneous categories are excluded to avoid misclassification.',
          'Multiple state tax actions can describe the same statute; records are not independent treatment shocks.',
          '1 observation means one documented source action, not one tax percentage point.',
          'Tax changes include credits, exemptions and base changes; do not infer tax change direction.',
          'Fiscal estimates remain source-native strings; no fabricated numeric magnitude.',
          'Descriptions may contain future phase-ins or non-implemented elements.']}
    (out/'run_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='limitations'},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',default='data/state_tax_policy_actions');a=p.parse_args();run(a.out)
