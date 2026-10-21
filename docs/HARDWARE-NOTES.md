# 硬件实测记录

这份文档记录的是**量出来 / 试出来**的事实，不是官方文档抄的。
官方文档在引脚和极性这两件事上是错的或缺失的。

## 引脚对照

PhotoPainter 7.3"（`epd7in3e`），BCM GPIO 编号 ↔ 40 针排母物理针脚：

| 信号 | BCM GPIO | 物理针脚 | 方向 | 备注 |
|---|---|---|---|---|
| RST | 17 | 11 | out | |
| DC | 25 | 22 | out | |
| CS | 8 | 24 | out | 由 spidev 硬件管理 |
| BUSY | 24 | 18 | in | **极性与官方注释相反**，见下 |
| **PWR** | **27** | **13** | out | **官方说 18，是错的** |
| MOSI | 10 | 19 | — | SPI0.0 |
| SCLK | 11 | 23 | — | SPI0.0 |

## PWR_PIN：18 → 27

官方 `epdconfig.py` 的 `class RaspberryPi` 里：

```python
PWR_PIN  = 18
```

在 PhotoPainter HAT 上，GPIO 18（物理针脚 12）走的是面板的 **1.2MHz 时钟线**。
把它当使能脚拉高，等于在驱动时钟，**面板永远不进入工作态**。

现象：
- 面板一直白屏
- `WriteImage()` 每次都在 `ReadBusyH()` 超时
- 不会有任何 Python 异常——只是刷不上

改成 `PWR_PIN = 27`（物理针脚 13）后面板立刻正常。

## BUSY 极性：官方注释是错的

官方 `epd7in3e.py` 里：

```python
def ReadBusyH(self):
    while(epdconfig.digital_read(self.busy_pin) == 0):   # 0: busy, 1: idle
        epdconfig.delay_ms(5)
```

注释说「0 是忙，1 是空闲」，于是**忙的时候等 == 0**。

实测 PhotoPainter：**空闲时低，忙的时候高**。所以官方这段逻辑永远等不到，
刷新挂死。

`epdconfig.py` 里读 BUSY 的是：

```python
self.GPIO_BUSY_PIN = gpiozero.Button(self.BUSY_PIN, pull_up = False)
...
elif pin == self.BUSY_PIN:
    return self.GPIO_BUSY_PIN.value
```

`pull_up=False` 是原始电平读。修法是在这里取反，让 SDK 看到的电平符合
它自己的假设，`epd7in3e.py` 就**一行都不用改**：

```python
elif pin == self.BUSY_PIN:
    return 1 - self.GPIO_BUSY_PIN.value
```

> 教训：SDK 注释和实测冲突时，**信实测**。

## ReadBusyH 没有超时

官方这个循环是裸 `while`，没有上界。面板一旦不拉 BUSY（排线接触不良、
面板休眠、引脚错），**显示线程永久卡死**，上传接口只会一直排队直到 429。

本项目的应对：`app.py` 的 worker 线程是 daemon，主进程不被拖死；
`_epd_lock` 保证其他线程不会同时去碰 SPI。

如果你要改 SDK，可以顺手加上界：

```python
def ReadBusyH(self):
    deadline = time.time() + 35      # 七寸彩屏满刷很久，35s 起步
    while epdconfig.digital_read(self.busy_pin) == 0:
        if time.time() > deadline:
            raise TimeoutError("BUSY stuck high for 35s")
        epdconfig.delay_ms(5)
```

## 180° 物理倒装

PhotoPainter HAT 在相框上是**倒焊**的。SPI 送出去的图和屏上实际看到的方向
相差 **180°**。

而 `epd7in3e` 内部把 800×480 竖幅再转 +90° 处理，所以完整链路是：

```
你画的 480×800 竖图
   ↓ epd7in3e 内部 rotate(+90)   → 800×480
   ↓ app.py 补偿 rotate(180)      → 送 SPI
   ↓ HAT 物理倒装                 → 屏上所见
```

**文字竖排的"顶端"因此是当前画面的右侧。**

## SPI / KMS

```
dtparam=spi=on
```

KMS（`dtoverlay=vc4-kms-v3d`）必须**关掉或注释掉**——它会占用和面板 SPI
相邻的 GPIO，导致刷屏时序错乱。`install.sh` 会自动注释。

## 串口救援

启用：

```bash
sudo systemctl enable serial-getty@serial0
# cmdline.txt 需含 console=serial0,115200
```

接线（**3.3V 逻辑，不要接 5V**）：

| CH340 | Zero W |
|---|---|
| GND | Pin 6 |
| TXD | Pin 8 (GPIO 14 / TXD) |
| RXD | Pin 10 (GPIO 15 / RXD) |

**注意**：Zero W 装在相框上时，串口线和排针位置冲突，两者不能同时接。
调试前先把 Zero W 从相框上取下来。

## WiFi 单选

BCM43438 同一时刻只能稳定地扮演 STA 或 AP 其中之一，切 AP 后
`192.168.1.x` 的 LAN 访问就没了。切回 STA 的唯一可靠办法是串口。