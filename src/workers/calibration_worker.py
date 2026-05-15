import multiprocessing as mp
import queue
import signal
import time
from typing import Dict, Any

from src.core.hardware import SerialDaemon, MockSerialDaemon


def _term_handler(signum, frame):
    raise SystemExit(f"Received signal {signum}")


def calibration_worker_entry(config: dict, cmd_q: mp.Queue, telemetry_q: mp.Queue):
    signal.signal(signal.SIGTERM, _term_handler)
    signal.signal(signal.SIGINT, _term_handler)
    CalibrationWorker(config, cmd_q, telemetry_q).run()


class CalibrationWorker:
    """Dedicated hardware polling process for physical calibration.

    No paradigm, no psychopy, no screen rendering. Only drains the serial
    hardware queue and accumulates raw dx/dy counts, pushing telemetry at ~30 Hz.
    """

    def __init__(self, config: Dict[str, Any], cmd_q: mp.Queue, telemetry_q: mp.Queue):
        self.config = config
        self.cmd_queue = cmd_q
        self.telemetry_queue = telemetry_q
        self._abort = False
        self._raw_dx = 0
        self._raw_dy = 0

    def _push(self, frame: dict):
        try:
            self.telemetry_queue.put_nowait(frame)
        except queue.Full:
            pass

    def _poll_commands(self):
        try:
            while not self.cmd_queue.empty():
                cmd = self.cmd_queue.get_nowait()
                if cmd.get("action") in ("ABORT", "POISON_PILL"):
                    self._abort = True
        except queue.Empty:
            pass

    def run(self):
        hw_daemon = None
        try:
            sp = self.config.get("Serial Port", "mock")

            # Use a simple monotonic clock (no psychopy dependency)
            t0 = time.monotonic()
            clock = lambda: time.monotonic() - t0

            if sp == "mock":
                hw_daemon = MockSerialDaemon()
                hw_daemon.start(time_func=clock)
            else:
                hw_daemon = SerialDaemon(sp)
                hw_daemon.start(time_func=clock)

            last_push = 0.0

            while not self._abort:
                self._poll_commands()
                if self._abort:
                    break

                for _, raw in hw_daemon.drain_queue():
                    parts = raw.strip().split(",")
                    if len(parts) >= 3:
                        try:
                            self._raw_dx += int(parts[1])
                        except (ValueError, TypeError):
                            pass
                        try:
                            self._raw_dy += int(parts[2])
                        except (ValueError, TypeError):
                            pass

                now = time.monotonic()
                if now - last_push >= 0.033:
                    last_push = now
                    self._push({
                        "action": "calibration_telemetry",
                        "raw_dx": self._raw_dx,
                        "raw_dy": self._raw_dy,
                    })

                time.sleep(0.001)

            self._push({"action": "calibration_done"})

        except Exception as e:
            self._push({"action": "calibration_error", "error": str(e)})
        finally:
            if hw_daemon:
                hw_daemon.stop()
