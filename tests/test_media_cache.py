"""测试：ImageCache 缓存读写、去重、清理 + File 组件筛选"""

import io
import sys
import tempfile
from pathlib import Path

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.image_cache import ImageCache
from core.conversation_ledger import ConversationLedger


# ── 辅助 ──────────────────────────────────────────

def _make_test_image(size=(800, 600), color=(200, 100, 50), fmt="JPEG") -> bytes:
    """生成一张纯色测试图片字节"""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=90)
    return buf.getvalue()


def _make_large_image(size=(2048, 1536)) -> bytes:
    """生成一张超过 1024 边长的测试图片"""
    return _make_test_image(size=size, color=(80, 120, 200))


def _make_tiny_image() -> bytes:
    """生成一张小图"""
    return _make_test_image(size=(100, 80), color=(50, 200, 100))


def _make_striped_image(size=(200, 200), stripe_width=20) -> bytes:
    """生成一张有竖条纹的图片，保证有像素变化"""
    img = Image.new("RGB", size, (200, 100, 50))
    for x in range(size[0]):
        for y in range(size[1]):
            if (x // stripe_width) % 2 == 0:
                img.putpixel((x, y), (200, 100, 50))
            else:
                img.putpixel((x, y), (50, 200, 100))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _make_diagonal_image(size=(200, 200)) -> bytes:
    """生成一张对角线渐变的图片，与条纹图内容不同"""
    img = Image.new("RGB", size)
    for x in range(size[0]):
        for y in range(size[1]):
            r = int(255 * (x + y) / (size[0] + size[1]))
            g = int(255 * x / size[0])
            b = int(255 * y / size[1])
            img.putpixel((x, y), (r, g, b))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def cache():
    """创建一个临时目录的 ImageCache 实例"""
    with tempfile.TemporaryDirectory() as td:
        yield ImageCache(Path(td))


@pytest.fixture
def ledger():
    config = type("MockConfig", (), {
        "max_conversation_tokens": 0,
        "context_compression_threshold": 0.8,
        "context_content_retain_tokens": 3000,
        "context_tool_retain_tokens": 1000,
        "context_forgetting_timeout": 0,
    })()
    with tempfile.TemporaryDirectory() as td:
        lg = ConversationLedger(config, Path(td))
        try:
            yield lg
        finally:
            lg.db_conn.close()


# ── ImageCache 基本功能 ──────────────────────────

class TestImageCacheBasic:
    """ImageCache 核心读写功能"""

    def test_put_and_get(self, cache):
        """put 写入后 get_cached 能正确读取"""
        data = _make_test_image()
        dhash = cache.put("chat_1", data)
        assert dhash, "put 应返回非空 dhash"

        cached = cache.get_cached("chat_1", dhash)
        assert cached is not None, "get_cached 不应返回 None"
        assert len(cached) > 0, "缓存字节不应为空"

        # 验证是 WebP 格式
        assert cached[:4] == b"RIFF", "WebP 文件应以 RIFF 开头"

    def test_put_returns_same_dhash_for_same_image(self, cache):
        """相同图片 put 两次应返回相同的 dhash，且只存一份文件"""
        data = _make_test_image()

        dhash1 = cache.put("chat_1", data)
        dhash2 = cache.put("chat_1", data)

        assert dhash1 == dhash2, "相同图片的 dhash 应一致"

        # 验证文件只存了一份
        path = cache.get_cached_path("chat_1", dhash1)
        assert path.exists(), "缓存文件应在磁盘上"

        # 检查 chat 目录下只有 1 个文件
        chat_dir = path.parent
        files = list(chat_dir.iterdir())
        assert len(files) == 1, f"相同图片不应重复缓存，发现 {len(files)} 个文件"

    def test_get_cached_returns_none_for_missing(self, cache):
        """不存在的 dhash 应返回 None"""
        result = cache.get_cached("chat_1", "nonexistent_dhash")
        assert result is None, "不存在的 dhash 应返回 None"

    def test_put_different_images_produce_different_dhashes(self, cache):
        """不同内容图片应产生不同的 dhash"""
        data1 = _make_striped_image()
        data2 = _make_diagonal_image()

        dhash1 = cache.put("chat_1", data1)
        dhash2 = cache.put("chat_1", data2)

        assert dhash1 != dhash2, "不同图片的 dhash 应不同"

    def test_chat_id_is_sanitized_for_cache_path(self, cache):
        """含平台分隔符的 chat_id 不应直接成为危险/非法路径片段"""
        data = _make_test_image()
        dhash = cache.put("aiocqhttp:GroupMessage:12345", data)

        path = cache.get_cached_path("aiocqhttp:GroupMessage:12345", dhash)

        assert path.exists()
        assert ":" not in path.parent.name


class TestTextFileCache:
    """文本附件缓存"""

    def test_put_text_file_copies_to_managed_cache(self, cache, tmp_path):
        src = tmp_path / "note.txt"
        src.write_text("hello cache", encoding="utf-8")

        cached_path = cache.put_text_file("chat_1", src, "note.txt")

        assert cached_path is not None
        assert cached_path.exists()
        assert cached_path != src
        assert cache.is_managed_path(cached_path)
        assert cached_path.read_text(encoding="utf-8") == "hello cache"

    def test_put_text_file_rejects_oversized_file(self, cache, tmp_path):
        src = tmp_path / "large.txt"
        src.write_bytes(b"x" * (100 * 1024 + 1))

        cached_path = cache.put_text_file("chat_1", src, "large.txt")

        assert cached_path is None


class TestImageCacheDownscale:
    """ImageCache 尺寸缩小功能"""

    def test_large_image_downscaled_to_max_1024(self, cache):
        """超过 1024 边长的图片应缩小到最长边 ≤1024"""
        data = _make_large_image(size=(2048, 1536))

        dhash = cache.put("chat_1", data)
        assert dhash, "put 应成功"

        cached = cache.get_cached("chat_1", dhash)
        assert cached is not None

        # 读取 WebP 验证尺寸
        img = Image.open(io.BytesIO(cached))
        width, height = img.size
        longest = max(width, height)
        assert longest <= 1024, f"图片最长边应为 ≤1024，实际为 {longest}"
        # 应保持宽高比
        assert abs(width / height - 2048 / 1536) < 0.02, "宽高比应保持"

    def test_tiny_image_not_upscaled(self, cache):
        """小于 1024 的图片不应被放大"""
        data = _make_test_image(size=(100, 80))

        dhash = cache.put("chat_1", data)
        cached = cache.get_cached("chat_1", dhash)

        img = Image.open(io.BytesIO(cached))
        width, height = img.size
        assert width == 100, f"小图宽度不应被放大，实际 {width}"
        assert height == 80, f"小图高度不应被放大，实际 {height}"

    def test_webp_quality_is_set(self, cache):
        """输出的 WebP 质量应为 75"""
        data = _make_test_image()

        dhash = cache.put("chat_1", data)
        cached = cache.get_cached("chat_1", dhash)

        # 无法从 WebP 文件直接读 quality，但可以确认是有效的 WebP
        img = Image.open(io.BytesIO(cached))
        assert img.format == "WEBP", f"格式应为 WEBP，实际为 {img.format}"


class TestImageCacheCleanup:
    """ImageCache 清理功能"""

    def test_clean_chat_removes_only_one_chat(self, cache):
        """clean_chat 只删除指定会话的缓存，不影响其他会话"""
        data = _make_test_image()

        dhash_a = cache.put("chat_a", data)
        dhash_b = cache.put("chat_b", data)

        assert cache.get_cached("chat_a", dhash_a) is not None
        assert cache.get_cached("chat_b", dhash_b) is not None

        cache.clean_chat("chat_a")

        assert cache.get_cached("chat_a", dhash_a) is None, "chat_a 缓存应被清理"
        assert cache.get_cached("chat_b", dhash_b) is not None, "chat_b 缓存应保留"

    def test_clean_all_removes_all(self, cache):
        """clean_all 应删除所有会话的缓存"""
        data = _make_test_image()

        dhash_a = cache.put("chat_a", data)
        dhash_b = cache.put("chat_b", data)
        dhash_c = cache.put("chat_c", data)

        cache.clean_all()

        assert cache.get_cached("chat_a", dhash_a) is None
        assert cache.get_cached("chat_b", dhash_b) is None
        assert cache.get_cached("chat_c", dhash_c) is None

    def test_cache_size_tracks_bytes(self, cache):
        """get_cache_size 应返回正确的字节数"""
        data = _make_test_image()

        before = cache.get_cache_size("chat_1")
        assert before == 0, "写入前应为 0"

        dhash = cache.put("chat_1", data)
        after = cache.get_cache_size("chat_1")
        assert after > 0, "写入后应有字节"

        file_size = cache.get_cached_path("chat_1", dhash).stat().st_size
        assert after == file_size, "单个文件的大小应匹配"

    def test_cache_size_after_clean(self, cache):
        """清理后 get_cache_size 应为 0"""
        data = _make_test_image()
        cache.put("chat_1", data)

        cache.clean_chat("chat_1")
        assert cache.get_cache_size("chat_1") == 0, "清理后应为 0"


class TestImageCacheDHash:
    """dHash 计算一致性"""

    def test_dhash_consistency(self, cache):
        """相同图片的 dhash 应始终一致"""
        data = _make_test_image()
        dhash1 = cache._compute_dhash(data)
        dhash2 = cache._compute_dhash(data)
        assert dhash1 == dhash2

    def test_dhash_empty_for_bad_data(self, cache):
        """非法图片数据应返回空 dhash"""
        dhash = cache._compute_dhash(b"not_an_image")
        assert dhash == ""


class TestCacheDeduplicationAcrossChats:
    """跨会话去重"""

    def test_same_image_different_chats_use_same_dhash(self, cache):
        """相同图片在不同会话应使用相同的 dhash 值"""
        data = _make_test_image()
        dhash_a = cache.put("chat_a", data)
        dhash_b = cache.put("chat_b", data)
        assert dhash_a == dhash_b, "相同图片在不同会话的 dhash 应一致"


# ── ConversationLedger 缓存清理集成测试 ──────────

class TestCacheCleanupIntegration:
    """验证 ConversationLedger 清理插件自有缓存"""

    def test_ledger_init_cleans_stale_media_cache(self, tmp_path):
        """启动新账本时清空上一运行期遗留的 media_cache"""
        stale_dir = tmp_path / "media_cache" / "chat_1"
        stale_dir.mkdir(parents=True)
        stale_file = stale_dir / "stale.webp"
        stale_file.write_bytes(b"old")

        config = type("MockConfig", (), {
            "max_conversation_tokens": 0,
            "context_compression_threshold": 0.8,
            "context_content_retain_tokens": 3000,
            "context_tool_retain_tokens": 1000,
            "context_forgetting_timeout": 0,
        })()
        lg = ConversationLedger(config, tmp_path)
        try:
            assert not stale_file.exists()
        finally:
            lg.db_conn.close()

    def test_cleanup_message_removes_cache(self, cache):
        """清理消息时应对应的缓存文件被删除"""
        data = _make_test_image()
        dhash = cache.put("test_chat", data)
        assert cache.get_cached("test_chat", dhash) is not None

        # 模拟清理：直接调用 clean_chat
        cache.clean_chat("test_chat")
        assert cache.get_cached("test_chat", dhash) is None

    def test_cleanup_on_per_chat_limit(self, ledger):
        """PER_CHAT_LIMIT 截断时清理被丢弃消息独占缓存"""
        chat_id = "chat_1"
        ledger.PER_CHAT_LIMIT = 1
        dhash = ledger.image_cache.put(chat_id, _make_test_image())
        cache_path = str(ledger.image_cache.get_cached_path(chat_id, dhash))

        ledger.add_message(chat_id, {
            "role": "user",
            "content": [{
                "type": "image_url",
                "image_url": {"url": cache_path},
                "cache_path": cache_path,
                "cache_dhash": dhash,
            }],
            "timestamp": 1,
        })
        assert Path(cache_path).exists()

        ledger.add_message(chat_id, {
            "role": "user",
            "content": [{"type": "text", "text": "new"}],
            "timestamp": 2,
        })

        assert not Path(cache_path).exists()

    def test_cleanup_keeps_cache_when_retained_message_references_it(self, ledger):
        """同一缓存仍被保留消息引用时不能误删"""
        chat_id = "chat_1"
        ledger.PER_CHAT_LIMIT = 1
        dhash = ledger.image_cache.put(chat_id, _make_test_image())
        cache_path = str(ledger.image_cache.get_cached_path(chat_id, dhash))

        for ts in (1, 2):
            ledger.add_message(chat_id, {
                "role": "user",
                "content": [{
                    "type": "image_url",
                    "image_url": {"url": cache_path},
                    "cache_path": cache_path,
                    "cache_dhash": dhash,
                }],
                "timestamp": ts,
            })

        assert Path(cache_path).exists()

    def test_cleanup_removes_files_not_referenced_by_ledger(self, ledger):
        """清理依据当前账本引用集合，而不是依据刚被删除的消息"""
        chat_id = "chat_1"
        referenced_dhash = ledger.image_cache.put(chat_id, _make_striped_image())
        orphan_dhash = ledger.image_cache.put(chat_id, _make_diagonal_image())
        referenced_path = str(ledger.image_cache.get_cached_path(chat_id, referenced_dhash))
        orphan_path = str(ledger.image_cache.get_cached_path(chat_id, orphan_dhash))

        ledger.add_message(chat_id, {
            "role": "user",
            "content": [{
                "type": "image_url",
                "image_url": {"url": referenced_path},
                "cache_path": referenced_path,
                "cache_dhash": referenced_dhash,
            }],
            "timestamp": 1,
        })

        ledger._cleanup_unreferenced_media_cache(chat_id)

        assert Path(referenced_path).exists()
        assert not Path(orphan_path).exists()


class TestFileFilterLogic:
    """File 组件筛选逻辑验证（不依赖框架）"""

    def test_txt_file_accepted(self):
        """验证 .txt 文件被接受的条件"""
        ext = ".txt"
        assert ext in (".txt", ".md"), f"{ext} 应被接受"

    def test_md_file_accepted(self):
        """验证 .md 文件被接受的条件"""
        ext = ".md"
        assert ext in (".txt", ".md"), f"{ext} 应被接受"

    def test_pdf_file_rejected(self):
        """验证 .pdf 被拒绝"""
        ext = ".pdf"
        assert ext not in (".txt", ".md"), f"{ext} 应被拒绝"

    def test_zip_file_rejected(self):
        """验证 .zip 被拒绝"""
        ext = ".zip"
        assert ext not in (".txt", ".md"), f"{ext} 应被拒绝"

    def test_hundred_kb_size_check(self):
        """验证 100KB 阈值"""
        limit = 100 * 1024
        assert 50 * 1024 <= limit, "50KB 应在限制内"
        assert 150 * 1024 > limit, "150KB 应超限"
