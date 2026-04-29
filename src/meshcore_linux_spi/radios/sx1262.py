import asyncio
import logging
import math
import random
import time

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
        lbt=True,
        lbt_max_attempts=20,
        lbt_retry_delay=0.2,
        lbt_max_wait=4.0,
        tx_airtime_factor=1.0,
        flood_tx_delay_factor=0.5,
        direct_tx_delay_factor=0.2,
        tx_min_interval=0.0,
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
        self.lbt = bool(lbt)
        self.lbt_max_attempts = int(lbt_max_attempts)
        self.lbt_retry_delay = float(lbt_retry_delay)
        self.lbt_max_wait = float(lbt_max_wait)
        self.tx_airtime_factor = float(tx_airtime_factor)
        self.flood_tx_delay_factor = float(flood_tx_delay_factor)
        self.direct_tx_delay_factor = float(direct_tx_delay_factor)
        self.tx_min_interval = float(tx_min_interval)
        self.lora = SX126x()
        self.rx_callback = None
        self._rx_task = None
        self._initialized = False
        self._tx_lock = asyncio.Lock()
        self._last_tx_at = 0.0
        self._next_tx_at = 0.0
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
        self._request_rx()
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
                if not self.lora.available():
                    await asyncio.sleep(self.poll_interval)
                    continue

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
                elif status == self.lora.STATUS_HEADER_ERR:
                    logger.debug("RX header error")
                await asyncio.sleep(self.poll_interval)
            except Exception:
                logger.exception("RX loop error")
                await asyncio.sleep(1)

    async def send(self, data: bytes):
        if not self._initialized and not self.begin():
            raise RuntimeError("SX1262 init failed")
        async with self._tx_lock:
            if data:
                header = data[0]
                logger.debug(
                    "TX raw len=%d route=%d type=0x%02x data=%s",
                    len(data),
                    header & 0x03,
                    (header >> 2) & 0x0F,
                    data[:24].hex(),
                )
            estimated_airtime_ms = self._estimate_airtime_ms(len(data))
            await self._wait_for_tx_pacing(data, estimated_airtime_ms)
            if self.lbt:
                await self._wait_for_clear_channel()
            self.lora.beginPacket()
            self.lora.write(tuple(data), len(data))
            ok = self.lora.endPacket()
            if ok:
                deadline = time.monotonic() + 5.0
                while not self.lora._statusIrq and time.monotonic() < deadline:
                    await asyncio.sleep(self.poll_interval)
                ok = self.lora.status() == self.lora.STATUS_TX_DONE
            actual_airtime_ms = self._safe_airtime_ms(self.lora.transmitTime(), estimated_airtime_ms)
            self._request_rx()
            self._last_tx_at = time.monotonic()
            self._schedule_next_tx(actual_airtime_ms)
            return {"success": bool(ok), "airtime_ms": actual_airtime_ms}

    def _request_rx(self):
        self.lora.request(self.lora.RX_CONTINUOUS)

    async def _wait_for_tx_pacing(self, data: bytes, airtime_ms: float):
        delay = self._next_tx_at - time.monotonic()
        if delay > 0:
            logger.info("TX airtime guard delayed packet by %.1f seconds", delay)
            await asyncio.sleep(delay)

        factor = self._route_tx_delay_factor(data)
        if factor <= 0 or airtime_ms <= 0:
            return

        delay = random.uniform(0, airtime_ms * factor / 1000.0)
        if delay <= 0:
            return

        logger.info("TX random delay %.2f seconds (factor=%.2f)", delay, factor)
        await asyncio.sleep(delay)

    def _schedule_next_tx(self, airtime_ms: float):
        guard = max(self.tx_min_interval, max(0.0, airtime_ms) * self.tx_airtime_factor / 1000.0)
        if guard <= 0:
            return
        self._next_tx_at = max(self._next_tx_at, time.monotonic() + guard)

    def _route_tx_delay_factor(self, data: bytes):
        if not data:
            return 0.0
        route = data[0] & 0x03
        if route in (0x00, 0x01):
            return self.flood_tx_delay_factor
        return self.direct_tx_delay_factor

    def _estimate_airtime_ms(self, payload_len: int):
        sf = int(self.spreading_factor)
        bw = int(self.bandwidth)
        cr = max(1, min(4, int(self.coding_rate) - 4))
        if sf <= 0 or bw <= 0:
            return 0.0

        tsym = (2**sf) / bw
        low_data_rate_opt = 1 if sf >= 11 else 0
        crc = 1 if self.crc else 0
        numerator = 8 * payload_len - 4 * sf + 28 + 16 * crc
        denominator = 4 * (sf - 2 * low_data_rate_opt)
        payload_symbols = 8 + max(math.ceil(numerator / denominator) * (cr + 4), 0)
        preamble_symbols = self.preamble_length + 4.25
        return (preamble_symbols + payload_symbols) * tsym * 1000.0

    def _safe_airtime_ms(self, reported_ms: float, estimated_ms: float):
        if reported_ms and 0 < reported_ms <= max(estimated_ms * 5, 5000):
            return reported_ms
        if reported_ms:
            logger.warning(
                "Ignoring invalid LoRaRF transmitTime %.1f ms; estimated %.1f ms",
                reported_ms,
                estimated_ms,
            )
        return estimated_ms

    async def _wait_for_clear_channel(self):
        busy_checks = 0
        delay_total_ms = 0
        started = time.monotonic()
        max_attempts = max(self.lbt_max_attempts, 1)

        for attempt in range(max_attempts):
            try:
                if not await self._cad_channel_busy():
                    if busy_checks:
                        logger.info(
                            "LBT delayed TX by %d ms after %d busy CAD checks",
                            delay_total_ms,
                            busy_checks,
                        )
                    return
            except Exception:
                logger.exception("CAD check failed; transmitting without LBT")
                self._request_rx()
                return

            busy_checks += 1
            if attempt == max_attempts - 1 or time.monotonic() - started >= self.lbt_max_wait:
                logger.warning("LBT channel still busy after %d CAD checks; transmitting", busy_checks)
                return

            self._request_rx()
            delay_ms = int(self.lbt_retry_delay * 1000)
            delay_total_ms += delay_ms
            await asyncio.sleep(delay_ms / 1000)

    async def _cad_channel_busy(self):
        det_peak, det_min = self._cad_thresholds()
        cad_mask = self.lora.IRQ_CAD_DONE | self.lora.IRQ_CAD_DETECTED

        self.lora.setStandby(self.lora.STANDBY_RC)
        await asyncio.sleep(0.01)
        self.lora.clearIrqStatus(0x03FF)
        self.lora.setDioIrqParams(
            cad_mask,
            self.lora.IRQ_NONE,
            self.lora.IRQ_NONE,
            self.lora.IRQ_NONE,
        )
        self.lora.setCadParams(
            self.lora.CAD_ON_2_SYMB,
            det_peak,
            det_min,
            self.lora.CAD_EXIT_STDBY,
            0,
        )
        self.lora.setCad()

        deadline = time.monotonic() + 0.15
        irq = 0
        while time.monotonic() < deadline:
            irq = self.lora.getIrqStatus()
            if irq & self.lora.IRQ_CAD_DONE:
                break
            await asyncio.sleep(0.005)

        self.lora.clearIrqStatus(0x03FF)
        return bool(irq & self.lora.IRQ_CAD_DETECTED)

    def _cad_thresholds(self):
        return {
            7: (22, 10),
            8: (22, 10),
            9: (24, 10),
            10: (25, 10),
            11: (26, 10),
            12: (30, 10),
        }.get(self.spreading_factor, (22, 10))

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
