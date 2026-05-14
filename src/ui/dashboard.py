import os
import sys
import queue
import multiprocessing as mp
from typing import Dict, Any, Optional, List
import customtkinter as ctk

try:
    import serial
    import serial.tools.list_ports

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

from src.models.paradigm import PARADIGM_REGISTRY
from src.workers.stimulus_worker import worker_entry, create_ipc_queues


class MasterDashboard:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Experiment Controller")
        self.root.geometry("1100x750")

        try:
            self.root.tk.call("tk", "scaling", 1.0)
        except Exception:
            pass

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.worker_process: Optional[mp.Process] = None
        self.cmd_queue: Optional[mp.Queue] = None
        self.telemetry_queue: Optional[mp.Queue] = None

        self.auto_patterns = PARADIGM_REGISTRY["Looming"].get_available_patterns()
        self.manual_patterns = PARADIGM_REGISTRY[
            "ClassicLooming"
        ].get_available_patterns()

        self._create_widgets()
        self._load_default_config()
        self.root.after(16, self._poll_telemetry)

    def _get_serial_ports(self) -> List[str]:
        ports = []
        if HAS_SERIAL:
            ports = [p.device for p in serial.tools.list_ports.comports()]
        defaults = ["mock"]
        if sys.platform == "win32":
            defaults.append("COM3")
        else:
            defaults.append("/dev/ttyACM0")

        seen = set()
        result = []
        for p in defaults + ports:
            if p not in seen:
                seen.add(p)
                result.append(p)
        return result

    def _create_widgets(self):
        main_frame = ctk.CTkFrame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        cfg_frame = ctk.CTkFrame(main_frame)
        cfg_frame.pack(fill="x", pady=5, padx=5)

        ctk.CTkLabel(cfg_frame, text="Experiment Mode:").grid(
            row=0, column=0, sticky="w", padx=10, pady=5
        )
        self.pattern_var = ctk.StringVar()
        self.pattern_menu = ctk.CTkOptionMenu(
            cfg_frame, variable=self.pattern_var, values=self.auto_patterns
        )
        self.pattern_menu.grid(row=0, column=1, sticky="w", padx=10, pady=5)

        ctk.CTkLabel(cfg_frame, text="Subject ID:").grid(
            row=1, column=0, sticky="w", padx=10, pady=5
        )
        self.subject_var = ctk.StringVar(value="cricket_001")
        ctk.CTkEntry(cfg_frame, textvariable=self.subject_var).grid(
            row=1, column=1, sticky="w", padx=10, pady=5
        )

        self.lbl_sess_main = ctk.CTkLabel(cfg_frame, text="Start Session / Total:")
        self.lbl_sess_main.grid(row=2, column=0, sticky="w", padx=10, pady=5)

        sess_frame = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        sess_frame.grid(row=2, column=1, sticky="w", padx=10)

        self.session_start_var = ctk.StringVar(value="1")
        self.session_total_var = ctk.StringVar(value="2")
        self.entry_sess_start = ctk.CTkEntry(
            sess_frame, textvariable=self.session_start_var, width=60
        )
        self.entry_sess_start.pack(side="left")
        self.lbl_sess_div = ctk.CTkLabel(sess_frame, text=" / ")
        self.lbl_sess_div.pack(side="left")
        self.entry_sess_total = ctk.CTkEntry(
            sess_frame, textvariable=self.session_total_var, width=60
        )
        self.entry_sess_total.pack(side="left")

        ctk.CTkLabel(cfg_frame, text="ITI Range (sec):").grid(
            row=3, column=0, sticky="w", padx=10, pady=5
        )
        self.iti_range_var = ctk.StringVar(value="60-90")
        ctk.CTkEntry(cfg_frame, textvariable=self.iti_range_var).grid(
            row=3, column=1, sticky="w", padx=10, pady=5
        )

        ctk.CTkLabel(cfg_frame, text="ISI Range (sec):").grid(
            row=4, column=0, sticky="w", padx=10, pady=5
        )
        self.isi_range_var = ctk.StringVar(value="300-600")
        ctk.CTkEntry(cfg_frame, textvariable=self.isi_range_var).grid(
            row=4, column=1, sticky="w", padx=10, pady=5
        )

        ctk.CTkLabel(cfg_frame, text="Serial Port / Screen ID:").grid(
            row=5, column=0, sticky="w", padx=10, pady=5
        )
        hw_frame = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        hw_frame.grid(row=5, column=1, sticky="w", padx=10)

        serial_ports = self._get_serial_ports()
        self.serial_port_var = ctk.StringVar(
            value=serial_ports[0] if serial_ports else "mock"
        )
        self.screen_id_var = ctk.StringVar(value="1")

        ctk.CTkOptionMenu(
            hw_frame, variable=self.serial_port_var, values=serial_ports, width=110
        ).pack(side="left", padx=(0, 5))
        ctk.CTkEntry(hw_frame, textvariable=self.screen_id_var, width=50).pack(
            side="left"
        )

        self.debug_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(cfg_frame, text="Debug Mode", variable=self.debug_var).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=10, pady=10
        )

        ctk.CTkLabel(cfg_frame, text="Execution Mode:").grid(
            row=0, column=2, sticky="w", padx=10, pady=5
        )
        # ------------------------------------
        # Here to change default exec mode
        # ------------------------------------
        self.exec_mode_var = ctk.StringVar(value="Auto")
        self.exec_mode_var.trace_add("write", self._on_mode_change)
        self.mode_menu = ctk.CTkOptionMenu(
            cfg_frame, variable=self.exec_mode_var, values=["Auto", "Manual"]
        )
        self.mode_menu.grid(row=0, column=3, sticky="w", padx=10, pady=5)

        self.lbl_phys = ctk.CTkLabel(cfg_frame, text="l/v Ratio (ms) / Trials:")
        self.lbl_phys.grid(row=1, column=2, sticky="w", padx=10, pady=5)
        self.phys_frame = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        self.phys_frame.grid(row=1, column=3, sticky="w", padx=10)
        self.lv_ratio_var = ctk.StringVar(value="80")
        self.num_trials_var = ctk.StringVar(value="18")
        ctk.CTkEntry(self.phys_frame, textvariable=self.lv_ratio_var, width=60).pack(
            side="left", padx=(0, 5)
        )
        ctk.CTkEntry(self.phys_frame, textvariable=self.num_trials_var, width=60).pack(
            side="left"
        )

        self.lbl_deg = ctk.CTkLabel(cfg_frame, text="Init / Final Degree:")
        self.lbl_deg.grid(row=2, column=2, sticky="w", padx=10, pady=5)
        self.deg_frame = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        self.deg_frame.grid(row=2, column=3, sticky="w", padx=10)
        self.init_deg_var = ctk.StringVar(value="2.0")
        self.final_deg_var = ctk.StringVar(value="180.0")
        ctk.CTkEntry(self.deg_frame, textvariable=self.init_deg_var, width=60).pack(
            side="left", padx=(0, 5)
        )
        ctk.CTkEntry(self.deg_frame, textvariable=self.final_deg_var, width=60).pack(
            side="left"
        )

        status_frame = ctk.CTkFrame(
            main_frame, fg_color=("gray90", "gray13"), corner_radius=10
        )
        status_frame.pack(fill="x", pady=10, padx=5)
        status_frame.grid_columnconfigure(0, weight=1)
        status_frame.grid_columnconfigure(1, weight=1)

        metrics_frame = ctk.CTkFrame(status_frame, fg_color="transparent")
        metrics_frame.grid(row=0, column=0, sticky="nsew", padx=20, pady=15)

        self.lbl_phase_val = self._add_metric_row(
            metrics_frame, "Phase:", 0, color="gray"
        )
        self.lbl_sess_val = self._add_metric_row(metrics_frame, "Session:", 1)
        self.lbl_trial_val = self._add_metric_row(metrics_frame, "Trial Progress:", 2)
        self.lbl_hw_val = self._add_metric_row(
            metrics_frame, "Hardware State:", 3, color="cyan"
        )

        twin_frame = ctk.CTkFrame(status_frame, fg_color="transparent")
        twin_frame.grid(row=0, column=1, sticky="e", padx=20, pady=15)

        self.canvas = ctk.CTkCanvas(
            twin_frame,
            width=400,
            height=150,
            bg="black",
            highlightthickness=1,
            highlightbackground="#333333",
        )
        self.canvas.pack()

        ctrl_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        ctrl_frame.pack(fill="x", pady=10)
        self.start_btn = ctk.CTkButton(
            ctrl_frame,
            text="▶ Start Experiment",
            fg_color="green",
            hover_color="darkgreen",
            command=self.start_experiment,
        )
        self.start_btn.pack(side="left", padx=10)
        self.stop_btn = ctk.CTkButton(
            ctrl_frame,
            text="⏹ Stop Experiment",
            fg_color="red",
            hover_color="darkred",
            state="disabled",
            command=self.stop_experiment,
        )
        self.stop_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(self.root, text="Ready", fg_color="gray15")
        self.status_label.pack(side="bottom", fill="x")

        self._on_mode_change()

    def _add_metric_row(self, parent, label_text, row, color="white"):
        lbl = ctk.CTkLabel(
            parent,
            text=label_text,
            text_color=("gray35", "gray70"),
            font=("Segoe UI", 14),
        )
        lbl.grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
        val = ctk.CTkLabel(
            parent, text="—", text_color=color, font=("Segoe UI", 14, "bold")
        )
        val.grid(row=row, column=1, sticky="w", pady=4)
        return val

    def _on_mode_change(self, *args):
        mode = self.exec_mode_var.get()
        if mode == "Auto":
            self.pattern_menu.configure(values=self.auto_patterns)
            if self.auto_patterns:
                self.pattern_var.set(self.auto_patterns[0])
            self.lbl_phys.grid_remove()
            self.phys_frame.grid_remove()
            self.lbl_deg.grid_remove()
            self.deg_frame.grid_remove()

            self.lbl_sess_main.configure(text="Start Session / Total:")
            self.lbl_sess_div.pack(side="left")
            self.entry_sess_total.pack(side="left")
        else:
            self.pattern_menu.configure(values=self.manual_patterns)
            if self.manual_patterns:
                self.pattern_var.set(self.manual_patterns[0])
            self.lbl_phys.grid()
            self.phys_frame.grid()
            self.lbl_deg.grid()
            self.deg_frame.grid()

            self.lbl_sess_main.configure(text="Start Session:")
            self.lbl_sess_div.pack_forget()
            self.entry_sess_total.pack_forget()

    def _load_default_config(self):
        pass

    def _safe_int(self, val_str: str, default: int) -> int:
        return int(val_str) if val_str.strip().isdigit() else default

    def _safe_float(self, val_str: str, default: float) -> float:
        try:
            return float(val_str)
        except ValueError:
            return default

    def _build_config(self) -> Dict[str, Any]:
        root_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        out_dir = os.path.join(root_dir, "data")
        os.makedirs(out_dir, exist_ok=True)

        mode = self.exec_mode_var.get()
        paradigm_class = "Looming" if mode == "Auto" else "ClassicLooming"
        total_sessions = (
            self._safe_int(self.session_total_var.get(), 2) if mode == "Auto" else -1
        )

        return {
            "Subject ID": self.subject_var.get().strip() or "cricket_001",
            "Session Number": self._safe_int(self.session_start_var.get(), 1),
            "Total Sessions": total_sessions,
            "ITI Range (sec)": self.iti_range_var.get().strip() or "60-90",
            "ISI Range (sec)": self.isi_range_var.get().strip() or "300-600",
            "Experiment Pattern": self.pattern_var.get(),
            "Paradigm Class": paradigm_class,
            "Serial Port": self.serial_port_var.get().strip() or "mock",
            "Stimulus Screen ID": self._safe_int(self.screen_id_var.get(), 1),
            "Debug Mode": self.debug_var.get(),
            "Execution Mode": mode,
            "l/v Ratio (ms)": self._safe_float(self.lv_ratio_var.get(), 80.0),
            "Initial Degree (°)": self._safe_float(self.init_deg_var.get(), 2.0),
            "Final Degree (°)": self._safe_float(self.final_deg_var.get(), 180.0),
            "Number of Trials": self._safe_int(self.num_trials_var.get(), 18),
            "_output_dir": out_dir,
        }

    def _close_queues(self):
        for q in [self.cmd_queue, self.telemetry_queue]:
            if q is not None:
                try:
                    while not q.empty():
                        q.get_nowait()
                except Exception:
                    pass
                q.close()
                q.cancel_join_thread()

    def start_experiment(self):
        if self.worker_process and self.worker_process.is_alive():
            return

        self._close_queues()
        self.cmd_queue, self.telemetry_queue = create_ipc_queues()

        cfg = self._build_config()
        self.worker_process = mp.Process(
            target=worker_entry, args=(cfg, self.cmd_queue, self.telemetry_queue)
        )
        self.worker_process.start()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_label.configure(text="Running...", text_color="cyan")

    def stop_experiment(self):
        if self.cmd_queue:
            try:
                self.cmd_queue.put_nowait({"action": "ABORT"})
            except queue.Full:
                pass

    def _poll_telemetry(self):
        if self.telemetry_queue:
            latest_telemetry = None
            terminal_event = None

            for _ in range(100):
                try:
                    data = self.telemetry_queue.get_nowait()
                    action = data.get("action")
                    if action == "telemetry":
                        latest_telemetry = data
                    elif action in ["worker_done", "worker_abort", "worker_error"]:
                        terminal_event = data
                except queue.Empty:
                    break

            if latest_telemetry:
                self._update_telemetry_ui(latest_telemetry)
                self._draw_twin(latest_telemetry)

            if terminal_event:
                action = terminal_event.get("action")
                if action == "worker_done":
                    self._reset_ui("Experiment completed", "white")
                elif action == "worker_abort":
                    self._reset_ui("Experiment aborted", "orange")
                elif action == "worker_error":
                    self._reset_ui(
                        f"Error occurred: {terminal_event.get('error')}", "red"
                    )

        if self.worker_process and not self.worker_process.is_alive():
            if self.start_btn.cget("state") == "disabled":
                self._reset_ui("Worker disconnected", "white")

        self.root.after(16, self._poll_telemetry)

    def _update_telemetry_ui(self, data: dict):
        phase = str(data.get("phase", "—"))
        sess = str(data.get("session_num", "—"))
        t_idx = str(data.get("trial_idx", "—"))
        tot = str(data.get("total_trials", "—"))

        phase_lower = phase.lower()
        if "adaptation" in phase_lower:
            p_color = "#ff4d4d"
        elif "wait" in phase_lower or "iti" in phase_lower or "isi" in phase_lower:
            p_color = "orange"
        elif "looming" in phase_lower:
            p_color = "lime"
        else:
            p_color = "cyan"

        self.lbl_phase_val.configure(text=phase, text_color=p_color)
        self.lbl_sess_val.configure(text=sess)
        self.lbl_trial_val.configure(text=f"{t_idx} / {tot}")

        core_keys = {
            "action",
            "phase",
            "session_num",
            "trial_idx",
            "total_trials",
            "side",
            "theta",
            "twin_r_ratio",
            "hw_cmd",
        }
        hw_metrics = [f"{k}:{v}" for k, v in data.items() if k not in core_keys]
        hw_str = "  ".join(hw_metrics) if hw_metrics else "Active"
        self.lbl_hw_val.configure(text=hw_str)

    def _draw_twin(self, frame: Dict[str, Any]):
        self.canvas.delete("all")
        side = frame.get("side", "left")
        twin_r_ratio = frame.get("twin_r_ratio", 0.0)

        radius_canvas = min(100, max(2, twin_r_ratio * 100))
        centre_y = 75

        if side in ["left", "both", "—"]:
            self.canvas.create_oval(
                100 - radius_canvas,
                centre_y - radius_canvas,
                100 + radius_canvas,
                centre_y + radius_canvas,
                outline="white",
                width=1,
            )
        if side in ["right", "both", "—"]:
            self.canvas.create_oval(
                300 - radius_canvas,
                centre_y - radius_canvas,
                300 + radius_canvas,
                centre_y + radius_canvas,
                outline="white",
                width=1,
            )
        self.canvas.create_line(200, 0, 200, 150, fill="#333333", dash=(4, 2))

    def _reset_ui(self, status: str, color: str):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_label.configure(text=status, text_color=color)

        self.lbl_phase_val.configure(text="IDLE", text_color="gray")
        self.lbl_hw_val.configure(text="Disconnected", text_color="gray")
        self.canvas.delete("all")

        if self.worker_process:
            self.worker_process.join(timeout=2.0)
            if self.worker_process.is_alive():
                self.worker_process.terminate()
            self.worker_process = None

    def on_closing(self):
        self.stop_experiment()
        if self.worker_process:
            self.worker_process.join(timeout=2.0)
            if self.worker_process.is_alive():
                self.worker_process.terminate()
        self._close_queues()
        self.root.destroy()


def main():
    mp.set_start_method("spawn", force=True)
    root = ctk.CTk()
    app = MasterDashboard(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
