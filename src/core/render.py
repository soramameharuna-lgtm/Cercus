from typing import Dict, Any
import numpy as np


class CoreRenderer:
    def __init__(
        self, win_size: tuple, is_fullscr: bool, screen_id: int, wait_blanking: bool
    ):
        from psychopy import visual

        self.win = visual.Window(
            size=win_size,
            fullscr=is_fullscr,
            screen=screen_id,
            # 中性灰背景 (PsychoPy RGB: 0 = 中灰, -1 = 纯黑, +1 = 纯白)
            color=[0, 0, 0],
            colorSpace="rgb",
            units="pix",
            waitBlanking=wait_blanking,
        )
        self.objects: Dict[str, Any] = {}
        self.visual = visual

    _TYPE_MAP: Dict[str, str] = {
        "circle": "Circle",
        "rect": "Rect",
    }

    def _create_obj(self, cmd: dict) -> Any:
        # New protocol: reflection-based instantiation
        class_name = cmd.get("class_name")
        if class_name:
            cls = getattr(self.visual, class_name, None)
            if cls is None:
                return None
            init_kwargs = cmd.get("init_kwargs", {})
            return cls(self.win, **init_kwargs)

        # Legacy protocol: hardcoded type dispatch
        t = cmd.get("type")
        if t == "element_array":
            return self.visual.ElementArrayStim(
                self.win,
                nElements=cmd.get("n_elements", 1),
                elementTex=np.ones((4, 4)),
                elementMask=None,
                sizes=cmd.get("sizes", 4.0),
                xys=cmd.get("xys", np.zeros((1, 2))),
                colors=cmd.get("colors", 1.0),
                colorSpace="rgb",
                opacities=cmd.get("opacities", 1.0),
            )

        mapped = self._TYPE_MAP.get(t)
        if mapped:
            cls = getattr(self.visual, mapped, None)
            if cls is not None:
                if mapped == "Circle":
                    return cls(
                        self.win,
                        edges=cmd.get("edges", 128),
                        fillColor=cmd.get("fillColor", [-1, -1, -1]),
                        lineColor=cmd.get("lineColor", [-1, -1, -1]),
                        lineWidth=cmd.get("lineWidth", 1.0),
                        colorSpace="rgb",
                    )
                return cls(self.win)
        return None

    def _apply_command(self, cmd: dict):
        obj_id = cmd.get("id")

        # ElementArrayStim: always a special-case rendering path
        if cmd.get("type") == "element_array":
            key = obj_id or "__element_array__"
            if key not in self.objects:
                self.objects[key] = self._create_obj(cmd)
            obj = self.objects[key]
            if obj is None:
                return
            for attr in ("xys", "sizes", "colors", "opacities"):
                if attr in cmd:
                    setattr(obj, attr, cmd[attr])
            if "xys" in cmd:
                obj.nElements = cmd.get("n_elements", len(cmd["xys"]))
            obj.draw()
            return

        if obj_id is None:
            return

        # Lazy instantiation on first encounter
        if obj_id not in self.objects:
            obj = self._create_obj(cmd)
            if obj:
                self.objects[obj_id] = obj

        obj = self.objects.get(obj_id)
        if not obj:
            return

        # New protocol: generic property updates
        updates = cmd.get("updates")
        if updates:
            for k, v in updates.items():
                try:
                    setattr(obj, k, v)
                except Exception:
                    pass
        else:
            if not hasattr(obj, "_state_cache"):
                obj._state_cache = {}

            if "radius" in cmd and obj._state_cache.get("radius") != cmd["radius"]:
                # 使用 size 替代 radius，通过 GPU 缩放 1x1 的基准包围盒，避免每帧重构顶点导致的严重掉帧
                obj.size = (cmd["radius"] * 2, cmd["radius"] * 2)
                obj._state_cache["radius"] = cmd["radius"]

            if "pos" in cmd and obj._state_cache.get("pos") != cmd["pos"]:
                obj.pos = cmd["pos"]
                obj._state_cache["pos"] = cmd["pos"]

            if "width" in cmd and "height" in cmd:
                sz = (cmd["width"], cmd["height"])
                if obj._state_cache.get("size") != sz:
                    obj.size = sz
                    obj._state_cache["size"] = sz

            if "fillColor" in cmd and obj._state_cache.get("fillColor") != cmd["fillColor"]:
                obj.fillColor = cmd["fillColor"]
                obj._state_cache["fillColor"] = cmd["fillColor"]

            if "lineColor" in cmd and obj._state_cache.get("lineColor") != cmd["lineColor"]:
                obj.lineColor = cmd["lineColor"]
                obj._state_cache["lineColor"] = cmd["lineColor"]

            if "lineWidth" in cmd and obj._state_cache.get("lineWidth") != cmd["lineWidth"]:
                obj.lineWidth = cmd["lineWidth"]
                obj._state_cache["lineWidth"] = cmd["lineWidth"]

        obj.draw()

    def draw_commands(self, commands: list):
        for cmd in commands:
            self._apply_command(cmd)

    def flip(self):
        self.win.flip()

    def render_frame(self, commands: list):
        self.draw_commands(commands)
        self.flip()

    def close(self):
        self.win.close()


class ScreenEnvironment:
    """Manages 4 fixed sync blocks at screen bottom-left and bottom-right,
    plus one parameterizable photodiode marker block."""

    def __init__(self, win, sync_topology: list, pd_pos=None, pd_size=60):
        self.win = win
        from psychopy import visual

        w, h = pd_size, pd_size
        win_w, win_h = win.size
        half_w = win_w / 2.0
        half_h = win_h / 2.0
        margin = 10

        self._frame_counter = 0
        self._sync_blocks: list[visual.Rect] = []

        # ── 4 个同步块：物理坐标锚定在屏幕底边 ──
        # 左外、左内、右内、右外
        positions = [
            (-half_w + margin + w / 2, -half_h + margin + h / 2),
            (-half_w + margin + w * 1.5 + margin, -half_h + margin + h / 2),
            (half_w - margin - w * 1.5 - margin, -half_h + margin + h / 2),
            (half_w - margin - w / 2, -half_h + margin + h / 2),
        ]
        for pos in positions:
            sb = visual.Rect(
                win, width=w, height=h, pos=pos,
                fillColor=[-1, -1, -1], lineColor=[-1, -1, -1], colorSpace="rgb"
            )
            self._sync_blocks.append(sb)

        # ── 光电二极管标记块（Photodiode Marker）──
        # 位置参数化：通过配置指定绝对像素坐标 (从屏幕左上角原点)
        # 转换到 PsychoPy 坐标系 (中心原点): x - half_w, half_h - y
        if pd_pos is not None:
            pd_x, pd_y = pd_pos
            psy_x = pd_x - half_w
            psy_y = half_h - pd_y
        else:
            # 默认：左屏右下角 (约 x=1850, y=1030 在 3840×1080 下)
            psy_x = -half_w + margin + w * 1.5 + margin
            psy_y = -half_h + margin + h / 2
        self._pd_block = visual.Rect(
            win, width=w, height=h, pos=(psy_x, psy_y),
            fillColor=[-1, -1, -1], lineColor=[-1, -1, -1], colorSpace="rgb"
        )
        self._pd_last_color = [-1, -1, -1]

    def render(self, sync_states: list[int]):
        self._frame_counter += 1
        off = [-1, -1, -1]
        on = [1, 1, 1]

        if len(sync_states) == 4:
            # Direct 1:1 mapping for advanced 4-channel paradigms
            assignments = [on if s else off for s in sync_states]
        else:
            # Legacy fallback for 1 or 2 channel paradigms (e.g., Looming, OpticFlow)
            odd = self._frame_counter % 2 == 1
            trial_active = sync_states[0] if len(sync_states) > 0 else 0

            outer_color = on if (trial_active and odd) else off
            inner_color = on if trial_active else off

            assignments = [outer_color, inner_color, inner_color, outer_color]

        for sb, color in zip(self._sync_blocks, assignments):
            if getattr(sb, "_last_color", None) != color:
                sb.fillColor = color
                sb.lineColor = color
                sb._last_color = color
            sb.draw()

        # ── 光电块：绝对最后绘制，不被任何图层遮挡 ──
        # sync_states[0] = stim_active → 亮; 否则灭
        pd_on = sync_states[0] if len(sync_states) > 0 else 0
        pd_color = on if pd_on else off
        if self._pd_last_color != pd_color:
            self._pd_block.fillColor = pd_color
            self._pd_block.lineColor = pd_color
            self._pd_last_color = pd_color
        self._pd_block.draw()
