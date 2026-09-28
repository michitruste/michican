"""DBC handling: load one or more DBC files, decode received frames, encode frames to send."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import can
import cantools
from cantools.database.can import Message as DbcMessage


def signal_range(sig) -> tuple[float, float]:
    """Physical [min, max] of a signal: DBC limits if given, else what the raw bits allow."""
    lo, hi = sig.minimum, sig.maximum
    if lo is not None and hi is not None and lo != hi:
        return float(lo), float(hi)
    if sig.is_signed:
        raw_lo, raw_hi = -(2 ** (sig.length - 1)), 2 ** (sig.length - 1) - 1
    else:
        raw_lo, raw_hi = 0, 2 ** sig.length - 1
    a, b = sig.offset + sig.scale * raw_lo, sig.offset + sig.scale * raw_hi
    return float(min(a, b)), float(max(a, b))


@dataclass
class Decoded:
    message: DbcMessage
    values: dict[str, float]  # raw numeric (scaled) values, choices NOT applied


class DbcManager:
    """Holds the messages of every loaded DBC, indexed by (frame_id, is_extended)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.files: list[str] = []
        self._by_id: dict[tuple[int, bool], DbcMessage] = {}
        self._by_name: dict[str, DbcMessage] = {}

    # ------------------------------------------------------------------ loading
    def load(self, path: str) -> int:
        """Load a DBC (or ARXML/KCD/SYM) file. Returns the number of messages added."""
        db = cantools.database.load_file(path, strict=False)
        with self._lock:
            for msg in db.messages:
                self._by_id[(msg.frame_id, msg.is_extended_frame)] = msg
                self._by_name[msg.name] = msg
            self.files.append(os.path.abspath(path))
        return len(db.messages)

    def clear(self) -> None:
        with self._lock:
            self.files.clear()
            self._by_id.clear()
            self._by_name.clear()

    # ------------------------------------------------------------------ lookup
    def messages(self) -> list[DbcMessage]:
        with self._lock:
            return sorted(self._by_name.values(), key=lambda m: m.frame_id)

    def by_name(self, name: str) -> DbcMessage | None:
        return self._by_name.get(name)

    def lookup(self, arbitration_id: int, is_extended: bool) -> DbcMessage | None:
        return self._by_id.get((arbitration_id, is_extended))

    # ------------------------------------------------------------------ codec
    def decode(self, frame: can.Message) -> Decoded | None:
        if frame.is_error_frame or frame.is_remote_frame:
            return None
        msg = self._by_id.get((frame.arbitration_id, frame.is_extended_id))
        if msg is None:
            return None
        try:
            values = msg.decode(
                bytes(frame.data), decode_choices=False, allow_truncated=True
            )
        except Exception:
            return None
        return Decoded(msg, {k: float(v) for k, v in values.items()
                             if isinstance(v, (int, float))})

    @staticmethod
    def encode(msg: DbcMessage, values: dict[str, float], is_fd: bool = False) -> can.Message:
        """Build a can.Message from signal values. Raises ValueError on bad input."""
        data = msg.encode(values, scaling=True, padding=False, strict=True)
        return can.Message(
            arbitration_id=msg.frame_id,
            is_extended_id=msg.is_extended_frame,
            data=data,
            is_fd=is_fd or msg.is_fd or len(data) > 8,
            bitrate_switch=is_fd or msg.is_fd,
        )

    @staticmethod
    def active_signals(msg: DbcMessage, values: dict[str, float]):
        """Signals that belong in `msg` given the current multiplexer values."""
        if not msg.is_multiplexed():
            return list(msg.signals)
        active = []
        for sig in msg.signals:
            if not sig.multiplexer_ids:
                active.append(sig)
            elif values.get(sig.multiplexer_signal) in sig.multiplexer_ids:
                active.append(sig)
        return active
