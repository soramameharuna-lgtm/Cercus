class CoreRenderer:
    def __init__(
        self, win_size: tuple, is_fullscr: bool, screen_id: int, wait_blanking: bool
    ):
        from psychopy import visual

        self.win = visual.Window(
            size=win_size,
            fullscr=is_fullscr,
            screen=screen_id,
            color=[0, 0, 0],
            colorSpace="rgb",
            units="pix",
            waitBlanking=wait_blanking,
        )
        self.objects = {}
        self.visual = visual

    def _create_obj(self, cmd: dict):
        t = cmd.get("type")
        if t == "circle":
            return self.visual.Circle(self.win, edges=128)
        elif t == "rect":
            return self.visual.Rect(self.win)
        return None

    def render_frame(self, commands: list):
        for cmd in commands:
            obj_id = cmd.get("id")
            if obj_id not in self.objects:
                obj = self._create_obj(cmd)
                if obj:
                    self.objects[obj_id] = obj

            obj = self.objects.get(obj_id)
            if not obj:
                continue

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

        self.win.flip()

    def close(self):
        self.win.close()
