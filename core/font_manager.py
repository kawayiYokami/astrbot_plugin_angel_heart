"""长文转图字体的按需获取与选择。

插件不随包分发字体。字体在「真正要转图」时才需要：文件在、且能被 Pillow 真正
加载出中文字形才算就绪。WebUI 可以在三款内置霞鹜字体之间切换，也可以上传一份
自己的字体；选中项落盘到 ``fonts/selected.json``，切换后立刻开始下载。

:func:`ensure_fonts` 不等待下载：字体没就绪就顺手触发一次后台补齐并立刻返回
False，这条长回复按原文发出，下一条才可能有图。任何一步失败都不会拿容器里的
系统字体顶上——那会让整张图字形走样，正是「字体看着不对」的来路。

失败结果会记一小段时间，避免每条长回复都重新拉一次网络。
"""

from __future__ import annotations

import asyncio
import json
import os
import stat as stat_module
import time
import uuid
from pathlib import Path

import aiofiles
import aiohttp
from PIL import ImageFont

from astrbot.api import logger

_FONT_DIR: Path | None = None


def _framework_data_dir() -> Path | None:
    """向框架要插件数据目录。拿不到（框架未初始化、单测桩）返回 None。

    这里只放行「框架这条路走不通」的两种异常。其余异常要放出去：退回插件目录
    是本模块明确反对的做法（见 _resolve_font_dir），不能因为框架 API 改了签名
    就静默地去干那件被禁止的事。
    """
    try:
        from astrbot.core.star.star_tools import StarTools

        return Path(StarTools.get_data_dir("astrbot_plugin_angel_heart"))
    except (ImportError, AttributeError) as exc:
        logger.debug(f"AngelHeart: 取插件数据目录失败: {exc}")
        return None


def _resolve_font_dir() -> Path:
    """字体目录的正式落点：AstrBot 的插件数据目录下的 fonts/。

    绝不能放插件自己的目录里——插件更新是整目录替换，下好的字体和选中项会跟着
    一起没。插件其他数据（模板、决策、媒体缓存）本来就都在 plugin_data 下。
    框架不可用（单测直接 import、脚本里跑）时退回插件目录，只为让代码还能跑。
    """
    base = _framework_data_dir()
    if base is None:
        fallback = Path(__file__).resolve().parent.parent / "fonts"
        logger.warning(
            f"AngelHeart: 取不到插件数据目录，字体暂时存放在插件目录 {fallback}，"
            "插件更新后会被清掉"
        )
        return fallback
    return base / "fonts"


def font_dir() -> Path:
    """字体目录。首次使用时才解析，避免插件启动早期就依赖框架数据目录。"""
    global _FONT_DIR
    if _FONT_DIR is None:
        _FONT_DIR = _resolve_font_dir()
    return _FONT_DIR


def set_font_dir(path: Path) -> None:
    """把字体目录钉死在给定路径上。测试换目录用，生产代码不要调。"""
    global _FONT_DIR
    _FONT_DIR = Path(path)


def _ensure_font_dir() -> Path:
    """建好字体目录并返回它。

    必须由调用方包在自己的 try 里：目录只读挂载时这里会抛 OSError，
    落在下载流程的 try 之外就会逃逸、不记冷却。
    """
    target = font_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target

# 内置三款霞鹜字体：黑 / 宋 / 楷各一，直链与体积均已实测。
# expected_size 是登记的上线体积，只在下载完成后对账用；直链是 latest，
# 上游换版时体积会变，所以只告警不拒绝。
# 字重都是单字重，粗体由渲染器横向错位加厚；文楷取全量版，Lite 在 1200 汉字
# 抽样里缺 346 个，遇到这些字会整字留白。
# 文件名只从这张表取，绝不接受外部传入的名字参与路径拼接。
FONTS: dict[str, dict] = {
    "neoxihei": {
        "label": "霞鹜新晰黑",
        "script": "黑体",
        "file": "LXGWNeoXiHei.ttf",
        "url": "https://github.com/lxgw/LxgwNeoXiHei/releases/latest/download/LXGWNeoXiHei.ttf",
        "expected_size": 7473598,
    },
    "neozhisong": {
        "label": "霞鹜新致宋",
        "script": "宋体",
        "file": "LXGWNeoZhiSong.ttf",
        "url": "https://github.com/lxgw/LxgwNeoZhiSong/releases/latest/download/LXGWNeoZhiSong.ttf",
        "expected_size": 10481222,
    },
    "wenkai": {
        "label": "霞鹜文楷",
        "script": "楷体",
        "file": "LXGWWenKai-Regular.ttf",
        "url": "https://github.com/lxgw/LxgwWenKai/releases/latest/download/LXGWWenKai-Regular.ttf",
        "expected_size": 25928940,
    },
    "custom": {
        "label": "自定义上传",
        "script": "",
        "file": "Custom.ttf",
        "url": None,
        "expected_size": 0,
    },
}

DEFAULT_FONT = "neoxihei"

SELECTION_FILE_NAME = "selected.json"

DOWNLOAD_TIMEOUT = 240      # 单次获取总超时（秒），26MB 走慢速链路也要给够
RETRY_COOLDOWN = 600        # 失败后多久之内不再重试（秒）
# 体积只做「明显不是字体」的粗筛，真正能不能用交给字形探测。
# 下载与上传必须用同一个下限：两套阈值不一致会造成「上传成功、却永远不就绪」，
# 把本来能转图的部署悄悄打回不转图。
_MIN_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 40 * 1024 * 1024  # WebUI 上传的体积上限

_download_task: asyncio.Task | None = None
_download_key: str | None = None
_last_failure_at = 0.0
_selected: str | None = None
# 字形探测要真解析十几 MB 字体（实测单款 40~170ms），WebUI 每 2 秒轮询一次状态，
# 每次都重探会周期性卡住事件循环。按 (路径, 体积, mtime) 缓存结论：文件一被替换，
# 键就变了，缓存自然失效，不会把旧字的结论套到新字上。
# 条目数受用户上传/换版的次数限制，每条只是一个 bool，不会长成问题。
_probe_cache: dict[tuple[str, int, int], bool] = {}


def _usable(path: Path) -> bool:
    """文件存在、体积像样、且真能画出中文字形。三者缺一都不算就绪。"""
    try:
        stat = path.stat()
    except OSError:
        return False
    # 只 stat 一次：字体目录可能挂在不快的卷上，状态接口被前端每两秒拉一次，
    # 一次多打一记 stat 就是白付的延迟。
    if not stat_module.S_ISREG(stat.st_mode) or stat.st_size < _MIN_BYTES:
        return False
    cache_key = (str(path), stat.st_size, stat.st_mtime_ns)
    hit = _probe_cache.get(cache_key)
    if hit is None:
        hit = _probe_glyphs(path)
        _probe_cache[cache_key] = hit
    return hit


def _selection_file() -> Path:
    """选中项文件路径。按当前 font_dir() 现算，测试替换目录后才不会写错地方。"""
    return font_dir() / SELECTION_FILE_NAME


def _read_selection() -> str:
    """从磁盘读选中项。脏值一律回落到默认，不把文件里的字符串拼进路径。"""
    try:
        data = json.loads(_selection_file().read_text("utf-8"))
        key = str(data.get("font") or "")
    except Exception:
        return DEFAULT_FONT
    return key if key in FONTS else DEFAULT_FONT


def selected_font() -> str:
    """当前选中的字体 key。首次访问才读盘，之后走内存。"""
    global _selected
    if _selected is None:
        _selected = _read_selection()
    return _selected


def current_font_path() -> Path:
    """当前选中字体的本地路径。文件不存在也照样返回路径，由调用方判就绪。"""
    return font_dir() / FONTS[selected_font()]["file"]


def _write_selection(key: str) -> None:
    """落盘选中项。

    插件数据目录可能是只读挂载：写不进去只丢持久化，进程内选择照常生效，
    不能因为落盘失败让用户点了没反应。
    """
    global _selected
    _selected = key
    selection_file = _selection_file()
    tmp = selection_file.with_name(f"selected.{uuid.uuid4().hex}.tmp")
    try:
        _ensure_font_dir()
        tmp.write_text(json.dumps({"font": key}, ensure_ascii=False), "utf-8")
        os.replace(tmp, selection_file)
    except OSError as exc:
        logger.debug(f"AngelHeart: 字体选择未能落盘，仅本次运行生效: {exc}")
    finally:
        tmp.unlink(missing_ok=True)


def selected_font_ready() -> bool:
    """当前选中的字体是否已就绪。只查本地，不触发任何网络动作。

    不做「验过一次就永远就绪」的短路：缓存按文件 (体积, mtime) 记住探测结论，
    文件被删或被替换时结论自动失效，不会出现「面板说就绪、文件其实没了」。
    """
    return _usable(current_font_path())


async def _fetch(session: aiohttp.ClientSession, name: str, url: str) -> None:
    """下载单个字体文件。

    先落 .part，字节数与加载能力都校验通过才原子改名。Pillow 只读 sfnt 头部和
    cmap，截断的文件照样能加载，所以长度对账不能省——否则半截字体进了正式路径，
    之后每次都被判为就绪，出图缺字形也没有任何告警。
    """
    target = font_dir() / name
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


async def _download_selected() -> bool:
    """真正干活的那一层：只补当前选中的那一款，失败记冷却。

    全程用开头快照下来的 key 判定成败，绝不回头再读「当前选中项」：
    下载途中用户切到别的款时，这里下完的是原来那款，拿新选中项判就绪会
    把「下完了」误判成「下完了但加载不了」，还给别的款记上一次假冷却。
    """
    global _last_failure_at

    if _last_failure_at and time.monotonic() - _last_failure_at < RETRY_COOLDOWN:
        return False

    key = selected_font()
    spec = FONTS[key]
    name, url = spec["file"], spec["url"]
    if not url:
        # 自定义款没有远端来源，只能由用户上传，不算下载失败。
        return False

    try:
        # 建目录必须在 try 内：插件数据目录只读挂载、权限不足时这里会抛 OSError，
        # 落在 try 之外就会逃逸出去、不记冷却，变成每条长回复都建一个立刻失败的任务。
        _ensure_font_dir()
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if not _usable(font_dir() / name):
                await _fetch(session, name, url)
    except Exception as exc:
        _last_failure_at = time.monotonic()
        logger.warning(f"AngelHeart: {spec['label']}获取失败，下次长文再试: {exc}")
        return False

    # 只判这一款自己的文件，不碰当前选中项
    if not _usable(font_dir() / name):
        _last_failure_at = time.monotonic()
        logger.warning(f"AngelHeart: {spec['label']}文件已落地但无法加载，下次长文再试")
        return False

    _last_failure_at = 0.0
    _clear_render_cache()
    _warn_size_drift(spec)
    logger.info(f"AngelHeart: {spec['label']}已就绪，下一次长文即可转图")
    return True


def _warn_size_drift(spec: dict) -> None:
    """对账上线体积。直链是 latest，上游换版就会对不上，只告警不拒绝。"""
    expected = spec.get("expected_size") or 0
    try:
        actual = (font_dir() / spec["file"]).stat().st_size
    except OSError:
        return
    if expected and actual != expected:
        logger.warning(
            f"AngelHeart: {spec['label']}体积与登记值不符 {actual} != {expected}，"
            "上游可能已换版"
        )


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
    """确保同一时刻只有一个下载任务在跑，且它下的一定是当前选中款。"""
    global _download_task, _download_key

    key = selected_font()
    task = _download_task
    if task is not None and not task.done():
        if _download_key == key:
            return
        # 用户已经切到别的款：旧任务下的不是他要的，取消掉重开，
        # 否则新款永远排不上队，而旧款下完还会被判成失败。
        task.cancel()
        _download_task = None
    if _last_failure_at and time.monotonic() - _last_failure_at < RETRY_COOLDOWN:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的事件循环，这次触发直接放弃（不要先建协程，避免未 await 警告）
        return
    _download_key = key
    _download_task = loop.create_task(_download_selected())
    _download_task.add_done_callback(_on_download_done)


async def ensure_fonts() -> bool:
    """选中字体就绪返回 True；不就绪则触发一次后台下载，本次直接返回 False。

    下载绝不占用发送路径：这一条长回复按原文发出，字体在后台补齐，下一条
    长回复才可能有图。宁可晚一点转，也不要让用户等着一条消息发不出去。
    """
    if selected_font_ready():
        return True
    _start_download()
    return False


async def shutdown_fonts() -> None:
    """插件卸载/停用时取消后台下载，避免往已删除的插件数据目录里继续写文件。"""
    global _download_task
    task = _download_task
    _download_task = None
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# ========== WebUI 字体管理 ==========


def reset_probe_cache() -> None:
    """清掉字形探测结论。文件被替换时 mtime 已经能自动失效，这个口子留给
    「同一 mtime 但内容被改写」这类极端情况与测试使用。"""
    _probe_cache.clear()


def reset_font_state() -> None:
    """测试与重载用：把选中项与探测缓存一起打回未读盘状态。"""
    global _selected
    _selected = None
    _probe_cache.clear()


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
    # 四个汉字正常至少画出三种不同形状；少于三种就说明缺字被画成了同一个方框
    return len(signatures) >= 3


def status_snapshot() -> dict:
    """给 WebUI 的当前状态：可选字体清单、选中项、是否就绪、是否在下载、冷却剩余。"""
    key = selected_font()
    fonts = []
    for font_key, spec in FONTS.items():
        path = font_dir() / spec["file"]
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        fonts.append(
            {
                "key": font_key,
                "label": spec["label"],
                "script": spec["script"],
                "file": spec["file"],
                "size": size,
                "ready": _usable(path) if size else False,
                "selected": font_key == key,
                "custom": spec["url"] is None,
            }
        )

    cooldown = 0.0
    if _last_failure_at:
        remaining = RETRY_COOLDOWN - (time.monotonic() - _last_failure_at)
        cooldown = max(0.0, remaining)

    task = _download_task
    return {
        "dir": str(font_dir()),
        "selected": key,
        "ready": selected_font_ready(),
        "downloading": task is not None and not task.done(),
        "cooldown_remaining": round(cooldown, 1),
        "fonts": fonts,
    }


def _clear_render_cache() -> None:
    """字体文件或选中项变了，渲染器里的字形缓存必须一起失效。

    延迟导入：渲染器依赖 PIL 与大量排版常量，font_manager 在插件启动早期就会被
    导入，没必要把这份开销带进启动路径。
    """
    try:
        from .long_image_render import clear_font_cache
    except ImportError:
        # 唯一该放行的情况：渲染器还没被导入过，没有缓存要清
        return
    clear_font_cache()


def start_download_now() -> bool:
    """WebUI 手动触发的下载。冷却期内、或当前款没有远端来源时返回 False。"""
    if not FONTS[selected_font()]["url"]:
        # 自定义款只能上传，别建一个必然立刻失败的任务再报「已开始下载」
        return False
    if _last_failure_at and time.monotonic() - _last_failure_at < RETRY_COOLDOWN:
        return False
    task = _download_task
    if task is not None and not task.done() and _download_key == selected_font():
        return True
    _start_download()
    return _download_task is not None and not _download_task.done()


def select_font(key: str) -> dict:
    """切换字体：落盘选中项、失效缓存，未就绪且可下载就立刻开始下载。

    用户主动点选不该被上一次失败的冷却拦住，所以先清冷却再触发。
    """
    global _last_failure_at

    if key not in FONTS:
        raise ValueError("没有这款字体")

    # 探测缓存键含路径，切款本身就会命中不到旧结论，不必手动清
    _write_selection(key)
    _clear_render_cache()
    _last_failure_at = 0.0

    started = False
    spec = FONTS[key]
    if spec["url"] and not _usable(font_dir() / spec["file"]):
        started = start_download_now()

    snapshot = status_snapshot()
    snapshot["started"] = started
    return snapshot


async def install_font(key: str, upload) -> dict:
    """接收 WebUI 上传的字体，落到该款字体的正式路径，并切换为选中项。

    ``upload`` 只需提供异步 ``read(size)``。落盘到唯一临时名，体积与字形双重
    校验，任一条不过就删掉临时文件、抛 ValueError，正式路径一个字都不动。

    不依赖框架的 save_upload_stream：那个工具在源码里明确标注不属于插件可用
    API，随时可能改动，这里自己分块写就行。
    """
    spec = FONTS.get(key)
    if spec is None:
        raise ValueError("未知字体")
    if spec["url"] is not None:
        # 内置款只走自动下载，不让上传覆盖：否则用户传错一次，自动下载再也补不回来
        raise ValueError("内置字体请用下载，自定义请上传到自定义款")

    name = spec["file"]
    _ensure_font_dir()
    target = font_dir() / name
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

    global _last_failure_at
    _last_failure_at = 0.0
    # 探测缓存键含 mtime，换了文件自动命中不到旧结论，不必手动清
    _write_selection(key)
    _clear_render_cache()
    return {"key": key, "size": target.stat().st_size, "ready": selected_font_ready()}
