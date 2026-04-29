#!/usr/bin/env python3
"""Patch the LoRaRF wheel for the Waveshare SX1262 LoRaWAN HAT.

Upstream LoRaRF 1.4.0 does not match the Raspberry Pi HAT wiring:
- the board needs GPIO21 as a manual chip-select line around SPI transfers;
- the RF switch is controlled by one TXEN pin: LOW for TX, HIGH for RX.

The deb package installs only bundled wheels, so patching the wheel at build
time keeps installation reproducible and avoids editing files in the venv.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


SX126X_PATH = "LoRaRF/SX126x.py"

PATCHES = [
    ("    _busy = 23\n", "    _busy = 23\n    _cs_define = 21\n"),
    (
        "        gpio.setup(busy, gpio.IN)\n",
        "        gpio.setup(busy, gpio.IN)\n        gpio.setup(self._cs_define, gpio.OUT)\n",
    ),
    (
        "        # save current txen and rxen pin state and set txen pin to high and rxen pin to low\n"
        "        if self._txen != -1 and self._rxen != -1 :\n"
        "            self._txState = gpio.input(self._txen)\n"
        "            self._rxState = gpio.input(self._rxen)\n"
        "            gpio.output(self._txen, gpio.HIGH)\n"
        "            gpio.output(self._rxen, gpio.LOW)\n",
        "        # save current txen pin state and set txen pin to LOW\n"
        "        if self._txen != -1 :\n"
        "            self._txState = gpio.input(self._txen)\n"
        "            gpio.output(self._txen, gpio.LOW)\n",
    ),
    (
        "        # save current txen and rxen pin state and set txen pin to low and rxen pin to high\n"
        "        if self._txen != -1 and self._rxen != -1 :\n"
        "            self._txState = gpio.input(self._txen)\n"
        "            self._rxState = gpio.input(self._rxen)\n"
        "            gpio.output(self._txen, gpio.LOW)\n"
        "            gpio.output(self._rxen, gpio.HIGH)\n",
        "        # save current txen pin state and set txen pin to high\n"
        "        if self._txen != -1 :\n"
        "            self._txState = gpio.input(self._txen)\n"
        "            gpio.output(self._txen, gpio.HIGH)\n",
    ),
    (
        "        # save current txen and rxen pin state and set txen pin to low and rxen pin to high\n"
        "        if self._txen != -1 and self._rxen != -1 :\n"
        "            self._txState = gpio.input(self._txen)\n"
        "            self._rxState = gpio.input(self._rxen)\n"
        "            gpio.output(self._txen, gpio.LOW)\n"
        "            gpio.output(self._rxen, gpio.HIGH)\n",
        "        # save current txen pin state and set txen pin to high\n"
        "        if self._txen != -1 :\n"
        "            self._txState = gpio.input(self._txen)\n"
        "            gpio.output(self._txen, gpio.HIGH)\n",
    ),
    (
        "            # for transmit, calculate transmit time and set back txen and rxen pin to previous state\n"
        "            self._transmitTime = time.time() - self._transmitTime\n"
        "            if self._txen != -1 and self._rxen != -1 :\n"
        "                gpio.output(self._txen, self._txState)\n"
        "                gpio.output(self._rxen, self._rxState)\n",
        "            # for transmit, calculate transmit time and set back txen pin to previous state\n"
        "            self._transmitTime = time.time() - self._transmitTime\n"
        "            if self._txen != -1 :\n"
        "                gpio.output(self._txen, self._txState)\n",
    ),
    (
        "            # for receive, get received payload length and buffer index and set back txen and rxen pin to previous state\n"
        "            (self._payloadTxRx, self._bufferIndex) = self.getRxBufferStatus()\n"
        "            if self._txen != -1 and self._rxen != -1 :\n"
        "                gpio.output(self._txen, self._txState)\n"
        "                gpio.output(self._rxen, self._rxState)\n",
        "            # for receive, get received payload length and buffer index and set back txen pin to previous state\n"
        "            (self._payloadTxRx, self._bufferIndex) = self.getRxBufferStatus()\n"
        "            if self._txen != -1 :\n"
        "                gpio.output(self._txen, self._txState)\n",
    ),
    (
        "        # set back txen and rxen pin to previous state\n"
        "        if self._txen != -1 and self._rxen != -1 :\n"
        "            gpio.output(self._txen, self._txState)\n"
        "            gpio.output(self._rxen, self._rxState)\n",
        "        # set back txen pin to previous state\n"
        "        if self._txen != -1 :\n"
        "            gpio.output(self._txen, self._txState)\n",
    ),
    (
        "        # set back txen and rxen pin to previous state\n"
        "        if self._txen != -1 and self._rxen != -1 :\n"
        "            gpio.output(self._txen, self._txState)\n"
        "            gpio.output(self._rxen, self._rxState)\n",
        "        # set back txen pin to previous state\n"
        "        if self._txen != -1 :\n"
        "            gpio.output(self._txen, self._txState)\n",
    ),
    (
        "        if self.busyCheck() : return\n"
        "        buf = [opCode]\n"
        "        for i in range(nBytes) : buf.append(data[i])\n"
        "        spi.xfer2(buf)\n",
        "        if self.busyCheck() : return\n"
        "        gpio.output(self._cs_define, gpio.LOW)\n"
        "        buf = [opCode]\n"
        "        for i in range(nBytes) : buf.append(data[i])\n"
        "        spi.xfer2(buf)\n"
        "        gpio.output(self._cs_define, gpio.HIGH)\n",
    ),
    (
        "        if self.busyCheck() : return ()\n"
        "        buf = [opCode]\n"
        "        for i in range(nAddress) : buf.append(address[i])\n"
        "        for i in range(nBytes) : buf.append(0x00)\n"
        "        feedback = spi.xfer2(buf)\n"
        "        return tuple(feedback[nAddress+1:])\n",
        "        if self.busyCheck() : return ()\n"
        "        gpio.output(self._cs_define, gpio.LOW)\n"
        "        buf = [opCode]\n"
        "        for i in range(nAddress) : buf.append(address[i])\n"
        "        for i in range(nBytes) : buf.append(0x00)\n"
        "        feedback = spi.xfer2(buf)\n"
        "        gpio.output(self._cs_define, gpio.HIGH)\n"
        "        return tuple(feedback[nAddress+1:])\n",
    ),
]


def replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise SystemExit(f"LoRaRF SX126x patch pattern not found: {old[:80]!r}")
    return text.replace(old, new, 1)


def find_lorarf_wheel(wheel_dir: Path) -> Path:
    wheels = sorted(wheel_dir.glob("LoRaRF-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected exactly one LoRaRF wheel in {wheel_dir}, found {len(wheels)}")
    return wheels[0]


def patch_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel, "r") as zin:
        entries = zin.infolist()
        data = {entry.filename: zin.read(entry.filename) for entry in entries}

    text = data[SX126X_PATH].decode()
    for old, new in PATCHES:
        text = replace_once(text, old, new)
    data[SX126X_PATH] = text.encode()

    tmp = Path(tempfile.mkstemp(suffix=".whl")[1])
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for entry in entries:
                zout.writestr(entry, data[entry.filename])
        shutil.move(tmp, wheel)
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: {Path(argv[0]).name} WHEEL_DIR", file=sys.stderr)
        return 2
    patch_wheel(find_lorarf_wheel(Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
