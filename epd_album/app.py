#!/usr/bin/env python3
"""
Pi Zero W E-Paper Album — SIMPLE single-process service.

Design for Zero W (single-core ARMv6, 512MB RAM):
- Single Python process: Flask + threading.Thread worker
- No multiprocessing, no spawn, no fork
- Threading.Lock protects EPD access (display worker is daemon thread)
- Flask threaded=True handles multiple HTTP requests
"""
import os
import sys
import io
import time
import threading
import queue
import logging
from pathlib import Path

# SDK path
EPD_LIB_PATH = "/home/pi/waveshare_epd_lib"
sys.path.insert(0, EPD_LIB_PATH)

from flask import Flask, request, jsonify, send_file  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("/home/pi/epd_album.log"), logging.StreamHandler()],
)
log = logging.getLogger("epd-album")

EPD_W = 800
EPD_H = 480
UPLOAD_DIR = Path("/home/pi/epd_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# =====================================================================
# Text rendering
# =====================================================================

def _load_font(size):
    for path in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _render_text_image(text, size, align, color_name, bg_name, vertical, script_type,
                      line_spacing, char_spacing):
    colors = {"black": (0, 0, 0), "white": (255, 255, 255), "yellow": (255, 255, 0),
              "red": (255, 0, 0), "blue": (0, 0, 255), "green": (0, 255, 0)}
    fg = colors.get(color_name, (255, 255, 255))
    bg = colors.get(bg_name, (255, 255, 255))
    font = _load_font(size)

    if vertical:
        cw, ch = EPD_H, EPD_W
        img = Image.new("RGB", (cw, ch), bg)
        draw = ImageDraw.Draw(img)
        char_h = size + max(0, char_spacing)
        col_w = size + 4

        if script_type == "cjk":
            chars = list(text)
            max_chars_per_col = max(2, (ch - 40) // char_h)
            cols = [chars[i:i+max_chars_per_col] for i in range(0, len(chars), max_chars_per_col)]
        else:
            import re as _re
            words = _re.findall(r"\S+|\s+", text)
            max_chars_per_col = max(2, (ch - 40) // char_h)
            cols = []
            cur_col = []
            cur_h = 0
            for w in words:
                row_h = char_h * len(w)
                if cur_h + row_h > ch - 40 and cur_col:
                    cols.append(cur_col)
                    cur_col = [w]
                    cur_h = row_h + char_h
                else:
                    cur_col.append(w)
                    cur_h += row_h + char_h
            if cur_col:
                cols.append(cur_col)
            cols = [" ".join(c) for c in cols]

        col_widths = []
        for col in cols:
            if isinstance(col, list):
                max_w = 0
                for ch_ in col:
                    bbox = draw.textbbox((0, 0), ch_, font=font)
                    max_w = max(max_w, bbox[2] - bbox[0])
                col_widths.append(max(size, max_w))
            else:
                bbox = draw.textbbox((0, 0), col, font=font)
                col_widths.append(max(size, bbox[2] - bbox[0]))
        total_w = sum(col_widths) + col_w * (len(cols) - 1)
        x = (cw - total_w) // 2

        for col, w_col in zip(cols, col_widths):
            if isinstance(col, list):
                col_pixel_h = char_h * len(col)
                y = (ch - col_pixel_h) // 2
                for ch_ in col:
                    draw.text((x, y), ch_, font=font, fill=fg)
                    y += char_h
            else:
                words = col.split(" ")
                col_pixel_h = 0
                for wi, w in enumerate(words):
                    col_pixel_h += char_h * len(w)
                    if wi < len(words) - 1:
                        col_pixel_h += char_h
                y = (ch - col_pixel_h) // 2
                for wi, w in enumerate(words):
                    for ch_ in w:
                        draw.text((x, y), ch_, font=font, fill=fg)
                        y += char_h
                    if wi < len(words) - 1:
                        y += char_h
            x += w_col + col_w
        img = img.rotate(-90, expand=True)
    else:
        img = Image.new("RGB", (EPD_W, EPD_H), bg)
        draw = ImageDraw.Draw(img)

        def wrap_h(s, fnt, max_w):
            cur = ""
            lines = []
            for ch_ in s:
                test = cur + ch_
                bbox = draw.textbbox((0, 0), test, font=fnt)
                if bbox[2] - bbox[0] <= max_w:
                    cur = test
                else:
                    if cur:
                        lines.append(cur)
                    cur = ch_
            if cur:
                lines.append(cur)
            return lines

        lines = wrap_h(text, font, EPD_W - 40)
        line_h = size + max(0, line_spacing)
        max_lines = EPD_H // max(1, line_h)
        while len(lines) > max_lines and size > 16:
            size -= 4
            font = _load_font(size)
            line_h = size + max(0, line_spacing)
            max_lines = EPD_H // max(1, line_h)
            lines = wrap_h(text, font, EPD_W - 40)

        total_h = line_h * len(lines)
        y = (EPD_H - total_h) // 2
        for ln in lines:
            bbox = draw.textbbox((0, 0), ln, font=font)
            w = bbox[2] - bbox[0]
            x = {"l": 20, "c": (EPD_W - w) // 2, "r": EPD_W - w - 20}[align]
            draw.text((x, y), ln, font=font, fill=fg)
            y += line_h

    return img


def _quantize_for_epd(img):
    pal_img = Image.new("P", (1, 1))
    sdk_palette = [
        (0, 0, 0),       (255, 255, 255), (255, 255, 0),
        (255, 0, 0),     (0, 0, 0),       (0, 0, 255),
        (0, 255, 0),
    ]
    pal_img.putpalette([c for color in sdk_palette for c in color] + [0] * (256 * 3 - len(sdk_palette) * 3))
    return img.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG).convert("RGB")


# =====================================================================
# Display worker — DAEMON THREAD in same process (no multiprocessing)
# =====================================================================

DISPLAY_Q = queue.Queue(maxsize=2)
_epd = None
_epd_lock = threading.Lock()


def _display_loop():
    """Single daemon thread that processes display queue forever."""
    global _epd
    log.info("Display thread started, pid=%d", os.getpid())
    while True:
        try:
            item = DISPLAY_Q.get()
            if item is None:
                break
            path = item
            log.info("Processing %s", path)
            with _epd_lock:
                img = Image.open(path).convert("RGB")
                img = img.rotate(180, expand=True)
                img = _quantize_for_epd(img)

                if _epd is None:
                    from waveshare_epd import epd7in3e
                    _epd = epd7in3e.EPD()
                    log.info("EPD object created")

                log.info("init() begin")
                t = time.time()
                _epd.init()
                log.info("init() done in %.1fs", time.time() - t)

                log.info("getbuffer()")
                buf = _epd.getbuffer(img)

                log.info("display() begin")
                t = time.time()
                _epd.display(buf)
                log.info("display() done in %.1fs", time.time() - t)
            DISPLAY_Q.task_done()
        except Exception as ex:
            log.exception("Display thread error: %s", ex)
            try:
                DISPLAY_Q.task_done()
            except Exception:
                pass
            # Reset _epd so next item re-creates it (in case of SPI hang)
            _epd = None


# Start daemon thread
threading.Thread(target=_display_loop, name="epd-display", daemon=True).start()


# =====================================================================
# Flask HTTP
# =====================================================================

app = Flask(__name__)


@app.route("/")
def index():
    html = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PiAlbum — E-Paper 上传</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.css">
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f5f7;color:#1d1d1f}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#6e6e73;font-size:14px;margin-bottom:24px}
.tabs{display:flex;gap:8px;margin-bottom:16px;border-bottom:1px solid #d2d2d7}
.tab{padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;font-size:14px}
.tab.on{border-bottom-color:#0071e3;color:#0071e3;font-weight:600}
.panel{display:none}
.panel.on{display:block}
.drop{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;border:2px dashed #c7c7cc;border-radius:12px;padding:40px 16px;min-height:160px;text-align:center;color:#6e6e73;cursor:pointer;background:#fff;box-sizing:border-box}
.drop:hover{border-color:#0071e3}
.drop .drop-hint{font-size:15px;line-height:1.5;pointer-events:none}
.drop input{position:absolute;inset:0;opacity:0;cursor:pointer;z-index:1}
.preview{position:relative;margin-top:16px;background:#fff;border-radius:12px;padding:8px}
.preview img{max-width:100%;display:block}
.row{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px;align-items:flex-end}
button{font:inherit;padding:10px 18px;border-radius:8px;border:none;cursor:pointer}
.primary{background:#0071e3;color:#fff;font-weight:600}
.primary:hover{background:#0077ed}
.primary:disabled{background:#c7c7cc;cursor:not-allowed}
.ghost{background:#e8e8ed;color:#1d1d1f}
.ghost:hover{background:#dcdce0}
textarea{width:100%;min-height:140px;font-size:18px;padding:12px;border:1px solid #c7c7cc;border-radius:8px;resize:vertical;box-sizing:border-box;font-family:inherit}
.select,.input{padding:8px 10px;border:1px solid #c7c7cc;border-radius:8px;font:inherit;background:#fff}
label.field{display:flex;flex-direction:column;gap:4px;font-size:13px;color:#6e6e73}
.status{margin-top:16px;padding:12px;border-radius:8px;font-size:14px;display:none}
.status.ok{background:#d1f4e0;color:#0a6e3f;display:block}
.status.err{background:#ffd5d5;color:#a01010;display:block}
.status.busy{background:#fff3c4;color:#8a6100;display:block}
.help{font-size:12px;color:#6e6e73;margin-top:8px;line-height:1.5}
#previewCanvas{display:block;width:100%;height:auto;border:1px solid #c7c7cc;border-radius:8px;background:#fff}
</style>
</head>
<body>
<div class="wrap">
<h1>PiAlbum — 7.3" 6色电子相册</h1>
<div class="sub">上传照片或励志文字 → 墨水屏显示（每次刷新约 30-40 秒）</div>

<div class="tabs">
  <div class="tab on" data-tab="img">📷 图片</div>
  <div class="tab" data-tab="txt">✍️ 文字</div>
</div>

<div class="panel on" id="panel-img">
  <label class="drop" id="drop">
    <input type="file" id="file" accept="image/*">
    <div class="drop-hint">
      <div style="font-size:28px;margin-bottom:8px">📷</div>
      <div>点击或拖拽图片到此处</div>
      <div class="help">支持 JPG / PNG / WebP</div>
    </div>
  </label>
  <div class="preview" id="cropwrap" style="display:none">
    <img id="cropper" src="">
  </div>
  <div class="row">
    <button class="ghost" id="rotateBtn" style="display:none" title="将原图顺时针旋转 90°">⟳ 右旋 90°</button>
    <button class="ghost" id="resetCrop" style="display:none">重置裁剪</button>
    <button class="primary" id="sendImg" disabled>上传并显示到墨水屏</button>
  </div>
  <div class="help">拖动裁剪框选择显示区域；框框比例匹配屏幕 800×480</div>
</div>

<div class="panel" id="panel-txt">
  <div style="display:grid;grid-template-columns:1fr 320px;gap:16px;align-items:start">
    <div>
      <label class="field"><span>文字内容</span>
        <textarea id="txt" rows="6" placeholder="一句话励志、座右铭、提醒…"></textarea>
      </label>
      <div class="row">
        <label class="field"><span>字号</span>
          <select class="select" id="fontSize">
            <option>24</option><option>36</option><option selected>48</option>
            <option>60</option><option>72</option><option>96</option>
          </select>
        </label>
        <label class="field"><span>对齐 (横排)</span>
          <select class="select" id="align">
            <option value="l">左对齐</option>
            <option value="c" selected>居中</option>
            <option value="r">右对齐</option>
          </select>
        </label>
        <label class="field"><span>字体颜色</span>
          <select class="select" id="color">
            <option value="black">黑</option>
            <option value="white" selected>白</option>
            <option value="yellow">黄</option>
            <option value="red">红</option>
            <option value="blue">蓝</option>
            <option value="green">绿</option>
          </select>
        </label>
        <label class="field"><span>背景色</span>
          <select class="select" id="bg">
            <option value="white" selected>白</option>
            <option value="black">黑</option>
            <option value="yellow">黄</option>
            <option value="red">红</option>
            <option value="blue">蓝</option>
            <option value="green">绿</option>
          </select>
        </label>
      </div>
      <div class="row">
        <label class="field"><span>行间距 (横排)</span>
          <input type="number" class="input" id="lineSpacing" min="0" max="200" value="20" step="2" style="width:80px">
          <span class="help">像素，建议 8~40</span>
        </label>
        <label class="field"><span>字符间距 (竖排)</span>
          <input type="number" class="input" id="charSpacing" min="0" max="200" value="6" step="2" style="width:80px">
          <span class="help">像素，建议 0~20</span>
        </label>
      </div>
      <div class="row">
        <label class="field" style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" id="vertical" style="width:18px;height:18px"><span>竖排文字</span></label>
        <label class="field" id="scriptTypeField" style="display:none;flex-direction:row;align-items:center;gap:8px">
          <span>排版：</span>
          <label><input type="radio" name="scriptType" value="cjk" checked> 汉字</label>
          <label><input type="radio" name="scriptType" value="latin"> 西文</label>
        </label>
      </div>
      <div class="row">
        <button class="primary" id="sendTxt">发送到墨水屏</button>
        <button class="ghost" id="refreshPreview">🔄 刷新预览</button>
      </div>
      <div class="help">排版以墨水屏 800×480 显示，旋转 180° 后正立</div>
    </div>
    <div>
      <label class="field"><span>预览 (800×480 缩略)</span>
        <canvas id="previewCanvas" width="320" height="192"></canvas>
      </label>
      <div class="help" id="previewHint" style="margin-top:6px">输入文字后点"刷新预览"</div>
    </div>
  </div>
</div>

<div class="status" id="status"></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.js"></script>
<script>
const $=s=>document.querySelector(s);
const status=$("#status");
function showStatus(msg,kind){status.className="status "+(kind||"");status.textContent=msg}

document.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",e=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));
  document.querySelectorAll(".panel").forEach(x=>x.classList.remove("on"));
  t.classList.add("on");
  $("#panel-"+t.dataset.tab).classList.add("on");
}));

let cropper=null;
const drop=$("#drop"),fileIn=$("#file"),cropWrap=$("#cropwrap"),cropImg=$("#cropper"),sendBtn=$("#sendImg"),rotateBtn=$("#rotateBtn");

rotateBtn.onclick=()=>{if(!cropper)return;cropper.rotate(90);};

fileIn.addEventListener("change",e=>loadFile(e.target.files[0]));
["dragover","dragenter"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.style.borderColor="#0071e3"}));
["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.style.borderColor="#c7c7cc"}));
drop.addEventListener("drop",e=>{const f=e.dataTransfer.files[0];if(f)loadFile(f)});

function loadFile(f){if(!f||!f.type.startsWith("image/"))return showStatus("请选择图片","err");
  const url=URL.createObjectURL(f);
  cropImg.src=url;cropWrap.style.display="block";
  $("#resetCrop").style.display="inline-block";
  if(cropper)cropper.destroy();
  cropper=new Cropper(cropImg,{aspectRatio:800/480,viewMode:1,background:false,autoCropArea:0.92,responsive:true});
  sendBtn.disabled=false;
  rotateBtn.style.display="inline-block";
}
$("#resetCrop").onclick=()=>cropper&&cropper.reset();

sendBtn.onclick=async()=>{
  if(!cropper)return;
  showStatus("上传中…","busy");sendBtn.disabled=true;
  try{
    const data=cropper.getCroppedCanvas({width:800,height:480,imageSmoothingQuality:"high"}).toDataURL("image/png");
    const r=await fetch("/upload",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({image:data})});
    const j=await r.json();
    if(j.ok)showStatus("✅ "+j.message,"ok");
    else showStatus("❌ "+j.error,"err");
  }catch(e){showStatus("❌ 上传失败: "+e,"err")}
  sendBtn.disabled=false;
};

function getScriptType(){const r=document.querySelector("input[name=scriptType]:checked");return r?r.value:"cjk";}

async function doSendTxt(){
  const txt=$("#txt").value.trim();if(!txt)return showStatus("请输入文字","err");
  showStatus("上传中…","busy");$("#sendTxt").disabled=true;
  try{
    const r=await fetch("/text",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      text:txt,size:+$("#fontSize").value,align:$("#align").value,color:$("#color").value,bg:$("#bg").value,
      vertical:$("#vertical").checked,script_type:getScriptType(),
      line_spacing:+$("#lineSpacing").value,char_spacing:+$("#charSpacing").value})});
    const j=await r.json();
    if(j.ok)showStatus("✅ "+j.message,"ok");
    else showStatus("❌ "+j.error,"err");
  }catch(e){showStatus("❌ 失败: "+e,"err")}
  $("#sendTxt").disabled=false;
}
$("#sendTxt").onclick=doSendTxt;
$("#vertical").onchange=()=>{$("#scriptTypeField").style.display=$("#vertical").checked?"flex":"none";doPreview();};

async function doPreview(){
  const txt=$("#txt").value.trim();
  if(!txt){$("#previewHint").textContent='输入文字后点"刷新预览"';return;}
  try{
    const r=await fetch("/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      text:txt,size:+$("#fontSize").value,align:$("#align").value,color:$("#color").value,bg:$("#bg").value,
      vertical:$("#vertical").checked,script_type:getScriptType(),
      line_spacing:+$("#lineSpacing").value,char_spacing:+$("#charSpacing").value})});
    if(!r.ok){$("#previewHint").textContent="预览失败";return;}
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const img=new Image();
    img.onload=()=>{
      const c=document.getElementById("previewCanvas");
      const ctx=c.getContext("2d");
      ctx.fillStyle="#fff";ctx.fillRect(0,0,c.width,c.height);
      ctx.drawImage(img,0,0,c.width,c.height);
      $("#previewHint").textContent="预览 (800×480 缩放)";
    };
    img.src=url;
  }catch(e){$("#previewHint").textContent="预览错误: "+e;}
}
$("#refreshPreview").onclick=doPreview;
let _previewTimer=null;
$("#txt").addEventListener("input",()=>{
  clearTimeout(_previewTimer);
  _previewTimer=setTimeout(doPreview, 400);
});

setInterval(async()=>{
  try{
    const r=await fetch("/queue");
    const j=await r.json();
    if(j.queue>0)showStatus("⏳ 队列中还有 "+j.queue+" 张等待刷新","busy");
  }catch{}
}, 5000);
</script>
</body>
</html>"""
    return html


@app.route("/health")
def health():
    return jsonify(ok=True, epd="epd7in3e (PhotoPainter)", w=EPD_W, h=EPD_H,
                   queue=DISPLAY_Q.qsize())


@app.route("/queue")
def queue_info():
    return jsonify(queue=DISPLAY_Q.qsize())


def _extract_text_params(data):
    return dict(
        text=data.get("text", "").strip(),
        size=int(data.get("size", 48)),
        align=data.get("align", "c"),
        color_name=data.get("color", "white"),
        bg_name=data.get("bg", "white"),
        vertical=data.get("vertical", False),
        script_type=data.get("script_type", "cjk"),
        line_spacing=int(data.get("line_spacing", 20)),
        char_spacing=int(data.get("char_spacing", 6)),
    )


@app.route("/preview", methods=["POST"])
def preview_text():
    try:
        data = request.get_json() or {}
        text = data.get("text", "").strip()
        if not text:
            return jsonify(ok=False, error="empty"), 400
        params = _extract_text_params(data)
        img = _render_text_image(**params)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")
    except Exception as ex:
        log.exception("preview failed")
        return jsonify(ok=False, error=str(ex)), 500


@app.route("/upload", methods=["POST"])
def upload_image():
    try:
        data = request.get_json()
        if not data or "image" not in data:
            return jsonify(ok=False, error="missing image"), 400
        b64 = data["image"].split(",", 1)[-1]
        import base64
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        path = UPLOAD_DIR / f"img_{int(time.time()*1000)}.png"
        img.save(path)
        if DISPLAY_Q.full():
            return jsonify(ok=False, error="队列满，请等当前刷新完成 (约 30s)"), 429
        DISPLAY_Q.put(str(path))
        log.info("Image queued: %s", path.name)
        return jsonify(ok=True, message=f"已加入队列 (位置 {DISPLAY_Q.qsize()})")
    except Exception as ex:
        log.exception("upload_image failed")
        return jsonify(ok=False, error=str(ex)), 500


@app.route("/text", methods=["POST"])
def upload_text():
    try:
        data = request.get_json() or {}
        text = data.get("text", "").strip()
        if not text:
            return jsonify(ok=False, error="empty text"), 400
        params = _extract_text_params(data)
        img = _render_text_image(**params)
        path = UPLOAD_DIR / f"txt_{int(time.time()*1000)}.png"
        img.save(path)
        if DISPLAY_Q.full():
            return jsonify(ok=False, error="队列满，请等当前刷新完成 (约 30s)"), 429
        DISPLAY_Q.put(str(path))
        log.info("Text queued: %s, vertical=%s, script=%s",
                 text[:30], params["vertical"], params["script_type"])
        return jsonify(ok=True, message=f"已加入队列 (位置 {DISPLAY_Q.qsize()})")
    except Exception as ex:
        log.exception("upload_text failed")
        return jsonify(ok=False, error=str(ex)), 500


if __name__ == "__main__":
    log.info("Starting PiAlbum on 0.0.0.0:80")
    # threaded=True: Flask serves concurrent requests (display blocks one thread)
    app.run(host="0.0.0.0", port=80, threaded=True, debug=False)
