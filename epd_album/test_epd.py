#!/usr/bin/env python3
"""One-shot EPD test: init + display a test pattern on the 7.3" e-Paper."""
import sys, time
sys.path.insert(0, "/home/pi/waveshare_epd_lib")
from PIL import Image, ImageDraw, ImageFont
from waveshare_epd import epd7in3e

print("[1/4] Init EPD...")
t0 = time.time()
epd = epd7in3e.EPD()
epd.init()
print(f"      init() OK ({time.time()-t0:.1f}s)")

print("[2/4] Clear...")
t0 = time.time()
epd.Clear()
print(f"      Clear OK ({time.time()-t0:.1f}s)")

print("[3/4] Build test image (800x480)...")
img = Image.new("RGB", (800, 480), "white")
d = ImageDraw.Draw(img)
# borders
d.rectangle([(0,0),(799,479)], outline="black", width=4)
# colored blocks
colors = [
    ("Black", (0,0,0)),
    ("White", (255,255,255)),
    ("Yellow", (255,255,0)),
    ("Red", (255,0,0)),
    ("Blue", (0,0,255)),
    ("Green", (0,255,0)),
]
for i, (name, c) in enumerate(colors):
    x0 = 20 + i*128
    d.rectangle([(x0, 60), (x0+120, 180)], fill=c, outline="black", width=2)
    d.text((x0+10, 200), name, fill="black")
d.text((20, 260), "PiAlbum 7.3\" e-Paper", fill="black")
d.text((20, 300), "6-color test pattern", fill="black")
d.text((20, 340), f"t={time.strftime('%H:%M:%S')}", fill="red")

print("[4/4] Display (this takes ~35s)...")
t0 = time.time()
buf_black = epd.getbuffer(img)
buf_cheerful = epd.getbuffer(img)
buf_red = epd.getbuffer(img)
epd.display(buf_black, buf_cheerful, buf_red)
print(f"      Display OK ({time.time()-t0:.1f}s)")
print("DONE")
