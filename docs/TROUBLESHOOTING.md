# 故障排查

按现象查。每条都给出**根因**，不只是给命令。

---

## 看不到 `PiAlbum` 热点

```bash
systemctl status hostapd
sudo iw dev wlan0 info          # type 应为 AP
sudo journalctl -u hostapd -n 30
```

**根因候选**

- NetworkManager 抢着管 `wlan0`。检查 `/etc/NetworkManager/conf.d/99-pialbum-unmanage.conf`
  里有 `unmanaged-devices=interface-name:wlan0`
- `/etc/default/hostapd` 的 `DAEMON_CONF` 没指向 `/etc/hostapd/hostapd.conf`
- Zero W 刚上电，hostapd 还没起。等 2 分钟。

---

## 连上热点但 IP 是 `169.254.x.x`

这是 **APIPA**，说明 DHCP 没响应。**最常见的重启后故障。**

```bash
systemctl status dnsmasq
sudo journalctl -u dnsmasq -n 30
```

看到这行就是它了：

```
dnsmasq: failed to create listening socket for 192.168.4.1:
         Cannot assign requested address
```

**根因**：`dnsmasq` 想绑定 `192.168.4.1`，但开机时 `wlan0` 还没这个地址。
竞态。

**修法**（已在 `systemd/wlan0-ap-ip.service` 里）：

```bash
systemctl enable --now wlan0-ap-ip.service
# 确认 enabled，这样重启也会跑
systemctl is-enabled wlan0-ap-ip.service
ip -4 addr show wlan0 | grep 192.168.4.1
```

两个容易踩的细节：

- 单元里必须用 `ip addr **replace**`。用 `add` 在地址已存在时退出码非零，
  systemd 判定失败，IP 反而加不上
- dnsmasq 配置里**不要**写 `listen-address=192.168.4.1`，那会让上面的竞态
  变成每次必踩

---

## 网页打得开，上传后屏幕不刷新

```bash
journalctl -u epd-album -n 50
curl http://127.0.0.1/health     # 看 ok / queue
curl http://127.0.0.1/queue
```

**根因候选**

- **显示线程卡在 BUSY**。`health` 里 `queue` 一直涨不降就是这个。
  面板 BUSY 极性不对，或排线接触不良。跑 `python3 ~/epd_album/test_epd.py` 单独验证
- **429 是正常的**。队列容量就是 2，连续上传会返回 429。**这不是故障**，
  是背压设计。墨水屏满刷要 15–20 秒，等一下再传

---

## 屏幕一直白 / 没有任何变化

```bash
python3 ~/epd_album/test_epd.py
```

```bash
# 确认 PWR_PIN 补丁在
grep -n 'PWR_PIN' ~/waveshare_epd_lib/waveshare_epd/epdconfig.py | head
```

应该看到：

```python
PWR_PIN  = 27  # PhotoPainter: power is GPIO 27, not 18
```

还是 `18` 就重跑补丁：

```bash
python3 epd_album/patch_photopainter.py ~/waveshare_epd_lib/waveshare_epd
```

**注意**：脚本只改 `class RaspberryPi` 那一段。`epdconfig.py` 里还有
`class JetsonNano` 和 `class SunriseX3`，**不要动**，它们是别的板子。

---

## 刷新调用永久不返回

BUSY 极性反了。等 `== 0` 永远等不到。

```bash
grep -n -A 2 'pin == self.BUSY_PIN' ~/waveshare_epd_lib/waveshare_epd/epdconfig.py | head
```

应该有：

```python
if pin == self.BUSY_PIN:
    # PhotoPainter BUSY is active-HIGH (SDK comment says otherwise).
    return 1 - self.GPIO_BUSY_PIN.value
```

没有就重跑补丁。

---

## 画面上下颠倒

180° 补偿没生效。看 `app.py` 的 `_display_loop()`，送屏前必须有
`rotate(180)`。链路是：

```
480×800 → SDK 内部 +90 → 800×480 → 我们 rotate(180) → SPI
```

少了最后一步，屏上就是倒的。详见
[HARDWARE-NOTES.md](HARDWARE-NOTES.md#180-物理倒装)。

---

## `ModuleNotFoundError: No module named 'epdconfig'`

SDK 内部用的是裸 `import epdconfig`（相对导入 `from . import epdconfig`），
只有在 SDK 目录恰好在 `sys.path` 上才工作。

```bash
# 确认 app.py 里 SDK 路径在 sys.path 里
grep -n 'sys.path' ~/epd_album/app.py
```

用 systemd 跑时工作目录可能和手工运行不同，**绝对路径**加进 `sys.path`。

---

## 启动后 WiFi 完全不通

Zero W 的 BCM43438 **同一时刻只能稳当做一个角色**。切 AP 后原来
`192.168.1.x` 的访问就没了——这是硬件限制，不是 bug。

如果 AP 也没起来：

```bash
sudo iw dev wlan0 link
nmcli device status
```

`systemctl status NetworkManager` 看它是不是又把 `wlan0` 抢回去了。

---

## 串口里什么都没有

```bash
ls /dev/ttyACM* /dev/ttyUSB*     # CH340 出现为 ttyACM0
sudo stty -F /dev/ttyACM0 115200 cs8 -cstopb -parenb -echo raw
# 发个回车唤醒 getty
echo "" | sudo tee /dev/ttyACM0 > /dev/null
timeout 5 cat /dev/ttyACM0
```

应该看到 `login:`。

空输出 → `serial-getty@serial0` 没跑：

```bash
systemctl status serial-getty@serial0
sudo systemctl enable --now serial-getty@serial0
```

还要确认 `/boot/firmware/cmdline.txt` 末尾有：

```
console=serial0,115200
```

---

## 串口里 shell 变成 `>` 符号

heredoc 没闭合，shell 在等更多输入。连按 **Ctrl+C** 退回提示符。

自动化脚本里避免 `<<EOF` 长 heredoc，改用 base64 传文件：

```bash
echo "<base64>" | base64 -d | sudo tee /path/to/file > /dev/null
```

---

## 重启后多久能用

正常约 **2 分钟**。包含 hostapd 起来、dnsmasq 起来、Flask 起来。

排障时别急着重启，先看这三个：

```bash
systemctl is-active hostapd dnsmasq epd-album wlan0-ap-ip
```

四个都 `active` 的话服务层没问题，剩下就是客户端缓存——手机上
**忘记该网络**再重连，清掉旧的 `169.254.x.x`。