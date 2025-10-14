import aiohttp
import base64
import io
from PIL import Image
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

class ImageProcessor:
    """图片处理器 - 负责异步下载和转换图片为JPEG格式"""

    def __init__(self, timeout: int = 30):
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def convert_url_to_data_url(self, url: str) -> str:
        """异步将图片URL转换为JPEG格式的Base64 Data URL

        参考上游AstrBot实现，统一转换为JPEG格式以避免MIME类型兼容性问题
        """
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        # 读取原始图片数据
                        image_data = await resp.read()

                        # 使用PIL加载图片并转换为JPEG
                        try:
                            # 加载图片
                            img = Image.open(io.BytesIO(image_data))

                            # 转换为RGB模式（处理RGBA、灰度等格式）
                            if img.mode in ('RGBA', 'LA', 'P'):
                                img = img.convert('RGB')

                            # 保存为JPEG到内存
                            output = io.BytesIO()
                            img.save(output, format='JPEG', quality=85)
                            jpeg_data = output.getvalue()

                            # 转换为Base64
                            base64_encoded = base64.b64encode(jpeg_data).decode('utf-8')

                            # 统一使用JPEG MIME类型
                            data_url = f"data:image/jpeg;base64,{base64_encoded}"

                            logger.debug(f"图片转换成功: {url} -> JPEG, 大小: {len(jpeg_data)} bytes")
                            return data_url

                        except Exception as e:
                            logger.warning(f"PIL图片处理失败: {e}, URL: {url}")
                            # PIL处理失败时，尝试直接编码原始数据
                            base64_encoded = base64.b64encode(image_data).decode('utf-8')
                            return f"data:image/jpeg;base64,{base64_encoded}"
                    else:
                        logger.warning(f"图片下载失败，状态码: {resp.status}, URL: {url}")
                        return ""

        except Exception as e:
            logger.error(f"图片转换异常: {e}, URL: {url}")
            return ""