"""CAN bus access via python-can: connect, background receive, send, cyclic send, logging."""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

import can

from .dbc import DbcManager, Decoded
from .signal_store import SignalStore

log = logging.getLogger(__name__)

INTERFACES = ["vector", "virtual", "pcan", "kvaser", "ixxat", "slcan", "socketcan"]
BITRATES = [125_000, 250_000, 500_000, 1_000_000]
DATA_BITRATES = [1_000_000, 2_000_000, 4_000_000, 5_000_000, 8_000_000]


@dataclass
class BusConfig:
    interface: str = "vector"
    channel: str = "0"          # vector: channel index (or comma list); virtual: any name
    bitrate: int = 500_000
    fd: bool = False
    data_bitrate: int = 2_000_000
    app_name: str = ""          # vector: "" -> use global channel index; e.g. "CANalyzer" -> app channel
    serial: int | None = None   # vector: pick a device by serial number
    listen_only: bool = False
    receive_own_messages: bool = True

    def bus_kwargs(self) -> dict:
        kw: dict = {"interface": self.interface,
                    "receive_own_messages": self.receive_own_messages}
        if self.interface == "virtual":
            kw["channel"] = self.channel or "vcan0"
            kw["preserve_timestamps"] = False
            return kw

        if self.interface == "vector":
            chans = [int(c) for c in str(self.channel).split(",") if c.strip()]
            kw["channel"] = chans[0] if len(chans) == 1 else chans
            kw["app_name"] = self.app_name or None
            if self.serial:
                kw["serial"] = self.serial
            if self.listen_only:
                kw["listen_only"] = True
        else:
            kw["channel"] = self.channel

        kw["bitrate"] = self.bitrate
        if self.fd:
            kw["fd"] = True
            kw["data_bitrate"] = self.data_bitrate
        return kw


def detect_channels(interface: str) -> list[dict]:
    """Return the channels python-can can find for `interface` (empty list if none)."""
    try:
        return list(can.detect_available_configs(interfaces=[interface]))
    except Exception as exc:  # driver not installed etc.
        log.warning("Channel detection failed for %s: %s", interface, exc)
        return []


def describe_channel(cfg: dict) -> str:
    if cfg.get("interface") == "vector":
        vcc = cfg.get("vector_channel_config")
        name = getattr(vcc, "name", f"hw ch {cfg.get('hw_channel')}")
        fd = " (FD)" if cfg.get("supports_fd") else ""
        return f"{cfg['channel_index']}: {name}  S/N {cfg.get('serial')}{fd}"
    return str(cfg.get("channel"))


@dataclass
class RxItem:
    msg: can.Message
    t: float                  # seconds since the first received frame
    decoded: Decoded | None


@dataclass
class CanManager:
    """Owns the python-can Bus plus the receive thread feeding the GUI."""

    dbc: DbcManager
    store: SignalStore
    rx_queue: "queue.SimpleQueue[RxItem]" = field(default_factory=queue.SimpleQueue)
    events: "queue.SimpleQueue[str]" = field(default_factory=queue.SimpleQueue)

    def __post_init__(self) -> None:
        self.bus: can.BusABC | None = None
        self.config: BusConfig | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._tasks: dict[str, can.broadcastmanager.CyclicSendTaskABC] = {}
        self._logger: can.Listener | None = None
        self._log_lock = threading.Lock()
        self.t0: float | None = None
        self.rx_count = 0
        self.tx_count = 0
        self.err_count = 0

    # ------------------------------------------------------------ connection
    @property
    def connected(self) -> bool:
        return self.bus is not None

    def connect(self, config: BusConfig) -> None:
        if self.bus is not None:
            self.disconnect()
        self.bus = can.Bus(**config.bus_kwargs())
        self.config = config
        self._stop.clear()
        self._thread = threading.Thread(target=self._rx_loop, name="can-rx", daemon=True)
        self._thread.start()
        log.info("Connected: %s", self.bus.channel_info)

    def disconnect(self) -> None:
        self.stop_all_periodic()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self.bus is not None:
            try:
                self.bus.shutdown()
            finally:
                self.bus = None
        self.stop_logging()

    def reset_time(self) -> None:
        self.t0 = None

    # ------------------------------------------------------------ receive
    def _rx_loop(self) -> None:
        bus = self.bus
        while not self._stop.is_set() and bus is not None:
            try:
                msg = bus.recv(timeout=0.05)
            except can.CanError as exc:
                self.events.put(f"Receive error: {exc}")
                time.sleep(0.2)
                continue
            if msg is None:
                continue
            self.process(msg)

    def process(self, msg: can.Message) -> RxItem:
        """Decode, store and queue one frame (also used for offline log import)."""
        if self.t0 is None:
            self.t0 = msg.timestamp
        t = msg.timestamp - self.t0
        if msg.is_error_frame:
            self.err_count += 1
        elif msg.is_rx:
            self.rx_count += 1
        else:
            self.tx_count += 1
        dec = self.dbc.decode(msg)
        if dec is not None:
            self.store.append_many(t, dec.message.name, dec.values)
        item = RxItem(msg, t, dec)
        self.rx_queue.put(item)
        with self._log_lock:
            if self._logger is not None:
                self._logger(msg)
        return item

    # ------------------------------------------------------------ transmit
    def _require_bus(self) -> can.BusABC:
        if self.bus is None:
            raise can.CanOperationError("Not connected")
        return self.bus

    def send(self, msg: can.Message) -> None:
        self._require_bus().send(msg, timeout=0.2)

    def start_periodic(self, key: str, msg: can.Message, period_s: float) -> None:
        """Start (or retarget) a cyclic transmission identified by `key`."""
        bus = self._require_bus()
        task = self._tasks.get(key)
        if task is not None and getattr(task, "period", None) == period_s:
            task.modify_data(msg)
            return
        self.stop_periodic(key)
        self._tasks[key] = bus.send_periodic(msg, period_s)

    def update_periodic(self, key: str, msg: can.Message) -> None:
        task = self._tasks.get(key)
        if task is not None:
            task.modify_data(msg)

    def stop_periodic(self, key: str) -> None:
        task = self._tasks.pop(key, None)
        if task is not None:
            task.stop()

    def stop_all_periodic(self) -> None:
        for key in list(self._tasks):
            self.stop_periodic(key)

    def periodic_keys(self) -> list[str]:
        return list(self._tasks)

    # ------------------------------------------------------------ logging
    def start_logging(self, path: str) -> None:
        """Record every frame to .blf / .asc / .csv / .log / .trc (by extension)."""
        with self._log_lock:
            if self._logger is not None:
                self._logger.stop()
            self._logger = can.Logger(path)

    def stop_logging(self) -> None:
        with self._log_lock:
            if self._logger is not None:
                self._logger.stop()
                self._logger = None

    @property
    def logging_active(self) -> bool:
        return self._logger is not None
