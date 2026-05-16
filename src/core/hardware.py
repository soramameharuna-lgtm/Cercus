import queue
import threading
import time
from typing import List, Tuple, Callable, Union

try:
    import serial

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    serial = None


class KinematicsParser:
    _DEFAULT_SCHEMA = [
        (0, 0, "ard_time"),
        (1, 0, "dx"),
        (2, 0, "dy"),
        (3, 0, "dz"),
        (4, 0, "stim_state"),
    ]

    def __init__(self, telemetry_schema: list = None, calib_factors: dict = None):
        self._field_defs = telemetry_schema or self._DEFAULT_SCHEMA
        self._calib_factors = calib_factors or {"dx": 1.0, "dy": 1.0, "dz": 1.0}

    def get_headers(self) -> list:
        return ["sys_time"] + [h for _, _, h in self._field_defs] + ["global_trial_id"]

    @staticmethod
    def _safe_int(val: str, default: int = 0) -> int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _parse_fields(self, raw: str) -> list:
        parts = raw.split(",")
        return [self._safe_int(parts[i], d) if i < len(parts) else d for i, d, _ in self._field_defs]

    def _apply_calibration(self, fields: list) -> list:
        out = list(fields)
        for idx, (_, _, key) in enumerate(self._field_defs):
            factor = self._calib_factors.get(key)
            if factor is not None and factor != 1.0:
                val = out[idx]
                if isinstance(val, (int, float)) and val != 0:
                    out[idx] = float(val) * factor
        return out

    def set_calib_factors(self, factors: dict):
        self._calib_factors.update(factors)

    def parse(self, sys_time: float, raw: str, g_id: int) -> list:
        raw = raw.strip()
        if not raw:
            return None
        fields = self._apply_calibration(self._parse_fields(raw))
        return [f"{sys_time:.6f}"] + fields + [g_id]

    def get_telemetry(self, raw: str) -> dict:
        raw = raw.strip()
        if not raw:
            return {h: "err" for _, _, h in self._field_defs}
        fields = self._apply_calibration(self._parse_fields(raw))
        keys = [h for _, _, h in self._field_defs]
        return dict(zip(keys, fields))


class SerialDaemon:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.05):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.data_queue = queue.Queue(maxsize=8192)
        self.tx_queue = queue.Queue(maxsize=128)
        self._serial = None
        self._running = False
        self._time_func: Callable[[], float] = None

    def start(self, time_func: Callable[[], float]):
        if time_func is None:
            raise ValueError("time_func is required — must be bound to core.Clock().getTime")
        self._time_func = time_func
        if not HAS_SERIAL:
            return

        max_retries = 5
        for attempt in range(max_retries):
            try:
                self._serial = serial.Serial(
                    port=self.port, baudrate=self.baudrate, timeout=self.timeout
                )
                self._serial.reset_input_buffer()
                self._running = True
                threading.Thread(target=self._reader_loop, daemon=True).start()
                threading.Thread(target=self._writer_loop, daemon=True).start()
                return
            except Exception:
                self._serial = None
                if attempt < max_retries - 1:
                    time.sleep(0.5)

    def _reader_loop(self):
        error_count = 0
        while self._running:
            try:
                if not self._serial or not getattr(self._serial, "is_open", False):
                    break

                if self._serial.in_waiting == 0:
                    time.sleep(0.001)
                    continue

                # Sample timestamp at earliest moment data is known present,
                # before the (potentially blocking) readline call.
                t_sys = self._time_func()
                raw = self._serial.readline()

                clean_raw = raw.decode("ascii", errors="ignore").strip()
                if clean_raw:
                    try:
                        self.data_queue.put_nowait((t_sys, clean_raw))
                    except queue.Full:
                        pass
                error_count = 0
            except Exception:
                if not self._running:
                    break
                error_count += 1
                time.sleep(0.01)
                if error_count > 100:
                    self._running = False
                    break

    def _writer_loop(self):
        while self._running:
            try:
                cmd = self.tx_queue.get(timeout=0.01)
            except queue.Empty:
                continue
            try:
                if self._serial and getattr(self._serial, "is_open", False):
                    if isinstance(cmd, bytes):
                        self._serial.write(cmd)
                    else:
                        self._serial.write(cmd.encode("ascii"))
            except Exception:
                pass

    def send_command(self, cmd: Union[str, bytes]):
        try:
            self.tx_queue.put_nowait(cmd)
        except queue.Full:
            pass

    def drain_queue(self) -> List[Tuple[float, str]]:
        items = []
        while not self.data_queue.empty():
            try:
                items.append(self.data_queue.get_nowait())
            except queue.Empty:
                break
        return items

    def is_alive(self) -> bool:
        return self._running

    def stop(self):
        self._running = False
        time.sleep(self.timeout + 0.05)
        # Drain pending TX commands
        while not self.tx_queue.empty():
            try:
                self.tx_queue.get_nowait()
            except queue.Empty:
                break
        if self._serial and getattr(self._serial, "is_open", False):
            self._serial.close()


class MockSerialDaemon:
    def __init__(self):
        self.data_queue = queue.Queue(maxsize=8192)
        self._running = False
        self._time_func: Callable[[], float] = None
        self._mock_generator = None

    def start(self, time_func: Callable[[], float], mock_generator: Callable[[int], str] = None):
        if time_func is None:
            raise ValueError("time_func is required — must be bound to core.Clock().getTime")
        self._time_func = time_func
        self._mock_generator = mock_generator
        self._running = True
        threading.Thread(target=self._mock_loop, daemon=True).start()

    def _mock_loop(self):
        t_ard = 0
        while self._running:
            t_sys = self._time_func()
            t_ard += 10
            if self._mock_generator:
                raw = self._mock_generator(t_ard)
            else:
                raw = f"{t_ard},0,0,0,0"
            try:
                self.data_queue.put_nowait((t_sys, raw))
            except queue.Full:
                pass
            time.sleep(0.01)

    def send_command(self, cmd: Union[str, bytes]):
        pass

    def drain_queue(self) -> List[Tuple]:
        items = []
        while not self.data_queue.empty():
            try:
                items.append(self.data_queue.get_nowait())
            except queue.Empty:
                break
        return items

    def is_alive(self) -> bool:
        return self._running

    def stop(self):
        self._running = False
