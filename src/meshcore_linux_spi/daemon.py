#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import shutil
import sqlite3
import struct
import time
from dataclasses import asdict
from pathlib import Path

from pymc_core import LocalIdentity
from pymc_core.companion import frame_server as frame_server_module
from pymc_core.companion import ADV_TYPE_CHAT, CompanionFrameServer, CompanionRadio
from pymc_core.companion.constants import (
    CMD_APP_START,
    CMD_DEVICE_QUERY,
    CMD_SET_DEVICE_PIN,
    ERR_CODE_BAD_STATE,
    FRAME_INBOUND_PREFIX,
    MAX_FRAME_SIZE,
    DEFAULT_PUBLIC_CHANNEL_SECRET,
    RESP_CODE_DEVICE_INFO,
)
from pymc_core.companion.models import Channel, Contact, NodePrefs, QueuedMessage

from .radios import create_radio
from .radios.factory import radio_defaults


def _build_single_advert_push_frame(contact):
    short, full = _ORIGINAL_BUILD_ADVERT_PUSH_FRAMES(contact)
    return short, None


_ORIGINAL_BUILD_ADVERT_PUSH_FRAMES = frame_server_module._build_advert_push_frames
frame_server_module._build_advert_push_frames = _build_single_advert_push_frame


STATE_DIR = Path(os.getenv("MESHCORE_STATE_DIR", "/var/lib/meshcore-linux-spi"))
DB_FILE = STATE_DIR / "state.sqlite3"
KEY_FILE = STATE_DIR / "identity.key"

_RADIO_DEFAULTS = radio_defaults()
DEFAULT_RADIO = {
    "frequency_hz": _RADIO_DEFAULTS["frequency"],
    "bandwidth_hz": _RADIO_DEFAULTS["bandwidth"],
    "spreading_factor": _RADIO_DEFAULTS["spreading_factor"],
    "coding_rate": _RADIO_DEFAULTS["coding_rate"],
    "tx_power_dbm": _RADIO_DEFAULTS["tx_power"],
}
ALLOWED_RADIO_PROFILES = {
    (
        DEFAULT_RADIO["frequency_hz"],
        DEFAULT_RADIO["bandwidth_hz"],
        DEFAULT_RADIO["spreading_factor"],
        DEFAULT_RADIO["coding_rate"],
    )
}


def _periodic_advert_interval_sec() -> int:
    try:
        return max(0, int(os.getenv("MESHCORE_ADVERT_INTERVAL_SEC", "43200")))
    except ValueError:
        logging.warning("Bad MESHCORE_ADVERT_INTERVAL_SEC value; using 43200")
        return 43200


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contacts (
                public_key TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                adv_type INTEGER NOT NULL DEFAULT 0,
                flags INTEGER NOT NULL DEFAULT 0,
                out_path_len INTEGER NOT NULL DEFAULT -1,
                out_path TEXT NOT NULL DEFAULT '',
                last_advert_timestamp INTEGER NOT NULL DEFAULT 0,
                lastmod INTEGER NOT NULL DEFAULT 0,
                gps_lat REAL NOT NULL DEFAULT 0,
                gps_lon REAL NOT NULL DEFAULT 0,
                sync_since INTEGER NOT NULL DEFAULT 0,
                last_advert_packet TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS channels (
                idx INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                secret TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_key TEXT NOT NULL DEFAULT '',
                txt_type INTEGER NOT NULL DEFAULT 0,
                timestamp INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL DEFAULT '',
                is_channel INTEGER NOT NULL DEFAULT 0,
                channel_idx INTEGER NOT NULL DEFAULT 0,
                path_len INTEGER NOT NULL DEFAULT 0,
                snr REAL NOT NULL DEFAULT 0,
                rssi INTEGER NOT NULL DEFAULT 0,
                packet_hash TEXT,
                delivered INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
            CREATE INDEX IF NOT EXISTS messages_undelivered_idx
                ON messages(delivered, id);
            """
        )
        self.db.commit()

    def get_json(self, key: str, default):
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            logging.warning("Bad JSON in state key %s", key)
            return default

    def set_json(self, key: str, value):
        self.db.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
        )
        self.db.commit()

    def load_prefs(self) -> NodePrefs:
        data = self.get_json("prefs", {})
        prefs = NodePrefs()
        for key, value in data.items():
            if hasattr(prefs, key):
                setattr(prefs, key, value)
        if not data:
            prefs.node_name = os.getenv("MESHCORE_NODE_NAME", "wf-alm-mc-node")
            prefs.frequency_hz = DEFAULT_RADIO["frequency_hz"]
            prefs.bandwidth_hz = DEFAULT_RADIO["bandwidth_hz"]
            prefs.spreading_factor = DEFAULT_RADIO["spreading_factor"]
            prefs.coding_rate = DEFAULT_RADIO["coding_rate"]
            prefs.tx_power_dbm = DEFAULT_RADIO["tx_power_dbm"]
            self.save_prefs(prefs)
        return prefs

    def save_prefs(self, prefs: NodePrefs):
        self.set_json("prefs", asdict(prefs))

    def load_custom_vars(self):
        return self.get_json("custom_vars", {})

    def save_custom_vars(self, vars_dict):
        self.set_json("custom_vars", dict(vars_dict))

    def load_flood_scope(self):
        value = self.get_json("flood_scope", "")
        return bytes.fromhex(value) if value else None

    def save_flood_scope(self, value: bytes | None):
        self.set_json("flood_scope", value.hex() if value else "")

    def load_contacts(self):
        rows = self.db.execute("SELECT * FROM contacts ORDER BY lastmod DESC").fetchall()
        return [Contact.from_dict(dict(row)) for row in rows]

    def save_contact(self, contact: Contact):
        logging.info(
            "Persist contact: name=%r key=%s type=%s lastmod=%s",
            contact.name,
            contact.public_key.hex(),
            contact.adv_type,
            contact.lastmod,
        )
        self.db.execute(
            """
            INSERT INTO contacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(public_key) DO UPDATE SET
                name=excluded.name,
                adv_type=excluded.adv_type,
                flags=excluded.flags,
                out_path_len=excluded.out_path_len,
                out_path=excluded.out_path,
                last_advert_timestamp=excluded.last_advert_timestamp,
                lastmod=excluded.lastmod,
                gps_lat=excluded.gps_lat,
                gps_lon=excluded.gps_lon,
                sync_since=excluded.sync_since,
                last_advert_packet=excluded.last_advert_packet
            """,
            (
                contact.public_key.hex(),
                contact.name,
                int(contact.adv_type),
                int(contact.flags),
                int(contact.out_path_len),
                contact.out_path.hex() if contact.out_path else "",
                int(contact.last_advert_timestamp),
                int(contact.lastmod or time.time()),
                float(contact.gps_lat),
                float(contact.gps_lon),
                int(contact.sync_since),
                contact.last_advert_packet.hex() if contact.last_advert_packet else "",
            ),
        )
        self.db.commit()

    def save_contacts(self, contacts):
        self.db.execute("DELETE FROM contacts")
        for contact in contacts:
            self.save_contact(contact)
        self.db.commit()

    def delete_contact(self, public_key: bytes):
        self.db.execute("DELETE FROM contacts WHERE public_key = ?", (public_key.hex(),))
        self.db.commit()

    def load_channels(self):
        rows = self.db.execute("SELECT * FROM channels ORDER BY idx").fetchall()
        return [(int(row["idx"]), Channel(row["name"], bytes.fromhex(row["secret"]))) for row in rows]

    def save_channels(self, channels):
        self.db.execute("DELETE FROM channels")
        for idx, channel in channels:
            if channel is None:
                continue
            self.db.execute(
                "INSERT INTO channels(idx, name, secret) VALUES(?, ?, ?)",
                (idx, channel.name, channel.secret.hex()),
            )
        self.db.commit()

    def add_message(self, msg: dict):
        packet_hash = msg.get("packet_hash")
        if packet_hash:
            exists = self.db.execute(
                "SELECT id FROM messages WHERE packet_hash = ? LIMIT 1", (packet_hash,)
            ).fetchone()
            if exists:
                return
        sender = msg.get("sender_key", b"")
        if isinstance(sender, bytes):
            sender = sender.hex()
        self.db.execute(
            """
            INSERT INTO messages(
                sender_key, txt_type, timestamp, text, is_channel, channel_idx,
                path_len, snr, rssi, packet_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sender or "",
                int(msg.get("txt_type", 0)),
                int(msg.get("timestamp", time.time())),
                msg.get("text", "") or "",
                1 if msg.get("is_channel") else 0,
                int(msg.get("channel_idx", 0)),
                int(msg.get("path_len", 0)),
                float(msg.get("snr") or 0.0),
                int(msg.get("rssi") or 0),
                packet_hash,
            ),
        )
        self.db.commit()

    def mark_delivered_like(self, msg: QueuedMessage):
        sender = msg.sender_key.hex() if msg.sender_key else ""
        self.db.execute(
            """
            UPDATE messages SET delivered = 1
            WHERE id = (
                SELECT id FROM messages
                WHERE delivered = 0 AND sender_key = ? AND timestamp = ?
                  AND text = ? AND is_channel = ? AND channel_idx = ?
                ORDER BY id LIMIT 1
            )
            """,
            (sender, msg.timestamp, msg.text, 1 if msg.is_channel else 0, msg.channel_idx),
        )
        self.db.commit()

    def pop_message(self):
        row = self.db.execute(
            "SELECT * FROM messages WHERE delivered = 0 ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        self.db.execute("UPDATE messages SET delivered = 1 WHERE id = ?", (row["id"],))
        self.db.commit()
        return QueuedMessage(
            sender_key=bytes.fromhex(row["sender_key"]) if row["sender_key"] else b"",
            txt_type=int(row["txt_type"]),
            timestamp=int(row["timestamp"]),
            text=row["text"],
            is_channel=bool(row["is_channel"]),
            channel_idx=int(row["channel_idx"]),
            path_len=int(row["path_len"]),
            snr=float(row["snr"]),
            rssi=int(row["rssi"]),
        )

    def storage_stats(self):
        total, used, free = shutil.disk_usage(self.path.parent)
        return (used // 1024, total // 1024)


class PersistentCompanionRadio(CompanionRadio):
    def __init__(self, *args, state: StateStore, **kwargs):
        self.state = state
        super().__init__(*args, initial_contacts=state.load_contacts(), **kwargs)
        prefs = state.load_prefs()
        prefs.frequency_hz = DEFAULT_RADIO["frequency_hz"]
        prefs.bandwidth_hz = DEFAULT_RADIO["bandwidth_hz"]
        prefs.spreading_factor = DEFAULT_RADIO["spreading_factor"]
        prefs.coding_rate = DEFAULT_RADIO["coding_rate"]
        prefs.tx_power_dbm = DEFAULT_RADIO["tx_power_dbm"]
        self.prefs = prefs
        self.node.node_name = prefs.node_name
        self.node.radio_config = asdict(prefs)
        self._custom_vars = state.load_custom_vars()
        flood_scope = state.load_flood_scope()
        if flood_scope:
            self.set_flood_scope(flood_scope)
        stored_channels = list(state.load_channels())
        if not stored_channels:
            stored_channels = [(0, Channel(name="Public", secret=DEFAULT_PUBLIC_CHANNEL_SECRET))]
            state.save_channels(stored_channels)
            logging.getLogger("meshcore-rfm95").info("Seeded default Public channel")
        for idx, channel in stored_channels:
            self.channels.set(idx, channel)

    def _save_prefs(self):
        if hasattr(self, "state"):
            self.state.save_prefs(self.prefs)

    async def _apply_advert_to_stores(self, contact, *args, **kwargs):
        local_key = self.get_public_key()
        if contact.public_key == local_key:
            logging.info("Ignored own advert: name=%r key=%s", contact.name, local_key.hex())
            return None
        applied = await super()._apply_advert_to_stores(contact, *args, **kwargs)
        if applied is not None:
            self.state.save_contact(applied)
        return applied

    def set_advert_name(self, name: str) -> None:
        super().set_advert_name(name)
        self.node.node_name = self.prefs.node_name
        self.node.radio_config = asdict(self.prefs)
        logging.info("Advert name set to %r", self.prefs.node_name)

    def set_custom_var(self, name: str, value: str) -> bool:
        ok = super().set_custom_var(name, value)
        if ok:
            self.state.save_custom_vars(self._custom_vars)
        return ok


class PersistentFrameServer(CompanionFrameServer):
    def __init__(self, *args, state: StateStore, password: str = "", **kwargs):
        self.state = state
        self.password = password
        self.device_pin = int(os.getenv("MESHCORE_PIN", "0") or "0")
        super().__init__(*args, **kwargs)

    def _setup_push_callbacks(self) -> None:
        super()._setup_push_callbacks()
        self.bridge.on_rx_log_data(self.push_rx_raw)

    async def _persist_companion_message(self, msg_dict: dict) -> None:
        self.state.add_message(msg_dict)

    def _sync_next_from_persistence(self):
        return self.state.pop_message()

    async def _persist_contact(self, contact) -> None:
        if isinstance(contact, Contact):
            self.state.save_contact(contact)
        else:
            logging.warning("Cannot persist non-Contact object: %r", contact)

    async def _save_contacts(self) -> None:
        self.state.save_contacts(self.bridge.contacts.get_all())

    async def _save_channels(self) -> None:
        max_channels = getattr(self.bridge.channels, "max_channels", 40)
        self.state.save_channels((idx, self.bridge.get_channel(idx)) for idx in range(max_channels))

    def _get_batt_and_storage(self):
        used_kb, total_kb = self.state.storage_stats()
        return (0, used_kb, total_kb)

    def _write_device_info_with_pin(self):
        # Base implementation hardcodes ble_pin=0; keep its wire layout but expose our PIN.
        prefs = self.bridge.get_self_info()
        max_contacts = getattr(getattr(self.bridge, "contacts", None), "max_contacts", 1000)
        max_channels = getattr(getattr(self.bridge, "channels", None), "max_channels", 40)
        frame = (
            bytes([RESP_CODE_DEVICE_INFO, 10, min(max_contacts // 2, 255), min(max_channels, 255)])
            + struct.pack("<I", self.device_pin)
            + self._build_date_bytes
            + self._model_bytes
            + self._version_bytes
            + bytes([getattr(prefs, "client_repeat", 0) & 0xFF, getattr(prefs, "path_hash_mode", 0) & 0xFF])
        )
        self._write_frame(frame)

    async def _cmd_device_query(self, data: bytes) -> None:
        if self.device_pin:
            self._write_device_info_with_pin()
        else:
            await super()._cmd_device_query(data)

    async def _cmd_set_radio_params(self, data: bytes) -> None:
        logging.info("Client radio params raw: %s", data.hex())
        if len(data) < 10:
            self._write_err(ERR_CODE_BAD_STATE)
            return
        freq_khz = struct.unpack_from("<I", data, 0)[0]
        bw = struct.unpack_from("<I", data, 4)[0]
        sf = data[8]
        cr = data[9]
        profile = (freq_khz * 1000, bw, sf, cr)
        if profile not in ALLOWED_RADIO_PROFILES:
            logging.warning("Rejected unsupported radio params: %s", profile)
            self._write_ok()
            return
        logging.info("Accepted radio params: %s", profile)
        ok = self.bridge.set_radio_params(*profile)
        self.state.save_prefs(self.bridge.prefs)
        self._write_ok() if ok else self._write_err(ERR_CODE_BAD_STATE)

    async def _cmd_set_tx_power(self, data: bytes) -> None:
        logging.info("Client TX power raw: %s", data.hex())
        if len(data) < 1:
            self._write_err(ERR_CODE_BAD_STATE)
            return
        power = max(2, min(20, struct.unpack_from("<b", data, 0)[0]))
        ok = self.bridge.set_tx_power(power)
        self.state.save_prefs(self.bridge.prefs)
        self._write_ok() if ok else self._write_err(ERR_CODE_BAD_STATE)

    async def _cmd_set_flood_scope(self, data: bytes) -> None:
        await super()._cmd_set_flood_scope(data)
        self.state.save_flood_scope(getattr(self.bridge, "_flood_transport_key", None))

    async def _cmd_send_channel_txt_msg(self, data: bytes) -> None:
        if len(data) >= 6:
            txt_type = data[0]
            channel_idx = data[1]
            text = data[6:].decode("utf-8", errors="replace").rstrip("\x00")
            channel = self.bridge.get_channel(channel_idx)
            channel_name = getattr(channel, "name", None) if channel is not None else None
            logging.info(
                "Client channel send: channel=%s name=%r type=%d chars=%d",
                channel_idx,
                channel_name,
                txt_type,
                len(text),
            )
            logging.debug("Client channel send text: %r", text)
        await super()._cmd_send_channel_txt_msg(data)

    async def _cmd_import_contact(self, data: bytes) -> None:
        await super()._cmd_import_contact(data)
        await self._save_contacts()

    async def _cmd_set_advert_name(self, data: bytes) -> None:
        await super()._cmd_set_advert_name(data)
        try:
            asyncio.create_task(self.bridge.advertise(flood=True))
        except Exception as e:
            logging.warning("Failed to schedule advert after rename: %s", e)

    async def _cmd_sync_next_message(self, data: bytes) -> None:
        msg = self.bridge.sync_next_message()
        if msg is not None:
            self.state.mark_delivered_like(msg)
            self._write_frame(self._build_message_frame(msg))
            return
        msg = await asyncio.to_thread(self._sync_next_from_persistence)
        if msg is None:
            from pymc_core.companion.constants import RESP_CODE_NO_MORE_MESSAGES

            self._write_frame(bytes([RESP_CODE_NO_MORE_MESSAGES]))
            return
        self._write_frame(self._build_message_frame(msg))

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if not self.password:
            return await super()._handle_client(reader, writer)
        try:
            prefix = await asyncio.wait_for(reader.readexactly(1), timeout=10)
            if prefix[0] != FRAME_INBOUND_PREFIX:
                writer.close()
                await writer.wait_closed()
                return
            len_bytes = await reader.readexactly(2)
            frame_len = struct.unpack("<H", len_bytes)[0]
            if frame_len > MAX_FRAME_SIZE:
                writer.close()
                await writer.wait_closed()
                return
            payload = await reader.readexactly(frame_len)
            cmd = payload[0] if payload else -1
            supplied = payload[1:].decode("utf-8", errors="replace").rstrip("\x00")
            if cmd != CMD_SET_DEVICE_PIN or supplied != self.password:
                logging.warning("Rejected TCP client without valid password")
                writer.close()
                await writer.wait_closed()
                return
            return await super()._handle_client(reader, writer)
        except Exception:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

def load_identity():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        return LocalIdentity(seed=KEY_FILE.read_bytes())
    identity = LocalIdentity()
    KEY_FILE.write_bytes(identity.get_signing_key_bytes())
    KEY_FILE.chmod(0o600)
    return identity


async def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    state = StateStore(DB_FILE)
    prefs = state.load_prefs()
    radio = create_radio()
    if not radio.begin():
        raise RuntimeError("LoRa radio initialization failed")

    identity = load_identity()
    companion = PersistentCompanionRadio(
        radio=radio,
        identity=identity,
        node_name=prefs.node_name,
        adv_type=ADV_TYPE_CHAT,
        radio_config=DEFAULT_RADIO,
        state=state,
    )
    await companion.start()

    server = PersistentFrameServer(
        companion,
        f"{identity.get_address_bytes()[0]:02x}",
        port=int(os.getenv("MESHCORE_PORT", "5000")),
        bind_address=os.getenv("MESHCORE_BIND", "0.0.0.0"),
        device_model=os.getenv("MESHCORE_DEVICE_MODEL", "MeshCore Linux SPI"),
        client_idle_timeout_sec=None,
        state=state,
        password=os.getenv("MESHCORE_PASSWORD", ""),
    )
    await server.start()

    logging.info(
        "MeshCore Linux SPI companion listening on %s:%s pubkey=%s db=%s",
        server.bind_address,
        server.port,
        identity.get_public_key().hex(),
        DB_FILE,
    )
    advert_interval_sec = _periodic_advert_interval_sec()
    if advert_interval_sec:
        logging.info("Periodic flood adverts enabled: every %s seconds", advert_interval_sec)
    else:
        logging.info("Periodic flood adverts disabled")

    try:
        if advert_interval_sec:
            await companion.advertise(flood=True)
        while True:
            await asyncio.sleep(advert_interval_sec or 3600)
            if advert_interval_sec:
                await companion.advertise(flood=True)
    finally:
        await server.stop()
        await companion.stop()


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
