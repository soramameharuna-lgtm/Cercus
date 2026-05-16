import math
import random
import numpy as np
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple, Callable


class BaseParadigm(ABC):
    @staticmethod
    def _apply_random_seed(config: dict) -> None:
        raw = str(config.get("Random Seed", "Auto")).strip()
        if raw.lower() in ("auto", ""):
            seed = np.random.randint(0, 2**31 - 1)
        else:
            try:
                seed = int(raw)
            except ValueError:
                seed = np.random.randint(0, 2**31 - 1)
        random.seed(seed)
        np.random.seed(seed)
        config["Random Seed"] = seed

    @classmethod
    def _schema_default(cls, key: str) -> Any:
        return cls.get_parameter_schema()[key]["default"]

    @classmethod
    def get_telemetry_schema(cls) -> list:
        """Return field definitions for hardware telemetry parsing.
        Each entry: (raw_index, default_value, header_key)
        """
        return [
            (0, 0, "ard_time"),
            (1, 0, "dx"),
            (2, 0, "dy"),
            (3, 0, "dz"),
            (4, 0, "stim_state"),
        ]

    @classmethod
    def get_mock_generator(cls) -> Callable[[int], str]:
        """Return a function that generates mock serial data for this paradigm."""

        def generator(t_ard: int) -> str:
            return f"{t_ard},0,0,0,0"

        return generator

    @classmethod
    def get_sync_channels(cls) -> List[str]:
        """Return named sync trigger channels for this paradigm.
        Each entry generates a Sync Block row in the dashboard topology UI.
        """
        return ["Sync Trigger"]

    @classmethod
    @abstractmethod
    def get_available_patterns(cls) -> List[str]:
        pass

    @classmethod
    @abstractmethod
    def get_parameter_schema(cls) -> Dict[str, Dict[str, Any]]:
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
    ) -> Tuple[bool, List[dict], dict, List[int]]:
        pass

    @abstractmethod
    def get_idle_frame(self, hw_telemetry: dict) -> Tuple[List[dict], dict, List[int]]:
        pass


# ---------------------------------------------------------------------------
# Looming Paradigm (Multi-modal: Visual + Wind)
# ---------------------------------------------------------------------------


class LoomingParadigm(BaseParadigm):
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
        self.config = config or {}
        self.viewing_distance_cm = float(self.config.get("Viewing Distance (cm)", 30.0))
        self.screen_width_cm = float(self.config.get("Screen Width (cm)", 53.0))

        screen_w_px = int(self.config.get("Screen Width (px)", 3840))
        screen_h_px = int(self.config.get("Screen Height (px)", 1080))

        self.init_deg = 2.0
        self.max_deg = 179.0
        self._wind_triggered = False
        self._baseline_delay = 1.0
        self._baseline_post = 1.5

        self.scale = 0.3 if debug_mode else 1.0

        if debug_mode:
            self.per_screen_w_px = screen_w_px // 6
            self.c_l = -screen_w_px // 12
            self.c_r = screen_w_px // 12
            self.mask_w = screen_w_px // 6
            self.mask_h = screen_h_px // 3
        else:
            self.per_screen_w_px = screen_w_px // 2
            self.c_l = -(screen_w_px // 4)
            self.c_r = screen_w_px // 4
            self.mask_w = screen_w_px // 2
            self.mask_h = screen_h_px

        self.init_px = self._deg_to_pix(self.init_deg)

    @classmethod
    def get_available_patterns(cls) -> List[str]:
        return list(cls.EXPERIMENT_PATTERNS.keys())

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "Execution Mode": {
                "type": "choice",
                "default": "Auto",
                "choices": ["Auto", "Manual"],
                "label": "Execution Mode",
            },
            "note": {
                "type": "info",
                "label": "This paradigm has fixed experiment patterns. Select a pattern above.",
            },
        }

    @classmethod
    def get_sync_channels(cls) -> List[str]:
        return ["Trial Active", "Phase Flip"]

    def _build_stimulus_commands(self, side: str, theta: float) -> List[dict]:
        r_px_l = self._deg_to_pix(theta if side in ("left", "both") else self.init_deg)
        r_px_r = self._deg_to_pix(theta if side in ("right", "both") else self.init_deg)

        bg_l = {
            "id": "_bg_l",
            "type": "rect",
            "width": self.mask_w,
            "height": self.mask_h,
            "pos": (self.c_l, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        bg_r = {
            "id": "_bg_r",
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
            "radius": r_px_l,
            "pos": (self.c_l, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }
        stim_r = {
            "id": "stim_r",
            "type": "circle",
            "radius": r_px_r,
            "pos": (self.c_r, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }
        bezel = {
            "id": "_bezel",
            "type": "rect",
            "width": 100,
            "height": self.mask_h * 1.5,
            "pos": (0, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }

        if side == "left":
            return [bg_l, stim_l, bg_r, stim_r, bezel]
        elif side == "right":
            return [bg_r, stim_r, bg_l, stim_l, bezel]
        else:
            return [bg_l, bg_r, stim_l, stim_r, bezel]

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
        r_cm = math.tan(math.radians(deg / 2.0)) * self.viewing_distance_cm
        return r_cm * (self.per_screen_w_px / self.screen_width_cm) * self.scale

    def get_idle_frame(self, hw_telemetry: dict) -> Tuple[List[dict], dict, List[int]]:
        cmds = self._build_stimulus_commands("both", self.init_deg)
        tel = {
            "phase": "Idle",
            "hw_cmd": None,
            "ui_color": "cyan",
            "ui_metrics": {
                "theta": self.init_deg,
                "side": "—",
                **hw_telemetry,
            },
            "ui_twin": {
                "side": "—",
                "radius_ratio": self.init_px / self.per_screen_w_px,
            },
        }
        return cmds, tel, [0, 0]

    def process_frame(
        self, elapsed_time: float, trial_context: dict, hw_telemetry: dict
    ) -> Tuple[bool, List[dict], dict, List[int]]:
        t_type = trial_context["type"]
        side = trial_context["screen_side"]

        is_done = False
        theta = self.init_deg
        hw_cmd = None
        phase = "Trial"
        stim_active = 0
        wind_active = 0

        if t_type in ["looming_wind", "baseline_visual"]:
            lv_s = trial_context.get("lv_ratio_ms", 100) / 1000.0
            init_rad = math.radians(self.init_deg / 2)
            t_col = lv_s / math.tan(init_rad) if math.tan(init_rad) != 0 else 0

            if elapsed_time >= t_col + 1.0:
                is_done = True
            else:
                delta = max(t_col - elapsed_time, 0.001)
                if delta > 0.001:
                    theta = math.degrees(2 * math.atan(lv_s / delta))
                else:
                    theta = self.max_deg
                theta = min(theta, self.max_deg)
                phase = "Looming"
                stim_active = 1

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
                if self._wind_triggered:
                    wind_active = 1

        cmds = self._build_stimulus_commands(side, theta)
        ui_color = (
            "lime"
            if phase == "Looming"
            else ("orange" if phase == "Baseline" else "cyan")
        )
        tel = {
            "phase": phase,
            "hw_cmd": hw_cmd,
            "ui_color": ui_color,
            "ui_metrics": {
                "theta": round(theta, 1),
                "side": side,
                **hw_telemetry,
            },
            "ui_twin": {
                "side": side,
                "radius_ratio": self._deg_to_pix(theta) / self.per_screen_w_px,
            },
        }
        return is_done, cmds, tel, [stim_active, wind_active]


# ---------------------------------------------------------------------------
# Classic Looming Paradigm (Visual only, configurable)
# ---------------------------------------------------------------------------


class ClassicLoomingParadigm(BaseParadigm):
    EXPERIMENT_PATTERNS = {
        "Classic Looming (Random L/R)": "Random L/R",
        "Classic Looming (Always Left)": "Always Left",
        "Classic Looming (Always Right)": "Always Right",
    }

    def __init__(self, debug_mode: bool = False, config: dict = None):
        self.config = config or {}
        self.viewing_distance_cm = float(self.config.get("Viewing Distance (cm)", 30.0))
        self.screen_width_cm = float(self.config.get("Screen Width (cm)", 53.0))
        self.lv_ratio_ms = float(
            self.config.get("l/v Ratio (ms)", self._schema_default("l/v Ratio (ms)"))
        )
        self.init_deg = float(
            self.config.get(
                "Initial Degree (°)", self._schema_default("Initial Degree (°)")
            )
        )
        self.max_deg = float(
            self.config.get(
                "Final Degree (°)", self._schema_default("Final Degree (°)")
            )
        )

        screen_w_px = int(self.config.get("Screen Width (px)", 3840))
        screen_h_px = int(self.config.get("Screen Height (px)", 1080))

        self.scale = 0.3 if debug_mode else 1.0

        if debug_mode:
            self.per_screen_w_px = screen_w_px // 6
            self.c_l = -screen_w_px // 12
            self.c_r = screen_w_px // 12
            self.mask_w = screen_w_px // 6
            self.mask_h = screen_h_px // 3
        else:
            self.per_screen_w_px = screen_w_px // 2
            self.c_l = -(screen_w_px // 4)
            self.c_r = screen_w_px // 4
            self.mask_w = screen_w_px // 2
            self.mask_h = screen_h_px

        self.init_px = self._deg_to_pix(self.init_deg)

    @classmethod
    def get_available_patterns(cls) -> List[str]:
        return list(cls.EXPERIMENT_PATTERNS.keys())

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "l/v Ratio (ms)": {
                "type": "float",
                "default": 80.0,
                "min": 1.0,
                "max": 10000.0,
                "label": "l/v Ratio (ms)",
            },
            "Initial Degree (°)": {
                "type": "float",
                "default": 2.0,
                "min": 0.1,
                "max": 179.0,
                "label": "Initial Degree (°)",
            },
            "Final Degree (°)": {
                "type": "float",
                "default": 180.0,
                "min": 1.0,
                "max": 179.9,
                "label": "Final Degree (°)",
            },
            "Number of Trials": {
                "type": "int",
                "default": 18,
                "min": 1,
                "max": 9999,
                "label": "Number of Trials",
            },
            "Execution Mode": {
                "type": "choice",
                "default": "Auto",
                "choices": ["Auto", "Manual"],
                "label": "Execution Mode",
            },
        }

    def generate_trials(self, pattern_key: str) -> List[Dict[str, Any]]:
        mode = self.EXPERIMENT_PATTERNS.get(pattern_key, pattern_key)
        num_trials = int(
            self.config.get(
                "Number of Trials", self._schema_default("Number of Trials")
            )
        )
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
        r_cm = math.tan(math.radians(deg / 2.0)) * self.viewing_distance_cm
        return r_cm * (self.per_screen_w_px / self.screen_width_cm) * self.scale

    @classmethod
    def get_sync_channels(cls) -> List[str]:
        return ["Trial Active", "Phase Flip"]

    def _build_stimulus_commands(self, side: str, theta: float) -> List[dict]:
        r_px_l = self._deg_to_pix(theta if side in ("left", "both") else self.init_deg)
        r_px_r = self._deg_to_pix(theta if side in ("right", "both") else self.init_deg)

        bg_l = {
            "id": "_bg_l",
            "type": "rect",
            "width": self.mask_w,
            "height": self.mask_h,
            "pos": (self.c_l, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        bg_r = {
            "id": "_bg_r",
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
            "radius": r_px_l,
            "pos": (self.c_l, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }
        stim_r = {
            "id": "stim_r",
            "type": "circle",
            "radius": r_px_r,
            "pos": (self.c_r, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }
        bezel = {
            "id": "_bezel",
            "type": "rect",
            "width": 100,
            "height": self.mask_h * 1.5,
            "pos": (0, 0),
            "fillColor": [-1, -1, -1],
            "lineColor": [-1, -1, -1],
        }

        if side == "left":
            return [bg_l, stim_l, bg_r, stim_r, bezel]
        elif side == "right":
            return [bg_r, stim_r, bg_l, stim_l, bezel]
        else:
            return [bg_l, bg_r, stim_l, stim_r, bezel]

    def get_idle_frame(self, hw_telemetry: dict) -> Tuple[List[dict], dict, List[int]]:
        cmds = self._build_stimulus_commands("both", self.init_deg)
        tel = {
            "phase": "Idle",
            "hw_cmd": None,
            "ui_color": "cyan",
            "ui_metrics": {
                "theta": self.init_deg,
                "side": "—",
                **hw_telemetry,
            },
            "ui_twin": {
                "side": "—",
                "radius_ratio": self.init_px / self.per_screen_w_px,
            },
        }
        return cmds, tel, [0, 0]

    def process_frame(
        self, elapsed_time: float, trial_context: dict, hw_telemetry: dict
    ) -> Tuple[bool, List[dict], dict, List[int]]:
        lv_s = trial_context["lv_ratio_ms"] / 1000.0
        init_deg = trial_context["initial_angle_deg"]
        final_deg = trial_context["final_angle_deg"]
        side = trial_context["direction"]

        init_rad = math.radians(init_deg / 2)
        t_col = lv_s / math.tan(init_rad) if math.tan(init_rad) != 0 else 0

        is_done = False
        theta = init_deg
        stim_active = 0

        if elapsed_time >= t_col + 1.0:
            is_done = True
            theta = final_deg
        else:
            delta = max(t_col - elapsed_time, 0.001)
            if delta > 0.001:
                theta = math.degrees(2 * math.atan(lv_s / delta))
            else:
                theta = final_deg
            theta = min(theta, final_deg)
            stim_active = 1

        cmds = self._build_stimulus_commands(side, theta)
        tel = {
            "phase": "Looming",
            "hw_cmd": None,
            "ui_color": "lime",
            "ui_metrics": {
                "theta": round(theta, 1),
                "side": side,
                **hw_telemetry,
            },
            "ui_twin": {
                "side": side,
                "radius_ratio": self._deg_to_pix(theta) / self.per_screen_w_px,
            },
        }
        return is_done, cmds, tel, [stim_active, 0]


# ---------------------------------------------------------------------------
# Optic Flow Paradigm (Vectorized dot-motion)
# ---------------------------------------------------------------------------


class OpticFlowParadigm(BaseParadigm):
    EXPERIMENT_PATTERNS = {
        "Optic Flow": "optic_flow",
    }

    def __init__(self, debug_mode: bool = False, config: dict = None):
        self.config = config or {}
        self.speed = float(
            self.config.get("Speed (deg/s)", self._schema_default("Speed (deg/s)"))
        )
        self.density = int(self.config.get("Density", self._schema_default("Density")))
        self.coherence = float(
            self.config.get("Coherence", self._schema_default("Coherence"))
        )
        self.direction = self.config.get("Direction", self._schema_default("Direction"))
        self.trial_duration = float(
            self.config.get(
                "Trial Duration (s)", self._schema_default("Trial Duration (s)")
            )
        )
        self.viewing_distance_cm = float(self.config.get("Viewing Distance (cm)", 30.0))
        self.screen_width_cm = float(self.config.get("Screen Width (cm)", 53.0))

        self.scale = 0.3 if debug_mode else 1.0

        screen_w_px = int(self.config.get("Screen Width (px)", 3840))
        screen_h_px = int(self.config.get("Screen Height (px)", 1080))
        self.screen_w = float(screen_w_px // 3 if debug_mode else screen_w_px)
        self.screen_h = float(screen_h_px // 2 if debug_mode else screen_h_px)

        half_w = self.screen_w / 2.0
        half_h = self.screen_h / 2.0
        self._x = np.random.uniform(-half_w, half_w, self.density).astype(np.float64)
        self._y = np.random.uniform(-half_h, half_h, self.density).astype(np.float64)

        coh_count = int(self.density * self.coherence)
        base_angle = 180.0 if self.direction == "Left" else 0.0
        angles = np.empty(self.density, dtype=np.float64)
        angles[:coh_count] = base_angle
        angles[coh_count:] = np.random.uniform(0.0, 360.0, self.density - coh_count)
        self._dx = np.cos(np.radians(angles))
        self._dy = np.sin(np.radians(angles))

    def _deg_to_pix(self, deg: float) -> float:
        deg = min(deg, 179.99)
        r_cm = math.tan(math.radians(deg / 2.0)) * self.viewing_distance_cm
        return r_cm * (self.screen_w / self.screen_width_cm) * self.scale

    @classmethod
    def get_available_patterns(cls) -> List[str]:
        return list(cls.EXPERIMENT_PATTERNS.keys())

    @classmethod
    def get_sync_channels(cls) -> List[str]:
        return ["Trial Active", "Phase Flip"]

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "Speed (deg/s)": {
                "type": "float",
                "default": 30.0,
                "min": 0.1,
                "max": 1000.0,
                "label": "Speed (deg/s)",
            },
            "Density": {
                "type": "int",
                "default": 200,
                "min": 1,
                "max": 50000,
                "label": "Density",
            },
            "Coherence": {
                "type": "float",
                "default": 1.0,
                "min": 0.0,
                "max": 1.0,
                "label": "Coherence (0–1)",
            },
            "Direction": {
                "type": "choice",
                "default": "Left",
                "choices": ["Left", "Right"],
                "label": "Direction",
            },
            "Trial Duration (s)": {
                "type": "float",
                "default": 5.0,
                "min": 0.1,
                "max": 600.0,
                "label": "Trial Duration (s)",
            },
            "Number of Trials": {
                "type": "int",
                "default": 10,
                "min": 1,
                "max": 9999,
                "label": "Number of Trials",
            },
            "Execution Mode": {
                "type": "choice",
                "default": "Auto",
                "choices": ["Auto", "Manual"],
                "label": "Execution Mode",
            },
            "Random Seed": {
                "type": "string",
                "default": "Auto",
                "label": "Random Seed",
            },
        }

    def generate_trials(self, pattern_key: str) -> List[Dict[str, Any]]:
        n = int(
            self.config.get(
                "Number of Trials", self._schema_default("Number of Trials")
            )
        )
        return [
            {
                "type": "optic_flow",
                "trial_idx": i,
                "speed": self.speed,
                "density": self.density,
                "coherence": self.coherence,
                "direction": self.direction,
                "trial_duration": self.trial_duration,
            }
            for i in range(n)
        ]

    def prepare_trial(self, trial_context: dict) -> str:
        density = trial_context["density"]
        coherence = trial_context["coherence"]
        direction = trial_context["direction"]

        half_w = self.screen_w / 2.0
        half_h = self.screen_h / 2.0
        self._x = np.random.uniform(-half_w, half_w, density).astype(np.float64)
        self._y = np.random.uniform(-half_h, half_h, density).astype(np.float64)

        coh_count = int(density * coherence)
        base_angle = 180.0 if direction == "Left" else 0.0
        angles = np.empty(density, dtype=np.float64)
        angles[:coh_count] = base_angle
        angles[coh_count:] = np.random.uniform(0.0, 360.0, density - coh_count)
        self._dx = np.cos(np.radians(angles))
        self._dy = np.sin(np.radians(angles))

        self._last_time = 0.0
        self._coh_count = int(density * coherence)

        # Pre-allocate per-frame rendering arrays (avoids GC jitter)
        self._xys = np.empty((density, 2), dtype=np.float64)
        self._sizes = np.full((density, 2), 15.0, dtype=np.float64)
        self._colors = np.full((density, 3), 1.0, dtype=np.float64)
        self._opacities = np.ones(density, dtype=np.float64)

        return ""

    def get_idle_frame(self, hw_telemetry: dict) -> Tuple[List[dict], dict, List[int]]:
        bg = {
            "id": "_bg",
            "type": "rect",
            "width": self.screen_w,
            "height": self.screen_h,
            "pos": (0, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        tel = {
            "phase": "Idle",
            "hw_cmd": None,
            "ui_color": "cyan",
            "ui_metrics": {
                "speed": 0.0,
                "density": self.density,
                "n_dots": self.density,
                **hw_telemetry,
            },
            "ui_twin": None,
        }
        return [bg], tel, [0, 0]

    def process_frame(
        self, elapsed_time: float, trial_context: dict, hw_telemetry: dict
    ) -> Tuple[bool, List[dict], dict, List[int]]:
        speed = trial_context["speed"]
        density = trial_context["density"]

        is_done = elapsed_time >= trial_context["trial_duration"]

        dt = elapsed_time - self._last_time
        if dt <= 0:
            dt = 1.0 / 60.0
        self._last_time = elapsed_time

        step = self._deg_to_pix(speed) * dt
        self._x += self._dx * step
        self._y += self._dy * step

        half_w = self.screen_w / 2.0
        half_h = self.screen_h / 2.0
        coh_count = getattr(self, "_coh_count", density)

        # Frame-level annihilation: 3% chance per frame for all particles
        annihilated = np.random.random(density) < 0.03
        noise = np.arange(density) >= coh_count
        annihilated &= noise  # only noise particles can be annihilated
        if np.any(annihilated):
            self._x[annihilated] = np.random.uniform(-half_w, half_w, annihilated.sum())
            self._y[annihilated] = np.random.uniform(-half_h, half_h, annihilated.sum())
            new_angles = np.random.uniform(0.0, 360.0, annihilated.sum())
            self._dx[annihilated] = np.cos(np.radians(new_angles))
            self._dy[annihilated] = np.sin(np.radians(new_angles))

        # Wrap-around boundary check (in-place)
        mask_right = self._x > half_w
        mask_left = self._x < -half_w
        mask_top = self._y > half_h
        mask_bottom = self._y < -half_h
        wrapped = mask_right | mask_left | mask_top | mask_bottom

        self._x[mask_right] -= self.screen_w
        self._x[mask_left] += self.screen_w
        self._y[mask_top] -= self.screen_h
        self._y[mask_bottom] += self.screen_h

        # Re-randomize direction for wrapped noise particles to break visual streaks
        wrapped_noise = wrapped & noise
        if np.any(wrapped_noise):
            new_angles = np.random.uniform(0.0, 360.0, wrapped_noise.sum())
            self._dx[wrapped_noise] = np.cos(np.radians(new_angles))
            self._dy[wrapped_noise] = np.sin(np.radians(new_angles))

        bg = {
            "id": "_bg",
            "type": "rect",
            "width": self.screen_w,
            "height": self.screen_h,
            "pos": (0, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        self._xys[:, 0] = self._x
        self._xys[:, 1] = self._y
        cmds = [
            bg,
            {
                "type": "element_array",
                "n_elements": density,
                "xys": self._xys,
                "sizes": self._sizes,
                "colors": self._colors,
                "opacities": self._opacities,
            },
        ]

        phase_sq = 1 if (math.floor(elapsed_time * 10) % 2 == 0) else 0
        tel = {
            "phase": "OpticFlow",
            "hw_cmd": None,
            "ui_color": "lime",
            "ui_metrics": {
                "speed": speed,
                "density": density,
                "n_dots": density,
                **hw_telemetry,
            },
            "ui_twin": None,
        }
        return is_done, cmds, tel, [1, phase_sq]


# ---------------------------------------------------------------------------
# Movement Trace Paradigm (Lissajous trajectory)
# ---------------------------------------------------------------------------


class MovementTraceParadigm(BaseParadigm):
    EXPERIMENT_PATTERNS = {
        "Movement Trace": "movement_trace",
    }

    def __init__(self, debug_mode: bool = False, config: dict = None):
        self.config = config or {}
        self.freq_x = float(self.config.get("Freq X", self._schema_default("Freq X")))
        self.freq_y = float(self.config.get("Freq Y", self._schema_default("Freq Y")))
        self.amp_x = float(
            self.config.get("Amplitude X", self._schema_default("Amplitude X"))
        )
        self.amp_y = float(
            self.config.get("Amplitude Y", self._schema_default("Amplitude Y"))
        )
        self.speed = float(self.config.get("Speed", self._schema_default("Speed")))
        self.n_trail = int(
            self.config.get("Trail Points", self._schema_default("Trail Points"))
        )
        self.trial_duration = float(
            self.config.get(
                "Trial Duration (s)", self._schema_default("Trial Duration (s)")
            )
        )

        self.scale = 0.3 if debug_mode else 1.0

        screen_w_px = int(self.config.get("Screen Width (px)", 3840))
        screen_h_px = int(self.config.get("Screen Height (px)", 1080))
        self.screen_w = float(screen_w_px // 3 if debug_mode else screen_w_px)
        self.screen_h = float(screen_h_px // 2 if debug_mode else screen_h_px)

        self._t_accum = 0.0
        self._trail_x = np.zeros(self.n_trail, dtype=np.float64)
        self._trail_y = np.zeros(self.n_trail, dtype=np.float64)
        self._trail_colors = np.zeros((self.n_trail, 3), dtype=np.float64)

    @classmethod
    def get_available_patterns(cls) -> List[str]:
        return list(cls.EXPERIMENT_PATTERNS.keys())

    @classmethod
    def get_sync_channels(cls) -> List[str]:
        return ["Quad Right", "Quad Upper", "Node Trigger", "Reserved"]

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "Freq X": {
                "type": "float",
                "default": 3.0,
                "min": 0.1,
                "max": 100.0,
                "label": "Frequency X",
            },
            "Freq Y": {
                "type": "float",
                "default": 2.0,
                "min": 0.1,
                "max": 100.0,
                "label": "Frequency Y",
            },
            "Amplitude X": {
                "type": "float",
                "default": 400.0,
                "min": 1.0,
                "max": 2000.0,
                "label": "Amplitude X (px)",
            },
            "Amplitude Y": {
                "type": "float",
                "default": 300.0,
                "min": 1.0,
                "max": 2000.0,
                "label": "Amplitude Y (px)",
            },
            "Speed": {
                "type": "float",
                "default": 1.0,
                "min": 0.01,
                "max": 100.0,
                "label": "Speed multiplier",
            },
            "Trail Points": {
                "type": "int",
                "default": 64,
                "min": 1,
                "max": 10000,
                "label": "Trail Points",
            },
            "Trial Duration (s)": {
                "type": "float",
                "default": 10.0,
                "min": 0.1,
                "max": 600.0,
                "label": "Trial Duration (s)",
            },
            "Number of Trials": {
                "type": "int",
                "default": 5,
                "min": 1,
                "max": 9999,
                "label": "Number of Trials",
            },
            "Execution Mode": {
                "type": "choice",
                "default": "Auto",
                "choices": ["Auto", "Manual"],
                "label": "Execution Mode",
            },
        }

    def generate_trials(self, pattern_key: str) -> List[Dict[str, Any]]:
        n = int(
            self.config.get(
                "Number of Trials", self._schema_default("Number of Trials")
            )
        )
        return [{"type": "movement_trace", "trial_idx": i} for i in range(n)]

    def prepare_trial(self, trial_context: dict) -> str:
        self._t_accum = 0.0
        self._last_sin = 0.0
        self._trail_x = np.zeros(self.n_trail, dtype=np.float64)
        self._trail_y = np.zeros(self.n_trail, dtype=np.float64)
        self._trail_colors = np.zeros((self.n_trail, 3), dtype=np.float64)

        # Pre-allocate per-frame rendering arrays (avoids GC jitter)
        self._xys = np.empty((self.n_trail, 2), dtype=np.float64)
        trail_range = np.linspace(12.0, 2.0, self.n_trail, dtype=np.float64)
        self._sizes = np.empty((self.n_trail, 2), dtype=np.float64)
        self._sizes[:, 0] = trail_range
        self._sizes[:, 1] = trail_range
        self._opacities = np.ones(self.n_trail, dtype=np.float64)
        self._trail_linspace = np.linspace(1.0, 0.0, self.n_trail, dtype=np.float64)

        return ""

    def get_idle_frame(self, hw_telemetry: dict) -> Tuple[List[dict], dict, List[int]]:
        bg = {
            "id": "_bg",
            "type": "rect",
            "width": self.screen_w,
            "height": self.screen_h,
            "pos": (0, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        tel = {
            "phase": "Idle",
            "hw_cmd": None,
            "ui_color": "cyan",
            "ui_metrics": {
                "n_trail": self.n_trail,
                "pos_x": 0.0,
                "pos_y": 0.0,
                **hw_telemetry,
            },
            "ui_twin": None,
        }
        return [bg], tel, [0, 0, 0, 0]

    def process_frame(
        self, elapsed_time: float, trial_context: dict, hw_telemetry: dict
    ) -> Tuple[bool, List[dict], dict, List[int]]:
        self._t_accum = elapsed_time * self.speed
        t = self._t_accum

        x = self.amp_x * self.scale * math.sin(self.freq_x * t)
        y = self.amp_y * self.scale * math.sin(self.freq_y * t)

        # In-place shift (avoids np.roll allocating new arrays)
        self._trail_x[1:] = self._trail_x[:-1]
        self._trail_y[1:] = self._trail_y[:-1]
        self._trail_colors[1:] = self._trail_colors[:-1]
        self._trail_x[0] = x
        self._trail_y[0] = y
        self._trail_colors[0] = [1.0, 1.0, 1.0]

        self._trail_colors[:, 0] = 2.0 * self._trail_linspace - 1.0
        self._trail_colors[:, 1] = 2.0 * self._trail_linspace - 1.0
        self._trail_colors[:, 2] = 2.0 * self._trail_linspace - 1.0

        bg = {
            "id": "_bg",
            "type": "rect",
            "width": self.screen_w,
            "height": self.screen_h,
            "pos": (0, 0),
            "fillColor": [0, 0, 0],
            "lineColor": [0, 0, 0],
        }
        self._xys[:, 0] = self._trail_x
        self._xys[:, 1] = self._trail_y
        cmds = [
            bg,
            {
                "type": "element_array",
                "n_elements": self.n_trail,
                "xys": self._xys[::-1],
                "sizes": self._sizes[::-1],
                "colors": self._trail_colors[::-1],
                "opacities": self._opacities[::-1],
            },
        ]

        is_done = elapsed_time >= self.trial_duration

        q_right = 1 if x >= 0 else 0
        q_upper = 1 if y >= 0 else 0
        curr_sin = math.sin(self.freq_x * t)
        node_trigger = 1 if (self._last_sin * curr_sin <= 0 and t > 0) else 0
        self._last_sin = curr_sin

        tel = {
            "phase": "MovementTrace",
            "hw_cmd": None,
            "ui_color": "lime",
            "ui_metrics": {
                "n_trail": self.n_trail,
                "pos_x": round(x, 1),
                "pos_y": round(y, 1),
                **hw_telemetry,
            },
            "ui_twin": None,
        }
        return is_done, cmds, tel, [q_right, q_upper, node_trigger, 0]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARADIGM_REGISTRY: Dict[str, type] = {
    "Looming": LoomingParadigm,
    "ClassicLooming": ClassicLoomingParadigm,
    "OpticFlow": OpticFlowParadigm,
    "MovementTrace": MovementTraceParadigm,
}


def get_available_patterns() -> dict:
    mapping = {}
    for cls_name, cls_obj in PARADIGM_REGISTRY.items():
        for pat in cls_obj.get_available_patterns():
            mapping[pat] = cls_name
    return mapping
