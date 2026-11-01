#!/usr/bin/env python3
"""Fetch the approved public SKK rules and compile an all-or-nothing SRS update."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MARKERS = {"7h15.ru1353t.1s.m4d3.by.5ukk4w.skk.moe", "this_ruleset_is_made_by_sukkaw.ruleset.skk.moe"}
MAX_BYTES = 32 * 1024 * 1024


def repository_path(relative: str) -> Path:
    p = PurePosixPath(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError(f"Unsafe output path: {relative}")
    result = ROOT.joinpath(*p.parts)
    if not result.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(f"Path escapes repository: {relative}")
    return result


def validate(payload: bytes, entry: dict) -> tuple[dict, int]:
    data = json.loads(payload)
    if not isinstance(data, dict) or set(data) != {"version", "rules"}:
        raise ValueError("Expected a native sing-box JSON rule-set with version and rules")
    if data['version'] not in (1, 2, 3, 4, 5) or not isinstance(data['rules'], list):
        raise ValueError("Unsupported version or rules type")
    count = 0
    def inspect(rule):
        nonlocal count
        if not isinstance(rule, dict):
            raise ValueError("Each rule must be an object")
        if rule.get('type') == 'logical':
            if rule.get('mode') not in ('and', 'or') or not isinstance(rule.get('rules'), list):
                raise ValueError("Invalid logical rule")
            for child in rule['rules']:
                inspect(child)
            return
        for key, value in rule.items():
            if key in ('type', 'invert'):
                continue
            if not isinstance(value, list):
                raise ValueError(f"Expected an array for rule field {key}")
            if key in ('ip_cidr', 'source_ip_cidr'):
                for cidr in value:
                    ipaddress.ip_network(cidr, strict=False)
            count += sum(1 for item in value if item not in MARKERS)
    for rule in data['rules']:
        inspect(rule)
    if count < entry['minimum_entries']:
        raise ValueError(f"Unexpected rule-count drop: {count} < {entry['minimum_entries']}")
    return data, count


def fetch(url: str) -> bytes:
    if not url.startswith('https://ruleset.skk.moe/sing-box/'):
        raise ValueError("Manifest source is outside the approved SKK endpoint")
    last_error = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'sing-box/1.14.4'})
            with urllib.request.urlopen(req, timeout=25) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Rule source exceeds 32 MiB size limit")
            return data
        except (OSError, ValueError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sing-box', default=os.environ.get('SING_BOX_BINARY', 'sing-box'))
    parser.add_argument('--source-dir', type=Path, help='Use local upstream JSON snapshots for offline validation')
    args = parser.parse_args()
    manifest = json.loads((ROOT/'manifest.json').read_text())
    entries = manifest['rules']
    assert len(entries) == len({e['tag'] for e in entries}) == 28
    version = subprocess.run([args.sing_box, 'version'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    if version != 'sing-box version 1.14.4':
        raise RuntimeError(f"Expected pinned core 1.14.4, received: {version}")
    # Validate all destinations before downloading or replacing any existing file.
    for entry in entries:
        for key in ('source_path', 'srs_path'):
            repository_path(entry[key])
    with tempfile.TemporaryDirectory(prefix='srs-build-') as work:
        stage = Path(work)
        def build(entry):
            if args.source_dir:
                relative = entry['source_path'].removeprefix('sources/')
                payload = args.source_dir.joinpath(*PurePosixPath(relative).parts).read_bytes()
            else:
                payload = fetch(entry['url'])
            _, count = validate(payload, entry)
            src = stage/entry['source_path']; src.parent.mkdir(parents=True, exist_ok=True); src.write_bytes(payload)
            binary = stage/entry['srs_path']; binary.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run([args.sing_box, 'rule-set', 'compile', str(src), '-o', str(binary)], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f"Compile failed for {entry['tag']}: {result.stderr.strip()}")
            blob = binary.read_bytes()
            if not blob.startswith(b'SRS') or len(blob) < 8:
                raise ValueError(f"Invalid compiled binary: {entry['tag']}")
            return {'tag':entry['tag'], 'upstream_url':entry['url'], 'source_path':entry['source_path'],
                    'srs_path':entry['srs_path'], 'source_bytes':len(payload), 'source_sha256':hashlib.sha256(payload).hexdigest(),
                    'srs_bytes':len(blob), 'srs_sha256':hashlib.sha256(blob).hexdigest(), 'semantic_entries':count}
        # Any download/validation/compile failure aborts before replacing the existing snapshot.
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(build, entries))
        metadata = {'compiler':version, 'sources_unchanged':True, 'rules':results}
        (stage/'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n')
        relative_paths = [p for e in entries for p in (e['source_path'],e['srs_path'])] + ['metadata.json']
        for relative in relative_paths:
            target = repository_path(relative); target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name+'.new')
            shutil.copyfile(stage/relative, temporary)
            os.replace(temporary, target)
    print(json.dumps({'rules':len(results), 'source_bytes':sum(r['source_bytes'] for r in results),
                      'srs_bytes':sum(r['srs_bytes'] for r in results), 'status':'ok'}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
