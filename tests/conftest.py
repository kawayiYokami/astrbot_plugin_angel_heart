"""pytest 配置文件 - 处理 Windows 上 SQLite 文件锁定问题"""
import gc
import pytest


@pytest.fixture(autouse=True)
def cleanup_sqlite_connections():
    """每个测试结束后强制垃圾回收，释放 SQLite 连接"""
    yield
    gc.collect()
