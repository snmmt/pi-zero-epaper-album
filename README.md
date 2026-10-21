# PiAlbum — 树莓派 Zero W 彩色墨水屏电子相框

把一张 **微雪 RPi Zero PhotoPainter 7.3"** 6 色彩色墨水屏，变成一个自带 WiFi 热点的独立电子相框。
任何手机或电脑连上它的热点，打开网页就能上传照片或励志文字，几秒后屏幕自动刷新。

无需云、无需账号、无需外网——相框离线也能用。

```
   手机 / 电脑                     树莓派 Zero W
  ┌──────────┐                  ┌─────────────────────────────┐
  │ 浏览器    │ ── WiFi ──────▶ │ hostapd   PiAlbum 热点       │
  │ 上传/预览 │   PiAlbum      │ dnsmasq   DHCP 192.168.4.x   │
  └──────────┘                  │ Flask     :80                │
                                │ worker    队列 → 墨水屏       │
                                │ SPI       → 7.3" ACeP 六色   │
                                └─────────────────────────────┘
```

---

## 目录

- [功能](#功能)
- [硬件](#硬件)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [技术要点](#技术要点)
  - [1. 微雪 SDK 的三个硬件坑](#1-微雪-sdk-的三个硬件坑)
  - [2. 单线程架构（不要用 multiprocessing）](#2-单线程架构不要用-multiprocessing)
  - [3. 180° 物理倒装](#3-180-物理倒装)
  - [4. AP 模式的 systemd 启动顺序](#4-ap-模式的-systemd-启动顺序)
- [文字排版](#文字排版)
- [运维手册](#运维手册)
- [故障排查](#故障排查)
- [许可证](#许可证)

---

## 功能

| | |
|---|---|
| **照片** | 上传 → 拖拽裁剪（比例锁定 800×480）→ 右旋 90° → 实时预览 → 自动转 6 色并刷屏 |
| **文字** | 横排 / 竖排（以相框右侧为顶端）· 汉字 / 西文切换 · 行间距 · 字符间距 · 实时预览 |
| **连接** | 自建热点 `PiAlbum`，不依赖家里路由器，出门也能用 |
| **队列** | 上传请求排队（容量 2），忙时返回 `429`，服务不崩 |
| **恢复** | 掉电重启后 AP、DHCP、网页服务全部自动起来 |
| **救援** | 串口（USB-TTL）救援通道，失联时仍可登录 |

---

## 硬件

| 部件 | 说明 |
|---|---|
| **核心板** | Raspberry Pi Zero W（512MB，单核 armv6，够用） |
| **屏幕** | 微雪 RPi Zero PhotoPainter 7.3"，800×480，**6 色 ACeP 彩墨水屏** |
| **连接** | HAT 形式直接插在 Zero W 的 40 针排母上，SPI 走 `/dev/spi0.0` |
| **供电** | micro-USB 5V |
| **可选** | CH340 USB-TTL 串口线（调试/救援必备，十几块钱） |

面板物理倒装 180°，所以本项目所有画面都在送屏前先 `rotate(180)`。

---

## 快速开始

在**已刷好 Raspberry Pi OS（Bookworm / Trixie）**的 Zero W 上：

```bash
git clone <this-repo> pi-zero-epaper-album
cd pi-zero-epaper-album

# 改密码（脚本里是占位符）
export PIALBUM_SSID=PiAlbum
export PIALBUM_PASSPHRASE='你的WiFi密码'

sudo bash epd_album/install.sh
sudo reboot
```

重启约 2 分钟后：

1. 手机 / 电脑连上 WiFi **`PiAlbum`**
2. 浏览器打开 **<http://192.168.4.1/>**
3. 上传一张照片或一段文字，看屏幕刷新

### 装完之后

```bash
# 看服务状态
systemctl status epd-album hostapd dnsmasq wlan0-ap-ip

# 看已连的客户端 / 分到的 IP
cat /var/log/daemon.log | grep dnsmasq | tail
ip neigh show dev wlan0

# 刷一张测试图（红/黑棋盘）
python3 ~/epd_album/test_epd.py
```

---

## 项目结构

```
pi-zero-epaper-album/
├── README.md
├── LICENSE
├── epd_album/
│   ├── app.py                  # Flask 服务 + 显示线程（核心，600 行）
│   ├── install.sh              # 一键安装（SPI / SDK / 补丁 / AP / systemd）
│   ├── patch_photopainter.py   # 微雪 SDK 的 PhotoPainter 硬件补丁
│   ├── test_epd.py             # 刷屏冒烟测试
│   ├── smoke_test.sh           # 端到端检查
│   └── patch_flip_font.py      # 字形翻转补丁（CJK 字库方向）
├── systemd/
│   ├── epd-album.service       # Flask 服务单元
│   └── wlan0-ap-ip.service     # 关键：保证 dnsmasq 启动前 IP 已就位
├── config/
│   ├── hostapd.conf            # 热点配置（SSID / 信道 / WPA2）
│   ├── pialbum-ap.dnsmasq.conf # DHCP 段 192.168.4.2–20
│   └── 99-pialbum-unmanage.conf# 让 NetworkManager 放手 wlan0
└── docs/
    ├── HARDWARE-NOTES.md       # 引脚 / 极性 / 旋转的实测记录
    └── TROUBLESHOOTING.md      # 故障排查手册
```

---

## 技术要点

这一节记录**真正花时间的地方**——都不是官方文档写清楚的。

### 1. 微雪 SDK 的三个硬件坑

官方 `waveshare_epd` 的 `epd7in3e` 是给微雪自己的 ESP32 驱动板写的。
插到 PhotoPainter HAT 上，**原样跑是全白屏**。要改两处：

| 坑 | 官方值 | PhotoPainter 实测 | 后果 |
|---|---|---|---|
| **PWR_PIN** | `18` | **`27`** | GPIO 18 是板上的 1.2MHz 时钟线。把它当电源拉高 → 面板永远不醒 → 每次刷新都在 BUSY 超时 → **全白** |
| **BUSY 极性** | 官方注释说低有效 | **高有效** | 官方注释是错的。等 `== 0` 永远不会退出 → 刷新永久卡住 |

`patch_photopainter.py` 把这两处改掉，并且**只动 `class RaspberryPi` 那一段**（`epdconfig.py` 里还有 JetsonNano / SunriseX3 三套引脚定义）。幂等，可以重复跑。

```bash
python3 epd_album/patch_photopainter.py /home/pi/waveshare_epd_lib/waveshare_epd
```

实测引脚（BCM 编号）：

| 信号 | GPIO | 物理针脚 |
|---|---|---|
| RST | 17 | 11 |
| DC | 25 | 22 |
| CS | 8 | 24 |
| BUSY | 24 | 18 |
| **PWR** | **27** | **13** |
| MOSI (SPI) | 10 | 19 |
| SCLK (SPI) | 11 | 23 |

### 2. 单线程架构（不要用 multiprocessing）

`app.py` 里显示是**一个 daemon 线程** + `threading.Lock`，不是 `multiprocessing.Process`。

原因（都是实测踩出来的）：

- Zero W 是**单核 armv6**。`mp.Process` fork/spawn 时会丢 socket fd，Flask 的连接随机断
- spawn 模式下子进程重新 import，SDK 的 `import epdconfig` 报 `ModuleNotFoundError`
- 配合 `waitress`（多进程 WSGI 服务器）会**死锁**

结论：**Flask `threaded=True` + 全局 `_epd_lock` 就够了**，别碰 `mp.Queue` / `mp.Process`。

### 3. 180° 物理倒装

PhotoPainter HAT 是**倒着焊**在相框上的，所以送进 SPI 的图和屏上实际看到的方向差 180°。

处理链路（顺序不能乱）：

```
你画的 480×800 竖图
   ↓ rotate(90)      ← SDK 的 epd7in3e 内部按 800×480 端口处理
800×480
   ↓ rotate(180)     ← 我们在 app.py 里补的，抵消物理倒装
最终上屏
```

所以文字界面里，竖排的"顶端"是**当前画面的右侧**——这一点在页面上有标注。

### 4. AP 模式的 systemd 启动顺序

这是**重启后手机拿不到 IP**的唯一原因，很容易漏。

`dnsmasq` 想监听 `192.168.4.1`，但开机时 `wlan0` 还没这个地址，于是：

```
dnsmasq: failed to create listening socket for 192.168.4.1:
         Cannot assign requested address
```

`dnsmasq.service` 直接 `failed` → 手机连上热点拿到 `169.254.x.x`（APIPA）→ 网页打不开。

**修法**是用一个 oneshot 单元把 IP 抢在 dnsmasq 之前设好：

```ini
# systemd/wlan0-ap-ip.service
[Unit]
After=hostapd.service
Before=dnsmasq.service
Wants=hostapd.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/ip addr replace 192.168.4.1/24 dev wlan0
```

两个细节：

- 用 `ip addr **replace**` 而不是 `add`——`add` 在地址已存在时返回非零，systemd 会误判为失败
- 不要在 dnsmasq 配置里写 `listen-address=192.168.4.1`，那会把上面的竞态变成必然踩中

---

## 文字排版

页面上文字区提供：

- **横排 / 竖排** 切换。竖排以相框**当前右侧为顶端**
- **汉字 / 西文** 切换按钮（不做自动语种识别，省一次误判）
- **行间距 / 字符间距** 滑块
- **实时预览 canvas**，所见即所得

西文竖排按 A4 纸张的阅读习惯排：从左到右逐行，行内从上到下。

---

## 运维手册

### 串口救援通道

强烈建议**常备**。切 AP 时如果失联，串口是唯一能进去的路。

接线（**3.3V 逻辑电平，不要接 5V**）：

| CH340 | Pi Zero W 针脚 |
|---|---|
| GND | Pin 6 (GND) |
| TXD | Pin 8  (GPIO 14, TXD) |
| RXD | Pin 10 (GPIO 15, RXD) |

```bash
# Pi 上启用（安装脚本已做）
sudo systemctl enable serial-getty@serial0

# 观察启动
picocom -b 115200 /dev/ttyACM0
# 或
sudo stty -F /dev/ttyACM0 115200 cs8 -cstopb -parenb -echo raw && cat /dev/ttyACM0
```

登录：`pi` / 你的密码。

> ⚠️ 串口和相框**不能同时插**——Zero W 只有一个位置。调试时先从相框上拆下来。

### 日常操作

```bash
systemctl restart epd-album      # 重启网页服务
systemctl restart hostapd dnsmasq # 重启热点
sudo reboot                      # 整机重启（约 2 分钟恢复）

# 改热点名字/密码
sudo nano /etc/hostapd/hostapd.conf && sudo systemctl restart hostapd
```

### 刷屏耗时

7.3" 六色 ACeP 满刷**约 15–20 秒**，连续操作时更慢。上传请求进队列（容量 2），
满了返回 `429 Too Many Requests`——**这是设计好的背压，不是故障**。

---

## 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| 手机连上热点但 IP 是 `169.254.x.x` | dnsmasq 没起来 | `systemctl status dnsmasq`，看是不是 "Cannot assign requested address" → 确认 `wlan0-ap-ip.service` 已 enable |
| 完全看不到 `PiAlbum` 热点 | hostapd 没起来 | `systemctl status hostapd`；`sudo iw dev wlan0 info` |
| 网页打得开但上传后没反应 | worker 线程卡在 BUSY | `journalctl -u epd-album -n 50`；串口进去跑 `test_epd.py` |
| 屏幕全白 / 完全不刷新 | PWR_PIN 还是 18 | 重跑 `patch_photopainter.py`，确认输出里有 `PWR_PIN 18 -> 27` |
| 刷新永久卡住不返回 | BUSY 极性不对 | 同上，确认 `digital_read(BUSY)` 已取反 |
| 画面上下颠倒 | 180° 补偿失效 | 检查 `_display_loop()` 里的 `rotate(180)` |
| `ModuleNotFoundError: epdconfig` | SDK 用了裸 import | 确保 `sys.path` 含 SDK 的 `lib` 目录 |

---

## 许可证

MIT。Waveshare 的 `e-Paper` SDK 版权归微雪所有，本仓库不包含它——`install.sh` 会在安装时自行 clone 并打补丁。

详见 [LICENSE](LICENSE)。