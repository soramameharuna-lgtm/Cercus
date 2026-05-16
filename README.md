# Cercus - User Guide

**Cercus** is a multiprocess, closed-loop stimulus control framework designed for high-temporal-precision behavioral and neuroscience experiments. Built on a Master-Worker architecture, it physically decouples UI scheduling, visual rendering, hardware telemetry, and data persistence.

## 1. Architecture Overview

The system enforces strict unidirectional data flow and functional isolation:

- **Master Dashboard (`dashboard.py`)**: A non-blocking GUI responsible for parameter configuration, dynamic form generation, and low-frequency status monitoring.
- **Pure Logic Core (`paradigm.py`)**: A mathematical modeling layer. It processes time deltas and hardware feedback to output standardized rendering instruction streams and telemetry states.
- **Stateless Renderer (`core_render.py`)**: Executes basic geometric drawing instructions (e.g., `circle`, `rect`, `element_array`) without maintaining state.
- **Asynchronous Hardware Daemon (`core_hardware.py`)**: Handles high-frequency sensor data acquisition and TTL trigger signal dispatch.
- **Dual-Track Logger (`core_logger.py`)**: Separates high-frequency kinematics telemetry from low-frequency experimental state transitions.

## 2. Core Features & Execution Modes

- **Dual-Track Execution Mode**:
  - **Auto**: Continuously executes the entire session automatically based on randomized ITI/ISI intervals.
  - **Manual**: After the ITI, the renderer safely suspends and waits for external input (`Space` bar) to precisely trigger a single trial.
- **Digital Twin Monitor**: The master UI includes a miniature monitor that maps the physical dual-screen stimulus state proportionally.
- **Hardware Fallback**: If no hardware is available, the serial port can be configured to `mock` to inject a virtual data stream for debugging.

## 3. Built-in Paradigms

Four standard paradigm matrices are currently built-in and can be dynamically loaded and configured via the dashboard:

1. **Multimodal Looming (Looming Paradigm)**:
   - Includes pure visual and pure wind baselines.
   - Contains 7 calibrated visuo-tactile multisensory stimuli with gradient wind field triggers from TTC -373ms to +200ms.
2. **Classic Visual Looming (ClassicLooming Paradigm)**:
   - Pure visual parameterized model. Supports dynamic configuration of `l/v Ratio`, initial/final degrees, and left/right presentation logic.
3. **Optic Flow (OpticFlow Paradigm)**:
   - Vectorized dot-motion model. Configurable speed, density, coherence, and direction.
4. **Movement Trace (MovementTrace Paradigm)**:
   - Lissajous trajectory tracking. Configurable X/Y frequency, amplitude, and trail length.

## 4. Installation & Execution

Run the system within an isolated virtual environment (e.g., Conda). Install dependencies:

Bash

```
pip install -r requirements.txt
```

Launch the dashboard:

Bash

```
python main.py
```

## 5. Data Output Specifications

Dual-track record files are automatically generated in the `data/` directory, strictly aligned via `global_trial_id` and timestamps:

1. `{Subject}_session_{n}_events.csv`: Logs low-frequency control flow events and parameter details.
2. `{Subject}_session_{n}_kinematics.csv`: Logs high-frequency closed-loop telemetry data.

## 6. Extension: Adding New Paradigms

New experimental paradigms can be introduced without modifying the underlying rendering or control flow code. Implement the following in `src/models/paradigm.py`:

1. **Inherit Base Class**: Create a new class inheriting from `BaseParadigm`.
2. **Define UI Mapping Interfaces**:
   - `get_available_patterns(cls)`: Return a list of supported pattern names.
   - `get_parameter_schema(cls)`: Declare the dynamic UI parameter configuration dictionary. The framework uses the `type` field (e.g., `int`, `float`, `choice`, `bool`) to generate the dashboard form and injects values into the instance context.
3. **Implement Core Lifecycle**:
   - `generate_trials(self, pattern_key)`: Construct and return the trial contexts (`List[dict]`) for the session based on the selected pattern.
   - `prepare_trial(self, trial_context)`: Return hardware initialization serial commands before a trial starts (or an empty string).
   - `get_idle_frame(self, hw_telemetry)`: Return steady-state rendering instructions for ITI/ISI phases as `(cmds, telemetry_dict, sync_states)`.
   - `process_frame(self, elapsed_time, trial_context, hw_telemetry)`: The frame-level closed-loop calculation core. Return the state tuple `(is_done, cmds, telemetry_dict, sync_states)` based on the timestamp and hardware telemetry.
4. **Standardized Instructions**: The `cmds` returned must use standardized dictionaries supported by the renderer (e.g., `type="rect"`, `type="element_array"`, with `pos`, `colors`, `sizes`).
5. **Global Registration**: Add the new class to the `PARADIGM_REGISTRY` dictionary at the bottom of `src/models/paradigm.py` to enable dashboard parsing.
