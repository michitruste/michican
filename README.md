# MichiCAN

Desktop tool to **read, send and plot CAN signals** through a Vector CANcase (VN16xx, VN5xxx, …)
using **python-can**, **cantools** and DBC files, with a PySide6 + pyqtgraph GUI.

```
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py                      # or: python main.py examples/demo.dbc
```

## Features

| Area | What you get |
|---|---|
| Connection | `vector` (also `virtual`, `pcan`, `kvaser`, …), channel **Detect**, bitrate, CAN FD + data bitrate, listen-only, Vector app-name mapping |
| DBC | Load one or more DBC/ARXML/KCD/SYM files (menu, command line or drag & drop). Remembered between sessions. Multiplexed messages are supported. |
| Signals tree | Every message and signal with live value (enum names shown), unit, frame count, search filter. **Tick a signal to plot it.** |
| Plot | Rolling time window (0 = whole history), **Overlay** or **Stacked** (one Y axis per signal), Pause to zoom/pan, crosshair readout under the plot |
| Trace | *Fixed* (one row per ID, with cycle time and count) or *Scrolling* (chronological). Decoded signals per frame, filter, pause. |
| Transmit | Pick a DBC message, edit signal values (enum drop-downs, ranges from the DBC), **Send once** or **Start cyclic**. Editing a value while cyclic updates the running frame immediately. Raw frames (hex ID/data) too. |
| Recording | File ▸ Start recording → `.blf` / `.asc` / `.csv` / `.trc` |
| Offline | File ▸ Open log file: decode and plot an existing BLF/ASC log |
| Export | File ▸ Export signals to CSV (plotted signals, or all) |

## Using the Vector CANcase

1. Install the **Vector Driver Setup** (includes the *XL Driver Library*, `vxlapi64.dll`).
   Without it python-can reports `Vector XL library not found`.
2. Choose interface `vector` and click **Detect**. The list shows every CAN channel the driver sees
   (`index: name  S/N …`). With the *App name* field empty, the channel is that **global index**.
3. Alternatively, type an app name (e.g. `CANalyzer`) and use that application's channel mapping
   from *Vector Hardware Config* (Tools ▸ Vector Hardware Config…). Channel `0` = the app's CAN1.
4. Set the bitrate (and FD + data bitrate for CAN FD), then **Connect**.

The Vector bus is opened with `receive_own_messages=True`, so frames you send appear in the trace
as `Tx` and their signals are plotted too.

## Trying it without hardware

1. `python main.py examples/demo.dbc`
2. Interface `virtual`, channel `vcan0`, **Connect**
3. Tools ▸ **Demo traffic (virtual bus)**. It plays every DBC message with changing values.
4. Expand the tree and tick a few signals. Double-click `TesterCommand` to send it.

## Project layout

```
main.py                     entry point (dark Fusion theme)
michican/
  bus.py                    BusConfig -> can.Bus kwargs, rx thread, send / send_periodic, can.Logger
  dbc.py                    load DBCs, decode frames, encode signal dicts (mux-aware)
  signal_store.py           thread-safe numpy ring buffers per signal (+ CSV export)
  simulator.py              demo traffic generator for the virtual bus
  gui/
    main_window.py          wiring, menus, 50 ms GUI tick, settings persistence
    connection_bar.py       interface/channel/bitrate toolbar
    signal_tree.py          DBC browser with live values and plot check boxes
    plot_panel.py           pyqtgraph overlay/stacked plot, crosshair
    trace_view.py           QAbstractTableModel trace (fixed / scrolling)
    transmit_panel.py       DBC and raw frame transmit, cyclic task list
examples/demo.dbc           sample database
```

**Threading model:** a background thread calls `bus.recv()` and decodes each frame with cantools.
It appends values to the `SignalStore` and pushes the frame to a queue. The GUI drains that queue
every 50 ms, so the GUI never blocks on the bus and high bus loads don't flood the Qt event loop.
Cyclic transmission uses python-can's `bus.send_periodic()` tasks.

## Notes

- Signal buffers keep up to 200 000 points per signal (oldest half dropped after that). Change it in `SignalStore(max_points_per_signal=…)`.
- Encoding uses `strict=True`, so values outside the DBC range are rejected with a message instead of being sent silently wrong.
- Unused payload bits are sent as `0`.
