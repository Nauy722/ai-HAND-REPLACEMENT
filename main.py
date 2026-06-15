from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import requests
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import io
import base64

app = FastAPI()

class Operation(BaseModel):
    type: str
    width: Optional[int] = None
    height: Optional[int] = None
    text: Optional[str] = None
    position: Optional[str] = "bottom-right"
    ratio: Optional[str] = None
    maintain_aspect: Optional[bool] = False
    # 色调调节参数，全部可选
    brightness: Optional[float] = None
    contrast: Optional[float] = None
    saturation: Optional[float] = None
    sharpness: Optional[float] = None

class ProcessRequest(BaseModel):
    images: List[str]
    operations: List[Operation]

def download_image(url: str) -> Image.Image:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")

def apply_operations(img: Image.Image, ops: List[Operation]) -> Image.Image:
    for op in ops:
        if op.type == "resize":
            w = op.width or img.width
            h = op.height or img.height
            if op.maintain_aspect:
                img.thumbnail((w, h), Image.Resampling.LANCZOS)
            else:
                img = img.resize((w, h), Image.Resampling.LANCZOS)

        elif op.type == "crop":
            if op.ratio:
                parts = op.ratio.split(":")
                if len(parts) == 2:
                    target = float(parts[0]) / float(parts[1])
                    cur = img.width / img.height
                    if cur > target:
                        new_w = int(img.height * target)
                        left = (img.width - new_w) // 2
                        img = img.crop((left, 0, left + new_w, img.height))
                    else:
                        new_h = int(img.width / target)
                        top = (img.height - new_h) // 2
                        img = img.crop((0, top, img.width, top + new_h))

        elif op.type == "watermark":
            txt = op.text or "Watermark"
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("arial.ttf", size=max(img.width//20, 20))
            except:
                font = ImageFont.load_default()
            bbox = draw.textbbox((0,0), txt, font=font)
            tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
            margin = 10
            if op.position == "bottom-right":
                xy = (img.width - tw - margin, img.height - th - margin)
            elif op.position == "bottom-left":
                xy = (margin, img.height - th - margin)
            elif op.position == "top-right":
                xy = (img.width - tw - margin, margin)
            elif op.position == "top-left":
                xy = (margin, margin)
            else:
                xy = (img.width - tw - margin, img.height - th - margin)
            draw.text((xy[0]+2, xy[1]+2), txt, fill=(0,0,0), font=font)
            draw.text(xy, txt, fill=(255,255,255), font=font)

        elif op.type == "color_adjust":
            # 按需应用色调调整
            if op.brightness is not None:
                enhancer = ImageEnhance.Brightness(img)
                img = enhancer.enhance(op.brightness)
            if op.contrast is not None:
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(op.contrast)
            if op.saturation is not None:
                enhancer = ImageEnhance.Color(img)
                img = enhancer.enhance(op.saturation)
            if op.sharpness is not None:
                enhancer = ImageEnhance.Sharpness(img)
                img = enhancer.enhance(op.sharpness)

    return img

def image_to_base64(img: Image.Image, fmt="JPEG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/{fmt.lower()};base64,{b64}"

@app.post("/process")
async def process_images(req: ProcessRequest):
    try:
        md_parts = []
        for i, url in enumerate(req.images):
            img = download_image(url)
            img = apply_operations(img, req.operations)
            b64_str = image_to_base64(img, "JPEG")
            md_parts.append(f"![处理图片{i+1}]({b64_str})")
        return {"success": True, "markdown": "\n\n".join(md_parts)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"处理失败：{str(e)}")
