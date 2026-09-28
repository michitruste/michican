"""Demo traffic generator: plays every DBC message on a python-can *virtual* bus.

Lets you try the GUI without hardware: connect with interface "virtual", then
Tools > Start demo traffic. It opens a second virtual bus on the same channel.
"""
from __future__ import annotations

import math
import threading
import time

import can

from .dbc import DbcManager, signal_range


def _value(sig, t: float, phase: float) -> float:
    if sig.choices:
        keys = sorted(int(k) for k in sig.choices)
        return keys[int(t / 2 + phase) % len(keys)]
    lo, hi = signal_range(sig)
    if sig.length == 1:
        return float(int(t + phase) % 2)
    v = lo + (hi - lo) * (0.5 + 0.45 * math.sin(2 * math.pi * (t / 8 + phase)))
    if sig.scale == int(sig.scale) and sig.offset == int(sig.offset):
        v = round(v)
    return v


class DemoTraffic:
    def __init__(self, dbc: DbcManager, channel: str) -> None:
        self.dbc = dbc
        self.channel = channel
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="demo-traffic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        bus = can.Bus(interface="virtual", channel=self.channel)
        messages = self.dbc.messages()
        next_due = {m.name: 0.0 for m in messages}
        sent = {m.name: 0 for m in messages}
        t0 = time.perf_counter()
        try:
            while not self._stop.is_set():
                now = time.perf_counter() - t0
                for i, m in enumerate(messages):
                    if now < next_due[m.name]:
                        continue
                    next_due[m.name] = now + (m.cycle_time or 100) / 1000.0
                    values = {s.name: _value(s, now, 0.13 * j + 0.07 * i)
                              for j, s in enumerate(m.signals)}
                    # Multiplexed message: step through the mux ids frame by frame.
                    for s in m.signals:
                        if s.is_multiplexer:
                            ids = sorted({mid for x in m.signals for mid in (x.multiplexer_ids or [])
                                          if x.multiplexer_signal == s.name})
                            if ids:
                                values[s.name] = ids[sent[m.name] % len(ids)]
                    sent[m.name] += 1
                    values = {s.name: values[s.name] for s in self.dbc.active_signals(m, values)}
                    try:
                        data = m.encode(values, strict=False, padding=False)
                    except Exception:
                        continue
                    bus.send(can.Message(arbitration_id=m.frame_id,
                                         is_extended_id=m.is_extended_frame,
                                         is_fd=m.is_fd, data=data))
                time.sleep(0.001)  # high-resolution on Windows with Python >= 3.11
        finally:
            bus.shutdown()
