# -*- coding: utf-8 -*-
import os
import json
import base64
import logging
import re
from io import BytesIO
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image, ImageEnhance
from werkzeug.utils import secure_filename

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 初始化Flask应用
app = Flask(__name__)
CORS(app, origins=["*"])  # 允许所有跨域请求

# 配置目录
BASE_DIR = Path("/tmp/image_processor")
UPLOAD_FOLDER = BASE_DIR / "uploads"
OUTPUT_FOLDER = BASE_DIR / "outputs"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['OUTPUT_FOLDER'] = str(OUTPUT_FOLDER)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 限制50MB

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_base64_image(s):
    """判断字符串是否为 base64 编码的图片"""
    if not isinstance(s, str):
        return False
    # 去掉 data:image/...;base64, 前缀
    if s.startswith('data:image'):
        return True
    # 尝试解码 base64
    try:
        # 标准 base64 字符集，忽略空白
        if re.match(r'^[A-Za-z0-9+/]+=*$', s):
            data = base64.b64decode(s)
            # 进一步检查是否为图片头
            if data.startswith(b'\x89PNG') or data.startswith(b'\xff\xd8') or data.startswith(b'GIF'):
                return True
    except Exception:
        pass
    return False

def load_image(path_or_base64):
    # 如果是 base64 则处理
    if path_or_base64.startswith('data:image') or (len(path_or_base64) > 100 and not os.path.exists(path_or_base64)):
        # ... base64 解析代码 ...
        pass
    else:
        # 直接尝试打开，不提前检查路径合法性
        try:
            return Image.open(path_or_base64)
        except FileNotFoundError:
            raise Exception(f"文件不存在: {path_or_base64}")
        except Exception as e:
            raise Exception(f"无法加载图片: {str(e)}")

def save_image_base64(image, format='PNG'):
    """将PIL图像保存为base64字符串"""
    buffer = BytesIO()
    # 若保存为 JPEG 且图像含 alpha 通道，转为 RGB
    if format.upper() == 'JPEG' and image.mode in ('RGBA', 'LA', 'P'):
        image = image.convert('RGB')
    image.save(buffer, format=format)
    base64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f"data:image/{format.lower()};base64,{base64_str}"

def save_image_to_file(image, filepath, quality=85, fmt=None):
    """安全保存图像到文件，自动处理格式和模式"""
    filepath = Path(filepath).resolve()
    # 限制输出目录必须在 OUTPUT_FOLDER 内
    if not str(filepath).startswith(str(OUTPUT_FOLDER.resolve())):
        raise ValueError(f"保存路径必须在 {OUTPUT_FOLDER} 内")
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    # 确定保存格式
    if fmt is None:
        fmt = filepath.suffix[1:].upper() if filepath.suffix else 'PNG'
    fmt = fmt.upper()
    
    # 处理模式转换
    save_image = image
    if fmt in ('JPEG', 'JPG') and image.mode in ('RGBA', 'LA', 'P'):
        save_image = image.convert('RGB')
    
    save_kwargs = {'format': fmt}
    if fmt in ('JPEG', 'JPG'):
        save_kwargs['quality'] = quality
    save_image.save(filepath, **save_kwargs)

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
                width = op.get('width')
                height = op.get('height')
                keep_ratio = op.get('keep_ratio', True)
                orig_w, orig_h = image.size
                
                if width is not None and height is not None:
                    # 同时给定了宽高
                    if keep_ratio:
                        # 按比例缩放至适应框内（不裁剪，可能留白）
                        ratio = min(width / orig_w, height / orig_h)
                        new_w = int(orig_w * ratio)
                        new_h = int(orig_h * ratio)
                    else:
                        new_w, new_h = width, height
                elif width is not None:
                    # 只给了宽度
                    if keep_ratio:
                        ratio = width / orig_w
                        new_w = width
                        new_h = int(orig_h * ratio)
                    else:
                        new_w = width
                        new_h = orig_h
                elif height is not None:
                    # 只给了高度
                    if keep_ratio:
                        ratio = height / orig_h
                        new_h = height
                        new_w = int(orig_w * ratio)
                    else:
                        new_w = orig_w
                        new_h = height
                else:
                    # 无参数，不缩放
                    new_w, new_h = orig_w, orig_h
                
                if new_w > 0 and new_h > 0:
                    image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
                logger.info(f"Resized image to {image.size}")
            
            elif action == 'crop':
                if image is None:
                    return jsonify({"status": "error", "message": "请先加载图片"}), 400
                box = op.get('box')
                if not box or len(box) != 4:
                    return jsonify({"status": "error", "message": "crop需要 box 参数 [left, top, right, bottom]"}), 400
                # 转换为整数
                box = [int(v) for v in box]
                image = image.crop(box)
                logger.info(f"Cropped image to {image.size}")
            
            elif action == 'adjust_brightness':
                if image is None:
                    return jsonify({"status": "error", "message": "请先加载图片"}), 400
                factor = float(op.get('factor', 1.0))
                enhancer = ImageEnhance.Brightness(image)
                image = enhancer.enhance(factor)
                logger.info(f"Adjusted brightness with factor {factor}")
            
            elif action == 'save_image':
                if image is None:
                    return jsonify({"status": "error", "message": "请先处理图片"}), 400
                path = op.get('path')
                quality = op.get('quality', 85)
                fmt = op.get('format', None)
                
                if path and isinstance(path, str) and path.strip():
                    # 保存到文件（路径会被限制在 OUTPUT_FOLDER）
                    try:
                        save_image_to_file(image, path, quality, fmt)
                    except ValueError as e:
                        return jsonify({"status": "error", "message": str(e)}), 400
                    result["outputs"].append({"type": "file", "path": str(Path(path).resolve())})
                else:
                    # 返回 base64
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
    
    # 使用安全文件名并保存到 UPLOAD_FOLDER
    safe_name = secure_filename(file.filename)
    # 防止同名覆盖，加入时间戳或随机串
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_name = f"{timestamp}_{safe_name}"
    save_path = UPLOAD_FOLDER / unique_name
    file.save(save_path)
    return jsonify({"status": "success", "path": str(save_path)}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
