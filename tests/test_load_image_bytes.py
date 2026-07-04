"""
测试：ConversationLedger._load_image_bytes 对裸本地路径的兼容性

上游 PreProcessStage 会把 Image component 的 url 覆写为 ensure_jpeg 输出的
本地临时路径（不带 file:/// 前缀），_load_image_bytes 需要能直接读取这类路径。
"""

import asyncio
import struct
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.conversation_ledger import ConversationLedger


def _make_minimal_jpeg(path: str, width: int = 1, height: int = 1):
    """生成一个 1x1 的最小有效 JPEG 文件，不依赖 PIL。"""
    # JPEG 头 + SOF0 + SOS + EOI 的最小结构
    # 参考 JPEG 规范的最小有效文件
    data = b""
    # SOI
    data += b"\xff\xd8"
    # APP0 (JFIF)
    data += b"\xff\xe0"
    data += struct.pack(">H", 16)  # length
    data += b"JFIF\x00"
    data += b"\x01\x01"  # version
    data += b"\x00"  # units
    data += struct.pack(">HH", 1, 1)  # x/y density
    data += b"\x00\x00"  # thumbnail
    # DQT (量化表)
    data += b"\xff\xdb"
    data += struct.pack(">H", 67)  # length
    data += b"\x00"  # precision 0
    data += b"\x00" * 64  # 全是 0 的量化表
    # SOF0 (Start of Frame)
    data += b"\xff\xc0"
    data += struct.pack(">H", 11)  # length
    data += b"\x08"  # precision 8
    data += struct.pack(">HH", height, width)  # height, width
    data += b"\x01"  # number of components
    data += b"\x01\x11\x00"  # component 1: Y, sampling 1x1, quantization table 0
    # SOS (Start of Scan)
    data += b"\xff\xda"
    data += struct.pack(">H", 8)  # length
    data += b"\x01"  # number of components
    data += b"\x01\x00"  # component 1, DC/AC table 0
    data += b"\x00\x3f\x00"  # spectral selection, successive approximation
    # 扫描数据
    data += b"\x7f"
    # EOI
    data += b"\xff\xd9"

    with open(path, "wb") as f:
        f.write(data)
    return data


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def ledger(temp_dir):
    config = type("MockConfig", (), {
        "max_conversation_tokens": 5000,
        "context_content_retain_tokens": 3000,
        "context_tool_retain_tokens": 1000,
        "enable_context_compression_for_chat_ids": [],
    })()
    lg = ConversationLedger(config, temp_dir)
    yield lg
    lg.db_conn.close()


class TestLoadImageBytesBarePath:
    """测试 _load_image_bytes 对裸文件路径的兼容性"""

    @pytest.mark.asyncio
    async def test_bare_local_path_succeeds(self, ledger, temp_dir):
        """裸路径应该被识别并读取为文件"""
        img_file = str(temp_dir / "test_image.jpg")
        expected = _make_minimal_jpeg(img_file)

        result = await ledger._load_image_bytes(img_file)

        assert result == expected, "返回的字节与写入的 JPEG 不一致"
        assert len(result) > 0, "不应返回空字节"

    @pytest.mark.asyncio
    async def test_nonexistent_path_returns_empty(self, ledger):
        """不存在的路径不应抛出异常，应返回空字节"""
        result = await ledger._load_image_bytes("/tmp/nonexistent_file_12345.jpg")
        assert result == b"", "不存在的路径应返回空字节"

    @pytest.mark.asyncio
    async def test_file_larger_than_limit_returns_empty(self, ledger):
        """超过 10MB 阈值应拒绝读取"""
        import os
        import tempfile

        large_file = tempfile.mktemp(suffix=".jpg")
        try:
            # 写入 11MB
            with open(large_file, "wb") as f:
                f.write(b"\xff" * (11 * 1024 * 1024))

            result = await ledger._load_image_bytes(large_file)
            assert result == b"", "超过 10MB 的大文件应返回空字节"
        finally:
            try:
                os.unlink(large_file)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_http_url_unchanged(self, ledger):
        """HTTP URL 不应被路径兜底逻辑拦截，走到下载分支"""
        result = await ledger._load_image_bytes("http://example.com/image.jpg")
        # 因为测试环境没有网络，会返回空，但不应抛出异常
        assert result == b""

    @pytest.mark.asyncio
    async def test_file_uri_scheme_unchanged(self, ledger):
        """file:/// 路径仍走原始分支"""
        import os

        img_file = tempfile.mktemp(suffix=".jpg")
        try:
            _make_minimal_jpeg(img_file)
            file_uri = "file:///" + os.path.abspath(img_file).replace("\\", "/").lstrip("/")

            result = await ledger._load_image_bytes(file_uri)

            assert len(result) > 0, "file:/// 路径应正常读取"
        finally:
            try:
                os.unlink(img_file)
            except OSError:
                pass
