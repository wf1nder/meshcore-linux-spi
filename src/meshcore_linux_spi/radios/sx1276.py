import asyncio
import logging
import time

from LoRaRF import SX127x
from pymc_core.hardware.base import LoRaRadio


logger = logging.getLogger("SX1276Radio")


class SX1276Radio(LoRaRadio):
    def __init__(
        self,
        bus_id=0,
        cs_id=1,
        reset_pin=25,
        irq_pin=22,
        frequency=869_618_000,
        tx_power=17,
        spreading_factor=8,
        bandwidth=62_500,
        coding_rate=8,
        preamble_length=17,
        sync_word=0x12,
        crc=True,
        poll_interval=0.02,
        **_,
    ):
        self.bus_id = bus_id
        self.cs_id = cs_id
        self.frequency = frequency
        self.tx_power = tx_power
        self.spreading_factor = spreading_factor
        self.bandwidth = bandwidth
        self.coding_rate = coding_rate
        self.preamble_length = preamble_length
        self.sync_word = sync_word
        self.crc = crc
        self.poll_interval = poll_interval

        self.reset_pin = reset_pin
        self.irq_pin = irq_pin
        self.lora = SX127x()

        self.rx_callback = None
        self._rx_task = None
        self._initialized = False
        self._tx_lock = asyncio.Lock()
        self._last_rssi = 0
        self._last_snr = 0.0

    def begin(self):
        if self._initialized:
            return True
        # Polling avoids LoRaRF's RPi.GPIO edge-detection path, which is fragile
        # on some Pi kernels and conflicts with the old working service setup.
        if not self.lora.begin(self.bus_id, self.cs_id, self.reset_pin, -1):
            return False
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
        self.lora.request(self.lora.RX_CONTINUOUS)
        self._initialized = True
        self._ensure_rx_task()
        logger.info("SX1276/RFM95 initialized on SPI bus=%s cs=%s", self.bus_id, self.cs_id)
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
                if self.lora.wait(0.001):
                    status = self.lora.status()
                    if status == self.lora.STATUS_RX_DONE:
                        length = self.lora.available()
                        data = bytes(self.lora.read(length)) if length else b""
                        self._last_rssi = int(self.lora.packetRssi())
                        self._last_snr = float(self.lora.snr())
                        if data and self.rx_callback:
                            header = data[0]
                            route = header & 0x03
                            ptype = (header >> 2) & 0x0F
                            logger.info(
                                "RX raw len=%d route=%d type=0x%02x rssi=%d snr=%.1f data=%s",
                                len(data),
                                route,
                                ptype,
                                self._last_rssi,
                                self._last_snr,
                                data[:16].hex(),
                            )
                            self.rx_callback(data, self._last_rssi, self._last_snr)
                    elif status == self.lora.STATUS_CRC_ERR:
                        logger.debug("RX CRC error")
                await asyncio.sleep(self.poll_interval)
            except Exception:
                logger.exception("RX loop error")
                await asyncio.sleep(1)

    async def send(self, data: bytes):
        if not self._initialized and not self.begin():
            raise RuntimeError("RFM95 init failed")
        async with self._tx_lock:
            self.lora.standby()
            self.lora.beginPacket()
            self.lora.write(tuple(data), len(data))
            if not self.lora.endPacket():
                self.lora.request(self.lora.RX_CONTINUOUS)
                return None
            ok = await asyncio.to_thread(self.lora.wait, 10)
            self.lora.request(self.lora.RX_CONTINUOUS)
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
        self.tx_power = min(max(int(power_dbm), 2), 20)
        self.lora.setTxPower(self.tx_power, self.lora.TX_POWER_PA_BOOST)

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
