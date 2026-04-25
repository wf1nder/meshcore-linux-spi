import asyncio
import logging

from LoRaRF import SX126x
from pymc_core.hardware.base import LoRaRadio


logger = logging.getLogger("SX1262Radio")


class SX1262Radio(LoRaRadio):
    def __init__(
        self,
        bus_id=0,
        cs_id=0,
        reset_pin=18,
        busy_pin=20,
        irq_pin=16,
        txen_pin=6,
        rxen_pin=-1,
        frequency=869_618_000,
        tx_power=17,
        spreading_factor=8,
        bandwidth=62_500,
        coding_rate=8,
        preamble_length=17,
        sync_word=0x12,
        crc=True,
        poll_interval=0.02,
        dio2_rf_switch=True,
        **_,
    ):
        self.bus_id = bus_id
        self.cs_id = cs_id
        self.reset_pin = reset_pin
        self.busy_pin = busy_pin
        self.irq_pin = irq_pin
        self.txen_pin = txen_pin
        self.rxen_pin = rxen_pin
        self.frequency = frequency
        self.tx_power = tx_power
        self.spreading_factor = spreading_factor
        self.bandwidth = bandwidth
        self.coding_rate = coding_rate
        self.preamble_length = preamble_length
        self.sync_word = sync_word
        self.crc = crc
        self.poll_interval = poll_interval
        self.dio2_rf_switch = dio2_rf_switch
        self.lora = SX126x()
        self.rx_callback = None
        self._rx_task = None
        self._initialized = False
        self._tx_lock = asyncio.Lock()
        self._last_rssi = 0
        self._last_snr = 0.0

    def begin(self):
        if self._initialized:
            return True
        if not self.lora.begin(
            bus=self.bus_id,
            cs=self.cs_id,
            reset=self.reset_pin,
            busy=self.busy_pin,
            irq=self.irq_pin,
            txen=self.txen_pin,
            rxen=self.rxen_pin,
        ):
            return False
        if self.dio2_rf_switch:
            self.lora.setDio2RfSwitch(True)
        self.configure_radio(
            frequency=self.frequency,
            bandwidth=self.bandwidth,
            spreading_factor=self.spreading_factor,
            coding_rate=self.coding_rate,
        )
        self.set_tx_power(self.tx_power)
        self.lora.setLoRaPacket(
            self.lora.HEADER_EXPLICIT,
            self.preamble_length,
            255,
            self.crc,
            False,
        )
        self.lora.request()
        self._initialized = True
        self._ensure_rx_task()
        logger.info(
            "SX1262 initialized on SPI bus=%s cs=%s reset=%s busy=%s irq=%s",
            self.bus_id,
            self.cs_id,
            self.reset_pin,
            self.busy_pin,
            self.irq_pin,
        )
        return True

    def set_rx_callback(self, callback):
        self.rx_callback = callback
        self._ensure_rx_task()

    def _ensure_rx_task(self):
        if not self._initialized:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._rx_task is None or self._rx_task.done():
            self._rx_task = loop.create_task(self._rx_loop())

    async def _rx_loop(self):
        while self._initialized:
            try:
                if self.lora.wait(1):
                    status = self.lora.status()
                    if status == self.lora.STATUS_RX_DONE:
                        length = self.lora.available()
                        data = bytes(self.lora.read(length)) if length else b""
                        self._last_rssi = int(self.lora.packetRssi())
                        self._last_snr = float(self.lora.snr())
                        if data and self.rx_callback:
                            header = data[0]
                            logger.info(
                                "RX raw len=%d route=%d type=0x%02x rssi=%d snr=%.1f data=%s",
                                len(data),
                                header & 0x03,
                                (header >> 2) & 0x0F,
                                self._last_rssi,
                                self._last_snr,
                                data[:16].hex(),
                            )
                            self.rx_callback(data, self._last_rssi, self._last_snr)
                    elif status == self.lora.STATUS_CRC_ERR:
                        logger.debug("RX CRC error")
                    self.lora.request()
                await asyncio.sleep(self.poll_interval)
            except Exception:
                logger.exception("RX loop error")
                await asyncio.sleep(1)

    async def send(self, data: bytes):
        if not self._initialized and not self.begin():
            raise RuntimeError("SX1262 init failed")
        async with self._tx_lock:
            self.lora.beginPacket()
            self.lora.write(tuple(data), len(data))
            ok = self.lora.endPacket()
            self.lora.request()
            return {"success": bool(ok), "airtime_ms": self.lora.transmitTime()}

    async def wait_for_rx(self):
        fut = asyncio.get_running_loop().create_future()

        def once(data, *_):
            if not fut.done():
                fut.set_result(data)

        old = self.rx_callback
        self.rx_callback = once
        try:
            return await fut
        finally:
            self.rx_callback = old

    def configure_radio(self, frequency=None, bandwidth=None, spreading_factor=None, coding_rate=None):
        if frequency is not None:
            self.frequency = frequency
            self.lora.setFrequency(frequency)
        if spreading_factor is not None:
            self.spreading_factor = spreading_factor
        if bandwidth is not None:
            self.bandwidth = bandwidth
        if coding_rate is not None:
            self.coding_rate = coding_rate
        self.lora.setLoRaModulation(
            self.spreading_factor,
            self.bandwidth,
            self.coding_rate,
            self.spreading_factor >= 11,
        )
        self.lora.setSyncWord(self.sync_word)

    def set_tx_power(self, power_dbm):
        self.tx_power = min(max(int(power_dbm), 2), 22)
        self.lora.setTxPower(self.tx_power)

    def sleep(self):
        self._initialized = False
        self.lora.sleep()

    def get_last_rssi(self):
        return self._last_rssi

    def get_last_snr(self):
        return self._last_snr

    def check_radio_health(self):
        self._ensure_rx_task()
        return self._rx_task is not None and not self._rx_task.done()
