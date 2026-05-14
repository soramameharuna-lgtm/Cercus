import multiprocessing as mp
import queue
import random
from typing import Dict, Any

from src.core.hardware import SerialDaemon, MockSerialDaemon, KinematicsParser
from src.core.logger import GroundTruthLogger
from src.core.render import CoreRenderer
from src.models.paradigm import PARADIGM_REGISTRY


def create_ipc_queues():
    return mp.Queue(maxsize=32), mp.Queue(maxsize=256)


def worker_entry(config, cmd_q, telemetry_q):
    GenericWorker(config, cmd_q, telemetry_q).run()


class ExperimentAbort(Exception):
    pass


class GenericWorker:
    def __init__(self, config: Dict[str, Any], cmd_q: mp.Queue, telemetry_q: mp.Queue):
        self.config = config
        self.cmd_queue = cmd_q
        self.telemetry_queue = telemetry_q

        p_name = config.get("Paradigm Class", "Looming")
        p_cls = PARADIGM_REGISTRY.get(p_name, PARADIGM_REGISTRY["Looming"])
        self.paradigm = p_cls(debug_mode=config.get("Debug Mode", False), config=config)

        self.parser = KinematicsParser()
        self.abort_flag = False
        self._last_tel_data = {"dx": "—", "dy": "—", "dz": "—", "stim_state": "—"}
        self.event = None

    def _push(self, frame: dict, force: bool = False):
        try:
            if force:
                self.telemetry_queue.put(frame, timeout=2.0)
            else:
                self.telemetry_queue.put_nowait(frame)
        except queue.Full:
            pass
        except (BrokenPipeError, EOFError):
            self.abort_flag = True

    def _sync_state(self, clear_keys: bool = True):
        if clear_keys and self.event:
            self.event.getKeys()

        parent = mp.parent_process()
        if parent is not None and not parent.is_alive():
            self.abort_flag = True

        if self.abort_flag:
            raise ExperimentAbort()

        try:
            while not self.cmd_queue.empty():
                cmd = self.cmd_queue.get_nowait()
                if cmd.get("action") == "ABORT":
                    self.abort_flag = True
                    raise ExperimentAbort()
        except queue.Empty:
            pass

    def _drain_hardware(self, logger: GroundTruthLogger, hw_daemon) -> dict:
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
            tel = self.parser.get_telemetry(items[-1][1])
            if tel.get("dx") != "err":
                self._last_tel_data = tel
        return self._last_tel_data

    def _build_telemetry(
        self, session_num: int, trial_idx: int, total_trials: int, data: dict
    ) -> dict:
        payload = {
            "action": "telemetry",
            "session_num": session_num,
            "trial_idx": trial_idx,
            "total_trials": total_trials,
        }
        payload.update(data)
        return payload

    def run(self):
        hw_daemon, logger, renderer, core_module = None, None, None, None
        try:
            from psychopy import core, event

            core_module = core
            self.event = event

            sp = self.config.get("Serial Port", "mock")
            hw_daemon = MockSerialDaemon() if sp == "mock" else SerialDaemon(sp)
            clock = core.Clock()
            hw_daemon.start(time_func=clock.getTime)

            self.event.globalKeys.add(
                key="escape", func=lambda: setattr(self, "abort_flag", True)
            )
            logger = GroundTruthLogger(self.config.get("_output_dir", "."))

            debug = self.config.get("Debug Mode", False)
            renderer = CoreRenderer(
                win_size=(1200, 600) if debug else (3840, 1080),
                is_fullscr=not debug,
                screen_id=0 if debug else int(self.config["Stimulus Screen ID"]),
                wait_blanking=not debug,
            )

            t0 = clock.getTime()
            while clock.getTime() - t0 < 5.0:
                self._sync_state()
                hw_tel = self._drain_hardware(logger, hw_daemon)
                cmds, tel = self.paradigm.get_idle_frame(hw_tel)
                tel["phase"] = "Adaptation"
                renderer.render_frame(cmds)
                self._push(self._build_telemetry(0, 0, 0, tel))

            if self.config.get("Execution Mode") == "Auto":
                self.event.clearEvents()
                while True:
                    self._sync_state(clear_keys=False)
                    hw_tel = self._drain_hardware(logger, hw_daemon)
                    cmds, tel = self.paradigm.get_idle_frame(hw_tel)
                    tel["phase"] = "WAIT [SPACE] (Auto Start)"
                    renderer.render_frame(cmds)
                    self._push(self._build_telemetry(0, 0, 0, tel))

                    keys = self.event.getKeys(["space", "escape"])
                    if "escape" in keys:
                        self.abort_flag = True
                        raise ExperimentAbort()
                    if "space" in keys:
                        break

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
                    if init_cmd:
                        hw_daemon.send_command(init_cmd)

                    if t_idx > 0:
                        dur = random.uniform(
                            *map(float, self.config["ITI Range (sec)"].split("-"))
                        )
                        t_iti = clock.getTime()
                        logger.log_event("iti_start", t_iti, duration=dur)
                        while clock.getTime() - t_iti < dur:
                            self._sync_state()
                            hw_tel = self._drain_hardware(logger, hw_daemon)
                            cmds, tel = self.paradigm.get_idle_frame(hw_tel)
                            tel["phase"] = f"ITI ({clock.getTime()-t_iti:.1f}s)"
                            renderer.render_frame(cmds)
                            self._push(
                                self._build_telemetry(
                                    current_session, t_idx, len(trials), tel
                                )
                            )

                    if self.config.get("Execution Mode") == "Manual":
                        # 强制排空 ITI 阶段累积的键盘缓冲，切断状态污染
                        self.event.clearEvents()
                        while True:
                            self._sync_state(clear_keys=False)
                            hw_tel = self._drain_hardware(logger, hw_daemon)
                            cmds, tel = self.paradigm.get_idle_frame(hw_tel)
                            tel["phase"] = "Wait [SPACE]"
                            renderer.render_frame(cmds)
                            self._push(
                                self._build_telemetry(
                                    current_session, t_idx, len(trials), tel
                                )
                            )

                            keys = self.event.getKeys(["space", "escape"])
                            if "escape" in keys:
                                self.abort_flag = True
                                raise ExperimentAbort()
                            if "space" in keys:
                                break

                    logger.advance_trial()
                    logger.log_event("trial_start", clock.getTime(), **trial)
                    t_trial = clock.getTime()

                    while True:
                        self._sync_state()
                        elap = clock.getTime() - t_trial
                        hw_tel = self._drain_hardware(logger, hw_daemon)

                        is_done, cmds, tel = self.paradigm.process_frame(
                            elap, trial, hw_tel
                        )
                        if is_done:
                            break

                        if tel.get("hw_cmd"):
                            hw_daemon.send_command(tel["hw_cmd"])

                        renderer.render_frame(cmds)
                        self._push(
                            self._build_telemetry(
                                current_session, t_idx + 1, len(trials), tel
                            )
                        )
                    logger.flush()

                if total_sessions == -1 or s_idx < total_sessions - 1:
                    isi_dur = random.uniform(
                        *map(float, self.config["ISI Range (sec)"].split("-"))
                    )
                    t_isi = clock.getTime()
                    while clock.getTime() - t_isi < isi_dur:
                        self._sync_state()
                        hw_tel = self._drain_hardware(logger, hw_daemon)
                        cmds, tel = self.paradigm.get_idle_frame(hw_tel)
                        tel["phase"] = f"ISI ({clock.getTime()-t_isi:.1f}s)"
                        renderer.render_frame(cmds)
                        self._push(
                            self._build_telemetry(current_session, 0, len(trials), tel)
                        )
                s_idx += 1

            self._push({"action": "worker_done"}, force=True)

        except ExperimentAbort:
            self._push({"action": "worker_abort"}, force=True)
        except Exception as e:
            self._push({"action": "worker_error", "error": str(e)}, force=True)
        finally:
            if hw_daemon:
                hw_daemon.stop()
            if logger:
                logger.close()
            if renderer:
                renderer.close()
            if core_module:
                core_module.quit()
