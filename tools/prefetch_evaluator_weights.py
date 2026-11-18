"""Download a pinned Hub weight file in verified ranges into a cloud cache."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import fcntl
import hashlib
import json
from pathlib import Path, PurePosixPath
import time

from huggingface_hub import HfApi, hf_hub_url
import requests


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',required=True)
    parser.add_argument('--filename',required=True)
    parser.add_argument('--cache-dir',type=Path,required=True)
    parser.add_argument('--status',type=Path,required=True)
    parser.add_argument('--revision',default='main')
    parser.add_argument('--workers',type=int,default=8)
    args=parser.parse_args()
    relative=PurePosixPath(args.filename)
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe weight filename')
    metadata=HfApi().model_info(args.repo,revision=args.revision,files_metadata=True)
    item=next(row for row in metadata.siblings if row.rfilename==args.filename)
    if not item.lfs or not item.lfs.sha256:raise ValueError('Expected a SHA-256-pinned LFS weight')
    root=args.cache_dir.resolve()/('models--'+args.repo.replace('/','--'))
    blobs=root/'blobs';blobs.mkdir(parents=True,exist_ok=True)
    lock=(blobs/(item.lfs.sha256+'.prefetch.lock')).open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    url=hf_hub_url(args.repo,args.filename,revision=metadata.sha)
    parts=blobs/(item.lfs.sha256+'.parts');parts.mkdir(exist_ok=True)
    total=item.size;span=(total+args.workers-1)//args.workers

    def fetch(index):
        first=index*span;end=min(total,first+span);path=parts/str(index)
        offset=path.stat().st_size if path.exists() else 0
        if offset>end-first:raise ValueError('Oversized fragment')
        failures=0
        while first+offset<end:
            begin=first+offset;last=min(end-1,begin+4*1024**2-1)
            try:
                with requests.get(url,params={'range_start':begin,'range_end':last},
                    headers={'Range':f'bytes={begin}-{last}'},stream=True,timeout=(20,60)) as response:
                    response.raise_for_status()
                    if response.status_code!=206 or response.headers.get('Content-Range')!=f'bytes {begin}-{last}/{total}':
                        raise ValueError('Server did not honor exact range')
                    data=response.content
                    if len(data)!=last-begin+1:raise ValueError('Short range')
                with path.open('ab')as output:output.write(data)
                offset+=len(data);failures=0
                print({'part':index,'bytes':offset,'total':end-first},flush=True)
            except Exception as error:
                failures+=1;print({'part':index,'error_type':type(error).__name__,'retry':failures},flush=True)
                time.sleep(min(60,failures*5))
        return path

    with ThreadPoolExecutor(max_workers=args.workers)as pool:files=list(pool.map(fetch,range(args.workers)))
    digest=hashlib.sha256();temporary=blobs/(item.lfs.sha256+'.assembling')
    with temporary.open('wb')as output:
        for path in files:
            with path.open('rb')as source:
                for block in iter(lambda:source.read(8*1024**2),b''):
                    digest.update(block);output.write(block)
    if temporary.stat().st_size!=total or digest.hexdigest()!=item.lfs.sha256:
        raise ValueError('Assembled weight checksum mismatch')
    target=blobs/item.lfs.sha256;temporary.replace(target)
    snapshot=root/'snapshots'/metadata.sha/args.filename;snapshot.parent.mkdir(parents=True,exist_ok=True)
    snapshot.unlink(missing_ok=True);snapshot.symlink_to(target)
    refs=root/'refs';refs.mkdir(exist_ok=True)
    if args.revision=='main':(refs/'main').write_text(metadata.sha)
    args.status.parent.mkdir(parents=True,exist_ok=True)
    args.status.write_text(json.dumps({'repo':args.repo,'filename':args.filename,'revision':metadata.sha,
        'bytes':total,'sha256':digest.hexdigest(),'verified':True},indent=2))
    print('Verified cached weight ready',flush=True)


if __name__=='__main__':main()
