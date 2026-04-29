# meshcore-linux-spi

Run a MeshCore node/companion on a Raspberry Pi with a raw SPI LoRa radio.

This project helps bring up MeshCore on Linux SBCs, especially Raspberry Pi
boards. It exposes the MeshCore companion protocol over TCP, so the mobile app
can connect by IP address, while the Pi talks directly to an SX1276/RFM95 or
SX1262 LoRa radio over SPI.

The service handles radio TX/RX, node identity, contacts, channels and message
persistence.

Status: early but working prototype extracted from a Raspberry Pi Zero 2 W +
Adafruit RFM95/OLED Bonnet setup. The packaged install path is the recommended
way to run it.

## Supported Hardware

Tested:

- Raspberry Pi Zero 2 W
- Adafruit RFM95 + OLED Bonnet, using the SX1276/RFM95 backend

Implemented but not yet field-tested in this project:

- Waveshare SX1262 868/915M LoRaWAN HAT / Node Module for Raspberry Pi, using
  the SX1262 backend

Not suitable:

- UART "fixed-point transmission" LoRa HATs/modules. Those are serial modems
  with their own framing/protocol and do not expose raw MeshCore LoRa packets.

## Why SPI

MeshCore needs raw LoRa packets. The service must control packet bytes, route
type, payload type, path, transport codes, sync word, SF/BW/CR, CRC, preamble,
RSSI/SNR and continuous RX.

SPI SX1276/SX1262 modules expose the radio chip directly. UART LoRa modems
usually add their own address/framing/filtering and cannot be assumed to
preserve arbitrary MeshCore packets byte-for-byte.

## Architecture

- `meshcore_linux_spi.daemon`: TCP companion service, persistence, config
- `meshcore_linux_spi.radios.sx1276`: SX1276/RFM95 radio backend
- `meshcore_linux_spi.radios.sx1262`: SX1262 radio backend
- SQLite store under `/var/lib/meshcore-linux-spi/state.sqlite3`
- persistent MeshCore identity under `/var/lib/meshcore-linux-spi/identity.key`
- systemd unit: `meshcore-linux-spi.service`

The TCP companion service listens on port `5000` by default.

## Quick Install From deb

On a Raspberry Pi, install a release package:

```sh
# If another MeshCore companion service is already running, stop it first.
# For example:
# sudo systemctl disable --now meshcore-rfm95

sudo apt install ./meshcore-linux-spi_<version>_arm64.deb
sudo systemctl start meshcore-linux-spi
```

The package installs Python/system dependencies through apt, creates an internal
venv, installs bundled Python wheels, enables the systemd service, and creates:

- config: `/etc/default/meshcore-linux-spi`
- state: `/var/lib/meshcore-linux-spi`
- service: `meshcore-linux-spi.service`

Configure the board/profile if needed:

```sh
sudoedit /etc/default/meshcore-linux-spi
sudo systemctl restart meshcore-linux-spi
```

Check logs:

```sh
journalctl -u meshcore-linux-spi -f
```

Connect the MeshCore mobile client to:

```text
<raspberry-pi-ip>:5000
```

## Radio Profiles

Default profile:

```text
frequency: 869.618 MHz
bandwidth: 62.5 kHz
spreading factor: 8
coding rate: 8
sync word: 0x12
```

These defaults match the profile used during development. Change them in
`/etc/default/meshcore-linux-spi` if your local MeshCore region/profile differs.

## Board Presets

`MESHCORE_BOARD=adafruit-rfm95-bonnet`

```text
backend: sx1276
SPI: bus 0, CS 1
RESET: GPIO25
DIO0: GPIO22, currently unused by the polling SX1276 backend
```

`MESHCORE_BOARD=waveshare-sx1262-lorawan-hat`

```text
backend: sx1262
SPI: bus 0, CS 0
RESET: GPIO18
BUSY: GPIO20
DIO1/IRQ: GPIO16
TXEN: GPIO6
DIO2 RF switch: enabled
```

You can override any preset pin:

```sh
MESHCORE_SPI_BUS=0
MESHCORE_SPI_CS=0
MESHCORE_RESET_PIN=18
MESHCORE_BUSY_PIN=20
MESHCORE_IRQ_PIN=16
MESHCORE_TXEN_PIN=6
MESHCORE_DIO2_RF_SWITCH=true
```

## Raspberry Pi Setup

Enable SPI:

```sh
sudo raspi-config
```

Interface Options -> SPI -> enable.

Or add to `/boot/firmware/config.txt`:

```ini
dtparam=spi=on
```

If you install from a deb package, no manual Python package installation is
needed.

## Install From Source

```sh
git clone https://github.com/YOUR-USER/meshcore-linux-spi.git
cd meshcore-linux-spi
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install .
```

Run manually:

```sh
MESHCORE_BOARD=adafruit-rfm95-bonnet meshcore-linux-spi
```

For Waveshare:

```sh
MESHCORE_BOARD=waveshare-sx1262-lorawan-hat meshcore-linux-spi
```

## Manual systemd Install

This is mainly useful for development. For normal Raspberry Pi installs, prefer
the deb package above.

```sh
sudo mkdir -p /opt/meshcore-linux-spi
sudo cp -a src pyproject.toml README.md LICENSE /opt/meshcore-linux-spi/
sudo cp examples/meshcore-linux-spi.default /etc/default/meshcore-linux-spi
sudo cp packaging/systemd/meshcore-linux-spi.service /lib/systemd/system/

sudo python3 -m venv /opt/meshcore-linux-spi/venv
sudo /opt/meshcore-linux-spi/venv/bin/pip install -U pip
sudo /opt/meshcore-linux-spi/venv/bin/pip install /opt/meshcore-linux-spi

sudo systemctl daemon-reload
sudo systemctl enable --now meshcore-linux-spi
```

## Configuration

Edit:

```sh
sudoedit /etc/default/meshcore-linux-spi
sudo systemctl restart meshcore-linux-spi
```

Important variables:

```sh
MESHCORE_BIND=0.0.0.0
MESHCORE_PORT=5000
MESHCORE_STATE_DIR=/var/lib/meshcore-linux-spi
MESHCORE_BOARD=adafruit-rfm95-bonnet
MESHCORE_NODE_NAME=meshcore-linux-spi
MESHCORE_FREQ=869618000
MESHCORE_BW=62500
MESHCORE_SF=8
MESHCORE_CR=8
MESHCORE_TX_POWER=17
MESHCORE_LBT=true
MESHCORE_TX_AIRTIME_FACTOR=1
MESHCORE_FLOOD_TX_DELAY_FACTOR=0.5
MESHCORE_DIRECT_TX_DELAY_FACTOR=0.2
```

`MESHCORE_TX_AIRTIME_FACTOR` is the silent-time multiplier after each TX.
For EU-style 10% duty-cycle operation, set it to `9`.

## Persistence

State is stored in SQLite:

```text
/var/lib/meshcore-linux-spi/state.sqlite3
```

Tables:

- `kv`: preferences and custom values
- `contacts`: contacts and advert data
- `channels`: channel names and secrets
- `messages`: queued/persisted messages

Identity is stored separately:

```text
/var/lib/meshcore-linux-spi/identity.key
```

Back up both files if you want to preserve the node identity and state.

## Build a deb Package

On the target architecture, for example on a Raspberry Pi:

```sh
sudo apt install -y python3 python3-pip python3-venv python3-dev build-essential git ca-certificates dpkg-dev
./packaging/build-deb.sh
```

Or build an arm64 package from another machine with Docker:

```sh
docker run --rm --platform linux/arm64 \
  -v "$PWD:/work" -w /work debian:trixie-slim \
  bash -lc 'apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv python3-dev build-essential git ca-certificates dpkg-dev && ./packaging/build-deb.sh'
```

The script creates:

```text
dist/meshcore-linux-spi_<version>_<arch>.deb
```

The deb includes:

- project source under `/opt/meshcore-linux-spi`
- downloaded Python wheels under `/opt/meshcore-linux-spi/wheels`
- systemd unit
- `/etc/default/meshcore-linux-spi`
- postinst script that creates `/opt/meshcore-linux-spi/venv`

Install:

```sh
sudo apt install ./dist/meshcore-linux-spi_*.deb
sudo systemctl restart meshcore-linux-spi
```

## Security

The stock MeshCore TCP companion flow does not provide a robust password layer
for arbitrary IP exposure. Do not expose port `5000` directly to the internet.

Recommended:

- WireGuard or Tailscale
- firewall allowlist
- SSH tunnel

There is experimental `MESHCORE_PASSWORD` code in the daemon, but it may break
stock clients because they may not send auth before normal companion commands.

## Troubleshooting

Watch raw RX:

```sh
journalctl -u meshcore-linux-spi -f | grep 'RX raw'
```

If nearby nodes receive packets but this service shows no `RX raw`, suspect RF:

- antenna
- u.FL/SMA connection
- wrong board/pins
- Pi power supply noise
- bad placement

If `RX raw` exists but messages do not appear:

- `Unknown channel hash`: channel secret does not match
- `No contact found`: the sender contact is not known yet
- `RX MULTIPART`: multipart payload support is incomplete in the current Python stack

## References

- MeshCore: https://meshcore.co.uk/
- MeshCore companion protocol docs: https://docs.meshcore.io/companion_protocol/
- pymc_core on PyPI: https://pypi.org/project/pymc-core/
- LoRaRF on PyPI: https://pypi.org/project/LoRaRF/
- Adafruit RFM95W Bonnet: https://www.adafruit.com/product/4074
- Waveshare SX1262 LoRaWAN HAT product page: https://www.waveshare.com/sx1262-lorawan-hat.htm
- Waveshare SX1262 LoRaWAN/GNSS HAT wiki: https://www.waveshare.com/wiki/SX1262_XXXM_LoRaWAN/GNSS_HAT
- Semtech SX1261/2 datasheet: https://www.semtech.com/products/wireless-rf/lora-connect/sx1262

## Known Limitations

- SX1276/RFM95 backend is field-tested; SX1262 backend is implemented but needs
  hardware validation.
- Multipart messages are not fully handled yet.
- The current deb package is pragmatic and creates a Python venv in postinst.
- The project is not yet packaged as an official Debian Python package.
