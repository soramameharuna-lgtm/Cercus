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
            color=[-1, -1, -1],
            colorSpace="rgb",
            units="pix",
            waitBlanking=wait_blanking,
        )
        self.objects: Dict[str, Any] = {}
        self.visual = visual

    def _create_obj(self, cmd: dict):
        t = cmd.get("type")
        if t == "circle":
            return self.visual.Circle(self.win, edges=128)
        elif t == "rect":
            return self.visual.Rect(self.win)
        elif t == "element_array":
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
        return None

    def _apply_command(self, cmd: dict):
        obj_id = cmd.get("id")
        cmd_type = cmd.get("type")

        if cmd_type == "element_array":
            key = obj_id or "__element_array__"
            if key not in self.objects:
                self.objects[key] = self._create_obj(cmd)
            obj = self.objects[key]
            if obj is None:
                return
            if "xys" in cmd:
                obj.xys = cmd["xys"]
                obj.nElements = cmd.get("n_elements", len(cmd["xys"]))
            if "sizes" in cmd:
                obj.sizes = cmd["sizes"]
            if "colors" in cmd:
                obj.colors = cmd["colors"]
            if "opacities" in cmd:
                obj.opacities = cmd["opacities"]
            obj.draw()
            return

        if obj_id is None:
            return
        if obj_id not in self.objects:
            obj = self._create_obj(cmd)
            if obj:
                self.objects[obj_id] = obj

        obj = self.objects.get(obj_id)
        if not obj:
            return

        if "radius" in cmd:
            obj.radius = cmd["radius"]
        if "pos" in cmd:
            obj.pos = cmd["pos"]
        if "width" in cmd and "height" in cmd:
            obj.size = (cmd["width"], cmd["height"])
        if "fillColor" in cmd:
            obj.fillColor = cmd["fillColor"]
        if "lineColor" in cmd:
            obj.lineColor = cmd["lineColor"]

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
    """Manages 4 fixed sync blocks at screen bottom-left and bottom-right."""

    def __init__(self, win, sync_topology: list):
        self.win = win
        from psychopy import visual

        w, h = 60, 60
        win_w, win_h = win.size
        half_w = win_w / 2.0
        half_h = win_h / 2.0
        margin = 10

        self._frame_counter = 0
        self._sync_blocks: list[visual.Rect] = []

        # 强制物理坐标：左外、左内、右内、右外
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

    def render(self, sync_states: list[int]):
        self._frame_counter += 1
        odd = self._frame_counter % 2 == 1
        trial_active = sync_states[0] if len(sync_states) > 0 else 0

        off = [-1, -1, -1]
        on = [1, 1, 1]

        outer_color = on if (trial_active and odd) else off
        inner_color = on if trial_active else off

        assignments = [outer_color, inner_color, inner_color, outer_color]
        for sb, color in zip(self._sync_blocks, assignments):
            sb.fillColor = color
            sb.lineColor = color
            sb.draw()
