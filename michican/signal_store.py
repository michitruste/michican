"""Thread-safe time-series storage for decoded signal values."""
from __future__ import annotations

import threading

import numpy as np


class SignalBuffer:
    """Growable numpy buffer; drops the oldest half once `max_points` is reached."""

    def __init__(self, max_points: int) -> None:
        self.max_points = max_points
        self._t = np.empty(1024, dtype=np.float64)
        self._v = np.empty(1024, dtype=np.float64)
        self.n = 0

    def append(self, t: float, v: float) -> None:
        if self.n == len(self._t):
            if self.n >= self.max_points:
                keep = self.n // 2
                self._t[:keep] = self._t[self.n - keep:self.n]
                self._v[:keep] = self._v[self.n - keep:self.n]
                self.n = keep
            else:
                size = min(len(self._t) * 2, self.max_points)
                self._t = np.resize(self._t, size)
                self._v = np.resize(self._v, size)
        self._t[self.n] = t
        self._v[self.n] = v
        self.n += 1

    def window(self, t_start: float | None) -> tuple[np.ndarray, np.ndarray]:
        t = self._t[:self.n]
        i = 0 if t_start is None else int(np.searchsorted(t, t_start))
        return t[i:].copy(), self._v[i:self.n].copy()


class SignalStore:
    """Maps a signal key ("Message.Signal") to its SignalBuffer."""

    def __init__(self, max_points_per_signal: int = 200_000) -> None:
        self.max_points = max_points_per_signal
        self._lock = threading.Lock()
        self._buffers: dict[str, SignalBuffer] = {}
        self._latest: dict[str, tuple[float, float]] = {}

    def append_many(self, t: float, prefix: str, values: dict[str, float]) -> None:
        with self._lock:
            for name, v in values.items():
                key = f"{prefix}.{name}"
                buf = self._buffers.get(key)
                if buf is None:
                    buf = self._buffers[key] = SignalBuffer(self.max_points)
                buf.append(t, v)
                self._latest[key] = (t, v)

    def window(self, key: str, t_start: float | None):
        with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                return np.empty(0), np.empty(0)
            return buf.window(t_start)

    def latest(self, key: str) -> tuple[float, float] | None:
        return self._latest.get(key)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._buffers)

    def clear(self) -> None:
        with self._lock:
            self._buffers.clear()
            self._latest.clear()

    def export_csv(self, path: str, keys: list[str]) -> None:
        """Write the selected signals as long-format CSV: time,signal,value."""
        import csv
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_s", "signal", "value"])
            for key in keys:
                t, v = self.window(key, None)
                for ti, vi in zip(t, v):
                    w.writerow([f"{ti:.6f}", key, vi])
