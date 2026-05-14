### Cercus - User Guide

**Cercus** is a multiprocess, closed-loop stimulus control framework designed for high-temporal-precision behavioral and neuroscience experiments. Built on a Master-Worker architecture, it physically decouples UI scheduling, visual rendering, hardware telemetry, and data persistence.

#### 1. Architecture Overview

The system enforces strict unidirectional data flow and functional isolation:

- **Master Dashboard (`dashboard.py`)**: A non-blocking GUI responsible for parameter configuration, process lifecycle management, and low-frequency status monitoring (Digital Twin).
- **Pure Logic Core (`paradigm.py`)**: A mathematical modeling layer with zero renderer dependencies. It takes time deltas and hardware feedback to output standardized rendering instruction streams and telemetry states.
- **Stateless Renderer (`core_render.py`)**: A PsychoPy drawing container. It only executes basic geometric drawing instructions from the logic core, enabling plug-and-play rendering.
- **Asynchronous Hardware Daemon (`core_hardware.py`)**: A serial communication layer guarded by an independent thread, handling high-frequency sensor data acquisition and TTL trigger signal dispatch.
- **Dual-Track Logger (`core_logger.py`)**: Separates high-frequency kinematics telemetry (Kinematics Log) from low-frequency experimental state transitions (Event Log).

#### 2. Core Features & Execution Modes

- **Dual-Track Execution Mode**:
    - **Auto**: Standard procedure for behavioral assays. Continuously executes the entire session automatically based on randomized ITI/ISI intervals.
    - **Manual**: Standard procedure for electrophysiology. After the ITI, the renderer safely suspends (maintaining the visual baseline and hardware heartbeat) and waits for external input (`Space` bar) to precisely trigger a single trial.
- **Digital Twin Monitor**: The master UI includes a real-time miniature monitor that maps the 3840x1080 dual-screen stimulus state to the control panel.
- **Hardware Fallback**: If no hardware is available, the serial port can be set to `mock` to inject a virtual data stream, ensuring the debugging pipeline remains uninterrupted.

#### 3. Built-in Paradigms

The system currently includes two standard paradigm matrices, both accessible via the UI dropdown menu:

1. **Multimodal Looming (Looming Paradigm)**:
    - Includes `Baseline Visual` and `Baseline Wind`.
    - Contains 7 precisely calibrated visuo-tactile multisensory stimuli (`Looming + Wind`), providing gradient wind field hardware triggers from TTC -373ms to +200ms.
2. **Classic Visual Looming (ClassicLooming Paradigm)**:
    - A pure visual parameterized model. Supports dynamic UI configuration of `l/v Ratio (ms)`, `Initial Degree`, and `Final Degree`.
    - Supports presentation logic: `Random L/R`, `Always Left`, and `Always Right`.

#### 4. Installation

It is recommended to run this system within an isolated virtual environment (e.g., Conda). Core dependencies include UI, visual rendering, and hardware communication modules:

```bash
pip install -r requirements.txt
```

#### 5. Running an Experiment

**Step 1: Launch the Dashboard**
Execute the entry file in your terminal:

```bash
python main.py
```

**Step 2: Configure Parameters**
Fill in the core information (Subject ID, Mode, Paradigm, Physical Parameters, and Serial Port).
_Note: When **Debug Mode** is enabled, the renderer launches in a windowed mode (1200x600) and bypasses WaitBlanking, facilitating debugging on single-screen setups._

**Step 3: Execution and Monitoring**
Click "Start Experiment". If in Manual mode, follow the status bar prompts to trigger via `Space`; if in Auto mode, the system will initiate the closed-loop process automatically.

#### 6. Data Output Specifications

Upon starting the experiment, the system automatically generates dual-track record files in the `data/` directory. Each session yields two files, strictly aligned via `global_trial_id` and timestamps:

1. `{Subject}_session_{n}_events.csv`: Logs low-frequency control flow events (including variable-length JSON details).
2. `{Subject}_session_{n}_kinematics.csv`: Logs extremely high-frequency closed-loop telemetry data (dx, dy, dz, etc.).

#### 7. Extension: Adding New Paradigms

To introduce entirely new experimental paradigms, there is no need to modify the underlying rendering or control flow code. You simply need to:

1. Create a new class inheriting from `BaseParadigm` in `src/models/paradigm.py`.
2. Implement the `process_frame()` method: Calculate theoretical coordinates in memory and return a standardized instruction dictionary (containing `id`, `type`, `pos`, `radius`/`size`). The underlying renderer and scheduler will automatically handle the rest.
