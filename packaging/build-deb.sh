#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 - "$PROJECT/pyproject.toml" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
print(re.search(r'^version = "([^"]+)"', text, re.M).group(1))
PY
)"
ARCH="$(dpkg --print-architecture)"
PKG="meshcore-linux-spi_${VERSION}_${ARCH}"
WORK="$PROJECT/dist/$PKG"

rm -rf "$WORK"
mkdir -p \
  "$WORK/DEBIAN" \
  "$WORK/opt/meshcore-linux-spi" \
  "$WORK/etc/default" \
  "$WORK/lib/systemd/system" \
  "$WORK/var/lib/meshcore-linux-spi"

mkdir -p "$WORK/opt/meshcore-linux-spi/src"
cp -a "$PROJECT/src/meshcore_linux_spi" "$WORK/opt/meshcore-linux-spi/src/"
find "$WORK/opt/meshcore-linux-spi/src" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$WORK/opt/meshcore-linux-spi/src" -type f -name '*.py[co]' -delete
cp "$PROJECT/pyproject.toml" "$PROJECT/README.md" "$PROJECT/LICENSE" \
  "$WORK/opt/meshcore-linux-spi/"
cp "$PROJECT/examples/meshcore-linux-spi.default" "$WORK/etc/default/meshcore-linux-spi"
cp "$PROJECT/packaging/systemd/meshcore-linux-spi.service" \
  "$WORK/lib/systemd/system/meshcore-linux-spi.service"

python3 -m pip wheel --wheel-dir "$WORK/opt/meshcore-linux-spi/wheels" "$PROJECT"
python3 - "$WORK/opt/meshcore-linux-spi/wheels" <<'PY'
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

wheel_dir = Path(sys.argv[1])
wheel = next(wheel_dir.glob("LoRaRF-*.whl"))
with zipfile.ZipFile(wheel, "r") as zin:
    entries = zin.infolist()
    data = {entry.filename: zin.read(entry.filename) for entry in entries}

path = "LoRaRF/SX126x.py"
text = data[path].decode()

replacements = [
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

for old, new in replacements:
    if old not in text:
        raise SystemExit(f"LoRaRF SX126x patch pattern not found: {old[:80]!r}")
    text = text.replace(old, new, 1)

data[path] = text.encode()
tmp = Path(tempfile.mkstemp(suffix=".whl")[1])
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    for entry in entries:
        zout.writestr(entry, data[entry.filename])
shutil.move(tmp, wheel)
PY

cat > "$WORK/DEBIAN/control" <<EOF
Package: meshcore-linux-spi
Version: $VERSION
Section: net
Priority: optional
Architecture: $ARCH
Maintainer: meshcore-linux-spi contributors
Depends: python3, python3-venv, python3-pip, python3-rpi-lgpio
Description: MeshCore TCP companion service for Linux SPI LoRa radios
 A systemd service that exposes a MeshCore companion TCP endpoint and talks
 to raw SPI SX1276/RFM95 or SX1262 LoRa radios.
EOF

cat > "$WORK/DEBIAN/conffiles" <<'EOF'
/etc/default/meshcore-linux-spi
EOF

cat > "$WORK/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
python3 -m venv --system-site-packages /opt/meshcore-linux-spi/venv
/opt/meshcore-linux-spi/venv/bin/pip install \
  --force-reinstall --no-index --find-links /opt/meshcore-linux-spi/wheels \
  meshcore-linux-spi
/opt/meshcore-linux-spi/venv/bin/pip uninstall -y RPi.GPIO >/dev/null 2>&1 || true
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
  systemctl enable meshcore-linux-spi.service || true
fi
exit 0
EOF

cat > "$WORK/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "deconfigure" ]; then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop meshcore-linux-spi.service || true
    systemctl disable meshcore-linux-spi.service || true
  fi
fi
exit 0
EOF

chmod 0755 "$WORK/DEBIAN/postinst" "$WORK/DEBIAN/prerm"
dpkg-deb --build "$WORK" "$PROJECT/dist/${PKG}.deb"
echo "$PROJECT/dist/${PKG}.deb"
