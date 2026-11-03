"""霞鹜新晰黑字体的按需获取。

插件不随包分发字体。首次真正要转图时才调用 :func:`ensure_fonts`：字体文件在、
且能被 Pillow 真正加载出中文字形才算就绪。单字重 7.1MB，粗体由渲染器描边加厚，
不为常规与加粗各存一份文件。

:func:`ensure_fonts` 不等待下载：字体没就绪就顺手触发一次后台补齐并立刻返回
False，这条长回复按原文发出，下一条才可能有图。任何一步失败都不会拿容器里的
系统字体顶上——那会让整张图字形走样，正是「字体看着不对」的来路。

失败结果会记一小段时间，避免每条长回复都重新拉一次网络。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import aiofiles
import aiohttp
from PIL import ImageFont

from astrbot.api import logger

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# 思源黑体（Google 的 Noto Sans SC，与思源黑体同源同形），SIL OFL 1.1。
# 可变字体一个文件含 9 个字重，粗体是字体自带的，不必再为加粗单独下一份文件。
_RELEASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc"

# 字重键名 <-> 文件名。WebUI 上传、状态查询与后台下载共用同一份映射，
# 文件名只从这张表取，绝不接受外部传入的名字参与路径拼接。
WEIGHT_FILES = {
    "regular": "NotoSansSC-VF.ttf",
}

# 远端文件名带方括号，必须转义；本地落盘用干净名字，避免路径里出现 [ ]
FONT_FILES = {
    "NotoSansSC-VF.ttf": f"{_RELEASE}/NotoSansSC%5Bwght%5D.ttf",
}

DOWNLOAD_TIMEOUT = 240      # 单次获取总超时（秒），17.7MB 走慢速链路也要给够
RETRY_COOLDOWN = 600        # 失败后多久之内不再重试（秒）
# 体积只做「明显不是字体」的粗筛，真正能不能用交给字形探测。
# 下载与上传必须用同一个下限：两套阈值不一致会造成「上传成功、却永远不就绪」，
# 把本来能转图的部署悄悄打回不转图。
_MIN_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 40 * 1024 * 1024  # WebUI 上传的体积上限

_download_task: asyncio.Task | None = None
_last_failure = 0.0
# 就绪是终态：字体文件只增不减，验过一次之后不必每条消息再解析 11.7MB 字体。
# 每条长回复省掉的不是网络等待，而是事件循环里约 45ms 的同步 IO。
_ready = False


def _usable(path: Path) -> bool:
    """文件存在、体积像样、且真能画出中文字形。三者缺一都不算就绪。"""
    try:
        if not path.is_file() or path.stat().st_size < _MIN_BYTES:
            return False
    except OSError:
        return False
    return _probe_glyphs(path)


def fonts_ready() -> bool:
    """两套字体是否都已就绪。只查本地，不触发任何网络动作。"""
    global _ready
    if _ready:
        return True
    if not all(_usable(FONT_DIR / name) for name in FONT_FILES):
        return False
    _ready = True
    return True


async def _fetch(session: aiohttp.ClientSession, name: str, url: str) -> None:
    """下载单个字重。

    先落 .part，字节数与加载能力都校验通过才原子改名。Pillow 只读 sfnt 头部和
    cmap，截断的文件照样能加载，所以长度对账不能省——否则半截字体进了正式路径，
    之后每次都被判为就绪，出图缺字形也没有任何告警。
    """
    target = FONT_DIR / name
    # part 名带 pid 与时间戳：插件 reload 时残留的旧任务可能与新任务同时下载，
    # 同名 .part 会被双方交错写入，字节对账却各自都过，最后拿混合内容去加载。
    part = target.with_name(f"{name}.{os.getpid()}.{int(time.time() * 1000)}.part")
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"{name} 下载返回 HTTP {resp.status}")
            expected = resp.content_length
            written = 0
            async with aiofiles.open(part, "wb") as fh:
                while True:
                    chunk = await resp.content.read(1 << 16)
                    if not chunk:
                        break
                    written += len(chunk)
                    await fh.write(chunk)
        if expected is not None and written != expected:
            raise RuntimeError(f"{name} 只收到 {written}/{expected} 字节")
        if not _usable(part):
            raise RuntimeError(f"{name} 下载完成但无法加载")
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)


async def _download_all() -> bool:
    """真正干活的那一层：串行补齐两套字体，失败记冷却。"""
    global _last_failure

    if _last_failure and time.monotonic() - _last_failure < RETRY_COOLDOWN:
        return False

    try:
        # mkdir 必须在 try 内：插件目录只读挂载、权限不足时这里会抛 OSError，
        # 落在 try 之外就会逃逸出去、不记冷却，变成每条长回复都建一个立刻失败的任务。
        FONT_DIR.mkdir(parents=True, exist_ok=True)
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for name, url in FONT_FILES.items():
                if _usable(FONT_DIR / name):
                    continue
                await _fetch(session, name, url)
    except Exception as exc:
        _last_failure = time.monotonic()
        logger.warning(f"AngelHeart: 新晰黑获取失败，下次长文再试: {exc}")
        return False

    if not fonts_ready():
        _last_failure = time.monotonic()
        logger.warning("AngelHeart: 字体文件已落地但无法加载，下次长文再试")
        return False

    _last_failure = 0.0
    _clear_render_cache()
    logger.info("AngelHeart: 新晰黑字体已就绪，下一次长文即可转图")
    return True


def _on_download_done(task: asyncio.Task) -> None:
    """后台任务没人 await，这里接手异常，避免 asyncio 打未取回警告。"""
    global _download_task
    if _download_task is task:
        _download_task = None
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"AngelHeart: 字体后台下载异常: {exc}")


def _start_download() -> None:
    """确保同一时刻只有一个下载任务在跑，冷却期内连任务都不建。"""
    global _download_task

    if _download_task is not None and not _download_task.done():
        return
    if _last_failure and time.monotonic() - _last_failure < RETRY_COOLDOWN:
        return
    try:
        _download_task = asyncio.create_task(_download_all())
    except RuntimeError:
        # 没有运行中的事件循环，这次触发直接放弃
        _download_task = None
        return
    _download_task.add_done_callback(_on_download_done)


async def ensure_fonts() -> bool:
    """字体就绪返回 True；不就绪则触发一次后台下载，本次直接返回 False。

    下载绝不占用发送路径：这一条长回复按原文发出，字体在后台补齐，下一条
    长回复才可能有图。宁可晚一点转，也不要让用户等着一条消息发不出去。
    """
    if fonts_ready():
        return True
    _start_download()
    return False


async def shutdown_fonts() -> None:
    """插件卸载/停用时取消后台下载，避免往已删除的插件目录里继续写文件。"""
    global _download_task
    task = _download_task
    _download_task = None
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# ========== WebUI 字体管理 ==========


def reset_ready_cache() -> None:
    """就绪缓存是按「字体只增不减」做的，上传/替换字体后必须手动失效。"""
    global _ready
    _ready = False


def _probe_glyphs(path: Path) -> bool:
    """真把几个常用汉字画出来，确认字体里确实有中文字形。

    不能用「画出了东西就算有字形」：缺字时 Pillow 画的是 ``.notdef`` 方框，
    方框本身也有 bbox，Arial 这类纯英文字体会被判成合格，最后整篇中文全是豆腐块。
    这里比较几个不同汉字的位图——全等就说明它们被画成了同一个方框。
    """
    try:
        font = ImageFont.truetype(str(path), 32)
    except Exception:
        return False

    signatures = set()
    for char in "永国字体":
        try:
            mask = font.getmask(char)
        except Exception:
            return False
        if mask.getbbox() is None:
            return False
        signatures.add((mask.size, bytes(mask)))
    # 四个不同汉字正常应画出四种不同形状；只剩一种即整篇是同一个方框
    return len(signatures) >= 3


def status_snapshot() -> dict:
    """给 WebUI 的当前状态：两套字重、是否就绪、是否在下载、冷却剩余。"""
    weights = []
    for key, name in WEIGHT_FILES.items():
        path = FONT_DIR / name
        size = path.stat().st_size if path.is_file() else 0
        weights.append(
            {"key": key, "file": name, "ready": _usable(path), "size": size}
        )

    cooldown = 0.0
    if _last_failure:
        remaining = RETRY_COOLDOWN - (time.monotonic() - _last_failure)
        cooldown = max(0.0, remaining)

    task = _download_task
    return {
        "dir": str(FONT_DIR),
        "ready": fonts_ready(),
        "downloading": task is not None and not task.done(),
        "cooldown_remaining": round(cooldown, 1),
        "weights": weights,
    }


def _clear_render_cache() -> None:
    """字体文件变了，渲染器里的字形缓存必须一起失效。

    延迟导入：渲染器依赖 PIL 与大量排版常量，font_manager 在插件启动早期就会被
    导入，没必要把这份开销带进启动路径。
    """
    try:
        from .long_image_render import clear_font_cache
    except Exception:
        return
    clear_font_cache()


def start_download_now() -> bool:
    """WebUI 手动触发的下载。冷却期内返回 False，由调用方给出提示。"""
    if _last_failure and time.monotonic() - _last_failure < RETRY_COOLDOWN:
        return False
    task = _download_task
    if task is not None and not task.done():
        return True
    _start_download()
    return _download_task is not None


async def install_font(key: str, upload) -> dict:
    """接收 WebUI 上传的字体，落到正式路径。

    ``upload`` 只需提供异步 ``read(size)``。落盘到唯一临时名，体积与字形双重
    校验，任一条不过就删掉临时文件、抛 ValueError，正式路径一个字都不动。

    不依赖框架的 save_upload_stream：那个工具在源码里明确标注不属于插件可用
    API，随时可能改动，这里自己分块写就行。
    """
    name = WEIGHT_FILES.get(key)
    if name is None:
        raise ValueError(f"未知字重: {key}")

    FONT_DIR.mkdir(parents=True, exist_ok=True)
    target = FONT_DIR / name
    tmp = target.with_name(f"{name}.upload.{uuid.uuid4().hex}.tmp")
    written = 0
    try:
        async with aiofiles.open(tmp, "wb") as fh:
            while True:
                chunk = await upload.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise ValueError(
                        f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限"
                    )
                await fh.write(chunk)
        if written < _MIN_BYTES:
            raise ValueError(f"文件只有 {written} 字节，不像是完整字体")
        if not _probe_glyphs(tmp):
            raise ValueError("这个文件加载不出中文字形，可能不是中文字体或已损坏")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)

    global _last_failure
    _last_failure = 0.0
    reset_ready_cache()
    _clear_render_cache()
    return {"key": key, "size": target.stat().st_size, "ready": fonts_ready()}
