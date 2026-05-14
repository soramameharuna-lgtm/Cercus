import math
import random
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple


class BaseParadigm(ABC):
    @classmethod
    @abstractmethod
    def get_available_patterns(cls) -> List[str]:
        pass

    @abstractmethod
    def generate_trials(self, pattern_key: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def prepare_trial(self, trial_context: dict) -> str:
        pass

    @abstractmethod
    def process_frame(
        self, elapsed_time: float, trial_context: dict, hw_telemetry: dict
    ) -> Tuple[bool, List[dict], dict]:
        pass

    @abstractmethod
    def get_idle_frame(self, hw_telemetry: dict) -> Tuple[List[dict], dict]:
        pass


class LoomingParadigm(BaseParadigm):
    VIEWING_DISTANCE_CM = 30.0
    SINGLE_SCREEN_WIDTH_CM = 53.0
    SINGLE_SCREEN_WIDTH_PX = 1920

    EXPERIMENT_PATTERNS = {
        "Baseline Visual": {
            "type": "baseline_visual",
            "target_ttc_ms": None,
            "lv_ratio_ms": 100,
        },
        "Baseline Wind": {
            "type": "baseline_wind",
            "target_ttc_ms": None,
            "lv_ratio_ms": None,
        },
        "Looming + Wind (TTC -373ms / 30°)": {
            "type": "looming_wind",
            "target_ttc_ms": -373,
            "lv_ratio_ms": 100,
        },
        "Looming + Wind (TTC -308ms / 36°)": {
            "type": "looming_wind",
            "target_ttc_ms": -308,
            "lv_ratio_ms": 100,
        },
        "Looming + Wind (TTC -261ms / 42°)": {
            "type": "looming_wind",
            "target_ttc_ms": -261,
            "lv_ratio_ms": 100,
        },
        "Looming + Wind (TTC -225ms / 48°)": {
            "type": "looming_wind",
            "target_ttc_ms": -225,
            "lv_ratio_ms": 100,
        },
        "Looming + Wind (TTC -119ms / 80°)": {
            "type": "looming_wind",
            "target_ttc_ms": -119,
            "lv_ratio_ms": 100,
        },
        "Looming + Wind (TTC 0ms / 180°)": {
            "type": "looming_wind",
            "target_ttc_ms": 0,
            "lv_ratio_ms": 100,
        },
        "Looming + Wind (TTC +200ms)": {
            "type": "looming_wind",
            "target_ttc_ms": 200,
            "lv_ratio_ms": 100,
        },
    }

    def __init__(self, debug_mode: bool = False, config: dict = None):
        self.init_deg = 2.0
        self.max_deg = 179.0
        self._wind_triggered = False
        self._baseline_delay = 1.0
        self._baseline_post = 1.5

        self.scale = 0.3 if debug_mode else 1.0
        self.c_l = -300 if debug_mode else -960
        self.c_r = 300 if debug_mode else 960
        self.mask_w = 600 if debug_mode else 1920
        self.mask_h = 600 if debug_mode else 1080

        self.init_px = self._deg_to_pix(self.init_deg)
        self.sync_size = self._deg_to_pix(2.0)

    @classmethod
    def get_available_patterns(cls) -> List[str]:
        return list(cls.EXPERIMENT_PATTERNS.keys())

    def generate_trials(self, pattern_key: str) -> List[Dict[str, Any]]:
        p = self.EXPERIMENT_PATTERNS[pattern_key]
        trials = []
        for direction in ["left"] * 9 + ["right"] * 9:
            d = {
                "type": p["type"],
                "target_ttc_ms": p["target_ttc_ms"],
                "lv_ratio_ms": p["lv_ratio_ms"],
            }
            if p["type"] == "baseline_visual":
                d["wind_dir"], d["screen_side"] = "none", direction
            else:
                d["wind_dir"], d["screen_side"] = direction, direction
            trials.append(d)
        random.shuffle(trials)
        return trials

    def prepare_trial(self, trial_context: dict) -> str:
        self._wind_triggered = False
        if trial_context["type"] == "looming_wind":
            wind_dir = trial_context.get("wind_dir", "none")
            if wind_dir == "none":
                return ""
            lv_s = trial_context.get("lv_ratio_ms", 100) / 1000.0
            init_rad = math.radians(self.init_deg / 2)
            t_col_s = lv_s / math.tan(init_rad) if math.tan(init_rad) != 0 else 0
            delay_ms = max(
                0, int(round(t_col_s * 1000)) + trial_context["target_ttc_ms"]
            )
            dir_char = "R" if wind_dir == "right" else "L"
            return f"<{dir_char},{delay_ms}>"
        elif trial_context["type"] == "baseline_wind":
            self._baseline_delay = random.uniform(0.1, 1.2)
            self._baseline_post = random.uniform(1.0, 2.0)
        return ""

    def _deg_to_pix(self, deg: float) -> float:
        deg = min(deg, 179.99)
        r_cm = math.tan(math.radians(deg / 2.0)) * self.VIEWING_DISTANCE_CM
        return (
            r_cm
            * (self.SINGLE_SCREEN_WIDTH_PX / self.SINGLE_SCREEN_WIDTH_CM)
            * self.scale
        )

    def _build_commands(
        self, side: str, theta: float, is_baseline: bool = False
    ) -> List[dict]:
        r_px = self._deg_to_pix(theta)

        mask_l = {
            "id": "mask_l",
            "type": "rect",
            "width": self.mask_w,
            "height": self.mask_h,
            "pos": (self.c_l, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        mask_r = {
            "id": "mask_r",
            "type": "rect",
            "width": self.mask_w,
            "height": self.mask_h,
            "pos": (self.c_r, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        stim_l = {
            "id": "stim_l",
            "type": "circle",
            "radius": self.init_px,
            "pos": (self.c_l, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }
        stim_r = {
            "id": "stim_r",
            "type": "circle",
            "radius": self.init_px,
            "pos": (self.c_r, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }

        sync_y = -self.mask_h / 2 + self.sync_size / 2
        sync_off = self.mask_w / 2 - self.sync_size / 2
        sync_l = {
            "id": "sync_l",
            "type": "rect",
            "width": self.sync_size,
            "height": self.sync_size,
            "pos": (self.c_l - sync_off, sync_y),
            "fillColor": [1, 1, 1],
            "lineColor": [1, 1, 1],
        }
        sync_r = {
            "id": "sync_r",
            "type": "rect",
            "width": self.sync_size,
            "height": self.sync_size,
            "pos": (self.c_r + sync_off, sync_y),
            "fillColor": [1, 1, 1],
            "lineColor": [1, 1, 1],
        }

        # 锁定图层渲染顺序：活动区 -> 遮罩区 -> 静态区 -> 同步块
        if is_baseline or side == "both":
            return [mask_l, mask_r, stim_l, stim_r]
        elif side == "right":
            stim_r["radius"] = r_px
            return [stim_r, mask_l, stim_l, sync_r]
        elif side == "left":
            stim_l["radius"] = r_px
            return [stim_l, mask_r, stim_r, sync_l]
        return []

    def get_idle_frame(self, hw_telemetry: dict) -> Tuple[List[dict], dict]:
        cmds = self._build_commands("both", self.init_deg, is_baseline=True)
        tel = {
            "phase": "Idle",
            "theta": self.init_deg,
            "side": "—",
            "hw_cmd": None,
            "twin_r_ratio": self.init_px / self.SINGLE_SCREEN_WIDTH_PX,
        }
        tel.update(hw_telemetry)
        return cmds, tel

    def process_frame(
        self, elapsed_time: float, trial_context: dict, hw_telemetry: dict
    ) -> Tuple[bool, List[dict], dict]:
        t_type = trial_context["type"]
        side = trial_context["screen_side"]

        is_done = False
        theta = self.init_deg
        hw_cmd = None
        phase = "Trial"

        if t_type in ["looming_wind", "baseline_visual"]:
            lv_s = trial_context.get("lv_ratio_ms", 100) / 1000.0
            init_rad = math.radians(self.init_deg / 2)
            t_col = lv_s / math.tan(init_rad) if math.tan(init_rad) != 0 else 0

            if elapsed_time >= t_col + 1.0:
                is_done = True
            else:
                if elapsed_time < t_col - 1e-5:
                    theta = math.degrees(2 * math.atan(lv_s / (t_col - elapsed_time)))
                else:
                    theta = self.max_deg
                theta = min(theta, self.max_deg)
                phase = "Looming"

        elif t_type == "baseline_wind":
            wind_dir = trial_context.get("wind_dir", "none")
            if (
                not self._wind_triggered
                and elapsed_time >= self._baseline_delay
                and wind_dir != "none"
            ):
                dir_char = "R" if wind_dir == "right" else "L"
                hw_cmd = f"<{dir_char},0>"
                self._wind_triggered = True

            if (
                self._wind_triggered
                and (elapsed_time - self._baseline_delay) >= self._baseline_post
            ):
                is_done = True
            else:
                phase = "Baseline"
                side = "both" if not self._wind_triggered else side

        cmds = self._build_commands(
            side, theta, is_baseline=(t_type == "baseline_wind")
        )
        tel = {
            "phase": phase,
            "theta": theta,
            "side": side,
            "hw_cmd": hw_cmd,
            "twin_r_ratio": self._deg_to_pix(theta) / self.SINGLE_SCREEN_WIDTH_PX,
        }
        tel.update(hw_telemetry)
        return is_done, cmds, tel


class ClassicLoomingParadigm(BaseParadigm):
    VIEWING_DISTANCE_CM = 30.0
    SINGLE_SCREEN_WIDTH_CM = 53.0
    SINGLE_SCREEN_WIDTH_PX = 1920

    EXPERIMENT_PATTERNS = {
        "Classic Looming (Random L/R)": "Random L/R",
        "Classic Looming (Always Left)": "Always Left",
        "Classic Looming (Always Right)": "Always Right",
    }

    def __init__(self, debug_mode: bool = False, config: dict = None):
        self.config = config or {}
        self.lv_ratio_ms = float(self.config.get("l/v Ratio (ms)", 80.0))
        self.init_deg = float(self.config.get("Initial Degree (°)", 2.0))
        self.max_deg = float(self.config.get("Final Degree (°)", 180.0))

        self.scale = 0.3 if debug_mode else 1.0
        self.c_l = -300 if debug_mode else -960
        self.c_r = 300 if debug_mode else 960
        self.mask_w = 600 if debug_mode else 1920
        self.mask_h = 600 if debug_mode else 1080

        self.init_px = self._deg_to_pix(self.init_deg)
        self.sync_size = self._deg_to_pix(2.0)

    @classmethod
    def get_available_patterns(cls) -> List[str]:
        return list(cls.EXPERIMENT_PATTERNS.keys())

    def generate_trials(self, pattern_key: str) -> List[Dict[str, Any]]:
        mode = self.EXPERIMENT_PATTERNS[pattern_key]
        num_trials = int(self.config.get("Number of Trials", 18))
        trials = []

        if mode == "Always Left":
            directions = ["left"] * num_trials
        elif mode == "Always Right":
            directions = ["right"] * num_trials
        else:
            half = num_trials // 2
            extra_left = num_trials - half
            directions = ["left"] * extra_left + ["right"] * half
            random.shuffle(directions)

        for direction in directions:
            trials.append(
                {
                    "type": "classic_looming",
                    "direction": direction,
                    "lv_ratio_ms": self.lv_ratio_ms,
                    "initial_angle_deg": self.init_deg,
                    "final_angle_deg": self.max_deg,
                }
            )
        return trials

    def prepare_trial(self, trial_context: dict) -> str:
        return ""

    def _deg_to_pix(self, deg: float) -> float:
        deg = min(deg, 179.99)
        r_cm = math.tan(math.radians(deg / 2.0)) * self.VIEWING_DISTANCE_CM
        return (
            r_cm
            * (self.SINGLE_SCREEN_WIDTH_PX / self.SINGLE_SCREEN_WIDTH_CM)
            * self.scale
        )

    def _build_commands(
        self, side: str, theta: float, is_baseline: bool = False
    ) -> List[dict]:
        r_px = self._deg_to_pix(theta)

        mask_l = {
            "id": "mask_l",
            "type": "rect",
            "width": self.mask_w,
            "height": self.mask_h,
            "pos": (self.c_l, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        mask_r = {
            "id": "mask_r",
            "type": "rect",
            "width": self.mask_w,
            "height": self.mask_h,
            "pos": (self.c_r, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        stim_l = {
            "id": "stim_l",
            "type": "circle",
            "radius": self.init_px,
            "pos": (self.c_l, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }
        stim_r = {
            "id": "stim_r",
            "type": "circle",
            "radius": self.init_px,
            "pos": (self.c_r, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }

        sync_y = -self.mask_h / 2 + self.sync_size / 2
        sync_off = self.mask_w / 2 - self.sync_size / 2
        sync_l = {
            "id": "sync_l",
            "type": "rect",
            "width": self.sync_size,
            "height": self.sync_size,
            "pos": (self.c_l - sync_off, sync_y),
            "fillColor": [1, 1, 1],
            "lineColor": [1, 1, 1],
        }
        sync_r = {
            "id": "sync_r",
            "type": "rect",
            "width": self.sync_size,
            "height": self.sync_size,
            "pos": (self.c_r + sync_off, sync_y),
            "fillColor": [1, 1, 1],
            "lineColor": [1, 1, 1],
        }

        # 锁定图层渲染顺序：活动区 -> 遮罩区 -> 静态区 -> 同步块
        if is_baseline or side == "both":
            return [mask_l, mask_r, stim_l, stim_r]
        elif side == "right":
            stim_r["radius"] = r_px
            return [stim_r, mask_l, stim_l, sync_r]
        elif side == "left":
            stim_l["radius"] = r_px
            return [stim_l, mask_r, stim_r, sync_l]
        return []

    def get_idle_frame(self, hw_telemetry: dict) -> Tuple[List[dict], dict]:
        cmds = self._build_commands("both", self.init_deg, is_baseline=True)
        tel = {
            "phase": "Idle",
            "theta": self.init_deg,
            "side": "—",
            "hw_cmd": None,
            "twin_r_ratio": self.init_px / self.SINGLE_SCREEN_WIDTH_PX,
        }
        tel.update(hw_telemetry)
        return cmds, tel

    def process_frame(
        self, elapsed_time: float, trial_context: dict, hw_telemetry: dict
    ) -> Tuple[bool, List[dict], dict]:
        lv_s = trial_context["lv_ratio_ms"] / 1000.0
        init_deg = trial_context["initial_angle_deg"]
        final_deg = trial_context["final_angle_deg"]
        side = trial_context["direction"]

        init_rad = math.radians(init_deg / 2)
        t_col = lv_s / math.tan(init_rad) if math.tan(init_rad) != 0 else 0

        is_done = False
        theta = init_deg

        if elapsed_time >= t_col + 1.0:
            is_done = True
            theta = final_deg
        else:
            if elapsed_time < t_col - 1e-5:
                theta = math.degrees(2 * math.atan(lv_s / (t_col - elapsed_time)))
            else:
                theta = final_deg
            theta = min(theta, final_deg)

        cmds = self._build_commands(side, theta)
        tel = {
            "phase": "Looming",
            "theta": theta,
            "side": side,
            "hw_cmd": None,
            "twin_r_ratio": self._deg_to_pix(theta) / self.SINGLE_SCREEN_WIDTH_PX,
        }
        tel.update(hw_telemetry)
        return is_done, cmds, tel


PARADIGM_REGISTRY = {
    "Looming": LoomingParadigm,
    "ClassicLooming": ClassicLoomingParadigm,
}


def get_available_patterns() -> dict:
    mapping = {}
    for cls_name, cls_obj in PARADIGM_REGISTRY.items():
        for pat in cls_obj.get_available_patterns():
            mapping[pat] = cls_name
    return mapping
