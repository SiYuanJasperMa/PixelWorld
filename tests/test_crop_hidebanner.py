from pixelworld.audit import failed_scratch_path

def test_real_denied_crop_banner():
    path='/tmp/pixelworld_denied_banner_fixture_f129.png'
    command=f'ffmpeg -hide_banner -loglevel error -y -i first_frame.png -vf "crop=460:280:350:420,scale=920:560:flags=lanczos" {path}'
    item={'command':command,'exit_code':251,'aggregated_output':f'[image2 @ 0x7db2100] Could not open file : {path}\nError muxing a packet\n'}
    assert failed_scratch_path(path,item)
    assert not failed_scratch_path(path,{**item,'exit_code':0})
    assert not failed_scratch_path(path,{**item,'command':command.replace('first_frame.png','/other/reference.png')})
    assert not failed_scratch_path(path,{**item,'command':command.replace('crop=460:280:350:420','movie=/other/reference.png')})
