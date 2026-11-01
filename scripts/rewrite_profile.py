#!/usr/bin/env python3
"""Privately replace only the approved SKK provider URLs with hosted SRS URLs."""
import argparse
import copy
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path,help='Private sing-box JSON outside this public repository')
    parser.add_argument('--output',required=True,type=Path,help='New private output file outside this public repository')
    parser.add_argument('--base-url',required=True,help='Public repository raw root URL, ending at the branch name')
    args=parser.parse_args()
    for path in (args.input,args.output):
        if path.resolve().is_relative_to(ROOT.resolve()):
            parser.error('Private input/output profiles must stay outside this public repository')
    if args.output.exists():
        parser.error('Output already exists; choose a new private filename')
    parsed=urlsplit(args.base_url)
    if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error('Base URL must be a public HTTPS root without credentials, query, or fragment')
    manifest=json.loads((ROOT/'manifest.json').read_text())
    replacements={r['url']:r['srs_path'] for r in manifest['rules']}
    config=json.loads(args.input.read_text())
    before=copy.deepcopy(config)
    count=0
    for provider in config.get('route',{}).get('rule_set',[]):
        original=provider.get('url')
        if original in replacements:
            provider['type']='remote'
            provider['format']='binary'
            provider['url']=args.base_url.rstrip('/')+'/'+replacements[original]
            count+=1
    if count!=len(replacements):
        parser.error(f'Expected all {len(replacements)} approved SKK providers; found {count}')
    assert config['outbounds']==before['outbounds']
    assert config['route']['rules']==before['route']['rules']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    descriptor=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as output:
        output.write(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'converted_providers':count,'output':str(args.output)}))

if __name__=='__main__':
    main()
