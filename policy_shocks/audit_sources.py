"""Inventory original fiscal shock archives, without republishing source data.

The initial verified direct-download source is the author-hosted Romer & Romer (2010)
ZIP. This stage inventories files and hashes. It does NOT manufacture a shock series.
Standard-library only, with conservative archive validation.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import urllib.request
import zipfile

MAX_DOWNLOAD = 15 * 1024 * 1024
MAX_MEMBERS = 4000
MAX_UNCOMPRESSED = 150 * 1024 * 1024
ALLOWED_URL = 'https://eml.berkeley.edu/~dromer/papers/DataSet.zip'
SOURCE_ID = 'romer_romer_2010'


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def retrieve(url: str = ALLOWED_URL) -> bytes:
    if url != ALLOWED_URL:
        raise ValueError('Source download URL must match the explicitly reviewed author URL')
    req = urllib.request.Request(url, headers={'User-Agent': 'EconomicPolicyResearch/1.0 (academic source-audit)'})
    with urllib.request.urlopen(req, timeout=40) as response:
        # Avoid redirecting to an unexpected site or downgrading to HTTP.
        if response.geturl() != ALLOWED_URL:
            raise ValueError(f'Source URL redirected unexpectedly: {response.geturl()}')
        data = response.read(MAX_DOWNLOAD + 1)
    if not data or len(data) > MAX_DOWNLOAD:
        raise ValueError('Original archive empty or exceeds safety cap')
    return data


def _validate_name(name: str):
    if '\\' in name or '\x00' in name or name.startswith('/') or ':' in name:
        raise ValueError(f'Unsafe ZIP path: {name!r}')
    chunks = PurePosixPath(name).parts
    if not chunks or any(c in ('', '.', '..') for c in chunks) or name.startswith('./'):
        raise ValueError(f'Unsafe ZIP path: {name!r}')


def audit_zip(payload: bytes, *, source_id: str = SOURCE_ID) -> dict:
    if source_id != SOURCE_ID:
        raise ValueError('Unsupported auto-audited study ID')
    inventory=[]; seen=set(); total=0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files=archive.infolist()
        if len(files) > MAX_MEMBERS:
            raise ValueError('Too many ZIP members')
        for info in files:
            _validate_name(info.filename)
            key=info.filename.casefold()
            if key in seen: raise ValueError(f'Duplicate ZIP path: {info.filename}')
            seen.add(key)
            mode=info.external_attr >> 16
            if stat.S_ISLNK(mode): raise ValueError(f'ZIP symlink rejected: {info.filename}')
            if info.flag_bits & 1: raise ValueError('Encrypted ZIP member rejected')
            if info.is_dir(): continue
            total += info.file_size
            if total > MAX_UNCOMPRESSED: raise ValueError('ZIP uncompressed contents exceed cap')
            if info.compress_size == 0 and info.file_size > 0:
                raise ValueError('Suspicious compression ratio')
            if info.compress_size > 0 and info.file_size/info.compress_size > 1000:
                raise ValueError('Suspicious compression ratio')
            body=archive.read(info)
            if len(body)!=info.file_size: raise ValueError('Corrupt ZIP member length')
            suffix=Path(info.filename).suffix.lower()
            inventory.append({'member':info.filename,'bytes':len(body),
               'sha256':sha256(body),'format':suffix or 'unknown',
               'potential_tabular':suffix in {'.csv','.txt','.tsv','.dta','.xls','.xlsx','.sav','.wf1'}})
    return {'source_id':source_id,'source_archive_sha256':sha256(payload),
            'source_archive_bytes':len(payload),'members':inventory,
            'member_count':len(inventory),'uncompressed_bytes':total,
            'numeric_shocks_imported':0,
            'requires_codebook_review':True,
            'notes':['Archive members and checksums only; no variable semantics inferred.',
                     'Primary sources are not automatically licensed for redistribution.',
                     'An archival file may contain results, controls, or endogenous tax changes rather than exogenous shocks.']}


def run(*, out, local_zip=None):
    root=Path(__file__).resolve().parent
    registry=json.loads((root/'sources.json').read_text(encoding='utf-8'))
    source=next(x for x in registry['sources'] if x['id']==SOURCE_ID)
    payload=Path(local_zip).read_bytes() if local_zip else retrieve()
    audit=audit_zip(payload)
    audit.update({'retrieved_utc':datetime.now(timezone.utc).isoformat(),
                 'source_url':source['replication_url'],'study_url':source['study_url'],
                 'download_is_local_fixture':bool(local_zip)})
    folder=Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.iterdir()): raise FileExistsError('Output directory must be empty')
    (folder/'romer_2010_archive_inventory.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
    (folder/'research_source_status.json').write_text(json.dumps({
       'archive_audited':SOURCE_ID, 'studies':[
           {'id':x['id'],'retrieval_mode':x['retrieval_mode'],'study_url':x['study_url']}
           for x in registry['sources']],
       'validated_numeric_policy_shocks':0,'source_data_redistributed':False,
       'caution':'Archive audited, but numeric shock columns and identification not yet verified.'},indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'members':audit['member_count'],'archive_sha256':audit['source_archive_sha256'],
                      'numeric_shocks_imported':0,'out':str(folder)},indent=2))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--local-zip',help='Use a local ZIP for offline testing instead of network fetch')
    ap.add_argument('--out',default='fiscal_audit_output')
    args=ap.parse_args()
    run(out=args.out,local_zip=args.local_zip)

if __name__=='__main__':main()
