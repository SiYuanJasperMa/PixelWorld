"""Build the unchanged SAM2 connected-component extension in cloud evaluation storage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile
import urllib.request
import subprocess
from tools.pcve_native import SAM2_KERNEL_SHA256


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True)
    args=p.parse_args();bundle=args.bundle.resolve();root=bundle/'state/sam2_native_eval';root.mkdir(parents=True,exist_ok=True)
    filename='cuda_nvcc-linux-x86_64-11.8.89-archive.tar.xz';archive=root/filename
    expected='7ee8450dbcc16e9fe5d2a7b567d6dec220c5894a94ac6640459e06231e3b39a5'
    if not archive.exists():
        url='https://developer.download.nvidia.com/compute/cuda/redist/cuda_nvcc/linux-x86_64/'+filename
        temp=archive.with_suffix('.part')
        with urllib.request.urlopen(url,timeout=90)as response,temp.open('wb')as stream:
            while chunk:=response.read(1024*1024):stream.write(chunk)
        temp.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise ValueError('NVIDIA compiler checksum mismatch')
    directory=root/'cuda_nvcc-linux-x86_64-11.8.89-archive'
    if not directory.exists():
        with tarfile.open(archive)as tar:
            for member in tar.getmembers():
                if not(root/member.name).resolve().is_relative_to(root):raise ValueError('Unsafe compiler archive member')
            tar.extractall(root)
    cccl_name='cuda_cccl-linux-x86_64-11.8.89-archive.tar.xz';cccl_archive=root/cccl_name
    cccl_sha='99d77d9e4c75d5e4663e473577f1871e65bca4ea0b9023f544a3556f0c1776c7'
    if not cccl_archive.exists():
        with urllib.request.urlopen('https://developer.download.nvidia.com/compute/cuda/redist/cuda_cccl/linux-x86_64/'+cccl_name,timeout=90)as response:
            cccl_archive.write_bytes(response.read())
    if hashlib.sha256(cccl_archive.read_bytes()).hexdigest()!=cccl_sha:raise ValueError('NVIDIA CCCL checksum mismatch')
    cccl_dir=root/'cuda_cccl-linux-x86_64-11.8.89-archive'
    if not cccl_dir.exists():
        with tarfile.open(cccl_archive)as tar:
            for member in tar.getmembers():
                if not(root/member.name).resolve().is_relative_to(root):raise ValueError('Unsafe CCCL archive member')
            tar.extractall(root)
    import torch
    components=Path(torch.__file__).parent.parent/'nvidia'
    include=directory/'include';lib=directory/'lib64';lib.mkdir(exist_ok=True)
    for header in (cccl_dir/'include').iterdir():
        target=include/header.name
        if not target.exists():target.symlink_to(header.resolve(),target_is_directory=header.is_dir())
    for component in components.iterdir():
        for source_dir,dest in [(component/'include',include),(component/'lib',lib)]:
            if not source_dir.is_dir():continue
            for source in source_dir.iterdir():
                target=dest/source.name
                if not target.exists():target.symlink_to(source.resolve(),target_is_directory=source.is_dir())
    cudart=lib/'libcudart.so'
    if not cudart.exists():
        candidates=sorted(lib.glob('libcudart.so.*'))
        if not candidates:raise FileNotFoundError('CUDA11.8 runtime library')
        cudart.symlink_to(candidates[0].resolve())
    architectures='8.0;9.0+PTX'
    os.environ['CUDA_HOME']=str(directory);os.environ['TORCH_CUDA_ARCH_LIST']=architectures;os.environ['MAX_JOBS']='4'
    compiler=root/'gcc11/usr/bin/g++-11'
    if not compiler.is_file():
        packages=root/'gcc11/packages';packages.mkdir(parents=True,exist_ok=True)
        subprocess.run(['apt-get','download','gcc-11','g++-11','cpp-11','libstdc++-11-dev','libgcc-11-dev','gcc-11-base'],
                       cwd=packages,check=True)
        for package in packages.glob('*.deb'):subprocess.run(['dpkg-deb','-x',str(package),str(root/'gcc11')],check=True)
    os.environ['CXX']=str(compiler)
    os.environ['CC']=str(root/'gcc11/usr/bin/gcc-11')
    build=root/'build_multiarch_v1';build.mkdir(exist_ok=True)
    from torch.utils.cpp_extension import load
    import sam2
    source=Path(sam2.__file__).parent/'csrc/connected_components.cu'
    if not source.is_file():raise FileNotFoundError(source)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=SAM2_KERNEL_SHA256:
        raise ValueError('SAM2 CUDA kernel differs from the verified official source')
    module=load(name='_C',sources=[str(source)],extra_cflags=['-O3'],extra_cuda_cflags=['-O3','-ccbin',str(compiler)],
                build_directory=str(build),with_cuda=True,verbose=True)
    mask=torch.zeros((1,1,8,8),dtype=torch.uint8,device='cuda');mask[:,:,1:3,1:3]=1
    labels,counts=module.get_connected_componnets(mask)
    if not torch.all(counts[mask.bool()]==4):raise ValueError('Native SAM2 component-count smoke failed')
    # Validate this integer kernel against an independent CPU implementation
    # in addition to the basic native-kernel smoke test.
    import cv2,numpy as np
    rng=np.random.default_rng(20261119)
    for _ in range(32):
        array=(rng.random((32,32))>.65).astype(np.uint8)
        _,cpu_labels,stats,_=cv2.connectedComponentsWithStats(array,connectivity=8)
        expected_counts=np.where(array,stats[cpu_labels,cv2.CC_STAT_AREA],0)
        _,gpu_counts=module.get_connected_componnets(torch.from_numpy(array)[None,None].cuda())
        actual=gpu_counts.cpu().numpy()[0,0]
        if not np.array_equal(actual[array.astype(bool)],expected_counts[array.astype(bool)]):
            raise ValueError('Native SAM2 component counts disagree with CPU connected components')
    report={'extension':module.__file__,'extension_sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'compiler_archive_sha256':expected,'cccl_archive_sha256':cccl_sha,'torch':torch.__version__,
            'torch_cuda':torch.version.cuda,'cuda_architectures':architectures,
            'tested_device_capability':list(torch.cuda.get_device_capability()),
            'smoke_pass':True,'random_mask_parity_tests':32,'host_compiler_gate_override':False,'host_compiler':str(compiler)}
    (root/'ready.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()
