# BOUNDARY.md — Main Controller UI Boundary

## Non-Blocking Callbacks

- ALL CustomTkinter button clicks and event callbacks in `dashboard.py` MUST return immediately.
- Any expensive computation or hardware polling MUST be delegated to:
  - Background threads (`threading.Thread` with `daemon=True`).
  - Worker processes (`mp.Process`).
- Violation: `time.sleep()`, serial port reads, blocking network calls, or long-running loops inside any UI callback method.

## Pure Parameter Assembly

- The UI component's SOLE responsibility is:
  1. Collecting user input from form widgets.
  2. Serializing input into a configuration dictionary (`Dict[str, Any]`).
- PROHIBITED in UI thread:
  - Direct instantiation of hardware drivers (`SerialDaemon`, `MockSerialDaemon`).
  - Direct execution of experiment control logic.
  - Direct instantiation of renderers or paradigm objects.
- The configuration dictionary is passed to the worker process; the worker owns all hardware and experiment logic.
