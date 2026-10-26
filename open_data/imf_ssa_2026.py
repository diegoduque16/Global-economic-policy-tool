"""Validate a manual numeric transcription of IMF WP/26/011, Table 1.

This is a STAGING / PROVENANCE-ONLY data product. Never feed directly into a
causal analysis or warehouse while source discrepancies remain unresolved.
All reported component values and totals are preserved without modification.
"""
from __future__ import annotations
import argparse
import csv
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path

SOURCE_URL='https://www.imf.org/-/media/files/publications/wp/2026/english/wpiea2026011-source-pdf.pdf'
SOURCE_SHA256='c08aa5d3361dc5b5acccd85d80eb2cf3dd304c41b65f05d690bada03845f72a3'
COUNTRIES={'AGO','CMR','CIV','COD','ETH','GHA','KEN','MUS','MOZ','NGA','SEN','ZAF','TZA','UGA'}
NO_EPISODES={'AGO','COD'}
EXPECTED_INCONSISTENCIES={('KEN',2023),('KEN',2024)}


def audit(input_csv:Path, out:Path)->dict:
    data=input_csv.read_bytes()
    digest=sha256(data).hexdigest()
    if digest!=SOURCE_SHA256:
        raise ValueError('Transcription checksum unexpectedly changed; re-review original table')
    with input_csv.open(newline='',encoding='utf-8') as source:
        rows=list(csv.DictReader(source))
    required={'country','iso3','year','tax_pp_gdp','spend_pp_gdp',
              'published_total_pp_gdp','pdf_printed_page'}
    if not rows or set(rows[0])!=required:
        raise ValueError('Unexpected source columns')
    if len(rows)!=72:
        raise ValueError(f'Unexpected count {len(rows)}; inspect source table')
    seen=set(); checked=[]; issues=[]
    for row in rows:
        iso=row['iso3'];year=int(row['year']);page=int(row['pdf_printed_page'])
        if iso not in COUNTRIES or iso in NO_EPISODES or not 1990<=year<=2024 or page not in (12,13,14):
            raise ValueError('Source country/year/page invalid')
        key=(iso,year)
        if key in seen:raise ValueError(f'Duplicate source episode: {key}')
        seen.add(key)
        try:
            tax=Decimal(row['tax_pp_gdp']);spend=Decimal(row['spend_pp_gdp'])
            total=Decimal(row['published_total_pp_gdp'])
        except InvalidOperation as exc:raise ValueError('Non-numeric source field') from exc
        if not all(x.is_finite() and x>=0 for x in (tax,spend,total)) or total==0:
            raise ValueError('Nonpositive or nonfinite episode')
        computed=tax+spend
        discrepancy=(total-computed).quantize(Decimal('0.01'))
        flagged=discrepancy!=0
        status='QUARANTINED_PUBLISHED_TOTAL_MISMATCH' if flagged else 'TABLE_ARITHMETIC_CONSISTENT'
        annotated={**row,'component_sum_pp_gdp':f'{computed:.2f}',
            'published_minus_components_pp_gdp':f'{discrepancy:.2f}',
            'validation_status':status,
            'dominant_instrument':('tax' if tax>spend else 'spending' if spend>tax else 'equal')}
        checked.append(annotated)
        if flagged:issues.append({'iso3':iso,'year':year,'published_total_pp_gdp':str(total),
                  'component_sum_pp_gdp':str(computed),'difference_pp_gdp':str(discrepancy),
                  'printed_page':page,'flag':status})
    if set((x['iso3'],x['year']) for x in issues)!=EXPECTED_INCONSISTENCIES:
        raise ValueError('Unexpected mismatch pattern; inspect against source before installing')
    episodes_by_country={code:sum(x['iso3']==code for x in checked) for code in sorted(COUNTRIES)}
    report={'source_name':'IMF WP/26/011 Table 1 numeric transcription','source_url':SOURCE_URL,
        'source_printed_pages':[12,13,14], 'source_csv_sha256':digest,
        'source_stated_episode_count_introduction':73,
        'source_stated_episode_count_later_sections':72,
        'transcribed_country_year_episode_rows':len(rows),
        'coverage_countries_named_in_paper':14,
        'countries_with_episode_rows':len(set(x['iso3'] for x in rows)),
        'countries_explicitly_without_identified_episodes':sorted(NO_EPISODES),
        'by_country':episodes_by_country,'arithmetic_consistent_rows':len(rows)-len(issues),
        'quarantined_rows':len(issues),'issues':issues,
        'status':'QUARANTINED_PENDING_AUTHOR_OR_PRIMARY_SOURCE_CLARIFICATION',
        'warehouse_inclusion_permitted':False,
        'causal_inference_ready':False,
        'notes':['Do not impute no-episode countries to zero for all years.',
            'Preserve the published total: do not replace Kenya 2023/2024 with calculated totals without evidence.',
            'The paper reports 73 episodes in one section and 72 later; Table 1 transcribes to 72.',
            'The narrative-study identification strategy is subject to anticipation, selection, and measurement concerns.',
            'This is a manually transcribed derived numeric table, not an author-provided machine-readable dataset.',
            'Published Table 1 includes consolidations only; absence is not a verified no-policy-shock year.',
            'All magnitudes are estimated % of GDP; positive numbers denote fiscal tightening, not stimulus.']}
    out.mkdir(parents=True,exist_ok=True)
    with (out/'audited_episodes.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(checked[0]),lineterminator=chr(10))
        writer.writeheader();writer.writerows(checked)
    (out/'audit.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',default='research_data/imf_ssa_2026/table1_transcription.csv')
    p.add_argument('--out',default='research_data/imf_ssa_2026/audit')
    opts=p.parse_args()
    result=audit(Path(opts.input),Path(opts.out))
    print(json.dumps({k:result[k] for k in ('status','transcribed_country_year_episode_rows',
         'arithmetic_consistent_rows','quarantined_rows','warehouse_inclusion_permitted','issues')},indent=2))

if __name__=='__main__':main()
