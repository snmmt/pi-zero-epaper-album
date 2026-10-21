#!/usr/bin/env bash
#
# install.sh -- set up a Raspberry Pi Zero W as a standalone PiAlbum e-paper frame.
#
# Run as the `pi` user on a freshly imaged Raspberry Pi OS (Bookworm/Trixie).
# Reboot when done; the AP comes up automatically.
#
#   sudo bash install.sh
#
# Every password/SSID below is a placeholder. Edit before running.
set -euo pipefail

# ---------------------------------------------------------------- settings ---
SSID="${PIALBUM_SSID:-PiAlbum}"
PASSPHRASE="${PIALBUM_PASSPHRASE:-CHANGE_ME}"
AP_IP="192.168.4.1"
AP_NET="192.168.4.0/24"
DHCP_RANGE="192.168.4.2,192.168.4.20,255.255.255.0,24h"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
APP_DIR="/home/pi/epd_album"
SDK_DEST="/home/pi/waveshare_epd_lib"

echo "=== [1/7] enable SPI + serial console ==="
sudo sed -i 's/^dtparam=spi=.*//' /boot/firmware/config.txt
echo "dtparam=spi=on" | sudo tee -a /boot/firmware/config.txt >/dev/null
# KMS/DRM grabs the panel's SPI-adjacent GPIO lines on this board; disable it.
sudo sed -i 's/^dtoverlay=vc4-kms-v3d.*/#&/' /boot/firmware/config.txt
# Serial console as a rescue channel (optional but strongly recommended).
if ! grep -q 'console=serial0,115200' /boot/firmware/cmdline.txt; then
  sudo sed -i 's/$/ console=serial0,115200/' /boot/firmware/cmdline.txt
fi

echo "=== [2/7] install python packages ==="
sudo apt-get update -y
sudo apt-get install -y python3-pip python3-pil python3-rpi.gpio \
                        hostapd dnsmasq iw
sudo pip3 install --break-system-packages flask spidev gpiozero Pillow

echo "=== [3/7] fetch the Waveshare e-Paper SDK ==="
if [ ! -d "$SDK_DEST/waveshare_epd" ]; then
  git clone --depth 1 https://github.com/waveshare/e-Paper.git /tmp/e-Paper || {
    echo "!! git clone failed (network?). Download the repo manually and" >&2
    echo "   re-run, or copy python/lib/waveshare_epd into $SDK_DEST yourself." >&2
    exit 1
  }
  for d in /tmp/e-Paper/*/python/lib; do
    [ -d "$d/waveshare_epd" ] && cp -r "$d/waveshare_epd" "$SDK_DEST/" && break
  done
  rm -rf /tmp/e-Paper
fi
ls -d "$SDK_DEST/waveshare_epd" >/dev/null || { echo "!! SDK not found" >&2; exit 1; }

echo "=== [4/7] patch the SDK for the PhotoPainter HAT ==="
python3 "$HERE/patch_photopainter.py" "$SDK_DEST/waveshare_epd"

echo "=== [5/7] install the album service ==="
sudo mkdir -p "$APP_DIR"
sudo cp "$HERE/app.py" "$APP_DIR/app.py"
sudo chown pi:pi "$APP_DIR/app.py"
sudo cp "$REPO/systemd/epd-album.service" /etc/systemd/system/epd-album.service
sudo systemctl daemon-reload
sudo systemctl enable --now epd-album

echo "=== [6/7] configure the PiAlbum access point ==="
sudo tee /etc/hostapd/hostapd.conf >/dev/null <<EOF
interface=wlan0
driver=nl80211
ssid=$SSID
hw_mode=g
channel=7
ieee80211n=1
wmm_enabled=1
country_code=CN
ieee80211d=1
auth_algs=1
wpa=2
wpa_passphrase=$PASSPHRASE
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF
sudo sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

sudo cp "$REPO/config/pialbum-ap.dnsmasq.conf" /etc/dnsmasq.d/pialbum-ap.conf

# NetworkManager must not try to manage wlan0, or it fights hostapd for it.
sudo cp "$REPO/config/99-pialbum-unmanage.conf" /etc/NetworkManager/conf.d/
sudo systemctl reload NetworkManager

sudo cp "$REPO/systemd/wlan0-ap-ip.service" /etc/systemd/system/wlan0-ap-ip.service
sudo systemctl daemon-reload
sudo systemctl enable wlan0-ap-ip.service hostapd dnsmasq

echo "=== [7/7] enable the serial rescue console ==="
sudo systemctl enable serial-getty@serial0

cat <<'NOTE'

Done. Reboot now:

    sudo reboot

After ~2 minutes:
  * join WiFi "SSID_PLACEHOLDER" on your phone or laptop
  * open http://192.168.4.1/

If the AP does not appear, plug a USB-TTL adapter into the Zero W
(GND -> pin 6, RXD -> pin 10, TXD -> pin 8, 3.3V logic) at 115200 8N1
and watch the boot with `picocom -b 115200 /dev/ttyACM0`.
NOTE