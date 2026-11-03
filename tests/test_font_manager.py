"""字体获取与「没就绪就不转图」的测试。

守住三件事：
1. ``ensure_fonts`` 绝不能在发送路径上等下载——没就绪就返回 False 并起后台任务。
2. 下载失败要记冷却，且不留半截文件。
3. 转图入口在字体未就绪时必须放弃转图、把原文原样留下。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot_plugin_angel_heart.core import font_manager as fm


@pytest.fixture(autouse=True)
def _reset_state():
    """每个用例前后清掉冷却、就绪缓存与后台任务句柄，避免相互串味。"""
    fm._last_failure = 0.0
    fm._download_task = None
    fm._ready = False
    yield
    task = fm._download_task
    if task is not None and not task.done():
        task.cancel()
    fm._download_task = None
    fm._last_failure = 0.0
    fm._ready = False


class TestReadyCheck:
    def test_missing_fonts_are_not_ready(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)

        assert fm.fonts_ready() is False

    def test_tiny_file_is_not_ready(self, monkeypatch, tmp_path):
        """体积明显不对的文件不能算就绪，避免半截下载蒙混过关。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        for name in fm.FONT_FILES:
            (tmp_path / name).write_bytes(b"0" * 10)

        assert fm.fonts_ready() is False

    def test_unloadable_file_is_not_ready(self, monkeypatch, tmp_path):
        """体积够了但 Pillow 加载不了，同样不算就绪。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        for name in fm.FONT_FILES:
            (tmp_path / name).write_bytes(b"x" * (fm._MIN_BYTES + 1))

        assert fm.fonts_ready() is False


class TestEnsureIsNonBlocking:
    """契约层：字体没就绪也不能拖住这条消息的发送。"""

    @pytest.mark.asyncio
    async def test_ready_returns_true_without_download(self, monkeypatch):
        monkeypatch.setattr(fm, "fonts_ready", lambda: True)
        started = []
        monkeypatch.setattr(fm, "_start_download", lambda: started.append(1))

        assert await fm.ensure_fonts() is True
        assert started == [], "已就绪时不该再触发下载"

    @pytest.mark.asyncio
    async def test_not_ready_returns_false_and_starts_download(self, monkeypatch):
        monkeypatch.setattr(fm, "fonts_ready", lambda: False)
        started = []
        monkeypatch.setattr(fm, "_start_download", lambda: started.append(1))

        assert await fm.ensure_fonts() is False
        assert started == [1], "未就绪时必须顺手触发后台下载"

    @pytest.mark.asyncio
    async def test_never_waits_for_download(self, monkeypatch):
        """下载哪怕永不结束，ensure_fonts 也必须立刻返回。"""
        monkeypatch.setattr(fm, "fonts_ready", lambda: False)
        release = asyncio.Event()

        async def stuck():
            await release.wait()

        monkeypatch.setattr(fm, "_download_all", stuck)

        result = await asyncio.wait_for(fm.ensure_fonts(), timeout=0.5)
        release.set()

        assert result is False


class TestStartDownload:
    @pytest.mark.asyncio
    async def test_reuses_running_task(self, monkeypatch):
        release = asyncio.Event()
        calls = []

        async def slow():
            calls.append(1)
            await release.wait()

        monkeypatch.setattr(fm, "_download_all", slow)

        fm._start_download()
        fm._start_download()
        fm._start_download()
        await asyncio.sleep(0)

        assert len(calls) == 1, "同一时刻只该有一个下载任务在跑"
        release.set()

    @pytest.mark.asyncio
    async def test_cooldown_creates_no_task(self, monkeypatch):
        """冷却期内连任务都不该建，否则每条长回复都会凭空多一个对象。"""
        fm._last_failure = time.monotonic()
        started = []

        async def slow():
            started.append(1)

        monkeypatch.setattr(fm, "_download_all", slow)

        fm._start_download()
        await asyncio.sleep(0)

        assert fm._download_task is None
        assert started == []

    @pytest.mark.asyncio
    async def test_finished_task_allows_restart(self, monkeypatch):
        """任务跑完必须能重新起一个，不能「done 了却永远不再试」。"""
        calls = []

        async def quick():
            calls.append(1)
            return True

        monkeypatch.setattr(fm, "_download_all", quick)

        fm._start_download()
        await asyncio.sleep(0.05)
        fm._start_download()
        await asyncio.sleep(0.05)

        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_done_handle_is_cleared(self, monkeypatch):
        """任务结束后不该继续挂着对象（会连带持有异常与协程帧）。"""

        async def quick():
            return True

        monkeypatch.setattr(fm, "_download_all", quick)

        fm._start_download()
        await asyncio.sleep(0.05)

        assert fm._download_task is None

    @pytest.mark.asyncio
    async def test_mkdir_failure_records_cooldown(self, monkeypatch, tmp_path):
        """目录建不出来也必须记冷却，否则每条长回复都会建一个立刻失败的任务。"""
        blocker = tmp_path / "blocked"
        blocker.write_text("占位成文件，让下面的 mkdir 抛 OSError")
        monkeypatch.setattr(fm, "FONT_DIR", blocker / "fonts")
        fm._last_failure = 0.0

        assert await fm._download_all() is False
        assert fm._last_failure > 0.0, "mkdir 失败同样要进冷却"

        started = []

        async def slow():
            started.append(1)

        monkeypatch.setattr(fm, "_download_all", slow)
        fm._start_download()
        await asyncio.sleep(0)

        assert started == [], "冷却期内不该再建任务"


class TestReadyCache:
    def test_ready_is_cached(self, monkeypatch, tmp_path):
        """就绪后不再重复解析字体，否则每条长回复白付一次同步 IO。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        calls = []

        def fake_usable(path):
            calls.append(path)
            return True

        monkeypatch.setattr(fm, "_usable", fake_usable)

        assert fm.fonts_ready() is True
        assert fm.fonts_ready() is True
        assert len(calls) == len(fm.FONT_FILES), "第二次必须直接命中缓存"

    def test_not_ready_is_not_cached(self, monkeypatch, tmp_path):
        """不就绪不能缓存，否则补好字体也永远判不出来。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        monkeypatch.setattr(fm, "_usable", lambda path: False)

        assert fm.fonts_ready() is False
        monkeypatch.setattr(fm, "_usable", lambda path: True)
        assert fm.fonts_ready() is True


class TestDownloadAll:
    """真正下载那一层的行为。"""

    @pytest.fixture(autouse=True)
    def _loose_usable(self, monkeypatch):
        """正例不依赖真实字体：只认「文件存在且够大」。"""
        monkeypatch.setattr(
            fm, "_usable", lambda p: p.is_file() and p.stat().st_size > 100
        )

    @pytest.mark.asyncio
    async def test_success_marks_ready_and_clears_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        fm._last_failure = 0.0

        async def fake_fetch(session, name, url):
            (tmp_path / name).write_bytes(b"x" * 200)

        monkeypatch.setattr(fm, "_fetch", fake_fetch)

        assert await fm._download_all() is True
        assert fm._last_failure == 0.0

    @pytest.mark.asyncio
    async def test_failure_records_cooldown(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)

        async def boom(session, name, url):
            raise RuntimeError("network down")

        monkeypatch.setattr(fm, "_fetch", boom)

        assert await fm._download_all() is False
        assert fm._last_failure > 0.0, "失败必须留下冷却时间戳"

    @pytest.mark.asyncio
    async def test_cooldown_blocks_retry(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        fm._last_failure = time.monotonic()
        calls = []

        async def boom(session, name, url):
            calls.append(name)
            raise RuntimeError("network down")

        monkeypatch.setattr(fm, "_fetch", boom)

        assert await fm._download_all() is False
        assert calls == [], "冷却期内一句网络都不该拉"

    @pytest.mark.asyncio
    async def test_cooldown_expires_and_retries(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        fm._last_failure = time.monotonic() - fm.RETRY_COOLDOWN - 1
        calls = []

        async def boom(session, name, url):
            calls.append(name)
            raise RuntimeError("still down")

        monkeypatch.setattr(fm, "_fetch", boom)

        assert await fm._download_all() is False
        assert calls, "冷却过期后必须重新尝试"


class TestFetch:
    @pytest.mark.asyncio
    async def test_part_file_cleaned_on_failure(self, monkeypatch, tmp_path):
        """真实 _fetch：读到一半断连，必须把 .part 清掉、不碰正式路径。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)

        class _DroppingContent:
            def __init__(self):
                self._calls = 0

            async def read(self, size):
                self._calls += 1
                if self._calls == 1:
                    return b"x" * 64
                raise RuntimeError("connection dropped")

        class _Resp:
            status = 200
            content_length = 4096

            def __init__(self):
                self.content = _DroppingContent()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class _Session:
            def get(self, url):
                return _Resp()

        with pytest.raises(RuntimeError):
            await fm._fetch(
                _Session(), "NeoXiHei.ttf", "http://x.invalid/f.ttf"
            )

        assert list(tmp_path.iterdir()) == [], "失败后不能留下半截文件"


class TestShutdown:
    @pytest.mark.asyncio
    async def test_cancel_leaves_no_part_file(self, monkeypatch, tmp_path):
        """插件被卸载时取消下载，不能在半路留下 .part。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        holding = asyncio.Event()

        class _HangingContent:
            async def read(self, size):
                await holding.wait()
                return b""

        class _Resp:
            status = 200
            content_length = 4096

            def __init__(self):
                self.content = _HangingContent()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class _Session:
            def get(self, url):
                return _Resp()

        done = asyncio.Event()

        async def stuck():
            try:
                await fm._fetch(
                    _Session(), "NeoXiHei.ttf", "http://x.invalid/f.ttf"
                )
            finally:
                done.set()

        monkeypatch.setattr(fm, "_download_all", stuck)
        fm._start_download()
        await asyncio.sleep(0.05)

        await fm.shutdown_fonts()
        await asyncio.wait_for(done.wait(), timeout=1)

        assert fm._download_task is None, "取消后句柄必须清零"
        assert list(tmp_path.iterdir()) == [], "取消路径不能留下半截文件"

    @pytest.mark.asyncio
    async def test_shutdown_is_safe_without_task(self):
        """没有在跑的下载时，收口也要能直接过。"""
        fm._download_task = None
        await fm.shutdown_fonts()
        assert fm._download_task is None


class TestWebUIControl:
    """WebUI 用到的状态快照、手动下载与上传安装。"""

    def test_status_snapshot_shape(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        snap = fm.status_snapshot()

        assert set(snap) >= {"dir", "ready", "downloading", "cooldown_remaining", "weights"}
        assert snap["ready"] is False
        assert snap["downloading"] is False
        assert snap["cooldown_remaining"] == 0
        assert [w["key"] for w in snap["weights"]] == ["regular"]
        assert all(w["size"] == 0 for w in snap["weights"])

    def test_status_reports_cooldown(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        fm._last_failure = time.monotonic()

        snap = fm.status_snapshot()

        assert 0 < snap["cooldown_remaining"] <= fm.RETRY_COOLDOWN

    def test_reports_real_sizes(self, monkeypatch, tmp_path):
        """有文件时必须报出真实体积，前端要拿它显示 MB。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        monkeypatch.setattr(fm, "_usable", lambda p: False)
        (tmp_path / fm.WEIGHT_FILES["regular"]).write_bytes(b"x" * 4321)

        snap = fm.status_snapshot()
        sizes = {w["key"]: w["size"] for w in snap["weights"]}

        assert sizes["regular"] == 4321

    @pytest.mark.asyncio
    async def test_manual_fetch_blocked_by_cooldown(self, monkeypatch):
        fm._last_failure = time.monotonic()
        assert fm.start_download_now() is False

    @pytest.mark.asyncio
    async def test_manual_fetch_starts_task(self, monkeypatch):
        release = asyncio.Event()

        async def slow():
            await release.wait()

        monkeypatch.setattr(fm, "_download_all", slow)
        fm._last_failure = 0.0

        assert fm.start_download_now() is True
        assert fm._download_task is not None
        release.set()

    def test_reset_ready_cache(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        monkeypatch.setattr(fm, "_usable", lambda p: True)
        assert fm.fonts_ready() is True
        assert fm._ready is True

        fm.reset_ready_cache()

        assert fm._ready is False


_REAL_FONT = Path(__file__).resolve().parent.parent / "fonts" / fm.WEIGHT_FILES["regular"]
_HAS_REAL_FONT = _REAL_FONT.is_file() and _REAL_FONT.stat().st_size > 4 * 1024 * 1024


class _FakeUpload:
    """只暴露异步 read(size)，与框架 UploadFile 的关键接口一致。"""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def read(self, size: int) -> bytes:
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


class TestInstallFont:
    @pytest.mark.asyncio
    async def test_unknown_weight_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)

        with pytest.raises(ValueError, match="未知字重"):
            await fm.install_font("bold", _FakeUpload(b"x" * 200))

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_too_small_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)

        with pytest.raises(ValueError, match="不像是完整字体"):
            await fm.install_font("regular", _FakeUpload(b"x" * 5000))

        assert list(tmp_path.iterdir()) == [], "被拒的上传不能留任何残留"

    @pytest.mark.asyncio
    async def test_oversize_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        monkeypatch.setattr(fm, "MAX_UPLOAD_BYTES", 2048)

        with pytest.raises(ValueError, match="上限"):
            await fm.install_font("regular", _FakeUpload(b"x" * 8192))

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_bad_glyphs_rejected(self, monkeypatch, tmp_path):
        """能落盘但画不出字形的文件必须拒，且不能污染已有正式文件。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        existing = tmp_path / fm.WEIGHT_FILES["regular"]
        existing.write_bytes(b"original")

        with pytest.raises(ValueError, match="中文字形"):
            await fm.install_font("regular", _FakeUpload(b"y" * (2 * 1024 * 1024)))

        assert existing.read_bytes() == b"original", "校验失败不能动正式文件"
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            fm.WEIGHT_FILES["regular"]
        ]

    @pytest.mark.asyncio
    async def test_success_writes_target(self, monkeypatch, tmp_path):
        """落盘、改名、缓存失效这三步是自查逻辑，不依赖真实字体。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        monkeypatch.setattr(fm, "_probe_glyphs", lambda p: True)
        monkeypatch.setattr(fm, "_usable", lambda p: p.is_file())
        fm._last_failure = 12345.0
        fm._ready = True

        result = await fm.install_font("regular", _FakeUpload(b"z" * (2 * 1024 * 1024)))

        target = tmp_path / fm.WEIGHT_FILES["regular"]
        assert target.is_file()
        assert target.stat().st_size == 2 * 1024 * 1024
        assert result["key"] == "regular"
        assert result["size"] == 2 * 1024 * 1024
        assert fm._last_failure == 0.0, "上传成功要清掉旧的失败冷却"
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            fm.WEIGHT_FILES["regular"]
        ], "只该留下正式文件，临时文件必须清掉"

    @pytest.mark.asyncio
    async def test_success_clears_render_cache(self, monkeypatch, tmp_path):
        """换字体后渲染器缓存必须失效，否则出图还是旧字形。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        monkeypatch.setattr(fm, "_probe_glyphs", lambda p: True)
        monkeypatch.setattr(fm, "_usable", lambda p: p.is_file())
        cleared = []
        monkeypatch.setattr(fm, "_clear_render_cache", lambda: cleared.append(1))

        await fm.install_font("regular", _FakeUpload(b"z" * (2 * 1024 * 1024)))

        assert cleared == [1], "install 之后必须清渲染缓存"

    @pytest.mark.skipif(not _HAS_REAL_FONT, reason="容器内没有真实字体文件")
    def test_clear_font_cache_forces_reload(self):
        """渲染器缓存不清，替换磁盘文件后仍会画旧字形。"""
        from astrbot_plugin_angel_heart.core import long_image_render as render

        first = render.load(str(_REAL_FONT), 24)
        again = render.load(str(_REAL_FONT), 24)
        assert first is again, "同一 (path, size) 应命中缓存"

        render.clear_font_cache()

        reloaded = render.load(str(_REAL_FONT), 24)
        assert reloaded is not first, "清缓存后必须重新解析字体文件"

    @pytest.mark.skipif(not _HAS_REAL_FONT, reason="容器内没有真实字体文件")
    @pytest.mark.asyncio
    async def test_real_font_roundtrip(self, monkeypatch, tmp_path):
        """用真实字体走完整链路：上传 → 字形探测 → 就绪。"""
        monkeypatch.setattr(fm, "FONT_DIR", tmp_path)
        payload = _REAL_FONT.read_bytes()

        result = await fm.install_font("regular", _FakeUpload(payload))

        assert result["size"] == len(payload)
        assert (tmp_path / fm.WEIGHT_FILES["regular"]).is_file()
        assert fm.fonts_ready() is True, "单字重装好就是全部就绪"

    @pytest.mark.skipif(not _HAS_REAL_FONT, reason="容器内没有真实字体文件")
    def test_probe_accepts_real_font(self):
        assert fm._probe_glyphs(_REAL_FONT) is True

    def test_probe_rejects_garbage(self, tmp_path):
        junk = tmp_path / "junk.ttf"
        junk.write_bytes(b"not a font at all" * 1000)
        assert fm._probe_glyphs(junk) is False

    def test_probe_rejects_font_without_cjk(self, tmp_path):
        """纯西文字体不能算就绪。

        缺字时 Pillow 画的是 .notdef 方框，方框本身有 bbox——只判「画出了东西」
        会把 Arial 这类字体放进来，最后整篇中文变成豆腐块。
        """
        latin = None
        for cand in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ):
            if Path(cand).is_file():
                latin = Path(cand)
                break
        if latin is None:
            pytest.skip("容器内没有西文字体可测")

        padded = tmp_path / "latin.ttf"
        padded.write_bytes(latin.read_bytes() + b"\0" * (2 * 1024 * 1024))

        assert fm._probe_glyphs(padded) is False, "没有中文字形的字体不能被判为可用"
        assert fm._usable(padded) is False


class TestConvertGuard:
    @pytest.mark.asyncio
    async def test_no_font_means_no_conversion(self):
        """字体未就绪时必须放弃转图，回复原样保留。"""
        from astrbot.core.message.components import Plain

        from tests.test_long_text_image import _event, _plugin

        plugin = _plugin()
        chain = [Plain(text="这一段足够长，本来该转成图片，但字体不在就必须原样发。")]

        with patch(
            "astrbot_plugin_angel_heart.main.ensure_fonts",
            AsyncMock(return_value=False),
        ), patch(
            "astrbot_plugin_angel_heart.main.render_long_text_image",
            MagicMock(),
        ) as render:
            converted = await plugin._try_convert_long_reply(_event(), chain)

        assert converted is False
        assert isinstance(chain[0], Plain)
        render.assert_not_called()
