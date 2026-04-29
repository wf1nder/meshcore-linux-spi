from __future__ import annotations

import os


BOARD_PRESETS = {
    "adafruit-rfm95-bonnet": {
        "backend": "sx1276",
        "bus_id": 0,
        "cs_id": 1,
        "reset_pin": 25,
        "irq_pin": 22,
    },
    "waveshare-sx1262-lorawan-hat": {
        "backend": "sx1262",
        "bus_id": 0,
        "cs_id": 0,
        "reset_pin": 18,
        "busy_pin": 20,
        "irq_pin": 16,
        "txen_pin": 6,
        "rxen_pin": -1,
        "dio2_rf_switch": True,
    },
}


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)), 0)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def radio_defaults() -> dict:
    return {
        "frequency": _env_int("MESHCORE_FREQ", 869_618_000),
        "bandwidth": _env_int("MESHCORE_BW", 62_500),
        "spreading_factor": _env_int("MESHCORE_SF", 8),
        "coding_rate": _env_int("MESHCORE_CR", 8),
        "tx_power": _env_int("MESHCORE_TX_POWER", 17),
        "preamble_length": _env_int("MESHCORE_PREAMBLE", 17),
        "sync_word": _env_int("MESHCORE_SYNC_WORD", 0x12),
        "poll_interval": float(os.getenv("MESHCORE_POLL_INTERVAL", "0.02")),
        "lbt": _env_bool("MESHCORE_LBT", True),
        "lbt_max_attempts": _env_int("MESHCORE_LBT_MAX_ATTEMPTS", 20),
        "lbt_retry_delay": float(os.getenv("MESHCORE_LBT_RETRY_DELAY", "0.2")),
        "lbt_max_wait": float(os.getenv("MESHCORE_LBT_MAX_WAIT", "4")),
        "tx_airtime_factor": float(os.getenv("MESHCORE_TX_AIRTIME_FACTOR", "1")),
        "flood_tx_delay_factor": float(os.getenv("MESHCORE_FLOOD_TX_DELAY_FACTOR", "0.5")),
        "direct_tx_delay_factor": float(os.getenv("MESHCORE_DIRECT_TX_DELAY_FACTOR", "0.2")),
        "tx_min_interval": float(os.getenv("MESHCORE_TX_MIN_INTERVAL", "0")),
    }


def create_radio():
    board = os.getenv("MESHCORE_BOARD", "adafruit-rfm95-bonnet")
    preset = dict(BOARD_PRESETS.get(board, {}))
    if not preset:
        preset["backend"] = os.getenv("MESHCORE_RADIO", "sx1276")

    backend = os.getenv("MESHCORE_RADIO", preset.pop("backend"))
    params = radio_defaults()
    params.update(preset)
    params.update(
        {
            "bus_id": _env_int("MESHCORE_SPI_BUS", params.get("bus_id", 0)),
            "cs_id": _env_int("MESHCORE_SPI_CS", params.get("cs_id", 0)),
            "reset_pin": _env_int("MESHCORE_RESET_PIN", params.get("reset_pin", 25)),
            "irq_pin": _env_int("MESHCORE_IRQ_PIN", params.get("irq_pin", -1)),
        }
    )

    if backend == "sx1276":
        from .sx1276 import SX1276Radio

        return SX1276Radio(**params)
    if backend == "sx1262":
        from .sx1262 import SX1262Radio

        params.update(
            {
                "busy_pin": _env_int("MESHCORE_BUSY_PIN", params.get("busy_pin", 20)),
                "txen_pin": _env_int("MESHCORE_TXEN_PIN", params.get("txen_pin", -1)),
                "rxen_pin": _env_int("MESHCORE_RXEN_PIN", params.get("rxen_pin", -1)),
                "dio2_rf_switch": _env_bool(
                    "MESHCORE_DIO2_RF_SWITCH", params.get("dio2_rf_switch", False)
                ),
            }
        )
        return SX1262Radio(**params)
    raise ValueError(f"Unsupported MESHCORE_RADIO={backend!r}")
