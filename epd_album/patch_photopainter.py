#!/usr/bin/env python3
"""
Patch the Waveshare e-Paper SDK for the RPi Zero PhotoPainter (epd7in3e).

The stock SDK is written for Waveshare's own ESP32 driver board. The PhotoPainter
HAT on a Raspberry Pi Zero W wires the control lines differently, so two edits
are required before the panel will respond at all.

Usage:
    python3 patch_photopainter.py /path/to/python/lib/waveshare_epd

Typical path inside the Waveshare e-Paper repo:

    e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd
    e-Paper/7in3_e-Paper_E/python/lib/waveshare_epd

Idempotent: safe to run more than once.
"""
import os
import re
import sys

# BCM GPIO numbers, as they appear in epdconfig.py
PWR_PIN_PHOTOPAINTER = 27   # stock SDK says 18
BUSY_PIN_PHOTOPAINTER = 24  # stock SDK says 17
RST_PIN_PHOTOPAINTER = 17   # stock SDK says 18
DC_PIN_PHOTOPAINTER = 25
CS_PIN_PHOTOPAINTER = 8


def patch_epdconfig(path):
    """
    epdconfig.py holds every platform variant of the pin map (RaspberryPi,
    Jetson, Nano, OrangePi...). We only touch the RaspberryPi class, which is
    the one that runs on a Zero W.

    Two changes:

    1. PWR_PIN 18 -> 27
       GPIO 18 is the 1.2 MHz clock line on the PhotoPainter HAT. Driving it
       as a power rail fights the clock and the panel never wakes, so every
       refresh times out on BUSY and the screen stays blank. Power is on
       GPIO 27.

    2. BUSY is read active-HIGH on this panel
       The SDK constructs gpiozero.Button(BUSY_PIN, pull_up=False) and reads
       .value. With pull_up=False that is a raw pin read. On the PhotoPainter
       BUSY idles LOW and asserts HIGH while the panel is refreshing -- the
       opposite of the SDK's own comment. Inverting digital_read for BUSY
       makes the SDK's "wait while busy" loop wait on the right level.
    """
    f = os.path.join(path, "epdconfig.py")
    if not os.path.exists(f):
        print(f"  !! not found: {f}")
        return False

    with open(f) as fh:
        src = fh.read()
    original = src
    notes = []

    # ---- find the RaspberryPi class block and patch only that one ----------
    m = re.search(r"^class RaspberryPi\b.*?(?=\nclass |\Z)", src, re.S | re.M)
    if not m:
        print("  !! could not locate 'class RaspberryPi' in epdconfig.py")
        return False

    block = m.group(0)

    # 1. PWR_PIN 18 -> 27 (RaspberryPi block only)
    new_block = re.sub(
        r"^(\s*PWR_PIN\s*=\s*)18\b",
        r"\g<1>27  # PhotoPainter: power is GPIO 27, not 18",
        block,
        count=1,
        flags=re.M,
    )
    if new_block != block:
        notes.append("RaspberryPi.PWR_PIN 18 -> 27")
        block = new_block

    # 2. Invert the BUSY read for the RaspberryPi block only.
    #    digital_read() already special-cases BUSY_PIN and returns .value;
    #    wrap that return so the level the SDK sees is the PhotoPainter's.
    old_busy = "        if pin == self.BUSY_PIN:\n            return self.GPIO_BUSY_PIN.value"
    new_busy = (
        "        if pin == self.BUSY_PIN:\n"
        "            # PhotoPainter BUSY is active-HIGH (SDK comment says otherwise).\n"
        "            return 1 - self.GPIO_BUSY_PIN.value"
    )
    if old_busy in block:
        block = block.replace(old_busy, new_busy, 1)
        notes.append("RaspberryPi digital_read(BUSY) inverted to active-HIGH")
    elif "return 1 - self.GPIO_BUSY_PIN.value" in block:
        pass  # already inverted
    else:
        print("  !! digital_read(BUSY) pattern not matched -- check manually")

    if block == m.group(0):
        print("  -- epdconfig.py already patched")
        return True

    src = src.replace(m.group(0), block, 1)
    with open(f, "w") as fh:
        fh.write(src)

    for n in notes:
        print(f"  ++ {n}")
    return True


def check_epd7in3e_busy_loop(path):
    """
    epd7in3e.ReadBusyH() is the wait loop:

        while(epdconfig.digital_read(self.busy_pin) == 0):
            epdconfig.delay_ms(5)

    With digital_read() inverted by patch_epdconfig(), == 0 correctly means
    "panel busy", so this loop needs no change. It does however have no
    timeout, which is why a failed refresh wedges the display worker forever.
    Report whether a bounded version is present.
    """
    f = os.path.join(path, "epd7in3e.py")
    if not os.path.exists(f):
        print(f"  !! not found: {f}")
        return
    src = open(f).read()
    bounded = "timeout" in src.lower() and "time.time()" in src
    print(f"  {'++' if bounded else '--'} ReadBusyH loop "
          f"{'has a timeout guard' if bounded else 'is UNBOUNDED (a stuck BUSY wedges the worker)'}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    sdk = os.path.abspath(sys.argv[1])
    if not os.path.isdir(sdk):
        print(f"not a directory: {sdk}")
        sys.exit(1)

    print(f"Patching PhotoPainter board config in {sdk}")
    patch_epdconfig(sdk)
    print("\nReadBusyH status:")
    check_epd7in3e_busy_loop(sdk)
    print("\nDone. Run test_epd.py to confirm the panel responds.")


if __name__ == "__main__":
    main()