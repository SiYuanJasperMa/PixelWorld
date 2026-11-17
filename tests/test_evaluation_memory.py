import pytest
from tools.evaluation_memory import container_memory,check_pool_memory


def test_v1_container_limit_replaces_host_memory_and_blocks_large_pool(tmp_path):
    memory=tmp_path/'memory';memory.mkdir()
    (memory/'memory.limit_in_bytes').write_text(str(32*1024**3))
    (memory/'memory.usage_in_bytes').write_text(str(25*1024**3))
    assert container_memory(tmp_path)['available_bytes']==7*1024**3
    with pytest.raises(MemoryError,match='waiting for container memory'):
        check_pool_memory(2,120,tmp_path)
    assert check_pool_memory(2,20,tmp_path)['source']=='v1'


def test_v2_available_capacity_and_unknown_limits_are_explicit(tmp_path):
    (tmp_path/'memory.max').write_text(str(128*1024**3))
    (tmp_path/'memory.current').write_text(str(16*1024**3))
    assert check_pool_memory(2,120,tmp_path)['available_bytes']==112*1024**3
    (tmp_path/'memory.max').write_text('max')
    assert container_memory(tmp_path)['available_bytes'] is None
