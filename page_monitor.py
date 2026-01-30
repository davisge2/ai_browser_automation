"""
Page Load Timing and Window Title Monitoring.
Captures screen stability timing and window title changes.
"""
import time
import hashlib
import threading
import logging
import ctypes
from typing import Optional, List, Tuple

import mss
import mss.tools

logger = logging.getLogger(__name__)


class PageLoadTimer:
    """
    Measures page load time by capturing screen hashes at intervals
    and detecting when the screen stabilises (no change for a threshold period).
    """

    def __init__(self, poll_interval_ms: int = 200, stability_threshold_ms: int = 1000):
        self.poll_interval = poll_interval_ms / 1000.0
        self.stability_threshold = stability_threshold_ms / 1000.0

    def _screen_hash(self, sct) -> str:
        screenshot = sct.grab(sct.monitors[0])
        return hashlib.md5(screenshot.rgb).hexdigest()

    def measure_load_time(self, timeout_ms: int = 30000) -> int:
        """
        Measure how long until the screen stabilises.

        Returns load time in milliseconds.
        """
        timeout = timeout_ms / 1000.0
        start = time.time()

        with mss.mss() as sct:
            prev_hash = self._screen_hash(sct)
            last_change = start

            while True:
                elapsed = time.time() - start
                if elapsed >= timeout:
                    break

                time.sleep(self.poll_interval)
                current_hash = self._screen_hash(sct)

                if current_hash != prev_hash:
                    last_change = time.time()
                    prev_hash = current_hash

                if time.time() - last_change >= self.stability_threshold:
                    break

        load_time_ms = int((last_change - start) * 1000)
        return load_time_ms


class WindowTitleMonitor:
    """
    Polls the foreground window title at regular intervals and records
    title changes with timestamps.
    """

    def __init__(self, poll_interval_ms: int = 500):
        self.poll_interval = poll_interval_ms / 1000.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._history: List[Tuple[float, str]] = []
        self._lock = threading.Lock()

    def _get_foreground_title(self) -> str:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        except Exception:
            return ""

    def _poll_loop(self):
        prev_title = None
        while self._running:
            title = self._get_foreground_title()
            if title != prev_title:
                with self._lock:
                    self._history.append((time.time(), title))
                prev_title = title
            time.sleep(self.poll_interval)

    def start(self):
        if self._running:
            return
        self._running = True
        self._history = []
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> List[Tuple[float, str]]:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        with self._lock:
            return list(self._history)

    @property
    def history(self) -> List[Tuple[float, str]]:
        with self._lock:
            return list(self._history)
