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
from src.workers.calibration_worker import calibration_worker_entry


class _SyncBlockRow:
    """Single sync block entry in the topology configurator."""

    def __init__(
        self,
        parent: ctk.CTkFrame,
        block_id: int,
        channel_name: str = "",
        on_delete: Optional[callable] = None,
    ):
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.pack(fill="x", padx=4, pady=2)
        self._channel_name = channel_name
        self._on_delete = on_delete

        label_text = channel_name if channel_name else f"ID {block_id}"
        ctk.CTkLabel(self.frame, text=label_text, width=120, anchor="w").pack(
            side="left", padx=(0, 4)
        )

        self.x_var = ctk.StringVar(value="-1800")
        self.y_var = ctk.StringVar(value="-500")
        self.w_var = ctk.StringVar(value="80")
        self.h_var = ctk.StringVar(value="80")

        for lbl, var in [
            ("X", self.x_var),
            ("Y", self.y_var),
            ("W", self.w_var),
            ("H", self.h_var),
        ]:
            ctk.CTkLabel(self.frame, text=lbl, width=14).pack(side="left")
            ctk.CTkEntry(self.frame, textvariable=var, width=50).pack(
                side="left", padx=(0, 4)
            )

        ctk.CTkButton(
            self.frame,
            text="X",
            width=28,
            fg_color="gray30",
            hover_color="red",
            command=self._request_delete,
        ).pack(side="right", padx=(4, 0))

    def _request_delete(self):
        if self._on_delete:
            self._on_delete(self)

    def get_topology(self, block_id: int) -> Dict[str, Any]:
        return {
            "id": block_id,
            "channel": self._channel_name,
            "x": self._safe_float(self.x_var.get(), 0.0),
            "y": self._safe_float(self.y_var.get(), 0.0),
            "w": self._safe_float(self.w_var.get(), 40.0),
            "h": self._safe_float(self.h_var.get(), 40.0),
        }

    @staticmethod
    def _safe_float(val: str, default: float) -> float:
        try:
            return float(val)
        except ValueError:
            return default

    def destroy(self):
        self.frame.destroy()


class CalibrationPanel:
    """Physical calibration panel — delegates process lifecycle to MasterDashboard."""

    def __init__(self, parent: ctk.CTkFrame):
        self._active = False
        self._has_data = False

        # Callbacks set by MasterDashboard
        self._cb_enter = None  # called when user clicks "Enter Calibration"
        self._cb_exit = None   # called when user clicks "Exit Calibration"
        self._cb_apply = None  # called with (factors: dict) on Apply

        self.frame = ctk.CTkFrame(parent)
        self.frame.pack(fill="x", padx=6, pady=(6, 0))

        header = ctk.CTkFrame(self.frame, fg_color="transparent")
        header.pack(fill="x", padx=6, pady=(6, 2))
        ctk.CTkLabel(
            header, text="Physical Calibration", font=("Segoe UI", 13, "bold")
        ).pack(side="left")

        self.toggle_btn = ctk.CTkButton(
            self.frame,
            text="Enter Calibration",
            fg_color="#1f6aa5",
            hover_color="#144870",
            command=self._on_toggle,
        )
        self.toggle_btn.pack(fill="x", padx=6, pady=(0, 4))

        row_dx = ctk.CTkFrame(self.frame, fg_color="transparent")
        row_dx.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(row_dx, text="Raw DX:", width=70, anchor="w").pack(side="left")
        self.lbl_dx = ctk.CTkLabel(row_dx, text="0", font=("Segoe UI", 13, "bold"))
        self.lbl_dx.pack(side="left", padx=(4, 0))

        row_dy = ctk.CTkFrame(self.frame, fg_color="transparent")
        row_dy.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(row_dy, text="Raw DY:", width=70, anchor="w").pack(side="left")
        self.lbl_dy = ctk.CTkLabel(row_dy, text="0", font=("Segoe UI", 13, "bold"))
        self.lbl_dy.pack(side="left", padx=(4, 0))

        row_ref = ctk.CTkFrame(self.frame, fg_color="transparent")
        row_ref.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(row_ref, text="Ref (°):", width=70, anchor="w").pack(
            side="left"
        )
        self.ref_var = ctk.StringVar(value="360")
        ctk.CTkEntry(row_ref, textvariable=self.ref_var, width=80).pack(
            side="left", padx=(4, 0)
        )

        self.apply_btn = ctk.CTkButton(
            self.frame,
            text="Calculate & Apply",
            fg_color="gray30",
            hover_color="gray40",
            state="disabled",
            command=self._on_apply,
        )
        self.apply_btn.pack(fill="x", padx=6, pady=(4, 6))

        self.status_lbl = ctk.CTkLabel(
            self.frame, text="Idle", text_color="gray", font=("Segoe UI", 11)
        )
        self.status_lbl.pack(padx=6, pady=(0, 6))

    # ---- public API for MasterDashboard ----

    def set_callbacks(self, enter: callable, exit_: callable, apply: callable):
        self._cb_enter = enter
        self._cb_exit = exit_
        self._cb_apply = apply

    def set_enabled(self, enabled: bool):
        self.toggle_btn.configure(state="normal" if enabled else "disabled")

    def handle_telemetry(self, data: dict):
        self.lbl_dx.configure(text=str(data.get("raw_dx", 0)))
        self.lbl_dy.configure(text=str(data.get("raw_dy", 0)))

    def on_calib_stopped(self):
        """Called by dashboard when calibration process exits."""
        self._active = False
        self._has_data = True
        self.toggle_btn.configure(
            text="Enter Calibration", fg_color="#1f6aa5", hover_color="#144870"
        )
        self.apply_btn.configure(state="normal")
        self.status_lbl.configure(
            text="Stopped. Apply or re-enter.", text_color="orange"
        )

    def reset(self):
        self._active = False
        self._has_data = False
        self.toggle_btn.configure(
            text="Enter Calibration", fg_color="#1f6aa5", hover_color="#144870",
            state="normal",
        )
        self.apply_btn.configure(state="disabled")
        self.lbl_dx.configure(text="0")
        self.lbl_dy.configure(text="0")
        self.status_lbl.configure(text="Idle", text_color="gray")

    # ---- internal ----

    def _on_toggle(self):
        if not self._active:
            self._active = True
            self._has_data = False
            self.toggle_btn.configure(
                text="Exit Calibration", fg_color="red", hover_color="darkred"
            )
            self.apply_btn.configure(state="disabled")
            self.lbl_dx.configure(text="0")
            self.lbl_dy.configure(text="0")
            self.status_lbl.configure(text="Calibrating...", text_color="cyan")
            if self._cb_enter:
                self._cb_enter()
        else:
            self.status_lbl.configure(text="Exiting...", text_color="gray")
            if self._cb_exit:
                self._cb_exit()

    def _on_apply(self):
        try:
            ref_deg = float(self.ref_var.get())
        except ValueError:
            self.status_lbl.configure(text="Invalid reference angle", text_color="red")
            return

        try:
            raw_dx = int(self.lbl_dx.cget("text"))
            raw_dy = int(self.lbl_dy.cget("text"))
        except (ValueError, TypeError):
            self.status_lbl.configure(text="Invalid raw data", text_color="red")
            return

        factors = {
            "dx": ref_deg / raw_dx if raw_dx != 0 else 1.0,
            "dy": ref_deg / raw_dy if raw_dy != 0 else 1.0,
        }
        self.status_lbl.configure(
            text=f"Applied: dx={factors['dx']:.6f}, dy={factors['dy']:.6f}",
            text_color="lime",
        )
        if self._cb_apply:
            self._cb_apply(factors)


class MasterDashboard:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Experiment Controller")
        self.root.geometry("1200x800")

        try:
            self.root.state("zoomed")
        except Exception:
            try:
                self.root.attributes("-zoomed", True)
            except Exception:
                pass

        try:
            self.root.tk.call("tk", "scaling", 1.0)
        except Exception:
            pass

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.worker_process: Optional[mp.Process] = None
        self.cmd_queue: Optional[mp.Queue] = None
        self.telemetry_queue: Optional[mp.Queue] = None

        self.calib_process: Optional[mp.Process] = None
        self.calib_cmd_queue: Optional[mp.Queue] = None
        self.calib_telemetry_queue: Optional[mp.Queue] = None

        self._param_vars: Dict[str, ctk.StringVar] = {}
        self._param_widgets: List[ctk.CTkBaseClass] = []
        self._sync_rows: List[_SyncBlockRow] = []

        self._create_widgets()
        self._load_default_config()
        self._on_paradigm_change()
        self.root.after(16, self._poll_telemetry)

    # ------------------------------------------------------------------
    # Serial port helper
    # ------------------------------------------------------------------
    def _get_serial_ports(self) -> List[str]:
        ports = []
        if HAS_SERIAL:
            ports = [p.device for p in serial.tools.list_ports.comports()]
        defaults = ["mock"]
        if sys.platform == "win32":
            defaults.append("COM3")
        else:
            defaults.append("/dev/ttyACM0")

        seen: set = set()
        result: List[str] = []
        for p in defaults + ports:
            if p not in seen:
                seen.add(p)
                result.append(p)
        return result

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------
    def _create_widgets(self):
        main_frame = ctk.CTkFrame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        top_row = ctk.CTkFrame(main_frame, fg_color="transparent")
        top_row.pack(fill="both", expand=True, pady=(0, 5), padx=5)

        # --- Left: Config ---
        cfg_frame = ctk.CTkFrame(top_row)
        cfg_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        # Row 0: Paradigm + Pattern
        ctk.CTkLabel(cfg_frame, text="Paradigm:").grid(
            row=0, column=0, sticky="w", padx=10, pady=5
        )
        self.paradigm_var = ctk.StringVar(value=list(PARADIGM_REGISTRY.keys())[0])
        self.paradigm_var.trace_add("write", self._on_paradigm_change)
        ctk.CTkOptionMenu(
            cfg_frame,
            variable=self.paradigm_var,
            values=list(PARADIGM_REGISTRY.keys()),
            width=160,
        ).grid(row=0, column=1, sticky="w", padx=10, pady=5)

        ctk.CTkLabel(cfg_frame, text="Pattern:").grid(
            row=0, column=2, sticky="w", padx=10, pady=5
        )
        self.pattern_var = ctk.StringVar()
        self.pattern_menu = ctk.CTkOptionMenu(
            cfg_frame,
            variable=self.pattern_var,
            values=["—"],
            width=240,
        )
        self.pattern_menu.grid(row=0, column=3, sticky="w", padx=10, pady=5)

        # Row 1: Subject ID
        ctk.CTkLabel(cfg_frame, text="Subject ID:").grid(
            row=1, column=0, sticky="w", padx=10, pady=5
        )
        self.subject_var = ctk.StringVar(value="cricket_001")
        ctk.CTkEntry(cfg_frame, textvariable=self.subject_var).grid(
            row=1, column=1, sticky="w", padx=10, pady=5
        )

        # Row 2: Session
        ctk.CTkLabel(cfg_frame, text="Start Session / Total:").grid(
            row=2, column=0, sticky="w", padx=10, pady=5
        )
        sess_frame = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        sess_frame.grid(row=2, column=1, sticky="w", padx=10)
        self.session_start_var = ctk.StringVar(value="1")
        self.session_total_var = ctk.StringVar(value="2")
        ctk.CTkEntry(sess_frame, textvariable=self.session_start_var, width=60).pack(
            side="left"
        )
        self._session_sep_label = ctk.CTkLabel(sess_frame, text=" / ")
        self._session_sep_label.pack(side="left")
        self._session_total_entry = ctk.CTkEntry(
            sess_frame, textvariable=self.session_total_var, width=60
        )
        self._session_total_entry.pack(side="left")

        # Row 3: Viewing Distance / Screen Width
        ctk.CTkLabel(cfg_frame, text="Viewing Distance (cm):").grid(
            row=3, column=0, sticky="w", padx=10, pady=5
        )
        self.viewing_distance_var = ctk.StringVar(value="30.0")
        ctk.CTkEntry(cfg_frame, textvariable=self.viewing_distance_var).grid(
            row=3, column=1, sticky="w", padx=10, pady=5
        )
        ctk.CTkLabel(cfg_frame, text="Screen Width (cm):").grid(
            row=3, column=2, sticky="w", padx=10, pady=5
        )
        self.screen_width_cm_var = ctk.StringVar(value="53.0")
        ctk.CTkEntry(cfg_frame, textvariable=self.screen_width_cm_var).grid(
            row=3, column=3, sticky="w", padx=10, pady=5
        )

        # Row 4: Resolution
        ctk.CTkLabel(cfg_frame, text="Resolution (W,H):").grid(
            row=4, column=0, sticky="w", padx=10, pady=5
        )
        self.resolution_var = ctk.StringVar(value="3840,1080")
        ctk.CTkEntry(cfg_frame, textvariable=self.resolution_var).grid(
            row=4, column=1, sticky="w", padx=10, pady=5
        )

        # Row 5: ITI / ISI
        ctk.CTkLabel(cfg_frame, text="ITI Range (sec):").grid(
            row=5, column=0, sticky="w", padx=10, pady=5
        )
        self.iti_range_var = ctk.StringVar(value="60-90")
        ctk.CTkEntry(cfg_frame, textvariable=self.iti_range_var).grid(
            row=5, column=1, sticky="w", padx=10, pady=5
        )
        ctk.CTkLabel(cfg_frame, text="ISI Range (sec):").grid(
            row=5, column=2, sticky="w", padx=10, pady=5
        )
        self.isi_range_var = ctk.StringVar(value="300-600")
        ctk.CTkEntry(cfg_frame, textvariable=self.isi_range_var).grid(
            row=5, column=3, sticky="w", padx=10, pady=5
        )

        # Row 6: Serial / Screen
        ctk.CTkLabel(cfg_frame, text="Serial Port / Screen ID:").grid(
            row=6, column=0, sticky="w", padx=10, pady=5
        )
        hw_frame = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        hw_frame.grid(row=6, column=1, columnspan=3, sticky="w", padx=10)
        serial_ports = self._get_serial_ports()
        self.serial_port_var = ctk.StringVar(
            value=serial_ports[0] if serial_ports else "mock"
        )
        self.screen_id_var = ctk.StringVar(value="1")
        ctk.CTkOptionMenu(
            hw_frame,
            variable=self.serial_port_var,
            values=serial_ports,
            width=110,
        ).pack(side="left", padx=(0, 5))
        ctk.CTkEntry(hw_frame, textvariable=self.screen_id_var, width=50).pack(
            side="left"
        )

        # Row 7: Debug Mode
        self.debug_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(cfg_frame, text="Debug Mode", variable=self.debug_var).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=10, pady=10
        )

        # Row 8+: Dynamic paradigm parameters
        self._param_frame = ctk.CTkScrollableFrame(cfg_frame)
        self._param_frame.grid(
            row=8, column=0, columnspan=4, sticky="nsew", padx=10, pady=(5, 10)
        )
        cfg_frame.grid_rowconfigure(8, weight=1)
        cfg_frame.grid_columnconfigure(0, weight=1)

        # --- Right: Sync Topology ---
        sync_outer = ctk.CTkFrame(top_row, width=340)
        sync_outer.pack(side="right", fill="y", padx=(5, 0))
        sync_outer.pack_propagate(False)

        sync_header = ctk.CTkFrame(sync_outer, fg_color="transparent")
        sync_header.pack(fill="x", padx=6, pady=(6, 2))
        ctk.CTkLabel(
            sync_header,
            text="Sync Block Topology",
            font=("Segoe UI", 13, "bold"),
        ).pack(side="left")

        self._sync_list_frame = ctk.CTkScrollableFrame(sync_outer, height=200)
        self._sync_list_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        ctk.CTkButton(
            sync_outer,
            text="+ Add Sync Block",
            fg_color="gray25",
            hover_color="gray35",
            command=self._add_sync_block,
        ).pack(fill="x", padx=6, pady=(0, 6))

        # --- Calibration Panel ---
        self._calib_panel = CalibrationPanel(sync_outer)
        self._calib_panel.set_callbacks(
            enter=self._start_calibration,
            exit_=self._stop_calibration,
            apply=self._apply_calibration,
        )

        # --- Status panel ---
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

        # --- Control panel ---
        ctrl_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        ctrl_frame.pack(fill="x", pady=20)
        self.start_btn = ctk.CTkButton(
            ctrl_frame,
            text="Start Experiment",
            fg_color="green",
            hover_color="darkgreen",
            command=self.start_experiment,
            height=45,
            font=("Segoe UI", 16, "bold"),
        )
        self.start_btn.pack(side="left", padx=20)
        self.stop_btn = ctk.CTkButton(
            ctrl_frame,
            text="Stop Experiment",
            fg_color="red",
            hover_color="darkred",
            state="disabled",
            command=self.stop_experiment,
            height=45,
            font=("Segoe UI", 16, "bold"),
        )
        self.stop_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(self.root, text="Ready", fg_color="gray15")
        self.status_label.pack(side="bottom", fill="x")

    # ------------------------------------------------------------------
    # Metric row helper
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Dynamic parameter form
    # ------------------------------------------------------------------
    def _on_paradigm_change(self, *args):
        p_name = self.paradigm_var.get()
        p_cls = PARADIGM_REGISTRY.get(p_name)
        if p_cls is None:
            return

        patterns = p_cls.get_available_patterns()
        self.pattern_menu.configure(values=patterns)
        if patterns:
            self.pattern_var.set(patterns[0])

        # Rebuild dynamic form
        for w in self._param_widgets:
            w.destroy()
        self._param_widgets.clear()
        self._param_vars.clear()

        schema = p_cls.get_parameter_schema()
        if schema:
            row = 0
            for key, meta in schema.items():
                p_type = meta.get("type", "info")
                label_text = meta.get("label", key)

                if p_type == "info":
                    lbl = ctk.CTkLabel(
                        self._param_frame,
                        text=label_text,
                        text_color="gray60",
                        font=("Segoe UI", 11),
                    )
                    lbl.grid(
                        row=row, column=0, columnspan=2, sticky="w", padx=10, pady=4
                    )
                    self._param_widgets.append(lbl)
                    row += 1
                    continue

                lbl = ctk.CTkLabel(self._param_frame, text=f"{label_text}:")
                lbl.grid(row=row, column=0, sticky="w", padx=10, pady=4)
                self._param_widgets.append(lbl)

                default_val = str(meta.get("default", ""))
                var = ctk.StringVar(value=default_val)
                self._param_vars[key] = var

                if p_type == "choice":
                    choices = meta.get("choices", [])
                    w = ctk.CTkOptionMenu(
                        self._param_frame,
                        variable=var,
                        values=choices,
                        width=160,
                    )
                elif p_type == "bool":
                    var = ctk.BooleanVar(value=bool(meta.get("default", False)))
                    self._param_vars[key] = var
                    w = ctk.CTkSwitch(self._param_frame, text="", variable=var)
                elif p_type == "filepath":
                    entry_frame = ctk.CTkFrame(
                        self._param_frame, fg_color="transparent"
                    )
                    entry_frame.grid(row=row, column=1, sticky="w", padx=10, pady=4)
                    w = ctk.CTkEntry(entry_frame, textvariable=var, width=120)
                    w.pack(side="left")
                    browse_btn = ctk.CTkButton(
                        entry_frame,
                        text="Browse",
                        width=60,
                        fg_color="gray30",
                        hover_color="gray40",
                        command=lambda v=var: self._browse_file(v),
                    )
                    browse_btn.pack(side="left", padx=(4, 0))
                    self._param_widgets.append(entry_frame)
                    row += 1
                    continue
                else:
                    w = ctk.CTkEntry(self._param_frame, textvariable=var, width=160)

                w.grid(row=row, column=1, sticky="w", padx=10, pady=4)
                self._param_widgets.append(w)
                row += 1

        # Bind execution mode trace to toggle Total Sessions visibility
        if "Execution Mode" in self._param_vars:
            exec_var = self._param_vars["Execution Mode"]
            exec_var.trace_add("write", self._on_exec_mode_change)
            self._on_exec_mode_change()

        # Rebuild sync rows from paradigm channels
        for r in self._sync_rows:
            r.destroy()
        self._sync_rows.clear()

        channels = p_cls.get_sync_channels()
        for i, ch in enumerate(channels):
            sync_row = _SyncBlockRow(
                self._sync_list_frame, i, ch, on_delete=self._delete_sync_row
            )
            self._sync_rows.append(sync_row)

    # ------------------------------------------------------------------
    # Execution mode toggle
    # ------------------------------------------------------------------
    def _on_exec_mode_change(self, *args):
        if "Execution Mode" not in self._param_vars:
            return
        mode = self._param_vars["Execution Mode"].get()
        if mode == "Manual":
            self._session_sep_label.pack_forget()
            self._session_total_entry.pack_forget()
        else:
            if not self._session_sep_label.winfo_ismapped():
                self._session_sep_label.pack(side="left")
            if not self._session_total_entry.winfo_ismapped():
                self._session_total_entry.pack(side="left")

    # ------------------------------------------------------------------
    # Sync block topology
    # ------------------------------------------------------------------
    def _browse_file(self, var: ctk.StringVar):
        from tkinter import filedialog

        path = filedialog.askopenfilename()
        if path:
            var.set(path)

    def _add_sync_block(self):
        idx = len(self._sync_rows)
        sync_row = _SyncBlockRow(
            self._sync_list_frame, idx, "", on_delete=self._delete_sync_row
        )
        self._sync_rows.append(sync_row)

    def _delete_sync_row(self, row: _SyncBlockRow):
        if row in self._sync_rows:
            self._sync_rows.remove(row)
            row.destroy()

    def _get_sync_topology(self) -> List[Dict[str, Any]]:
        return [row.get_topology(i) for i, row in enumerate(self._sync_rows)]

    # ------------------------------------------------------------------
    # Default config / helpers
    # ------------------------------------------------------------------
    def _load_default_config(self):
        pass

    def _safe_int(self, val_str: str, default: int) -> int:
        return int(val_str) if val_str.strip().isdigit() else default

    def _safe_float(self, val_str: str, default: float) -> float:
        try:
            return float(val_str)
        except ValueError:
            return default

    # ------------------------------------------------------------------
    # Config builder
    # ------------------------------------------------------------------
    def _build_config(self) -> Dict[str, Any]:
        root_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        out_dir = os.path.join(root_dir, "data")
        os.makedirs(out_dir, exist_ok=True)

        p_name = self.paradigm_var.get()

        # Collect paradigm-specific parameters from dynamic form
        paradigm_params: Dict[str, Any] = {}
        p_cls = PARADIGM_REGISTRY.get(p_name)
        if p_cls:
            schema = p_cls.get_parameter_schema()
            for key, meta in schema.items():
                if key not in self._param_vars:
                    continue
                var = self._param_vars[key]
                p_type = meta.get("type", "info")
                if p_type == "int":
                    paradigm_params[key] = self._safe_int(
                        var.get(), meta.get("default", 0)
                    )
                elif p_type == "float":
                    paradigm_params[key] = self._safe_float(
                        var.get(), meta.get("default", 0.0)
                    )
                elif p_type == "bool":
                    paradigm_params[key] = var.get()
                elif p_type == "choice":
                    paradigm_params[key] = var.get()
                elif p_type == "range":
                    paradigm_params[key] = var.get()
                elif p_type == "filepath":
                    paradigm_params[key] = var.get()

        # Parse resolution string into width/height
        res_str = self.resolution_var.get().strip()
        res_parts = [p.strip() for p in res_str.split(",")]
        screen_w_px = (
            self._safe_int(res_parts[0], 3840) if len(res_parts) >= 1 else 3840
        )
        screen_h_px = (
            self._safe_int(res_parts[1], 1080) if len(res_parts) >= 2 else 1080
        )

        exec_mode = paradigm_params.get("Execution Mode", "Auto")
        total_sessions = (
            self._safe_int(self.session_total_var.get(), 2)
            if exec_mode == "Auto"
            else -1
        )

        cfg: Dict[str, Any] = {
            "Subject ID": self.subject_var.get().strip() or "cricket_001",
            "Session Number": self._safe_int(self.session_start_var.get(), 1),
            "Total Sessions": total_sessions,
            "ITI Range (sec)": self.iti_range_var.get().strip() or "60-90",
            "ISI Range (sec)": self.isi_range_var.get().strip() or "300-600",
            "Experiment Pattern": self.pattern_var.get(),
            "Paradigm Class": p_name,
            "Serial Port": self.serial_port_var.get().strip() or "mock",
            "Stimulus Screen ID": self._safe_int(self.screen_id_var.get(), 1),
            "Debug Mode": self.debug_var.get(),
            "Viewing Distance (cm)": self._safe_float(
                self.viewing_distance_var.get(), 30.0
            ),
            "Screen Width (cm)": self._safe_float(self.screen_width_cm_var.get(), 53.0),
            "Screen Width (px)": screen_w_px,
            "Screen Height (px)": screen_h_px,
            "Sync Topology": self._get_sync_topology(),
            "_output_dir": out_dir,
        }
        cfg.update(paradigm_params)
        return cfg

    # ------------------------------------------------------------------
    # Experiment lifecycle
    # ------------------------------------------------------------------
    def _close_queues(self):
        for q_name in ("cmd_queue", "telemetry_queue"):
            q = getattr(self, q_name, None)
            if q is not None:
                try:
                    while True:
                        q.get_nowait()
                except (queue.Empty, ValueError, OSError):
                    pass
                try:
                    q.close()
                except Exception:
                    pass
                setattr(self, q_name, None)

    def _close_calib_queues(self):
        for q_name in ("calib_cmd_queue", "calib_telemetry_queue"):
            q = getattr(self, q_name, None)
            if q is not None:
                try:
                    while True:
                        q.get_nowait()
                except (queue.Empty, ValueError, OSError):
                    pass
                try:
                    q.close()
                except Exception:
                    pass
                setattr(self, q_name, None)

    def _kill_worker(self, proc: Optional[mp.Process], timeout: float = 4.0):
        if proc is None:
            return
        proc.join(timeout=timeout)
        if proc.is_alive():
            proc.terminate()

    # ---- Calibration lifecycle ----

    def _start_calibration(self):
        """Kill stimulus worker, then launch CalibrationWorker."""
        if self.calib_process and self.calib_process.is_alive():
            return

        # Shut down stimulus worker if running
        if self.worker_process and self.worker_process.is_alive():
            if self.cmd_queue:
                try:
                    self.cmd_queue.put_nowait({"action": "POISON_PILL"})
                except queue.Full:
                    pass
            self._close_queues()
            self._kill_worker(self.worker_process)
            self.worker_process = None

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        self._calib_panel.set_enabled(True)
        self.status_label.configure(text="Calibration mode", text_color="cyan")

        self._close_calib_queues()
        self.calib_cmd_queue, self.calib_telemetry_queue = create_ipc_queues()

        cfg = {"Serial Port": self.serial_port_var.get().strip() or "mock"}
        self.calib_process = mp.Process(
            target=calibration_worker_entry,
            args=(cfg, self.calib_cmd_queue, self.calib_telemetry_queue),
        )
        self.calib_process.start()

    def _stop_calibration(self):
        """Send POISON_PILL to CalibrationWorker."""
        if self.calib_cmd_queue:
            try:
                self.calib_cmd_queue.put_nowait({"action": "POISON_PILL"})
            except queue.Full:
                pass

    def _apply_calibration(self, factors: dict):
        """Write factors into config, kill calib worker, restart stimulus worker."""
        self._stop_calibration()
        self._close_calib_queues()
        self._kill_worker(self.calib_process)
        self.calib_process = None

        self._calib_factors = factors
        self.start_btn.configure(state="normal")
        self.status_label.configure(text="Calibration applied", text_color="lime")

    def _on_calib_process_exit(self):
        """Clean up after calibration worker exits."""
        self._close_calib_queues()
        self._kill_worker(self.calib_process)
        self.calib_process = None
        self._calib_panel.on_calib_stopped()
        self.start_btn.configure(state="normal")
        self.status_label.configure(text="Ready", text_color="white")

    # ---- Experiment lifecycle ----

    def start_experiment(self):
        if self.worker_process and self.worker_process.is_alive():
            return

        self._calib_panel.set_enabled(False)
        self._close_queues()
        self.cmd_queue, self.telemetry_queue = create_ipc_queues()

        cfg = self._build_config()
        # Inject calibration factors if available
        if hasattr(self, "_calib_factors") and self._calib_factors:
            cfg["calib_factors"] = self._calib_factors

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
                self.cmd_queue.put_nowait({"action": "POISON_PILL"})
            except queue.Full:
                pass

    # ------------------------------------------------------------------
    # Telemetry polling
    # ------------------------------------------------------------------
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
                    self._reset_ui(f"Error: {terminal_event.get('error')}", "red")

        if self.worker_process and not self.worker_process.is_alive():
            if self.start_btn.cget("state") == "disabled" and not self.calib_process:
                self._reset_ui("Worker disconnected", "white")

        # --- Calibration telemetry ---
        if self.calib_telemetry_queue:
            for _ in range(50):
                try:
                    data = self.calib_telemetry_queue.get_nowait()
                    action = data.get("action")
                    if action == "calibration_telemetry":
                        self._calib_panel.handle_telemetry(data)
                    elif action in ("calibration_done", "calibration_error"):
                        self._on_calib_process_exit()
                        break
                except (queue.Empty, ValueError):
                    break

        if self.calib_process and not self.calib_process.is_alive():
            self._on_calib_process_exit()

        self.root.after(16, self._poll_telemetry)

    def _update_telemetry_ui(self, data: dict):
        phase = str(data.get("phase", "—"))
        sess = str(data.get("session_num", "—"))
        t_idx = str(data.get("trial_idx", "—"))
        tot = str(data.get("total_trials", "—"))

        ui_color = data.get("ui_color", "cyan")
        ui_metrics = data.get("ui_metrics", {})

        self.lbl_phase_val.configure(text=phase, text_color=ui_color)
        self.lbl_sess_val.configure(text=sess)
        self.lbl_trial_val.configure(text=f"{t_idx} / {tot}")

        hw_str = (
            "  ".join(f"{k}:{v}" for k, v in ui_metrics.items())
            if ui_metrics
            else "Active"
        )
        self.lbl_hw_val.configure(text=hw_str, text_color="cyan")

    def _draw_twin(self, frame: Dict[str, Any]):
        self.canvas.delete("all")
        twin_cfg = frame.get("ui_twin")
        if not twin_cfg:
            return

        side = twin_cfg.get("side", "left")
        radius_ratio = twin_cfg.get("radius_ratio", 0.0)
        radius_canvas = min(100, max(2, radius_ratio * 100))
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
        self._calib_panel.reset()
        self._calib_panel.set_enabled(True)

        if self.worker_process:
            self._close_queues()
            self._kill_worker(self.worker_process)
            self.worker_process = None

    def on_closing(self):
        self._stop_calibration()
        self._close_calib_queues()
        self._kill_worker(self.calib_process)
        self.stop_experiment()
        self._close_queues()
        self._kill_worker(self.worker_process)
        self.root.destroy()


def main():
    mp.set_start_method("spawn", force=True)
    root = ctk.CTk()
    app = MasterDashboard(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
