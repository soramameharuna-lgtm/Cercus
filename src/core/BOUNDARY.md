# BOUNDARY.md — Core Infrastructure Immutability

## Renderer Absolute Stateless

- `render.py` (`CoreRenderer`) MUST remain a pure "command-receive and geometry-draw" engine.
- PROHIBITED: injecting ANY state machine tracking, time calculation, or business logic into the renderer.
- The renderer accepts draw commands and outputs pixels. Nothing more.

## Non-Blocking Hardware I/O

- `hardware.py` (`SerialDaemon`, `KinematicsParser`) and `kinematics.py` (`KinematicEngine`) main data processing flow MUST NOT contain:
  - `time.sleep()` in hot paths (startup retry is exempt).
  - Synchronous blocking waits.
  - Blocking network or serial calls outside dedicated daemon threads.
- All serial I/O MUST run in background daemon threads (`_reader_loop`, `_writer_loop`).
- The main frame loop MUST never block on hardware.

## Zero-Allocation Requirement

- High-frequency execution paths (`KinematicEngine.update`, `KinematicEngine.evaluate_trigger`) MUST:
  - Reuse pre-allocated memory (`__slots__`, in-place float operations).
  - Avoid creating new objects that trigger garbage collection (GC) jitter.
- Violation: any `list()`, `dict()`, `str()`, or object instantiation inside `update()` or `evaluate_trigger()`.
