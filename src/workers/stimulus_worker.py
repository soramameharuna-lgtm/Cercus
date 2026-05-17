import multiprocessing as mp
import queue
import random
import signal
import time
import numpy
from typing import Dict, Any, List

from src.core.hardware import SerialDaemon, MockSerialDaemon, KinematicsParser
from src.core.kinematics import KinematicEngine
from src.core.logger import GroundTruthLogger
from src.core.render import CoreRenderer, ScreenEnvironment
from src.models.paradigm import PARADIGM_REGISTRY


def create_ipc_queues():
    return mp.Queue(maxsize=32), mp.Queue(maxsize=256)


def _term_handler(signum, frame):
    raise SystemExit(f"Received signal {signum}")


def worker_entry(config, cmd_q, telemetry_q):
    import sys
    signal.signal(signal.SIGTERM, _term_handler)
    signal.signal(signal.SIGINT, _term_handler)

    try:
        GenericWorker(config, cmd_q, telemetry_q).run()
    finally:
        sys.exit(0)


class ExperimentAbort(Exception):
    pass


class HardwareDisconnectError(Exception):
    pass


class GenericWorker:
    def __init__(self, config: Dict[str, Any], cmd_q: mp.Queue, telemetry_q: mp.Queue):
        from src.models.paradigm import BaseParadigm
        BaseParadigm._apply_random_seed(config)
        self.config = config
        self.cmd_queue = cmd_q
        self.telemetry_queue = telemetry_q

        p_name = config.get("Paradigm Class", "Looming")
        p_cls = PARADIGM_REGISTRY.get(p_name, PARADIGM_REGISTRY["Looming"])
        self.paradigm = p_cls(debug_mode=config.get("Debug Mode", False), config=config)

        schema = self.paradigm.get_telemetry_schema()
        self.parser = KinematicsParser(schema, calib_factors=config.get("calib_factors"))
        matrix = config.get("calib_matrix")
        if (isinstance(matrix, list) and len(matrix) == 3
                and all(isinstance(r, list) and len(r) == 3 for r in matrix)):
            self.parser.set_calib_matrix(matrix)
        self.abort_flag = False
        self._last_tel_data = {h: 0.0 for _, _, h in schema}
        self.event = None
        self._last_telemetry_push = 0.0
        self._telemetry_interval = 0.03  # 30ms downsampling interval

        self.kinematic_engine = KinematicEngine(
            error_callback=self._kinematic_error_handler
        )

    def _push(self, frame: dict, force: bool = False):
        try:
            if force:
                try:
                    self.telemetry_queue.put(frame, timeout=0.1)
                except queue.Full:
                    self.abort_flag = True
                except (BrokenPipeError, EOFError, ValueError):
                    self.abort_flag = True
                return

            now = time.monotonic()
            if now - self._last_telemetry_push < self._telemetry_interval:
                return
            self._last_telemetry_push = now

            self.telemetry_queue.put_nowait(frame)
        except queue.Full:
            pass
        except (BrokenPipeError, EOFError, ValueError):
            self.abort_flag = True

    def _sync_state(self, clear_keys: bool = True):
        if clear_keys and self.event:
            self.event.getKeys()

        parent = mp.parent_process()
        if parent is not None and not parent.is_alive():
            self.abort_flag = True

        if self.abort_flag:
            raise ExperimentAbort("Received abort command")

        try:
            while not self.cmd_queue.empty():
                cmd = self.cmd_queue.get_nowait()
                if cmd.get("action") in ("ABORT", "POISON_PILL"):
                    self.abort_flag = True
                    break
        except queue.Empty:
            pass

        if self.abort_flag:
            raise ExperimentAbort("Received abort command")

    def _drain_hardware(self, logger, hw_daemon) -> dict:
        items = hw_daemon.drain_queue()
        if items:
            if logger and logger.is_open():
                kin_rows = [
                    p
                    for sys_t, raw in items
                    if (p := self.parser.parse(sys_t, raw, logger.global_trial_id))
                ]
                if kin_rows:
                    logger.log_kinematics_batch(kin_rows)
                if logger.kin_buffer_size() > 10000:
                    logger.flush_kinematics()

            for sys_t, raw in items:
                tel = self.parser.get_telemetry(raw)
                self.kinematic_engine.update(
                    float(sys_t),
                    float(tel.get("dx", 0.0)),
                    float(tel.get("dy", 0.0)),
                    float(tel.get("dz", 0.0)),
                )

            self._last_tel_data = self.parser.get_telemetry(items[-1][1])

        return self._inject_kinematics(self._last_tel_data)

    def _kinematic_error_handler(self, err_type: str, msg: str, data: object):
        pass

    def _inject_kinematics(self, hw_tel: dict) -> dict:
        eng = self.kinematic_engine
        return {
            **hw_tel,
            "k_angle": round(eng.cum_dz, 2),
            "k_turn_speed": round(eng.turn_speed, 2),
            "k_move_speed": round(eng.move_speed, 2),
            "k_disp": round(eng.cum_disp, 2),
            "pos_x": round(getattr(eng, "pos_x", 0.0), 2),
            "pos_y": round(getattr(eng, "pos_y", 0.0), 2),
        }

    @staticmethod
    def _sanitize_metrics(metrics: dict) -> dict:
        sanitized = {}
        for k, v in metrics.items():
            if isinstance(v, numpy.ndarray):
                if v.size <= 4:
                    sanitized[k] = v.tolist()
                else:
                    sanitized[k] = f"[array:{v.shape}]"
            elif isinstance(v, (list, tuple)):
                if len(v) <= 8:
                    sanitized[k] = v
                else:
                    sanitized[k] = f"[list:{len(v)}]"
            elif isinstance(v, dict):
                sanitized[k] = f"[dict:{len(v)}]"
            else:
                sanitized[k] = v
        return sanitized

    def _build_telemetry(
        self, session_num: int, trial_idx: int, total_trials: int, data: dict,
        hw_tel: dict = None,
    ) -> dict:
        payload = {
            "action": "telemetry",
            "session_num": session_num,
            "trial_idx": trial_idx,
            "total_trials": total_trials,
        }
        ui_metrics = data.get("ui_metrics", {})
        if hw_tel:
            for key in ("k_angle", "k_turn_speed", "k_move_speed", "k_disp", "pos_x", "pos_y"):
                if key in hw_tel:
                    ui_metrics[key] = hw_tel[key]
        data = {**data, "ui_metrics": self._sanitize_metrics(ui_metrics)}
        payload.update(data)
        return payload

    def _present(
        self,
        renderer: CoreRenderer,
        env: ScreenEnvironment,
        cmds: list,
        sync_states: List[int],
    ):
        renderer.draw_commands(cmds)
        env.render(sync_states)
        renderer.flip()

    def run(self):
        hw_daemon, logger, renderer, core_module = None, None, None, None
        try:
            from psychopy import core, event

            core_module = core
            self.event = event

            sp = self.config.get("Serial Port", "mock")
            clock = core.Clock()
            if sp == "mock":
                hw_daemon = MockSerialDaemon()
                hw_daemon.start(
                    time_func=clock.getTime,
                    mock_generator=self.paradigm.get_mock_generator(),
                )
            else:
                hw_daemon = SerialDaemon(sp)
                hw_daemon.start(time_func=clock.getTime)

            self.event.globalKeys.add(
                key="escape", func=lambda: setattr(self, "abort_flag", True)
            )
            logger = GroundTruthLogger(self.config.get("_output_dir", "."))
            logger.log_event("session_config", clock.getTime(), seed=self.config.get("Random Seed"))

            debug = self.config.get("Debug Mode", False)
            screen_w_px = int(self.config.get("Screen Width (px)", 3840))
            screen_h_px = int(self.config.get("Screen Height (px)", 1080))
            renderer = CoreRenderer(
                win_size=(screen_w_px // 3, screen_h_px // 2) if debug else (screen_w_px, screen_h_px),
                is_fullscr=not debug,
                screen_id=0 if debug else int(self.config["Stimulus Screen ID"]),
                wait_blanking=not debug,
            )

            sync_topology: List[Dict[str, Any]] = self.config.get("Sync Topology", [])
            env = ScreenEnvironment(renderer.win, sync_topology)

            # --- Adaptation ---
            self.kinematic_engine.reset()
            t0 = clock.getTime()
            while clock.getTime() - t0 < 5.0:
                self._sync_state()
                hw_tel = self._drain_hardware(logger, hw_daemon)
                if hw_daemon and not hw_daemon.is_alive():
                    raise HardwareDisconnectError("Serial daemon died")
                cmds, tel, sync_states = self.paradigm.get_idle_frame(hw_tel)
                tel["phase"] = "Adaptation"
                tel["ui_color"] = "#ff4d4d"
                self._present(renderer, env, cmds, sync_states)
                self._push(self._build_telemetry(0, 0, 0, tel, hw_tel=hw_tel))
            logger.flush_kinematics()

            # --- Auto-start wait ---
            if self.config.get("Execution Mode") in ("Auto", "Kinematic"):
                self.kinematic_engine.reset()
                self.event.clearEvents()
                while True:
                    self._sync_state(clear_keys=False)
                    if self.abort_flag:
                        raise ExperimentAbort("Aborted during wait phase")
                    hw_tel = self._drain_hardware(logger, hw_daemon)
                    if hw_daemon and not hw_daemon.is_alive():
                        raise HardwareDisconnectError("Serial daemon died")
                    cmds, tel, sync_states = self.paradigm.get_idle_frame(hw_tel)
                    tel["phase"] = "WAIT [SPACE] (Auto Start)"
                    tel["ui_color"] = "orange"
                    self._present(renderer, env, cmds, sync_states)
                    self._push(self._build_telemetry(0, 0, 0, tel, hw_tel=hw_tel))

                    all_keys = self.event.getKeys()
                    keys = [k for k in all_keys if k in ["space", "escape"]]
                    if "escape" in keys:
                        self.abort_flag = True
                        raise ExperimentAbort()
                    if "space" in keys:
                        break

            # --- Session loop ---
            total_sessions = int(self.config["Total Sessions"])
            s_idx = 0
            while True:
                if total_sessions != -1 and s_idx >= total_sessions:
                    break

                self._sync_state()
                current_session = int(self.config["Session Number"]) + s_idx
                trials = self.paradigm.generate_trials(
                    self.config["Experiment Pattern"]
                )

                logger.open_session(
                    self.config.get("Subject ID"),
                    current_session,
                    self.parser.get_headers(),
                )

                for t_idx, trial in enumerate(trials):
                    self._sync_state()
                    init_cmd = self.paradigm.prepare_trial(trial)

                    # --- ITI ---
                    if t_idx > 0:
                        iti_raw = self.config.get("ITI Range (sec)", "0-0")
                        parts = iti_raw.split("-")
                        dur = random.uniform(float(parts[0]), float(parts[1])) if len(parts) == 2 else float(parts[0])
                        if dur <= 0:
                            pass
                        else:
                            self.kinematic_engine.reset()
                            t_iti = clock.getTime()
                            logger.log_event("iti_start", t_iti, duration=dur)
                            while clock.getTime() - t_iti < dur:
                                self._sync_state()
                                hw_tel = self._drain_hardware(logger, hw_daemon)
                                if hw_daemon and not hw_daemon.is_alive():
                                    raise HardwareDisconnectError("Serial daemon died")
                                cmds, tel, sync_states = self.paradigm.get_idle_frame(
                                    hw_tel
                                )
                                tel["phase"] = f"ITI ({clock.getTime()-t_iti:.1f}s)"
                                tel["ui_color"] = "orange"
                                self._present(renderer, env, cmds, sync_states)
                                self._push(
                                    self._build_telemetry(
                                        current_session, t_idx, len(trials), tel,
                                        hw_tel=hw_tel,
                                    )
                                )

                    # --- Manual wait ---
                    if self.config.get("Execution Mode") == "Manual":
                        self.kinematic_engine.reset()
                        self.event.clearEvents()
                        while True:
                            self._sync_state(clear_keys=False)
                            if self.abort_flag:
                                raise ExperimentAbort("Aborted during wait phase")
                            hw_tel = self._drain_hardware(logger, hw_daemon)
                            if hw_daemon and not hw_daemon.is_alive():
                                raise HardwareDisconnectError("Serial daemon died")
                            cmds, tel, sync_states = self.paradigm.get_idle_frame(
                                hw_tel
                            )
                            tel["phase"] = "Wait [SPACE]"
                            tel["ui_color"] = "orange"
                            self._present(renderer, env, cmds, sync_states)
                            self._push(
                                self._build_telemetry(
                                    current_session, t_idx, len(trials), tel,
                                    hw_tel=hw_tel,
                                )
                            )

                            all_keys = self.event.getKeys()
                            keys = [k for k in all_keys if k in ["space", "escape"]]
                            if "escape" in keys:
                                self.abort_flag = True
                                raise ExperimentAbort()
                            if "space" in keys:
                                break

                    # --- Kinematic wait ---
                    if self.config.get("Execution Mode") == "Kinematic":
                        self.kinematic_engine.reset()
                        trig_dist = float(self.config.get("Trigger Dist (mm)", 5.0))
                        trig_angle = float(self.config.get("Trigger Angle (°)", 10.0))
                        trig_speed = float(self.config.get("Trigger Speed (units/s)", 0.0))
                        trig_speed_dur = float(self.config.get("Trigger Duration (ms)", 500.0))
                        while True:
                            self._sync_state(clear_keys=False)
                            if self.abort_flag:
                                raise ExperimentAbort("Aborted during wait phase")
                            hw_tel = self._drain_hardware(logger, hw_daemon)
                            if hw_daemon and not hw_daemon.is_alive():
                                raise HardwareDisconnectError("Serial daemon died")
                            cmds, tel, sync_states = self.paradigm.get_idle_frame(
                                hw_tel
                            )
                            tel["phase"] = (
                                f"Kinematic Δ={self.kinematic_engine.cum_disp:.1f}"
                                f" θ={self.kinematic_engine.cum_dz:.1f}"
                            )
                            tel["ui_color"] = "yellow"
                            self._present(renderer, env, cmds, sync_states)
                            self._push(
                                self._build_telemetry(
                                    current_session, t_idx, len(trials), tel,
                                    hw_tel=hw_tel,
                                )
                            )

                            all_keys = self.event.getKeys()
                            if "escape" in all_keys:
                                self.abort_flag = True
                                raise ExperimentAbort()
                            if self.kinematic_engine.evaluate_trigger(
                                trig_dist, trig_angle,
                                trig_speed, trig_speed_dur,
                            ):
                                break

                    logger.advance_trial()
                    logger.log_event("trial_start", clock.getTime(), **trial)
                    self.kinematic_engine.reset()
                    t_trial = clock.getTime()
                    if init_cmd:
                        hw_daemon.send_command(init_cmd)

                    # --- Trial frame loop ---
                    while True:
                        self._sync_state()
                        elap = clock.getTime() - t_trial
                        hw_tel = self._drain_hardware(logger, hw_daemon)
                        if hw_daemon and not hw_daemon.is_alive():
                            raise HardwareDisconnectError("Serial daemon died")

                        is_done, cmds, tel, sync_states = self.paradigm.process_frame(
                            elap, trial, hw_tel
                        )
                        if is_done:
                            break

                        if tel.get("hw_cmd"):
                            hw_daemon.send_command(tel["hw_cmd"])

                        self._present(renderer, env, cmds, sync_states)
                        self._push(
                            self._build_telemetry(
                                current_session, t_idx + 1, len(trials), tel,
                                hw_tel=hw_tel,
                            )
                        )
                    logger.flush()
                    logger.flush_kinematics()

                # --- ISI ---
                if total_sessions == -1 or s_idx < total_sessions - 1:
                    isi_raw = self.config.get("ISI Range (sec)", "0-0")
                    isi_parts = isi_raw.split("-")
                    isi_dur = random.uniform(float(isi_parts[0]), float(isi_parts[1])) if len(isi_parts) == 2 else float(isi_parts[0])
                    if isi_dur <= 0:
                        pass
                    else:
                        self.kinematic_engine.reset()
                        t_isi = clock.getTime()
                        while clock.getTime() - t_isi < isi_dur:
                            self._sync_state()
                            hw_tel = self._drain_hardware(logger, hw_daemon)
                            if hw_daemon and not hw_daemon.is_alive():
                                raise HardwareDisconnectError("Serial daemon died")
                            cmds, tel, sync_states = self.paradigm.get_idle_frame(hw_tel)
                            tel["phase"] = f"ISI ({clock.getTime()-t_isi:.1f}s)"
                            tel["ui_color"] = "orange"
                            self._present(renderer, env, cmds, sync_states)
                            self._push(
                                self._build_telemetry(current_session, 0, len(trials), tel, hw_tel=hw_tel)
                            )
                        logger.flush_kinematics()
                s_idx += 1

            logger.flush_kinematics()
            self._push({"action": "worker_done"}, force=True)

        except ExperimentAbort:
            self._push({"action": "worker_abort"}, force=True)
            time.sleep(0.1)
        except Exception as e:
            self._push({"action": "worker_error", "error": str(e)}, force=True)
            time.sleep(0.1)
        finally:
            # 仅清空 cmd_queue，绝对不能清空 telemetry_queue 以免吞掉发出的终端信号
            if self.cmd_queue:
                try:
                    while not self.cmd_queue.empty():
                        self.cmd_queue.get_nowait()
                except Exception:
                    pass

            # 取消底层 pipe 的阻塞等待
            for q in (self.cmd_queue, self.telemetry_queue):
                try:
                    if q is not None:
                        q.cancel_join_thread()
                except Exception:
                    pass

            if hw_daemon:
                hw_daemon.stop()
            if logger:
                logger.flush_kinematics()
                logger.close()
            if renderer:
                renderer.close()
