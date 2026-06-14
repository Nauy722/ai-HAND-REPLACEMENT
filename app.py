import os
import uuid
import json
import logging
from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageEnhance, ImageDraw, ImageFont, ImageColor
import requests
from io import BytesIO

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 配置静态文件夹用于存储和提供处理后的图片
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'processed')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ----------------- 核心图像处理工具类 -----------------
class ImageProcessor:
    @staticmethod
    def download_image(source):
        """支持下载 URL 图片或解析 Base64"""
        if not source:
            raise ValueError("图片源不能为空")
            
        # 1. 如果是 URL 链接
        if source.startswith('http://') or source.startswith('https://'):
            logger.info(f"正在从网络下载图片: {source[:60]}...")
            response = requests.get(source, timeout=15)
            response.raise_for_status()
            return Image.open(BytesIO(response.content))
            
        # 2. 如果是 Base64 格式
        elif source.startswith('data:image') or ';base64,' in source:
            logger.info("正在解析 Base64 格式图片...")
            import base64
            header, encoded = source.split(',', 1)
            image_data = base64.b64decode(encoded)
            return Image.open(BytesIO(image_data))
            
        else:
            raise ValueError("不支持的图片源格式 (必须是 http/https 链接或 Base64)")

    @staticmethod
    def apply_crop(img, params):
        """裁剪处理: 支持 auto_center_square, auto, [x, y, w, h]"""
        width, height = img.size
        box = params.get("box", "auto")
        aspect_ratio = params.get("aspect_ratio", "free")
        
        # 1. 智能居中正方形裁剪
        if box == "auto_center_square" or aspect_ratio == "1:1":
            min_edge = min(width, height)
            left = (width - min_edge) // 2
            top = (height - min_edge) // 2
            right = left + min_edge
            bottom = top + min_edge
            logger.info(f"执行中心正方形裁剪: {(left, top, right, bottom)}")
            return img.crop((left, top, right, bottom))
            
        # 2. 指定区域裁剪 (防御性解析，防止非整型崩溃)
        elif isinstance(box, list) and len(box) == 4:
            try:
                coords = [int(float(x)) for x in box]
                # 边界约束，防止越界报错
                coords[0] = max(0, min(coords[0], width))
                coords[1] = max(0, min(coords[1], height))
                coords[2] = max(coords[0] + 1, min(coords[2], width))
                coords[3] = max(coords[1] + 1, min(coords[3], height))
                logger.info(f"执行指定区域裁剪: {coords}")
                return img.crop(tuple(coords))
            except Exception as e:
                logger.warning(f"解析裁剪坐标失败, 跳过裁剪: {e}")
                
        # 3. 默认不裁剪或自动处理
        return img

    @staticmethod
    def apply_tone(img, params):
        """色调调整: 亮度、对比度、冷暖滤镜"""
        # 1. 调整亮度
        brightness = params.get("brightness", 1.0)
        if brightness != 1.0:
            try:
                enhancer = ImageEnhance.Brightness(img)
                img = enhancer.enhance(float(brightness))
                logger.info(f"调整亮度: {brightness}")
            except Exception as e:
                logger.error(f"亮度调整失败: {e}")

        # 2. 调整对比度
        contrast = params.get("contrast", 1.0)
        if contrast != 1.0:
            try:
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(float(contrast))
                logger.info(f"调整对比度: {contrast}")
            except Exception as e:
                logger.error(f"对比度调整失败: {e}")

        # 3. 冷暖滤镜处理
        filter_type = params.get("filter", "none")
        if filter_type != "none":
            # 转换为 RGB 进行色彩通道微调
            if img.mode != 'RGB':
                img = img.convert('RGB')
            r, g, b = img.split()
            
            if filter_type == "auto_warm":
                # 暖色调：稍微增加红、绿通道的值
                r = r.point(lambda i: min(255, int(i * 1.1)))
                g = g.point(lambda i: min(255, int(i * 1.05)))
                logger.info("应用暖色调滤镜")
            elif filter_type == "auto_cool":
                # 冷色调：稍微增加蓝、绿通道的值
                b = b.point(lambda i: min(255, int(i * 1.1)))
                g = g.point(lambda i: min(255, int(i * 1.05)))
                logger.info("应用冷色调滤镜")
                
            img = Image.merge('RGB', (r, g, b))
            
        return img

    @staticmethod
    def apply_watermark(img, params):
        """批量添加文本水印"""
        text = params.get("text", "")
        if not text:
            return img
            
        position = params.get("position", "bottom_right")
        opacity = params.get("opacity", 0.5)
        
        # 转换至 RGBA 模式以支持透明度
        txt_layer = Image.new('RGBA', img.size, (255, 255, 255, 0))
        
        # 尝试加载默认字体，如果失败则使用系统基础字体
        try:
            # 选用较大的字体
            font_size = max(15, min(img.size) // 25)
            font = ImageFont.load_default() # 在Linux环境下基础字体
        except Exception:
            font = ImageFont.load_default()
            
        draw = ImageDraw.Draw(txt_layer)
        width, height = img.size
        
        # 简易文本长宽估算
        text_w = len(text) * 12
        text_h = 20
        
        # 计算水印坐标
        if position == "bottom_right":
            x, y = width - text_w - 20, height - text_h - 20
        elif position == "center":
            x, y = (width - text_w) // 2, (height - text_h) // 2
        elif position == "top_left":
            x, y = 20, 20
        else:
            x, y = width - text_w - 20, height - text_h - 20
            
        # 绘制带透明度的半透明白色文字水印
        fill_color = (255, 255, 255, int(float(opacity) * 255))
        draw.text((x, y), text, fill=fill_color, font=font)
        
        # 复合层
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
            
        combined = Image.alpha_composite(img, txt_layer)
        return combined.convert('RGB') if img.mode != 'RGBA' else combined


# ----------------- API 路由 -----------------

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "message": "Visual Work Automation Agent is Running!"})

@app.route('/api/execute', methods=['POST'])
def execute_task():
    """
    统一的图像自动化处理执行入口 (代替原来的 /api/merge)
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "请求体不能为空 (JSON)"}), 400
            
        image_sources = data.get("images", [])
        params_json_str = data.get("params", "[]")
        
        # 兼容性设计：如果 params 直接是 List/Dict 而不是字符串
        if isinstance(params_json_str, str):
            operations = json.loads(params_json_str)
        else:
            operations = params_json_str
            
        if not image_sources:
            return jsonify({"error": "待处理的 'images' 列表不能为空"}), 400
            
        logger.info(f"接收到批量任务，待处理图片数: {len(image_sources)}, 操作列表: {operations}")
        
        processed_urls = []
        host_url = request.host_url  # 自动获取当前 Railway 部署的外部公网根域名
        
        # 循环批处理每一张图片
        for idx, src in enumerate(image_sources):
            try:
                # 1. 下载或解析
                img = ImageProcessor.download_image(src)
                original_format = img.format if img.format else "JPEG"
                
                target_format = original_format
                compress_quality = 85
                max_size_kb = None
                
                # 2. 依次应用大模型提取的操作序列
                for op in operations:
                    op_type = op.get("type")
                    op_params = op.get("params", {})
                    
                    if op_type == "crop":
                        img = ImageProcessor.apply_crop(img, op_params)
                    elif op_type == "tone":
                        img = ImageProcessor.apply_tone(img, op_params)
                    elif op_type == "watermark":
                        img = ImageProcessor.apply_watermark(img, op_params)
                    elif op_type == "convert":
                        target_format = op_params.get("target_format", original_format).upper()
                        if target_format == "JPG":
                            target_format = "JPEG"
                    elif op_type == "compress":
                        compress_quality = int(op_params.get("quality", 85))
                        max_size_kb = op_params.get("max_size_kb")
                        
                # 3. 统一规范图像色彩模式 (防止 PNG 透明通道保存为 JPG 时报错)
                if target_format == "JPEG" and img.mode in ('RGBA', 'LA'):
                    img = img.convert('RGB')
                    
                # 4. 生成唯一文件名，保存到 Railway 静态托管目录
                ext = "jpg" if target_format == "JPEG" else target_format.lower()
                filename = f"processed_{uuid.uuid4().hex}.{ext}"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                
                # 保存图片 (同时应用质量压缩)
                img.save(filepath, format=target_format, quality=compress_quality)
                
                # 5. 可选：如果设置了 max_size_kb，则进行动态二分法质量压缩
                if max_size_kb:
                    try:
                        max_size_bytes = int(max_size_kb) * 1024
                        current_size = os.path.getsize(filepath)
                        temp_quality = compress_quality
                        
                        # 如果文件超标，逐步下调质量直至达标
                        while current_size > max_size_bytes and temp_quality > 10:
                            temp_quality -= 10
                            img.save(filepath, format=target_format, quality=temp_quality)
                            current_size = os.path.getsize(filepath)
                            
                        logger.info(f"像素压缩完成，最终大小: {current_size/1024:.1f} KB, 质量因子: {temp_quality}")
                    except Exception as ce:
                        logger.error(f"执行大小压缩目标失败: {ce}")
                
                # 6. 生成公网绝对访问 URL
                # 例如: https://your-railway.up.railway.app/static/processed/processed_xxx.jpg
                public_url = f"{host_url.rstrip('/')}/static/processed/{filename}"
                processed_urls.append(public_url)
                logger.info(f"第 {idx+1} 张图片处理成功并保存为: {public_url}")
                
            except Exception as item_error:
                logger.error(f"处理第 {idx+1} 张图片时发生错误: {item_error}")
                # 异常容错处理：单张失败不卡死整个批处理流
                continue
                
        return jsonify({
            "status": "success",
            "processed_images": processed_urls,
            "count": len(processed_urls)
        })
        
    except Exception as e:
        logger.error(f"执行 API 时发生严重异常: {e}")
        return jsonify({"error": f"服务器内部错误: {str(e)}"}), 500

# 兼容路由：防止扣子工作流中旧的“拼图 (merge)”节点未完全改名导致异常
@app.route('/api/merge', methods=['POST'])
def legacy_merge():
    logger.info("捕获到历史 /api/merge 节点调用，自动路由至通用执行器")
    return execute_task()

if __name__ == '__main__':
    # 绑定 0.0.0.0 和 Railway 提供的 PORT 环境变量
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

