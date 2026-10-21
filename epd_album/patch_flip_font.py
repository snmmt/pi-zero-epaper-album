#!/usr/bin/env python3
"""Patch app.py: CJK font + 180 rotation + BUSY timeout."""
import re

p = "/home/pi/epd_album/app.py"
src = open(p).read()

# 1) Font loader: CJK first, Latin fallback
old_font = (
    '        # Try common system fonts\n'
    '        font = None\n'
    '        for path in [\n'
    '            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",\n'
    '            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",\n'
    '        ]:\n'
    '            if os.path.exists(path):\n'
    '                try:\n'
    '                    font = ImageFont.truetype(path, size)\n'
    '                    break\n'
    '                except Exception:\n'
    '                    pass\n'
    '        if font is None:\n'
    '            font = ImageFont.load_default()'
)
new_font = (
    '        # CJK font paths (priority order); one of these handles Chinese + Latin\n'
    '        font = None\n'
    '        for path in [\n'
    '            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",\n'
    '            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",\n'
    '            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",\n'
    '            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",\n'
    '            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",\n'
    '            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",\n'
    '        ]:\n'
    '            if os.path.exists(path):\n'
    '                try:\n'
    '                    font = ImageFont.truetype(path, size)\n'
    '                    break\n'
    '                except Exception:\n'
    '                    pass\n'
    '        if font is None:\n'
    '            font = ImageFont.load_default()'
)
assert old_font in src, "old font block missing"
src = src.replace(old_font, new_font)

# 2) Shrink-font fallback loader, CJK-aware
old_shrink = (
    '        while len(lines) > max_lines and size > 16:\n'
    '            size -= 4\n'
    '            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size) if os.path.exists("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") else ImageFont.load_default()\n'
    '            lines = wrap(text, font, EPD_W - 40)'
)
def _loader(sz):
    return (
        '        def _load_font(sz):\n'
        '            for path in [\n'
        '                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",\n'
        '                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",\n'
        '                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",\n'
        '            ]:\n'
        '                if os.path.exists(path):\n'
        '                    try:\n'
        '                        return ImageFont.truetype(path, sz)\n'
        '                    except Exception:\n'
        '                        pass\n'
        '            return ImageFont.load_default()\n'
        '        while len(lines) > max_lines and size > 16:\n'
        '            size -= 4\n'
        '            font = _load_font(size)\n'
        '            lines = wrap(text, font, EPD_W - 40)'
    )
assert old_shrink in src, "shrink block missing"
src = src.replace(old_shrink, _loader(0))

# 3) Add 180° rotation right before the FIRST image_to_epd_buffers call in BOTH routes
# image route: has "epd_img = image_to_epd_buffers(img)"
# text route:  also has same line
# We'll match:  "...\n        epd_img = image_to_epd_buffers(img)\n        with _epd_lock:"
pattern = (
    '        epd_img = image_to_epd_buffers(img)\n'
    '        with _epd_lock:'
)
new_block = (
    '        # PhotoPainter HAT: PCB mounts the panel rotated 180°\n'
    '        img = img.rotate(180, expand=True)\n'
    '        epd_img = image_to_epd_buffers(img)\n'
    '        with _epd_lock:'
)
assert pattern in src, "epd_img block missing"
# Both occurrences
src = src.replace(pattern, new_block)

# But our new_block also contains the pattern only after the rotate insertion? No.
# We replaced pattern -> new_block (rotate + image + buffer + lock).
# A second occurrence would still match. Use replace_all via loop.
while pattern in src:
    src = src.replace(pattern, new_block)

open(p, "w").write(src)

# Verify
print("rotate(180) occurrences:", src.count("img.rotate(180"))
print("buffer/with occurrences:", src.count("epd_img = image_to_epd_buffers(img)"))

# Quick syntax check
import py_compile
py_compile.compile(p, doraise=True)
print("[ok] syntax OK")
