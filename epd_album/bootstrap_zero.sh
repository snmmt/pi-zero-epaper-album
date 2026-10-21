#!/usr/bin/env bash
# Zero W bootstrap — runs via ssh, assumes sudo NOPASSWD already enabled.
# Installs everything needed for PiAlbum e-paper album.
set -e

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "=== 1/8 apt update + install packages ==="
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update 2>&1 | tail -3
sudo apt-get install -y python3-flask python3-pil python3-spidev python3-pip fonts-noto-cjk fonts-dejavu-core hostapd dnsmasq avahi-daemon git 2>&1 | tail -5

log "=== 2/8 Clone waveshare e-Paper SDK ==="
cd /home/pi
if [ ! -d waveshare_epd_lib ]; then
  # Try tarball download (clone fails with git clone due to file mode issue we saw earlier)
  curl -sL https://codeload.github.com/waveshare/e-Paper/tar.gz/refs/heads/master -o /tmp/e-Paper.tar.gz
  tar -xzf /tmp/e-Paper.tar.gz -C /tmp/ e-Paper-master/RaspberryPi_JetsonNano/python/ 2>&1 | tail -3
  mkdir -p waveshare_epd_lib
  cp -r /tmp/e-Paper-master/RaspberryPi_JetsonNano/python/lib/* waveshare_epd_lib/
  cp -r /tmp/e-Paper-master/RaspberryPi_JetsonNano/python/examples waveshare_epd_lib/ 2>/dev/null || true
fi
ls waveshare_epd_lib/waveshare_epd/epd7in3e.py && log "✅ SDK cloned"

log "=== 3/8 Patch SDK for PhotoPainter (PWR=27, ReadBusyH fix) ==="
EPDCONFIG=waveshare_epd_lib/waveshare_epd/epdconfig.py
sudo sed -i 's/^    PWR_PIN  = 18/    PWR_PIN  = 27  # PhotoPainter PCB/' $EPDCONFIG
grep "PWR_PIN" $EPDCONFIG | head -3

python3 - <<'PYEOF'
p = "/home/pi/waveshare_epd_lib/waveshare_epd/epd7in3e.py"
src = open(p).read()
old = (
    '    def ReadBusyH(self):\n'
    '        logger.debug("e-Paper busy H")\n'
    '        while(epdconfig.digital_read(self.busy_pin) == 0):      # 0: busy, 1: idle\n'
    '            epdconfig.delay_ms(5)\n'
    '        logger.debug("e-Paper busy H release")'
)
new = (
    '    def ReadBusyH(self):\n'
    '        logger.debug("e-Paper busy H")\n'
    '        # PhotoPainter 7.3 fix: BUSY is active-HIGH (1=busy, 0=idle).\n'
    '        # Original SDK comment is wrong. Wait for BUSY to go LOW (= idle).\n'
    '        # Add 10s timeout so we never deadlock on misconfig.\n'
    '        import time as _t\n'
    '        _t0 = _t.time()\n'
    '        while epdconfig.digital_read(self.busy_pin) == 1:\n'
    '            epdconfig.delay_ms(10)\n'
    '            if _t.time() - _t0 > 10:\n'
    '                logger.warning("EPD busy timeout (10s), forcing release")\n'
    '                break\n'
    '        logger.debug("e-Paper busy H release")'
)
assert old in src, "ReadBusyH block not found"
src = src.replace(old, new)
open(p, "w").write(src)
print("[ok] ReadBusyH patched")
PYEOF

log "=== 4/8 Sanity check: SDK import works ==="
sudo python3 -c "
import sys; sys.path.insert(0, '/home/pi/waveshare_epd_lib')
from waveshare_epd import epd7in3e
e = epd7in3e.EPD()
print('epd ok W=%d H=%d PWR=%d RST=%d DC=%d BUSY=%d' % (e.width, e.height, e.PWR_PIN, e.RST_PIN, e.DC_PIN, e.BUSY_PIN))
" 2>&1 | tail -5

log "=== 5/8 Install our Flask service from Pi4B ==="
# Will be scp'd separately; just create dirs
mkdir -p /home/pi/epd_album /home/pi/epd_uploads
log "(dirs ready)"

log "=== 6/8 Probe: SPI test (just check spidev open) ==="
python3 -c "
import spidev
spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 4000000
print('spidev open OK')
spi.close()
"

log "=== 7/8 Probe: GPIO 24 read (BUSY pin) ==="
sudo python3 -c "
import gpiozero
b = gpiozero.Button(24, pull_up=False)
print('GPIO24 (BUSY) =', b.value, '(1=busy, 0=idle expected)')
b.close()
" 2>&1 | tail -3

log "=== 8/8 Probe: scan I2C bus for any device (sanity) ==="
ls /dev/i2c* 2>&1 || echo "no i2c"
i2cdetect -y 1 2>&1 | head -5 || echo "no i2cdetect"

log "BOOTSTRAP DONE"
