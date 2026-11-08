#!/usr/bin/env python3
"""Copy sealed cloud journals to the research host; verify both ends; never remove a source."""
import argparse
import json
import shlex
import subprocess
from pathlib import Path

from retention.artifacts import digest, write_json

LIST_SEALED = '''
import json,re
from pathlib import Path
groups={}
for path in Path('/var/lib/fastmm/paper/runs').glob('btc-paper-dryrun-*.fmj'):
    match=re.fullmatch(r'btc-paper-dryrun-(\\d+)(?:\\.(\\d+))?\\.fmj',path.name)
    if match:
        session,part=int(match[1]),int(match[2] or 0)
        groups.setdefault(session,[]).append((part,str(path)))
newest=max(groups,default=0)
result=[]
for session,parts in sorted(groups.items()):
    last=max(part for part,_ in parts)
    result.extend(path for part,path in sorted(parts) if session != newest or part < last)
print(json.dumps(result))
'''


def ssh(host, command):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', host, shlex.join(command)],
                          check=True, capture_output=True, text=True).stdout


def archive(host, destination):
    destination.mkdir(parents=True, exist_ok=True)
    paths = json.loads(ssh(host, ['python3', '-c', LIST_SEALED]))
    for index, source in enumerate(paths, 1):
        local = destination / Path(source).name
        receipt = local.with_suffix('.receipt.json')
        if receipt.exists() and local.exists():
            if digest(local) != json.loads(receipt.read_text())['sha256']:
                raise ValueError(f'archive changed: {local}')
            continue
        before = ssh(host, ['sha256sum', source]).split()[0]
        partial = local.with_suffix('.partial')
        print(f'[{index}/{len(paths)}] copying {source}', flush=True)
        subprocess.run(['scp', '-q', f'{host}:{source}', str(partial)], check=True)
        after = ssh(host, ['sha256sum', source]).split()[0]
        if before != after or digest(partial) != before:
            raise ValueError('source changed or archive checksum mismatch')
        partial.replace(local)
        local.chmod(0o444)
        write_json(receipt, {'host': host, 'source': source, 'sha256': before})
        print(f'[{index}/{len(paths)}] verified {local.name}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    archive(args.host, args.destination.resolve())
