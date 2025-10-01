import aiohttp
import base64
from astrbot.api import logger
from typing import Optional

class ImageProcessor:
    """图片处理器 - 负责异步下载和转换图片"""
    
    def __init__(self, timeout: int = 30):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
    
    async def convert_url_to_data_url(self, url: str) -> str:
        """异步将图片URL转换为Base64 Data URL格式"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        # 动态获取MIME类型，失败时使用默认值
                        mime_type = resp.headers.get('Content-Type', 'image/jpeg')
                        
                        # 读取图片数据
                        image_data = await resp.read()
                        
                        # 转换为Base64
                        base64_encoded = base64.b64encode(image_data).decode('utf-8')
                        
                        # 构建Data URL
                        data_url = f"data:{mime_type};base64,{base64_encoded}"
                        
                        logger.debug(f"图片转换成功: {url} -> {mime_type}, 大小: {len(image_data)} bytes")
                        return data_url
                    else:
                        logger.warning(f"图片下载失败，状态码: {resp.status}, URL: {url}")
                        return url  # 转换失败返回原URL
                        
        except Exception as e:
            logger.error(f"图片转换异常: {e}, URL: {url}")
            return url  # 异常时返回原URL