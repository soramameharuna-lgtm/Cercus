import math
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
    """Dedicated hardware polling process for per-axis physical calibration.

    Supports a state machine: IDLE -> CALIBRATING_X / _Y / _Z -> IDLE.
    Accumulates raw dx/dy/dz simultaneously while calibrating a single axis,
    then reports the raw vector back so the UI can build the decoupling matrix.
    """

    STATE_IDLE = "IDLE"
    STATE_CALIB_X = "CALIBRATING_X"
    STATE_CALIB_Y = "CALIBRATING_Y"
    STATE_CALIB_Z = "CALIBRATING_Z"

    _AXIS_INDEX = {
        STATE_CALIB_X: 0,
        STATE_CALIB_Y: 1,
        STATE_CALIB_Z: 2,
    }

    def __init__(self, config: Dict[str, Any], cmd_q: mp.Queue, telemetry_q: mp.Queue):
        self.config = config
        self.cmd_queue = cmd_q
        self.telemetry_queue = telemetry_q
        self._abort = False
        self._state = self.STATE_IDLE
        self._raw_dx = 0
        self._raw_dy = 0
        self._raw_dz = 0
        self._target_mm = 0.0
        self._axis_label = ""

    def _push(self, frame: dict):
        try:
            self.telemetry_queue.put_nowait(frame)
        except (queue.Full, ValueError):
            pass

    def _poll_commands(self):
        try:
            while not self.cmd_queue.empty():
                cmd = self.cmd_queue.get_nowait()
                action = cmd.get("action")
                if action in ("ABORT", "POISON_PILL"):
                    self._abort = True
                elif action == "START_CALIBRATION":
                    axis = cmd.get("axis", "").upper()
                    radius_mm = float(cmd.get("radius_mm", 100.0))
                    rotations = float(cmd.get("rotations", 3.0))
                    self._target_mm = 2.0 * math.pi * radius_mm * rotations
                    self._axis_label = axis
                    self._state = f"CALIBRATING_{axis}"
                    self._raw_dx = 0
                    self._raw_dy = 0
                    self._raw_dz = 0
                elif action == "STOP_AXIS":
                    if self._state != self.STATE_IDLE:
                        self._push({
                            "action": "axis_calib_done",
                            "axis": self._axis_label,
                            "target_mm": self._target_mm,
                            "raw_vector": [self._raw_dx, self._raw_dy, self._raw_dz],
                        })
                        self._state = self.STATE_IDLE
        except (queue.Empty, ValueError):
            pass

    def run(self):
        hw_daemon = None
        try:
            sp = self.config.get("Serial Port", "mock")
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

                if hw_daemon and not hw_daemon.is_alive():
                    raise Exception("Serial daemon disconnected or died during calibration.")

                for _, raw in hw_daemon.drain_queue():
                    parts = raw.strip().split(",")
                    if len(parts) >= 4:
                        try:
                            self._raw_dx += int(parts[1])
                        except (ValueError, TypeError):
                            pass
                        try:
                            self._raw_dy += int(parts[2])
                        except (ValueError, TypeError):
                            pass
                        try:
                            self._raw_dz += int(parts[3])
                        except (ValueError, TypeError):
                            pass

                now = time.monotonic()
                if now - last_push >= 0.033:
                    last_push = now
                    self._push({
                        "action": "calibration_telemetry",
                        "state": self._state,
                        "raw_dx": self._raw_dx,
                        "raw_dy": self._raw_dy,
                        "raw_dz": self._raw_dz,
                    })

                time.sleep(0.001)

            # Only push final axis result if we completed normally (not on abort)
            if self._state != self.STATE_IDLE and not self._abort:
                self._push({
                    "action": "axis_calib_done",
                    "axis": self._axis_label,
                    "target_mm": self._target_mm,
                    "raw_vector": [self._raw_dx, self._raw_dy, self._raw_dz],
                })

            self._push({"action": "calibration_done"})

        except Exception as e:
            self._push({"action": "calibration_error", "error": str(e)})
        finally:
            if hw_daemon:
                hw_daemon.stop()
