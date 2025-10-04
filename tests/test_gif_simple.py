import unittest
import asyncio
import base64
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 模拟所有依赖模块
mock_pil_image = MagicMock()
sys.modules['PIL'] = MagicMock()
sys.modules['PIL.Image'] = mock_pil_image
sys.modules['astrbot'] = MagicMock()
sys.modules['astrbot.api'] = MagicMock()

# 创建模拟的logger
mock_logger = MagicMock()
sys.modules['astrbot.api'].logger = mock_logger

from core.image_processor import ImageProcessor


class TestGifSimple(unittest.TestCase):
    """简化的GIF转换测试"""

    def setUp(self):
        """设置测试环境"""
        with patch('aiohttp.ClientTimeout') as mock_timeout:
            mock_timeout.return_value = MagicMock(total=10)
            self.processor = ImageProcessor(timeout=10)
        
        self.test_url = "https://example.com/test.gif"
        self.test_image_data = b"fake_image_data"
        self.test_jpeg_data = b"fake_jpeg_data"

    def test_convert_url_to_data_url_returns_jpeg(self):
        """测试转换结果总是JPEG格式"""
        async def test():
            # 创建一个完全模拟的PIL Image
            mock_img = MagicMock()
            mock_img.mode = 'RGB'
            
            # 模拟BytesIO
            mock_output = MagicMock()
            mock_output.getvalue = MagicMock(return_value=self.test_jpeg_data)
            
            # 设置PIL.Image.open和io.BytesIO的模拟
            mock_pil_image.open.return_value = mock_img
            
            with patch('io.BytesIO', return_value=mock_output), \
                 patch('aiohttp.ClientSession') as mock_session_class:
                
                # 设置模拟对象
                mock_session = MagicMock()
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.read = AsyncMock(return_value=self.test_image_data)
                
                mock_session_class.return_value.__aenter__.return_value = mock_session
                mock_session.get.return_value.__aenter__.return_value = mock_response
                
                # 执行测试
                result = await self.processor.convert_url_to_data_url(self.test_url)
                
                # 验证结果
                print(f"转换结果: {result[:50]}...")
                
                # 关键验证：结果必须是JPEG格式
                self.assertIn("data:image/jpeg;base64,", result)
                self.assertNotIn("image/gif", result)
                
                print("✓ 转换结果为JPEG格式测试通过")
        
        # 运行异步测试
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(test())
        finally:
            loop.close()

    def test_convert_url_to_data_url_failure(self):
        """测试转换失败处理"""
        async def test():
            with patch('aiohttp.ClientSession') as mock_session_class:
                
                # 设置模拟对象
                mock_session = MagicMock()
                mock_response = MagicMock()
                mock_response.status = 404
                
                mock_session_class.return_value.__aenter__.return_value = mock_session
                mock_session.get.return_value.__aenter__.return_value = mock_response
                
                # 执行测试
                result = await self.processor.convert_url_to_data_url(self.test_url)
                
                # 验证结果
                self.assertEqual(result, "")
                print("✓ 失败处理测试通过")
        
        # 运行异步测试
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(test())
        finally:
            loop.close()

    def test_pil_failure_fallback(self):
        """测试PIL处理失败时的回退机制"""
        async def test():
            # 模拟PIL抛出异常
            mock_pil_image.open.side_effect = Exception("PIL error")
            
            with patch('aiohttp.ClientSession') as mock_session_class:
                
                # 设置模拟对象
                mock_session = MagicMock()
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.read = AsyncMock(return_value=self.test_image_data)
                
                mock_session_class.return_value.__aenter__.return_value = mock_session
                mock_session.get.return_value.__aenter__.return_value = mock_response
                
                # 执行测试
                result = await self.processor.convert_url_to_data_url(self.test_url)
                
                # 验证结果：即使PIL失败，也应该返回JPEG格式
                self.assertIn("data:image/jpeg;base64,", result)
                print("✓ PIL失败回退测试通过")
        
        # 运行异步测试
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(test())
        finally:
            loop.close()


if __name__ == '__main__':
    print("开始运行简化GIF转换测试...")
    unittest.main(verbosity=2)