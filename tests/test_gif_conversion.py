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
sys.modules['PIL'] = MagicMock()
sys.modules['PIL.Image'] = MagicMock()
sys.modules['astrbot'] = MagicMock()
sys.modules['astrbot.api'] = MagicMock()

# 创建模拟的logger
mock_logger = MagicMock()
sys.modules['astrbot.api'].logger = mock_logger

from core.image_processor import ImageProcessor


class TestGifConversion(unittest.TestCase):
    """测试GIF转换为JPEG功能"""

    def setUp(self):
        """设置测试环境"""
        with patch('aiohttp.ClientTimeout') as mock_timeout:
            mock_timeout.return_value = MagicMock(total=10)
            self.processor = ImageProcessor(timeout=10)
        
        self.test_gif_url = "https://example.com/test.gif"
        self.test_jpg_url = "https://example.com/test.jpg"
        self.test_image_data = b"fake_image_data"
        self.test_jpeg_data = b"fake_jpeg_data"

    def test_gif_to_jpeg_conversion(self):
        """测试GIF转换为JPEG格式"""
        async def test():
            # 模拟PIL Image
            mock_img = MagicMock()
            mock_img.mode = 'RGB'  # 模拟已经是RGB模式
            mock_output = MagicMock()
            mock_output.getvalue = MagicMock(return_value=self.test_jpeg_data)
            mock_img.save = MagicMock()
            
            # 模拟Image.open和BytesIO
            with patch('PIL.Image.open', return_value=mock_img), \
                 patch('io.BytesIO', return_value=mock_output), \
                 patch('aiohttp.ClientSession') as mock_session_class:
                
                # 设置模拟对象
                mock_session = MagicMock()
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.read = AsyncMock(return_value=self.test_image_data)
                
                mock_session_class.return_value.__aenter__.return_value = mock_session
                mock_session.get.return_value.__aenter__.return_value = mock_response
                
                # 执行测试
                result = await self.processor.convert_url_to_data_url(self.test_gif_url)
                
                # 验证结果
                expected_base64 = base64.b64encode(self.test_jpeg_data).decode('utf-8')
                expected_url = f"data:image/jpeg;base64,{expected_base64}"
                
                print(f"GIF转换结果: {result[:50]}...")
                self.assertEqual(result, expected_url)
                self.assertIn("image/jpeg", result)  # 确保是JPEG格式
                
                # 验证PIL调用
                mock_img.save.assert_called_once()
                print("✓ GIF转换为JPEG测试通过")
        
        # 运行异步测试
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(test())
        finally:
            loop.close()

    def test_jpg_passthrough(self):
        """测试JPG图片的处理"""
        async def test():
            # 模拟PIL Image
            mock_img = MagicMock()
            mock_img.mode = 'RGB'
            mock_output = MagicMock()
            mock_output.getvalue = MagicMock(return_value=self.test_jpeg_data)
            mock_img.save = MagicMock()
            
            with patch('PIL.Image.open', return_value=mock_img), \
                 patch('io.BytesIO', return_value=mock_output), \
                 patch('aiohttp.ClientSession') as mock_session_class:
                
                # 设置模拟对象
                mock_session = MagicMock()
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.read = AsyncMock(return_value=self.test_image_data)
                
                mock_session_class.return_value.__aenter__.return_value = mock_session
                mock_session.get.return_value.__aenter__.return_value = mock_response
                
                # 执行测试
                result = await self.processor.convert_url_to_data_url(self.test_jpg_url)
                
                # 验证结果
                expected_base64 = base64.b64encode(self.test_jpeg_data).decode('utf-8')
                expected_url = f"data:image/jpeg;base64,{expected_base64}"
                
                self.assertEqual(result, expected_url)
                self.assertIn("image/jpeg", result)  # 确保是JPEG格式
                print("✓ JPG处理测试通过")
        
        # 运行异步测试
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(test())
        finally:
            loop.close()

    def test_rgba_conversion(self):
        """测试RGBA格式图片转换为RGB"""
        async def test():
            # 模拟PIL Image (RGBA模式)
            mock_img = MagicMock()
            mock_img.mode = 'RGBA'  # 模拟RGBA模式
            mock_img.convert = MagicMock(return_value=mock_img)  # 模拟convert调用
            mock_output = MagicMock()
            mock_output.getvalue = MagicMock(return_value=self.test_jpeg_data)
            mock_img.save = MagicMock()
            
            with patch('PIL.Image.open', return_value=mock_img), \
                 patch('io.BytesIO', return_value=mock_output), \
                 patch('aiohttp.ClientSession') as mock_session_class:
                
                # 设置模拟对象
                mock_session = MagicMock()
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.read = AsyncMock(return_value=self.test_image_data)
                
                mock_session_class.return_value.__aenter__.return_value = mock_session
                mock_session.get.return_value.__aenter__.return_value = mock_response
                
                # 执行测试
                result = await self.processor.convert_url_to_data_url(self.test_gif_url)
                
                # 验证结果
                expected_base64 = base64.b64encode(self.test_jpeg_data).decode('utf-8')
                expected_url = f"data:image/jpeg;base64,{expected_base64}"
                
                self.assertEqual(result, expected_url)
                
                # 验证convert被调用
                mock_img.convert.assert_called_once_with('RGB')
                print("✓ RGBA转换为RGB测试通过")
        
        # 运行异步测试
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(test())
        finally:
            loop.close()


if __name__ == '__main__':
    print("开始运行GIF转换测试...")
    unittest.main(verbosity=2)