import os
import uuid
import json
import logging
from flask import Flask, request, jsonify, send_from_directory
from PIL import Image, ImageEnhance, ImageDraw, ImageFont
import requests
from io import BytesIO

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 配置静态文件夹用于存储和提供处理后的图片
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'processed')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 尝试加载中文字体（如果存在），否则使用默认字体
FONT_PATH = os.path.join(os.getcwd(), 'static', 'fonts', 'SimHei.ttf')  # 可自行上传字体文件
try:
    if os.path.exists(FONT_PATH):
        WATERMARK_FONT = ImageFont.truetype(FONT_PATH, 24)
        logger.info("中文字体加载成功")
    else:
        WATERMARK_FONT = ImageFont.load_default()
        logger.warning("未找到中文字体，将使用默认字体（可能无法显示中文）")
except Exception as e:
    WATERMARK_FONT = ImageFont.load_default()
    logger.warning(f"字体加载失败: {e}，使用默认字体")

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
            if img.mode != 'RGB':
                img = img.convert('RGB')
            r, g, b = img.split()
            
            if filter_type == "auto_warm":
                r = r.point(lambda i: min(255, int(i * 1.1)))
                g = g.point(lambda i: min(255, int(i * 1.05)))
                logger.info("应用暖色调滤镜")
            elif filter_type == "auto_cool":
                b = b.point(lambda i: min(255, int(i * 1.1)))
                g = g.point(lambda i: min(255, int(i * 1.05)))
                logger.info("应用冷色调滤镜")
                
            img = Image.merge('RGB', (r, g, b))
            
        return img

    @staticmethod
    def apply_watermark(img, params):
        """添加文本水印（支持中文）"""
        text = params.get("text", "")
        if not text:
            return img
            
        position = params.get("position", "bottom_right")
        opacity = params.get("opacity", 0.5)
        
        # 转换为 RGBA 以支持透明度
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        txt_layer = Image.new('RGBA', img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt_layer)
        width, height = img.size
        
        # 动态计算字体大小
        font_size = max(16, min(width, height) // 25)
        try:
            # 尝试调整字体大小
            font = ImageFont.truetype(FONT_PATH, font_size) if os.path.exists(FONT_PATH) else WATERMARK_FONT
        except:
            font = WATERMARK_FONT
        
        # 估算文字尺寸（粗略但有效）
        try:
            # 使用 getsize 方法（PIL 旧版）或 textbbox（新版）
            if hasattr(draw, 'textbbox'):
                bbox = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            else:
                text_w, text_h = draw.textsize(text, font=font)
        except:
            text_w, text_h = len(text) * font_size // 2, font_size
        
        # 计算水印坐标
        padding = 20
        if position == "bottom_right":
            x = width - text_w - padding
            y = height - text_h - padding
        elif position == "center":
            x = (width - text_w) // 2
            y = (height - text_h) // 2
        elif position == "top_left":
            x = padding
            y = padding
        else:
            x = width - text_w - padding
            y = height - text_h - padding
        
        # 绘制半透明白色文字
        fill_color = (255, 255, 255, int(255 * opacity))
        draw.text((x, y), text, fill=fill_color, font=font)
        
        # 合并图层
        combined = Image.alpha_composite(img, txt_layer)
        return combined

    @staticmethod
    def close_image(img):
        """安全关闭 PIL Image 对象，释放内存"""
        try:
            img.close()
        except:
            pass


# ----------------- API 路由 -----------------

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "message": "Visual Work Automation Agent is Running!"})

@app.route('/static/processed/<path:filename>')
def serve_processed_image(filename):
    """提供处理后的图片静态访问"""
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/execute', methods=['POST'])
def execute_task():
    """
    统一的图像自动化处理执行入口
    兼容前端字段名: image_urls / operations
    也保留旧版 images / params 以向后兼容
    """
    img = None  # 用于确保 finally 中关闭
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "请求体不能为空 (JSON)"}), 400
        
        # 兼容两种字段名：优先使用前端标准字段
        image_sources = data.get("image_urls") or data.get("images", [])
        operations = data.get("operations") or data.get("params", [])
        
        # 如果 operations 是字符串（旧版），则解析为列表
        if isinstance(operations, str):
            try:
                operations = json.loads(operations)
            except json.JSONDecodeError:
                operations = []
        
        if not image_sources:
            return jsonify({"error": "待处理的 'image_urls' 或 'images' 列表不能为空"}), 400
        
        logger.info(f"接收到批量任务，待处理图片数: {len(image_sources)}, 操作列表: {operations}")
        
        processed_urls = []
        host_url = request.host_url
        
        for idx, src in enumerate(image_sources):
            try:
                # 下载图片
                img = ImageProcessor.download_image(src)
                original_format = img.format if img.format else "JPEG"
                
                target_format = original_format
                compress_quality = 85
                max_size_kb = None
                
                # 依次应用操作
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
                
                # 格式兼容处理
                if target_format == "JPEG" and img.mode in ('RGBA', 'LA'):
                    img = img.convert('RGB')
                
                # 保存文件
                ext = "jpg" if target_format == "JPEG" else target_format.lower()
                filename = f"processed_{uuid.uuid4().hex}.{ext}"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                
                img.save(filepath, format=target_format, quality=compress_quality)
                
                # 动态压缩到指定大小
                if max_size_kb:
                    try:
                        max_size_bytes = int(max_size_kb) * 1024
                        current_size = os.path.getsize(filepath)
                        temp_quality = compress_quality
                        while current_size > max_size_bytes and temp_quality > 10:
                            temp_quality -= 10
                            img.save(filepath, format=target_format, quality=temp_quality)
                            current_size = os.path.getsize(filepath)
                        logger.info(f"压缩完成，最终大小: {current_size/1024:.1f} KB")
                    except Exception as ce:
                        logger.error(f"压缩失败: {ce}")
                
                # 生成公网 URL
                public_url = f"{host_url.rstrip('/')}/static/processed/{filename}"
                processed_urls.append(public_url)
                logger.info(f"第 {idx+1} 张图片处理成功: {public_url}")
                
                # 关闭图片对象，释放内存
                ImageProcessor.close_image(img)
                img = None
                
            except Exception as item_error:
                logger.error(f"处理第 {idx+1} 张图片时发生错误: {item_error}")
                # 确保关闭异常情况下的图片对象
                if img:
                    ImageProcessor.close_image(img)
                    img = None
                continue
        
        return jsonify({
            "status": "success",
            "processed_images": processed_urls,
            "count": len(processed_urls)
        })
        
    except Exception as e:
        logger.error(f"执行 API 时发生严重异常: {e}")
        return jsonify({"error": f"服务器内部错误: {str(e)}"}), 500
    finally:
        # 最终保障：如果还有未关闭的图片对象，关闭它
        if img:
            ImageProcessor.close_image(img)

@app.route('/api/merge', methods=['POST'])
def legacy_merge():
    """兼容旧版 /api/merge 路由"""
    logger.info("捕获到历史 /api/merge 节点调用，自动路由至通用执行器")
    return execute_task()


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
