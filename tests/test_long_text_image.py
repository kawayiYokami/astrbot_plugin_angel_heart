"""长回复转长图测试。

覆盖三件事：渲染器能把 Markdown 排成分页长图、阈值判定只对纯文本长回复
生效且失败必须退回原文、转图只替换发送用的消息链而不碰账本原文。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image as PILImage, ImageDraw, ImageOps

from astrbot.core.message.components import At, Image, Plain

from astrbot_plugin_angel_heart.core import long_image_render as renderer


@pytest.fixture(autouse=True)
def _fonts_always_ready():
    """本文件的用例只测转图链路，字体一律视为就绪。

    字体获取与「拿不到就不转」由 test_font_manager.py 单独覆盖；这里若走真实
    路径，在没字体也没有网络的干净环境里会去拉几十 MB，用例既慢又不稳定。
    """
    with patch(
        "astrbot_plugin_angel_heart.main.ensure_fonts",
        AsyncMock(return_value=True),
    ):
        yield


# ---------- 渲染器 ----------

def _existing_font_dir() -> Path | None:
    """环境里现成的字体目录：优先插件数据目录，其次旧的插件内目录。

    字体正式落点是 plugin_data（插件目录会被更新整目录替换掉），单测里框架桩
    拿不到它，所以这里直接按路径找。
    """
    data_root = Path(__file__).resolve().parents[3]
    candidates = (
        data_root / "plugin_data" / "astrbot_plugin_angel_heart" / "fonts",
        Path(__file__).resolve().parent.parent / "fonts",
    )
    for base in candidates:
        if base.is_dir() and any(base.glob("*.ttf")):
            return base
    return None


class TestRenderer:
    @pytest.fixture(autouse=True)
    def _real_font(self, monkeypatch):
        """渲染用例要真字形，指到环境里现成的那份字体目录上。"""
        base = _existing_font_dir()
        if base is None:
            pytest.skip("环境里没有可用字体，渲染用例无法验证字形")

        from astrbot_plugin_angel_heart.core import font_manager as fm

        monkeypatch.setattr(fm, "_FONT_DIR", base)
        renderer.clear_font_cache()

    def test_parses_markdown_structures(self):
        kinds = [
            b["kind"]
            for b in renderer.parse_blocks(
                "## 标题\n\n正文一段\n\n- 项目\n\n> 引用\n\n```\ncode\n```\n\n---\n\n| a | b |\n|---|---|\n| 1 | 2 |"
            )
        ]

        assert kinds == ["h", "p", "li", "quote", "code", "hr", "table"]

    def test_short_text_is_single_page(self):
        pages = renderer.render_markdown("只有一句话的短回复。", scale=2)

        assert len(pages) == 1
        # 输出必须保留超采样后的物理像素：手机 DPR 下才有足够分辨率落地
        assert pages[0].width == renderer.W * 2, "输出宽度必须是逻辑宽 × 超采样倍率"

    def test_pages_never_exceed_height_cap(self):
        paragraph = "群里越是安静的时候，冒出来的那句「我在」，反而最容易被记住。"
        # 段落数按当前字号取值：16px 下 240 段才超过单页高度上限
        text = "\n\n".join([paragraph] * 240)

        pages = renderer.render_markdown(text, scale=2)

        assert len(pages) > 1, "长文必须分页，不能一张画到底"
        assert all(page.height <= renderer.PAGE_MAX_H * 2 for page in pages)

    def test_oversized_single_paragraph_is_split(self):
        # 一整段一万四千字，单块高度必然超过上限，只能按行切开
        pages = renderer.render_markdown("\n".join(["一" * 60] * 240), scale=2)

        assert len(pages) > 1
        assert all(page.height <= renderer.PAGE_MAX_H * 2 for page in pages)

    def test_empty_text_rejected(self):
        with pytest.raises(ValueError):
            renderer.render_markdown("   \n\n  ", scale=2)

    def test_missing_glyph_does_not_raise(self):
        pages = renderer.render_markdown("表情符号 \U0001F600 和生僻字 \u9fa5 都要能跳过", scale=2)

        assert len(pages) == 1

    def test_lead_punct_ink_starts_at_pen(self):
        """行首开引号必须落在笔位附近，不能被后一个字盖掉。

        这是「引用块/代码块/表格里开引号消失」的直接回归点：那三处曾经传
        lead_shift=False，引号的墨迹留在字框右半边，而步进只占半字宽，后一个字
        正好压在上面，出来只剩一条细竖线，看着像数字 1。
        """
        font_path = Path(renderer.body_font_path())
        if not font_path.exists():
            pytest.skip("本环境没有可用的黑体字形，笔位与墨迹的比对需要真实字形")

        font = renderer.load(str(font_path), 40)
        pen_x = 80
        lead_w = round(renderer.advance(font, "「"))
        box = (0, 0, pen_x + lead_w, 100)  # 开引号独占的一格，下一个字从右侧开始

        def ink_pixels(chars):
            image = PILImage.new("RGB", (200, 100), "white")
            renderer.draw_line(
                ImageDraw.Draw(image), (pen_x, 20), [(c, font) for c in chars], "black"
            )
            layer = ImageOps.invert(image.convert("L")).crop(box)
            return sum(1 for v in layer.tobytes() if v > 8)

        alone = ink_pixels("「")
        assert alone > 0, "开引号没有画在笔位这一格里，会被下一个字覆盖"

        with_next = ink_pixels("「一")
        assert with_next == alone, "后一个字覆盖了开引号的部分笔画"

    def test_paginate_packs_blocks_greedily(self):
        items = [
            {"kind": "p", "lines": [], "line_h": 10, "draw_h": 10, "gap": 0, "indent": 0}
            for _ in range(10)
        ]

        pages = renderer.paginate(items, base_h=0, page_max=30)

        assert len(pages) == 4, "每页只装得下三块"


# ---------- 插件接线 ----------

class _FakeConfig:
    def __init__(self, enabled=True, threshold=10, alias="AngelHeart|小吉祥草王"):
        self.long_text_image_enabled = enabled
        self.long_text_image_threshold = threshold
        self.alias = alias
        self.strip_markdown_enabled = False
        self.strip_period_before_newline = False


def _plugin(config=None):
    from astrbot_plugin_angel_heart.main import AngelHeartPlugin

    plugin = object.__new__(AngelHeartPlugin)
    plugin.config_manager = config or _FakeConfig()
    plugin.context = MagicMock()
    return plugin


def _event():
    event = MagicMock()
    event.unified_msg_origin = "aiocqhttp:GroupMessage:1001"
    event.get_extra.return_value = None
    return event


def _paths(tmp_path, count=1):
    from PIL import Image as PILImage

    paths = []
    for i in range(count):
        path = tmp_path / f"page{i + 1}.webp"
        PILImage.new("RGB", (4, 4), (255, 255, 255)).save(path, "WEBP")
        paths.append(str(path))
    return paths


class TestLongReplyConvert:
    @pytest.mark.asyncio
    async def test_long_plain_text_replaced_by_image(self, tmp_path):
        plugin = _plugin()
        chain = [Plain(text="这是一段足够长的回复内容，用来触发转图判定。")]

        with patch(
            "astrbot_plugin_angel_heart.main.render_long_text_image",
            MagicMock(return_value=_paths(tmp_path, 1)),
        ) as render:
            converted = await plugin._try_convert_long_reply(_event(), chain)

        assert converted is True
        assert render.call_count == 1
        assert len(chain) == 1 and isinstance(chain[0], Image)

    @pytest.mark.asyncio
    async def test_multi_page_appends_every_image(self, tmp_path):
        plugin = _plugin()
        chain = [Plain(text="很长的一段回复" * 20)]

        with patch(
            "astrbot_plugin_angel_heart.main.render_long_text_image",
            MagicMock(return_value=_paths(tmp_path, 3)),
        ):
            converted = await plugin._try_convert_long_reply(_event(), chain)

        assert converted is True
        assert len(chain) == 3 and all(isinstance(c, Image) for c in chain)

    @pytest.mark.asyncio
    async def test_short_reply_kept_as_text(self):
        plugin = _plugin(_FakeConfig(threshold=100))
        chain = [Plain(text="短回复")]

        with patch(
            "astrbot_plugin_angel_heart.main.render_long_text_image", MagicMock()
        ) as render:
            converted = await plugin._try_convert_long_reply(_event(), chain)

        assert converted is False
        render.assert_not_called()
        assert chain[0].text == "短回复"

    @pytest.mark.asyncio
    async def test_disabled_by_config(self):
        plugin = _plugin(_FakeConfig(enabled=False, threshold=1))
        chain = [Plain(text="哪怕很长也不转图" * 10)]

        with patch(
            "astrbot_plugin_angel_heart.main.render_long_text_image", MagicMock()
        ) as render:
            converted = await plugin._try_convert_long_reply(_event(), chain)

        assert converted is False
        render.assert_not_called()

    @pytest.mark.asyncio
    async def test_chain_with_at_component_untouched(self):
        plugin = _plugin(_FakeConfig(threshold=1))
        chain = [At(qq="10001"), Plain(text="带艾特的长回复，不该被整条换掉")]

        with patch(
            "astrbot_plugin_angel_heart.main.render_long_text_image", MagicMock()
        ) as render:
            converted = await plugin._try_convert_long_reply(_event(), chain)

        assert converted is False
        render.assert_not_called()
        assert isinstance(chain[0], At) and isinstance(chain[1], Plain)

    @pytest.mark.asyncio
    async def test_render_failure_falls_back_to_text(self):
        plugin = _plugin()
        chain = [Plain(text="渲染会失败的这段长回复，必须原样留下。")]

        with patch(
            "astrbot_plugin_angel_heart.main.render_long_text_image",
            MagicMock(side_effect=RuntimeError("font missing")),
        ):
            converted = await plugin._try_convert_long_reply(_event(), chain)

        assert converted is False
        assert isinstance(chain[0], Plain), "渲染失败不能把回复弄丢"

    @pytest.mark.asyncio
    async def test_empty_result_falls_back_to_text(self):
        plugin = _plugin()
        chain = [Plain(text="渲染没有产出图片时也不能把回复弄丢。")]

        with patch(
            "astrbot_plugin_angel_heart.main.render_long_text_image",
            MagicMock(return_value=[]),
        ):
            converted = await plugin._try_convert_long_reply(_event(), chain)

        assert converted is False
        assert isinstance(chain[0], Plain)

    def test_brand_is_longest_alias(self):
        plugin = _plugin(_FakeConfig(alias="草王|小吉祥草王|纳西妲"))

        assert plugin._long_image_brand() == "小吉祥草王"

    def test_brand_empty_when_alias_blank(self):
        plugin = _plugin(_FakeConfig(alias="  |  "))

        assert plugin._long_image_brand() == ""


class _ImmediateTasks:
    """把追踪装饰器换成直接执行，避免测试里起真实子任务。"""

    async def run(self, event, fn, *, owns_pipeline=False):
        return await fn()


class TestLedgerKeepsOriginal:
    @pytest.mark.asyncio
    async def test_conversion_only_touches_outgoing_chain(self, tmp_path):
        """账本从 run_context.messages 取原文，转图只该动发送用的消息链。"""
        original = "这是一段需要转成图片的长回复原文。"
        content = [SimpleNamespace(text=original)]
        run_context = SimpleNamespace(
            messages=[SimpleNamespace(role="assistant", content=content)]
        )
        chain = [Plain(text=original)]

        event = _event()
        event.get_result.return_value = SimpleNamespace(chain=chain)

        plugin = _plugin(_FakeConfig(threshold=1))
        plugin._runtime_tasks = _ImmediateTasks()
        plugin._is_upstream_command_event = lambda ev: False
        plugin._is_astrbot_error_message = lambda text: False
        plugin.angel_context = MagicMock()
        plugin.angel_context.debounce_manager.charge_reply_energy = AsyncMock()

        with patch(
            "astrbot_plugin_angel_heart.main.render_long_text_image",
            MagicMock(return_value=_paths(tmp_path, 2)),
        ):
            await plugin.strip_markdown_on_decorating_result(event, run_context)

        assert len(chain) == 2 and all(isinstance(c, Image) for c in chain)
        assert run_context.messages[0].content[0].text == original, (
            "账本原文不能被图片替换"
        )
        plugin.angel_context.debounce_manager.charge_reply_energy.assert_awaited_once()
