import queue
import threading
import time
from typing import List, Tuple, Callable

try:
    import serial

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    serial = None


class KinematicsParser:
    @staticmethod
    def get_headers() -> list:
        return [
            "sys_time",
            "ard_time",
            "dx",
            "dy",
            "dz",
            "stim_state",
            "global_trial_id",
        ]

    @staticmethod
    def parse(sys_time: float, raw: str, g_id: int) -> list:
        parts = raw.split(",")
        if len(parts) >= 5:
            try:
                return [
                    f"{sys_time:.6f}",
                    int(parts[0]),
                    int(parts[1]),
                    int(parts[2]),
                    int(parts[3]),
                    int(parts[4]),
                    g_id,
                ]
            except (ValueError, TypeError, IndexError):
                pass
        return None

    @staticmethod
    def get_telemetry(raw: str) -> dict:
        parts = raw.split(",")
        if len(parts) >= 5:
            try:
                return {
                    "dx": int(parts[1]),
                    "dy": int(parts[2]),
                    "dz": int(parts[3]),
                    "stim_state": int(parts[4]),
                }
            except (ValueError, TypeError, IndexError):
                pass
        return {"dx": "err", "dy": "err", "dz": "err", "stim_state": "err"}


class SerialDaemon:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.05):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.data_queue = queue.Queue(maxsize=8192)
        self._serial = None
        self._running = False
        self._time_func = time.time

    def start(self, time_func: Callable[[], float] = None):
        if time_func:
            self._time_func = time_func
        if not HAS_SERIAL:
            return

        try:
            self._serial = serial.Serial(
                port=self.port, baudrate=self.baudrate, timeout=self.timeout
            )
            self._serial.reset_input_buffer()
            self._running = True
            threading.Thread(target=self._reader_loop, daemon=True).start()
        except Exception:
            self._serial = None

    def _reader_loop(self):
        error_count = 0
        while self._running:
            try:
                if not self._serial or not getattr(self._serial, "is_open", False):
                    break

                if self._serial.in_waiting == 0:
                    time.sleep(0.001)
                    continue

                # 时间戳采样前置，绑定硬件缓冲就绪时刻
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

    def send_command(self, cmd_str: str):
        if self._serial and getattr(self._serial, "is_open", False):
            self._serial.write(cmd_str.encode("ascii"))

    def drain_queue(self) -> List[Tuple[float, str]]:
        items = []
        while not self.data_queue.empty():
            try:
                items.append(self.data_queue.get_nowait())
            except queue.Empty:
                break
        return items

    def stop(self):
        self._running = False
        time.sleep(self.timeout + 0.05)
        if self._serial and getattr(self._serial, "is_open", False):
            self._serial.close()


class MockSerialDaemon:
    def __init__(self):
        self.data_queue = queue.Queue(maxsize=8192)
        self._running = False
        self._time_func = time.time

    def start(self, time_func: Callable[[], float] = None):
        self._time_func = time_func or time.time
        self._running = True
        threading.Thread(target=self._mock_loop, daemon=True).start()

    def _mock_loop(self):
        t_ard = 0
        while self._running:
            t_sys = self._time_func()
            t_ard += 10
            try:
                self.data_queue.put_nowait((t_sys, f"{t_ard},0,0,0,0"))
            except queue.Full:
                pass
            time.sleep(0.01)

    def send_command(self, cmd_str: str):
        pass

    def drain_queue(self) -> List[Tuple]:
        items = []
        while not self.data_queue.empty():
            try:
                items.append(self.data_queue.get_nowait())
            except queue.Empty:
                break
        return items

    def stop(self):
        self._running = False
