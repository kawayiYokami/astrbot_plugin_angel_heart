"""
ImageCache - 插件自有图片缓存

将上游传入的图片统一转为 WebP quality=75 最长边 ≤1024，
存入插件数据目录下的 media_cache/{chat_id}/{dhash}.webp，
不依赖上游临时文件生命周期。

清理时机由 ConversationLedger 在消息截断/压缩时联动调用。
"""

import io
import hashlib
import re
from pathlib import Path
from typing import Optional

from PIL import Image

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class ImageCache:
    """插件自有媒体缓存管理器。"""

    WEBP_QUALITY = 75
    MAX_SIDE = 1024
    MAX_TEXT_FILE_BYTES = 100 * 1024
    SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

    def __init__(self, root: Path):
        self._root = root / "media_cache"

    # ── 路径 ──────────────────────────────────────

    @classmethod
    def _safe_segment(cls, value: str, fallback: str = "item") -> str:
        raw = str(value or "").strip()
        safe = cls.SAFE_NAME_RE.sub("_", raw).strip("._")
        if not safe:
            safe = fallback
        if safe != raw or safe in {"", ".", ".."}:
            digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]
            safe = f"{safe[:70]}_{digest}"
        return safe[:120]

    def _chat_dir(self, chat_id: str) -> Path:
        p = self._root / self._safe_segment(chat_id, "chat")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _file_path(self, chat_id: str, dhash: str) -> Path:
        return self._chat_dir(chat_id) / f"{dhash}.webp"

    def _files_dir(self, chat_id: str) -> Path:
        p = self._chat_dir(chat_id) / "files"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ── 转换 ──────────────────────────────────────

    @staticmethod
    def _convert_to_webp(image_data: bytes, quality: int = WEBP_QUALITY, max_side: int = MAX_SIDE) -> Optional[bytes]:
        """将图片字节转为 WebP quality=75，最长边不超过 max_side。"""
        try:
            img = Image.open(io.BytesIO(image_data))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")

            # 缩小到最长边 ≤ max_side
            width, height = img.size
            longest = max(width, height)
            if longest > max_side:
                scale = max_side / float(longest)
                new_size = (
                    max(1, int(round(width * scale))),
                    max(1, int(round(height * scale))),
                )
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=quality, method=6)
            return buf.getvalue()
        except Exception as e:
            logger.warning(f"图片转 WebP 失败: {e}")
            return None

    @staticmethod
    def _compute_dhash(image_data: bytes) -> str:
        """计算图片 dHash，与 ConversationLedger._compute_dhash 保持一致的算法。"""
        try:
            img = Image.open(io.BytesIO(image_data))
            img = img.convert("L")
            img = img.resize((9, 8), Image.Resampling.LANCZOS)

            diff = []
            pixels = list(img.getdata())
            width, height = img.size
            for y in range(height):
                for x in range(width - 1):
                    idx = y * width + x
                    diff.append(pixels[idx] > pixels[idx + 1])

            decimal_value = 0
            for index, value in enumerate(diff):
                if value:
                    decimal_value += 1 << index

            return hex(decimal_value)[2:]
        except Exception as e:
            logger.warning(f"dHash 计算失败: {e}")
            return ""

    # ── 读写 ──────────────────────────────────────

    def get_cached_path(self, chat_id: str, dhash: str) -> Path:
        """返回缓存文件路径，不论文件是否存在。"""
        return self._file_path(chat_id, dhash)

    def get_cached(self, chat_id: str, dhash: str) -> Optional[bytes]:
        """读取已缓存的 WebP 字节，不存在返回 None。"""
        path = self._file_path(chat_id, dhash)
        if path.exists():
            try:
                return path.read_bytes()
            except Exception as e:
                logger.warning(f"读取缓存图片失败: {e}")
        return None

    def put(self, chat_id: str, image_data: bytes) -> Optional[str]:
        """将图片字节缓存为 WebP q75 max1024，返回 dhash（也是文件名）。"""
        webp_bytes = self._convert_to_webp(image_data)
        if webp_bytes is None:
            return None

        dhash = self._compute_dhash(image_data)
        if not dhash:
            return None

        path = self._file_path(chat_id, dhash)
        if path.exists():
            # 已缓存，无需重复写入
            return dhash

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(webp_bytes)
            return dhash
        except Exception as e:
            logger.warning(f"写入缓存图片失败: {e}")
            return None

    def put_text_file(
        self,
        chat_id: str,
        source_path: str | Path,
        display_name: str = "",
        max_bytes: int = MAX_TEXT_FILE_BYTES,
    ) -> Optional[Path]:
        """把小文本附件复制到插件缓存目录，返回稳定路径。"""
        try:
            src = Path(source_path)
            if not src.exists() or not src.is_file():
                return None
            if src.stat().st_size > max_bytes:
                return None

            data = src.read_bytes()
            safe_name = self._safe_segment(display_name or src.name, "file")
            digest = hashlib.sha256(data).hexdigest()[:16]
            target = self._files_dir(chat_id) / f"{digest}_{safe_name}"
            if not target.exists():
                target.write_bytes(data)
            return target
        except Exception as e:
            logger.warning(f"写入缓存文件失败: {e}")
            return None

    def is_managed_path(self, path: str | Path) -> bool:
        """判断路径是否位于插件媒体缓存目录内。"""
        try:
            root = self._root.resolve(strict=False)
            target = Path(path).resolve(strict=False)
            target.relative_to(root)
            return True
        except Exception:
            return False

    def remove_managed_path(self, path: str | Path) -> bool:
        """仅删除插件媒体缓存目录内的文件。"""
        try:
            target = Path(path)
            if not self.is_managed_path(target):
                return False
            if target.exists() and target.is_file():
                target.unlink()
                return True
        except Exception as e:
            logger.debug(f"删除缓存文件失败: {e}")
        return False

    def iter_managed_files(self, chat_id: str = "") -> list[Path]:
        """列出插件媒体缓存中的文件。"""
        target = self._chat_dir(chat_id) if chat_id else self._root
        if not target.exists():
            return []
        try:
            return [path for path in target.rglob("*") if path.is_file()]
        except Exception as e:
            logger.debug(f"列出缓存文件失败: {e}")
            return []

    # ── 清理 ──────────────────────────────────────

    def clean_chat(self, chat_id: str):
        """删除指定会话的全部缓存文件。"""
        d = self._chat_dir(chat_id)
        if d.exists():
            try:
                import shutil
                shutil.rmtree(d)
                logger.debug(f"ImageCache: 已清理会话 {chat_id} 的图片缓存")
            except Exception as e:
                logger.warning(f"清理会话 {chat_id} 缓存失败: {e}")

    def clean_all(self):
        """删除所有缓存文件。"""
        if self._root.exists():
            try:
                import shutil
                shutil.rmtree(self._root)
                self._root.mkdir(parents=True, exist_ok=True)
                logger.debug("ImageCache: 已清理所有图片缓存")
            except Exception as e:
                logger.warning(f"清理全部缓存失败: {e}")

    def get_cache_size(self, chat_id: str = "") -> int:
        """返回缓存目录占用字节数。可指定会话。"""
        target = self._chat_dir(chat_id) if chat_id else self._root
        if not target.exists():
            return 0
        total = 0
        try:
            for f in target.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
        except Exception:
            pass
        return total
