# Chromium Memory Dashboard

A dashboard for measuring and visualizing memory usage based on changes in Chromium's build-time and runtime flags.

## Key Features
- **Automated Build & Measurement**: Progressively builds and executes combinations of flags.
- **Granular Status Tracking**: Real-time updates showing whether the system is building, measuring, or accessing specific URLs.
- **Memory Measurement**: Uses Selenium to measure the total process memory footprint when accessing real websites (YouTube, Naver, etc.).
- **Real-time Dashboard**: Displays measurement results in graphs and tables using a Flask backend and Chart.js.
- **Progress Management**: Supports resuming from the last interrupted point and allows immediate force-stopping during execution.
- **Remote Build Support**: Offload heavy Chromium builds to a remote server via SSH with automatic retry logic.

---

## Installation Guide

### 1. Prerequisites
- Python 3.10 or higher
- Chromium source code (with `depot_tools` configured)
- Chrome Driver (compatible with the system path or the built binary version)

### 2. Setup
```bash
cd snu-chromium-memory-dashboard
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install flask psutil selenium paramiko
```

---

## Configuration

### 1. Project Settings
The configuration is managed via `settings.json`, which can be easily updated through the **Settings** page in the dashboard.
- **Local Environment**:
  - `local_chromium_path`: Path to the `src` directory of your local Chromium source.
  - `build_path`: Local build output directory (e.g., `out/Default`).
  - `depot_tools_path`: Path to your local `depot_tools` directory.
- **Execution Settings**:
  - `headless`: Whether to run the browser without a visible UI (Default: `true`).
  - `use_ssh`: Enable or disable remote server execution (Default: `false`).
  - `default_repeats`: Number of repeated measurements (iterations) per flag combination.
  - `measurement_interval`: Interval (seconds) between high-frequency memory captures (Default: `1.0`).

### 2. Memory Measurement Protocol (Two-Phase)
To ensure stable and accurate memory readings, the dashboard follows a precise two-phase measurement procedure for every URL in each iteration:

| Phase | Duration | Description |
| :--- | :--- | :--- |
| **1. Stabilization** | `stabilization_seconds` | After page load, the system waits for this period (Default: 20s) to allow network fetches (images, videos) and rendering to settle. |
| **2. Evaluation** | `evaluation_seconds` | The system captures memory usage every second during this period (Default: 20s). |

- **Representative Value**: The **maximum (peak)** memory value captured during the **Evaluation Phase** only is recorded as the representative value for that iteration.
- **Data Points**: For the default 20s+20s configuration with a 1s interval, a total of 41 data points are captured and stored in the `all` results array.

### 3. Remote Execution via SSH (`use_ssh: true`)
Offload heavy builds and measurements to a more powerful remote server.
- **SSH Config**: Set `host`, `port`, `user`, and `password`.
- **Remote Paths**: Specify `chromium_path`, `build_path`, and the newly added `depot_tools_path` for the remote environment.
- **Automatic Agent**: The system automatically deploys a `remote_agent.py` to the server, installs dependencies (`selenium`, `psutil`), and cleans up after completion or force-stop.

### 4. Settings UI Features
- **Smart Save Button**: The "Save Settings" button is disabled by default and only activates when changes are detected. It automatically disables again after a successful save.
- **Ctrl + S Shortcut**: Quickly save your changes using the `Ctrl + S` (or `Cmd + S`) keyboard shortcut.
- **Auto-Merging**: The system automatically merges new configuration fields from defaults into your existing `settings.json` without deleting unexposed fields like `debug` or `refresh_log`.

---

## How to Run

### Start the Dashboard Server
```bash
python3 app.py
```
After starting, the dashboard will be available at `http://localhost:5000`. The browser will attempt to open this URL automatically.

### Operation
- **Start Measurement**: Starts tasks defined in `memory_features.json`. Tasks already present in `test_results.json` are skipped.
- **Stop Measurement**: Immediately force-stops the current build or measurement process.

---

## Checking Results
- Results are cumulatively stored in `test_results.json`.
- Use the bar chart to compare average memory footprints across different configurations.
