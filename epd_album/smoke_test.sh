#!/usr/bin/env bash
set -e
log() { echo "[$(date +%H:%M:%S)] $*"; }

python3 - <<'PYEOF'
from PIL import Image, ImageDraw
import datetime
W, H = 800, 480
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
d.rectangle([(0,0),(W-1,H-1)], outline="black", width=4)
colors = [
    ("Black", (0,0,0)), ("White", (255,255,255)), ("Yellow", (255,255,0)),
    ("Red", (255,0,0)), ("Blue", (0,0,255)), ("Green", (0,255,0)),
]
for i,(n,c) in enumerate(colors):
    x0 = 20 + i*128
    d.rectangle([(x0,30),(x0+120,150)], fill=c, outline="black", width=2)
    d.text((x0+10,160), n, fill="black")
d.text((20,210), "PiAlbum 7.3 E-Paper", fill="black")
d.text((20,250), "6-color smoke test", fill="red")
d.text((20,290), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fill="blue")
d.text((20,340), "中文测试：墨水屏刷新成功", fill="black")
img.save("/home/pi/epd_uploads/smoke_test.png")
print("[ok] test image saved")
PYEOF

sudo python3 - <<'PYEOF'
import sys, time
sys.path.insert(0, "/home/pi/waveshare_epd_lib")
from PIL import Image
from waveshare_epd import epd7in3e
import epdconfig

t0 = time.time()
epd = epd7in3e.EPD()
print(f"EPD: W={epd.width} H={epd.height} | SDK PWR={epdconfig.RaspberryPi.PWR_PIN}")

print("init()...")
epd.init()
print(f"init() done in {time.time()-t0:.1f}s")

img = Image.open("/home/pi/epd_uploads/smoke_test.png").convert("RGB")
img = img.rotate(180, expand=True)
buf = epd.getbuffer(img)
print(f"buffer: {len(buf)} bytes")

print("display() ... (this takes ~35s) ...")
t = time.time()
epd.display(buf)
print(f"display() done in {time.time()-t:.1f}s")
print("SMOKE TEST OK")
PYEOF
