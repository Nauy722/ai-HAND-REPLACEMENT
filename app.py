# -*- coding: utf-8 -*-
import os
import json
import base64
import logging
import re
import requests
from io import BytesIO
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image, ImageEnhance, ImageDraw, ImageFont
from werkzeug.utils import secure_filename

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=["*"])

# 配置目录
BASE_DIR = Path("/tmp/image_processor")
UPLOAD_FOLDER = BASE_DIR / "uploads"
OUTPUT_FOLDER = BASE_DIR / "outputs"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['OUTPUT_FOLDER'] = str(OUTPUT_FOLDER)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_base64_image(s):
    if not isinstance(s, str):
        return False
    if s.startswith('data:image'):
        return True
    s_clean = re.sub(r'\s', '', s)
    if re.match(r'^[A-Za-z0-9+/]+=*$', s_clean):
        try:
            data = base64.b64decode(s_clean)
            if data.startswith(b'\xff\xd8') or data.startswith(b'GIF') or data.startswith(b'PNG'):
                return True
        except:
            pass
    return False

def load_image(source):
    """支持 HTTP/HTTPS URL、Base64 字符串和本地文件路径"""
    # 1. HTTP/HTTPS URL
    if isinstance(source, str) and (source.startswith('http://') or source.startswith('https://')):
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            resp = requests.get(source, timeout=15, headers=headers, allow_redirects=True)
            resp.raise_for_status()
            return Image.open(BytesIO(resp.content))
        except Exception as e:
            raise Exception(f"从URL下载图片失败: {str(e)}")

    # 2. Base64
    if isinstance(source, str) and (source.startswith('data:image') or is_base64_image(source)):
        try:
            if ',' in source:
                base64_str = source.split(',')[1]
            else:
                base64_str = source
            image_data = base64.b64decode(base64_str)
            return Image.open(BytesIO(image_data))
        except Exception as e:
            raise Exception(f"解析Base64图片失败: {str(e)}")

    # 3. 本地文件路径
    try:
        return Image.open(source)
    except FileNotFoundError:
        raise Exception(f"文件不存在: {source}")
    except Exception as e:
        raise Exception(f"无法加载图片: {str(e)}")

def save_image_to_file(image, filepath, quality=85, fmt=None):
    """将 PIL 图像保存到文件，路径限制在 OUTPUT_FOLDER 内"""
    filepath = Path(filepath).resolve()
    if not str(filepath).startswith(str(OUTPUT_FOLDER.resolve())):
        raise ValueError(f"保存路径必须在 {OUTPUT_FOLDER} 内")
    filepath.parent.mkdir(parents=True, exist_ok=True)

    if fmt is None:
        fmt = filepath.suffix[1:].upper() if filepath.suffix else 'PNG'
    else:
        fmt = fmt.upper()

    save_img = image
    if fmt in ('JPEG', 'JPG') and image.mode in ('RGBA', 'LA', 'P'):
        save_img = image.convert('RGB')

    save_kwargs = {'format': fmt}
    if fmt in ('JPEG', 'JPG'):
        save_kwargs['quality'] = quality
    save_img.save(filepath, **save_kwargs)

def save_image_base64(image, fmt='PNG'):
    """将 PIL 图像保存为 base64 字符串"""
    buffer = BytesIO()
    if fmt.upper() == 'JPEG' and image.mode in ('RGBA', 'LA', 'P'):
        image = image.convert('RGB')
    image.save(buffer, format=fmt)
    base64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f"data:image/{fmt.lower()};base64,{base64_str}"

@app.route('/api/execute', methods=['POST'])
def execute():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "缺少请求体"}), 400

        atomic_ops = data.get('atomic_ops', [])
        if not atomic_ops:
            return jsonify({"status": "error", "message": "atomic_ops 不能为空"}), 400

        image = None
        result = {"status": "success", "outputs": []}

        for op in atomic_ops:
            action = op.get('action')
            if action == 'load_image':
                path = op.get('path')
                if not path:
                    return jsonify({"status": "error", "message": "load_image 缺少 path"}), 400
                image = load_image(path)
                logger.info(f"Loaded image from {path}")

            elif action == 'resize':
                if image is None:
                    return jsonify({"status": "error", "message": "请先加载图片"}), 400
                orig_w, orig_h = image.size
                width = op.get('width')
                height = op.get('height')
                keep_ratio = op.get('keep_ratio', True)

                if width is not None and height is not None:
                    if keep_ratio:
                        ratio = min(width / orig_w, height / orig_h)
                        new_w = int(orig_w * ratio)
                        new_h = int(orig_h * ratio)
                    else:
                        new_w, new_h = width, height
                elif width is not None:
                    if keep_ratio:
                        ratio = width / orig_w
                        new_w = width
                        new_h = int(orig_h * ratio)
                    else:
                        new_w = width
                        new_h = orig_h
                elif height is not None:
                    if keep_ratio:
                        ratio = height / orig_h
                        new_h = height
                        new_w = int(orig_w * ratio)
                    else:
                        new_w = orig_w
                        new_h = height
                else:
                    new_w, new_h = orig_w, orig_h

                if new_w > 0 and new_h > 0:
                    image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    logger.info(f"Resized image to {image.size}")

            elif action == 'crop':
                if image is None:
                    return jsonify({"status": "error", "message": "请先加载图片"}), 400
                box = op.get('box')
                # 自动正方形裁剪
                if box == "auto_square":
                    w, h = image.size
                    side = min(w, h)
                    left = (w - side) // 2
                    top = (h - side) // 2
                    box = [left, top, left + side, top + side]
                # 兼容字符串列表
                elif isinstance(box, str):
                    try:
                        box = json.loads(box)
                    except:
                        box = [int(x.strip()) for x in box.split(',') if x.strip()]
                if not isinstance(box, list) or len(box) != 4:
                    return jsonify({"status": "error", "message": "crop 需要 box 参数 [left,top,right,bottom]"}), 400
                try:
                    box = [int(v) for v in box]
                except ValueError:
                    return jsonify({"status": "error", "message": "box 元素必须为整数"}), 400
                image = image.crop(box)
                logger.info(f"Cropped image to {image.size}")

            elif action == 'adjust_color':
                if image is None:
                    return jsonify({"status": "error", "message": "请先加载图片"}), 400
                temperature = op.get('temperature')  # 'warm' or 'cool'
                if temperature == 'warm':
                    # 简单暖色：增加R,G通道
                    r, g, b = image.split()
                    r = r.point(lambda i: i * 1.2)
                    g = g.point(lambda i: i * 1.1)
                    image = Image.merge('RGB', (r, g, b))
                elif temperature == 'cool':
                    r, g, b = image.split()
                    b = b.point(lambda i: i * 1.2)
                    g = g.point(lambda i: i * 1.1)
                    image = Image.merge('RGB', (r, g, b))
                else:
                    # 默认暖色
                    r, g, b = image.split()
                    r = r.point(lambda i: i * 1.1)
                    image = Image.merge('RGB', (r, g, b))
                logger.info(f"Adjusted color temperature to {temperature}")

            elif action == 'add_watermark':
                if image is None:
                    return jsonify({"status": "error", "message": "请先加载图片"}), 400
                text = op.get('text', 'AI手替')
                position = op.get('position', 'bottom-right')
                opacity = op.get('opacity', 0.6)

                # 转换为 RGBA 以支持透明度
                if image.mode != 'RGBA':
                    image = image.convert('RGBA')
                watermark = Image.new('RGBA', image.size, (0,0,0,0))
                draw = ImageDraw.Draw(watermark)

                # 尝试加载字体，若无则使用默认
                font = None
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
                except:
                    try:
                        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
                    except:
                        font = ImageFont.load_default()

                bbox = draw.textbbox((0,0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                if position == 'bottom-right':
                    xy = (image.width - text_w - 20, image.height - text_h - 20)
                elif position == 'top-left':
                    xy = (20, 20)
                else:  # bottom-center
                    xy = (image.width//2 - text_w//2, image.height - text_h - 20)

                draw.text(xy, text, fill=(255,255,255,int(255*opacity)), font=font)
                # 合成
                image = Image.alpha_composite(image, watermark).convert('RGB')
                logger.info(f"Added watermark '{text}' at {position}")

            elif action == 'save_image':
                if image is None:
                    return jsonify({"status": "error", "message": "请先处理图片"}), 400
                path = op.get('path')
                quality = op.get('quality', 85)
                fmt = op.get('format', None)

                if path and isinstance(path, str) and path.strip():
                    try:
                        save_image_to_file(image, path, quality, fmt)
                    except ValueError as e:
                        return jsonify({"status": "error", "message": str(e)}), 400
                    result["outputs"].append({"type": "file", "path": str(Path(path).resolve())})
                else:
                    final_fmt = fmt if fmt else (image.format if image.format else 'PNG')
                    b64 = save_image_base64(image, final_fmt)
                    result["outputs"].append({"type": "base64", "data": b64})
                logger.info(f"Saved image to {path if path else 'base64'}")

            else:
                return jsonify({"status": "error", "message": f"未知操作: {action}"}), 400

        return jsonify(result), 200

    except Exception as e:
        logger.exception("执行失败")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/merge', methods=['POST'])
def merge_images():
    """将多张 base64 图片拼接成网格（排版统一）"""
    try:
        data = request.get_json()
        images_b64 = data.get('images', [])
        cols = data.get('cols', 2)
        rows = data.get('rows', None)
        thumb_size = data.get('thumb_size', 300)

        if not images_b64:
            return jsonify({"error": "没有提供图片"}), 400

        # 解码并缩放所有图片
        imgs = []
        for b64 in images_b64:
            if ',' in b64:
                b64 = b64.split(',')[1]
            img_data = base64.b64decode(b64)
            img = Image.open(BytesIO(img_data))
            img.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)
            imgs.append(img)

        n = len(imgs)
        if rows is None:
            rows = (n + cols - 1) // cols

        # 计算每个单元格的尺寸（取各图最大宽高，使排列整齐）
        cell_w = max(img.width for img in imgs)
        cell_h = max(img.height for img in imgs)
        canvas_w = cell_w * cols
        canvas_h = cell_h * rows
        merged = Image.new('RGB', (canvas_w, canvas_h), (255,255,255))

        for idx, img in enumerate(imgs):
            if idx >= cols * rows:
                break
            x = (idx % cols) * cell_w
            y = (idx // cols) * cell_h
            # 居中放置
            offset_x = (cell_w - img.width) // 2
            offset_y = (cell_h - img.height) // 2
            merged.paste(img, (x + offset_x, y + offset_y))

        # 转为 base64 返回
        buff = BytesIO()
        merged.save(buff, format='PNG')
        merged_b64 = base64.b64encode(buff.getvalue()).decode()
        return jsonify({"merged_base64": f"data:image/png;base64,{merged_b64}"})

    except Exception as e:
        logger.exception("拼图失败")
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

@app.route('/api/upload', methods=['POST'])
def upload():
    """安全上传图片文件"""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "没有文件"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "文件名为空"}), 400
    if not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "不支持的文件类型"}), 400

    safe_name = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    unique_name = f"{timestamp}_{safe_name}"
    save_path = UPLOAD_FOLDER / unique_name
    file.save(str(save_path))
    return jsonify({"status": "success", "path": str(save_path)}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
