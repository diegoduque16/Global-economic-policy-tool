"""Public World Bank WDI ingestion. Uses only Python standard library."""
import argparse,csv,datetime,hashlib,json,time,urllib.parse,urllib.request
from pathlib import Path

BASE="https://api.worldbank.org/v2"
def fetch_json(url,timeout=30,retries=3):
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"OpenEconomicResearch/0.7 (academic research)"})
            with urllib.request.urlopen(req,timeout=timeout) as response: return response.read()
        except Exception:
            if attempt==retries-1: raise
            time.sleep(1.5*(attempt+1))

def pages(country,indicator,start,end,fetch=fetch_json):
    base=f"{BASE}/country/{urllib.parse.quote(country,safe=';')}/indicator/{urllib.parse.quote(indicator)}"
    params={"format":"json","per_page":"1000","date":f"{start}:{end}"}
    page=1
    while True:
        url=base+"?"+urllib.parse.urlencode({**params,"page":page})
        raw=fetch(url); obj=json.loads(raw)
        if not isinstance(obj,list) or len(obj)!=2 or not isinstance(obj[0],dict): raise ValueError(f"Unexpected API response: {url}")
        meta,rows=obj
        yield url,raw,meta,rows or []
        if page>=int(meta.get("pages",1)):break
        page+=1

def normalize(record,indicator,source_url,downloaded_at):
    code=record.get("countryiso3code")
    value=record.get("value")
    if not code or len(code)!=3 or not code.isalpha() or value is None:return None
    year=record.get("date","")
    if not year.isdigit() or len(year)!=4:return None
    return {"country_iso3":code.upper(),"year":int(year),"indicator":indicator,"value":float(value),"unit":"source_defined","source":"World Bank Indicators API","source_url":source_url,"downloaded_at_utc":downloaded_at,"obs_status":record.get("obs_status") or "","decimal":record.get("decimal") or ""}

def run(out,country,start,end,indicators,fetch=fetch_json):
    out=Path(out); (out/"raw").mkdir(parents=True,exist_ok=True); (out/"processed").mkdir(parents=True,exist_ok=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat(); manifest=[]; records=[]; errors=[]
    for indicator in indicators:
        try:
            for url,raw,meta,rows in pages(country,indicator,start,end,fetch):
                digest=hashlib.sha256(raw).hexdigest(); name=f"{indicator}_{meta.get('page',1)}_{digest[:12]}.json"
                (out/"raw"/name).write_bytes(raw)
                manifest.append({"indicator":indicator,"url":url,"file":name,"sha256":digest,"downloaded_at_utc":stamp,"returned":len(rows)})
                for r in rows:
                    item=normalize(r,indicator,url,stamp)
                    if item:records.append(item)
        except Exception as exc:errors.append({"indicator":indicator,"error":str(exc)})
    seen={}; conflicts=[]
    for r in records:
        key=(r['country_iso3'],r['year'],r['indicator'])
        if key in seen and seen[key]['value']!=r['value']:conflicts.append(key)
        seen[key]=r
    fields=['country_iso3','year','indicator','value','unit','source','source_url','downloaded_at_utc','obs_status','decimal']
    with (out/'processed'/'wdi_panel.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(sorted(seen.values(),key=lambda r:(r['country_iso3'],r['indicator'],r['year'])))
    report={"downloaded_at_utc":stamp,"country_query":country,"start":start,"end":end,"indicator_count":len(indicators),"raw_pages":len(manifest),"observations":len(seen),"conflicts":conflicts,"errors":errors,"manifest":manifest,"causal_claims_supported":False}
    (out/'run_report.json').write_text(json.dumps(report,indent=2));return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',default='data/wdi');p.add_argument('--countries',default='all');p.add_argument('--start',type=int,default=1960);p.add_argument('--end',type=int,default=2025);p.add_argument('--group',default='all');p.add_argument('--limit',type=int,default=0)
    args=p.parse_args(); groups=json.loads((Path(__file__).parent/'wdi_indicators.json').read_text()); indicators=list(dict.fromkeys(k for group,series in groups.items() if args.group in ('all',group) for k in series))
    if args.limit:indicators=indicators[:args.limit]
    result=run(args.out,args.countries,args.start,args.end,indicators)
    print(json.dumps({k:v for k,v in result.items() if k!='manifest'},indent=2))
