"""Keep native camera stderr away from the live ANSI screen."""
from __future__ import annotations

import os
import re
import select
import sys
import threading
import time
from typing import TextIO


_LIMIT = 64 * 1024
_BENIGN = (
    re.compile(r'(?:INFO: )?Created TensorFlow Lite XNNPACK delegate for CPU\.'),
    re.compile(r'(?:WARNING: )?All log messages before absl::InitializeLog\(\) '
               r'is called are written to STDERR'),
    re.compile(r'(?:W\d{4}\s+\S+\s+\d+\s+inference_feedback_manager\.cc:\d+\]\s+)?'
               r'Feedback manager requires a model with a single signature inference\. '
               r'Disabling support for feedback tensors\.'),
    re.compile(r'I\d{4}\s+\S+\s+\d+\s+gl_context\.cc:\d+\]\s+'
               r'GL version: \d[^\r\n]*'),
)


class NativeDiagnostics:
    """Capture process-wide FD 2 until the camera and alternate screen close.

    A drain thread prevents native writers from filling the pipe. Only the last
    64 KiB remain in memory; unexpected diagnostics replay after restoration.
    Use one context at a time, around the entire camera session and its cleanup.
    """

    def __init__(self, enabled: bool = True, output: TextIO | None = None):
        self.enabled = enabled
        self.output = sys.stderr if output is None else output
        self._saved = self._reader = None
        self._thread = None
        self._stop = threading.Event()
        self._data = bytearray()
        self._truncated = False

    @staticmethod
    def _flush_stderr():
        # A broken Python stream must never prevent restoring native stderr.
        try:
            sys.stderr.flush()
        except Exception:
            pass

    def __enter__(self):
        if not self.enabled:
            return self
        self._flush_stderr()
        writer = None
        try:
            self._inheritable = os.get_inheritable(2)
            self._saved = os.dup(2)
            self._reader, writer = os.pipe()
            os.set_blocking(self._reader, False)
            os.dup2(writer, 2)
            self._thread = threading.Thread(target=self._drain,
                                            name='terminal-eye-diagnostics', daemon=True)
            self._thread.start()
        except BaseException:
            self._restore()
            raise
        finally:
            if writer is not None:
                os.close(writer)
        return self

    def _drain(self):
        deadline = None
        while True:
            if self._stop.is_set():
                if deadline is None:
                    deadline = time.monotonic() + .1
                elif time.monotonic() >= deadline:
                    break
            readable, _, _ = select.select([self._reader], [], [], .05)
            if not readable:
                if self._stop.is_set():
                    break
                continue
            try:
                chunk = os.read(self._reader, 8192)
            except BlockingIOError:
                continue
            if not chunk:
                break
            self._data.extend(chunk)
            if len(self._data) > _LIMIT:
                del self._data[:-_LIMIT]
                self._truncated = True

    def _restore(self):
        try:
            if self._saved is not None:
                os.dup2(self._saved, 2, inheritable=self._inheritable)
        finally:
            if self._saved is not None:
                os.close(self._saved)
                self._saved = None
            self._stop.set()
            if self._thread is not None and self._thread.ident is not None:
                self._thread.join()
            if self._reader is not None:
                os.close(self._reader)
                self._reader = None

    def __exit__(self, exc_type, exc, traceback):
        if not self.enabled:
            return False
        self._flush_stderr()
        self._restore()
        lines = self._data.decode('utf-8', errors='replace').splitlines(keepends=True)
        self._data.clear()
        replay = ''.join(line for line in lines
                         if not any(pattern.fullmatch(line.rstrip('\r\n'))
                                    for pattern in _BENIGN))
        if self._truncated:
            replay = '[Native camera diagnostics truncated; showing recent output.]\n' + replay
        if replay:
            try:
                self.output.write(replay)
                self.output.flush()
            except Exception:
                if exc_type is None:
                    raise
        return False
