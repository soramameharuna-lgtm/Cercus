import csv
import json
import os
from typing import List

import numpy as np


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


class GroundTruthLogger:
    EVENT_COLUMNS = [
        "event_name",
        "timestamp",
        "session_num",
        "trial_in_session",
        "global_trial_id",
        "details",
    ]

    def __init__(self, output_dir: str):
        self.out = output_dir
        os.makedirs(self.out, exist_ok=True)
        self.global_trial_id = self._load_cache()
        self.session_num = 0
        self.trial_in_session = 0
        self._event_file = None
        self._event_writer = None
        self._kinematics_file = None
        self._kinematics_writer = None
        self._kin_buffer: List[list] = []

    def _load_cache(self) -> int:
        cache_path = os.path.join(self.out, ".trial_cache.txt")
        if os.path.exists(cache_path):
            try:
                return int(open(cache_path, "r").read().strip())
            except (ValueError, IOError):
                return 0
        return 0

    def _save_cache(self):
        cache_path = os.path.join(self.out, ".trial_cache.txt")
        with open(cache_path, "w") as f:
            f.write(str(self.global_trial_id))

    def open_session(self, subject_id: str, session_num: int, kin_headers: list):
        self.close()
        self.session_num = session_num
        self.trial_in_session = 0
        base_name = f"{subject_id}_session_{session_num}"

        event_path = os.path.join(self.out, f"{base_name}_events.csv")
        self._event_file = open(event_path, "w", newline="", encoding="utf-8-sig")
        self._event_writer = csv.writer(self._event_file)
        self._event_writer.writerow(self.EVENT_COLUMNS)

        kinematics_path = os.path.join(self.out, f"{base_name}_kinematics.csv")
        self._kinematics_file = open(
            kinematics_path, "w", newline="", encoding="utf-8-sig"
        )
        self._kinematics_writer = csv.writer(self._kinematics_file)
        self._kinematics_writer.writerow(kin_headers)

    def is_open(self) -> bool:
        return self._event_writer is not None and self._kinematics_writer is not None

    def close(self):
        self.flush_kinematics()
        if self._event_file:
            self._event_file.close()
            self._event_file, self._event_writer = None, None
        if self._kinematics_file:
            self._kinematics_file.close()
            self._kinematics_file, self._kinematics_writer = None, None

    def advance_trial(self):
        self.trial_in_session += 1
        self.global_trial_id += 1
        self._save_cache()

    def log_event(self, event_name: str, timestamp: float, **details):
        if not self._event_writer:
            return
        self._event_writer.writerow(
            [
                event_name,
                f"{timestamp:.6f}",
                self.session_num,
                self.trial_in_session,
                self.global_trial_id,
                json.dumps(details, cls=NumpyEncoder) if details else "",
            ]
        )

    def log_kinematics_batch(self, items: List[list]):
        if not self._kinematics_writer:
            return
        self._kin_buffer.extend(items)

    def flush_kinematics(self):
        if self._kin_buffer and self._kinematics_writer:
            self._kinematics_writer.writerows(self._kin_buffer)
            self._kin_buffer.clear()
            self._kinematics_file.flush()

    def kin_buffer_size(self) -> int:
        return len(self._kin_buffer)

    def flush(self):
        if self._event_file:
            self._event_file.flush()
        if self._kinematics_file:
            self._kinematics_file.flush()
