import io
import base64
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Union
from PIL import Image, ImageEnhance
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI手替 - 图片处理服务")

class ImageProcessRequest(BaseModel):
    """Coze 工作流传来的请求体"""
    operation: str = Field(..., description="操作类型: resize, crop, adjust_brightness")
    params: dict = Field(..., description="操作参数")
    image_url: str = Field(..., description="原始图片的 URL")
    task_type: Optional[str] = "image_process"

class ImageProcessResponse(BaseModel):
    success: bool
    message: str
    processed_image_base64: Optional[str] = None
    error_detail: Optional[str] = None

def download_image(url: str) -> Image.Image:
    """从 URL 下载图片并返回 PIL Image 对象"""
    try:
        # 设置超时，避免长时间阻塞
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        # 转换为 RGB 模式，避免 PNG 透明通道导致的问题
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        return img
    except Exception as e:
        logger.error(f"下载图片失败: {e}")
        raise HTTPException(status_code=400, detail=f"图片下载失败: {str(e)}")

def encode_image_base64(img: Image.Image) -> str:
    """将 PIL Image 转为 Base64 字符串"""
    buffer = io.BytesIO()
    # 保存为 JPEG（可根据需要改为 PNG）
    img.save(buffer, format='JPEG', quality=85)
    img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return img_base64

@app.post("/api/execute", response_model=ImageProcessResponse)
async def execute_image_process(request: ImageProcessRequest):
    """
    核心图片处理接口
    """
    logger.info(f"收到请求: operation={request.operation}, params={request.params}")
    
    try:
        # 1. 下载图片
        img = download_image(request.image_url)
        
        # 2. 根据操作类型处理
        if request.operation == "resize":
            width = request.params.get("width")
            height = request.params.get("height")
            if not width and not height:
                raise ValueError("resize 操作需要 width 或 height")
            # 如果只给一个尺寸，按比例缩放
            if width and height:
                new_size = (width, height)
            elif width:
                ratio = width / img.width
                new_size = (width, int(img.height * ratio))
            else:
                ratio = height / img.height
                new_size = (int(img.width * ratio), height)
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            
        elif request.operation == "crop":
            box = request.params.get("box")
            if not box or not isinstance(box, list) or len(box) != 4:
                raise ValueError("crop 操作需要 box 数组 [left, top, right, bottom]")
            # 确保坐标是整数且在图片范围内
            left, top, right, bottom = map(int, box)
            left = max(0, left)
            top = max(0, top)
            right = min(img.width, right)
            bottom = min(img.height, bottom)
            if left >= right or top >= bottom:
                raise ValueError("无效的裁剪区域")
            img = img.crop((left, top, right, bottom))
            
        elif request.operation == "adjust_brightness":
            factor = request.params.get("factor")
            if factor is None:
                raise ValueError("adjust_brightness 操作需要 factor 参数")
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(float(factor))
            
        else:
            raise ValueError(f"不支持的操作类型: {request.operation}")
        
        # 3. 将处理后的图片转为 Base64
        img_base64 = encode_image_base64(img)
        
        return ImageProcessResponse(
            success=True,
            message="图片处理成功",
            processed_image_base64=img_base64
        )
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception("图片处理失败")
        return ImageProcessResponse(
            success=False,
            message="图片处理失败",
            error_detail=str(e)
        )

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
