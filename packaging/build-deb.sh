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
python3 "$PROJECT/packaging/patch-lorarf-wheel.py" "$WORK/opt/meshcore-linux-spi/wheels"

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
