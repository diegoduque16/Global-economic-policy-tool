"""Import a separately downloaded JST Macrohistory CSV (no automatic redistribution).

The JST dataset is CC BY-NC-SA 4.0. Users must first review its license and
obtain the original release from https://www.macrohistory.net/database/ .
Excel users should export the data sheet as UTF-8 CSV. This program does not
assume a column represents a harmonized concept without a user-approved crosswalk.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path


def import_csv(source, out, *, year_column='year', country_column='iso', variables=()):
    if not variables:raise ValueError('Explicit variable names from the source codebook are required')
    out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    rows=[];geos=set();skipped=0
    with open(source,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        expected={year_column,country_column,*variables}
        missing=expected-set(reader.fieldnames or [])
        if missing:raise ValueError(f'Source variables missing: {sorted(missing)}')
        for r in reader:
            iso=(r.get(country_column) or '').strip().upper()
            try:year=int(r[year_column])
            except (ValueError,TypeError):skipped+=1;continue
            if not 1800<=year<=2026 or len(iso) not in (2,3):skipped+=1;continue
            for variable in variables:
                value=(r.get(variable) or '').strip()
                if not value:continue
                try:number=float(value)
                except ValueError:skipped+=1;continue
                if number!=number or number in (float('inf'),float('-inf')):skipped+=1;continue
                rows.append({'historical_country_code':iso,'year':year,'variable_as_published':variable,
                             'value':number,'unit':'JST_codebook_defined','source':'JST Macrohistory R6'})
                geos.add(iso)
    if not rows:raise ValueError('No usable JST observations')
    target=out/'jst_preliminary.csv'
    with target.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    report={'observations':len(rows),'historical_geography_codes':sorted(geos),
            'selected_variables':list(variables),'skipped_cells_or_rows':skipped,
            'status':'imported_not_harmonized_or_causally_validated',
            'license':'CC BY-NC-SA 4.0; do not redistribute outside license terms',
            'origin':'https://www.macrohistory.net/database/'}
    (out/'jst_import_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,help='CSV exported from JST original dataset')
    p.add_argument('--out',default='data/jst')
    p.add_argument('--year-column',default='year')
    p.add_argument('--country-column',default='iso')
    p.add_argument('--variables',required=True,help='Comma-separated columns confirmed in the source codebook')
    a=p.parse_args()
    print(json.dumps(import_csv(a.source,a.out,year_column=a.year_column,country_column=a.country_column,
                                variables=tuple(s.strip() for s in a.variables.split(',') if s.strip())),indent=2))
