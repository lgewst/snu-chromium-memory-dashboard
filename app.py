import multiprocessing
import os
import signal
import psutil
import json
from flask import Flask, jsonify, send_from_directory, request, render_template
from pipeline import ChromiumPipeline

app = Flask(__name__)
app.shared_state = None
app.pipeline_process = None

def run_pipeline_task(state):
    """
    Background worker that executes the Chromium measurement pipeline.
    This runs in a separate OS process to avoid blocking the Flask web server.

    Args:
        state (multiprocessing.managers.DictProxy): Shared state dictionary for 
            inter-process communication (IPC) between Flask and the worker.
    """
    state["is_running"] = True
    state["detailed_status"] = "Initializing..."
    try:
        pipeline = ChromiumPipeline(state=state)
        
        # Ensure the feature definition file exists
        if not os.path.exists('memory_features.json'):
            print("Error: memory_features.json not found.")
            return

        # Load IDs of tasks already completed to avoid redundant work
        completed_ids = set()
        if os.path.exists('test_results.json'):
            try:
                with open('test_results.json', 'r') as f:
                    results = json.load(f)
                    completed_ids = {res['id'] for res in results}
            except:
                pass

        with open('memory_features.json', 'r') as f:
            features = json.load(f)

        # Loop through features and execute if not already completed
        for feature in features:
            if feature['id'] in completed_ids:
                print(f"Skipping already completed task: {feature['id']}")
                continue
                
            state["current_task"] = feature['id']
            pipeline.run_pipeline(feature)
            
    except Exception as e:
        print(f"Pipeline process error: {e}")
    finally:
        state["is_running"] = False
        state["current_task"] = None
        state["detailed_status"] = "Idle"

def kill_child_processes(parent_pid, sig=signal.SIGKILL):
    """
    Safely terminates a process and all its recursive children.
    Essential for stopping heavy builds (ninja) or browser instances.

    Args:
        parent_pid (int): The process ID of the parent to terminate.
        sig (int): The signal to send (default: SIGKILL).
    """
    try:
        parent = psutil.Process(parent_pid)
        children = parent.children(recursive=True)
        for process in children:
            try:
                process.send_signal(sig)
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass

@app.route('/')
def index():
    """
    Serves the main dashboard page.
    
    Returns:
        str: Rendered dashboard.html template.
    """
    return render_template('dashboard.html')

@app.route('/settings')
def settings_page():
    """
    Serves the project settings page.
    
    Returns:
        str: Rendered settings.html template.
    """
    return render_template('settings.html')

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """
    API endpoint to fetch the current configuration from settings.json.
    If settings.json doesn't exist, it returns default settings.
    This also ensures missing fields are merged from defaults.
    
    Returns:
        Response: JSON object containing all settings.
    """
    settings = ChromiumPipeline.load_settings()
    return jsonify(settings)

@app.route('/api/settings', methods=['POST'])
def save_settings():
    """
    API endpoint to update settings.json with new values.
    It merges new values into existing ones to preserve fields like 'debug'.
    
    Returns:
        Response: JSON status (success/error).
    """
    new_settings = request.json
    settings_path = 'settings.json'
    
    try:
        # Load existing settings if they exist
        existing_settings = {}
        if os.path.exists(settings_path):
            with open(settings_path, 'r') as f:
                try:
                    existing_settings = json.load(f)
                except:
                    pass
        
        # Helper to recursively update dictionary
        def deep_update(d, u):
            for k, v in u.items():
                if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                    deep_update(d[k], v)
                else:
                    d[k] = v
            return d

        # Merge new settings into existing ones
        updated_settings = deep_update(existing_settings, new_settings)

        with open(settings_path, 'w') as f:
            json.dump(updated_settings, f, indent=2)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/browse', methods=['GET'])
def browse_path():
    """
    API endpoint to explore the filesystem for path selection.
    Used by the frontend file browser modal.
    
    Args (Query params):
        path (str): The directory to list. Defaults to current working directory.
        
    Returns:
        Response: JSON containing current_path and list of subdirectories.
    """
    current_path = request.args.get('path', os.getcwd())
    if not os.path.isabs(current_path):
        current_path = os.path.abspath(current_path)

    try:
        if not os.path.exists(current_path):
             current_path = os.getcwd()

        items = []
        parent = os.path.dirname(current_path)
        if parent != current_path:
            items.append({"name": "..", "path": parent, "is_dir": True})

        for item in sorted(os.listdir(current_path)):
            full_path = os.path.join(current_path, item)
            try:
                if os.path.isdir(full_path):
                    items.append({
                        "name": item,
                        "path": full_path,
                        "is_dir": True
                    })
            except (PermissionError, OSError):
                continue
        return jsonify({"current_path": current_path, "items": items})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 403

@app.route('/api/start', methods=['POST'])
def start_pipeline():
    """
    API endpoint to trigger the measurement pipeline in a background process.
    
    Returns:
        Response: JSON status (started/already running).
    """
    if app.shared_state["is_running"]:
        return jsonify({"status": "already running"}), 400
    
    app.pipeline_process = multiprocessing.Process(
        target=run_pipeline_task, 
        args=(app.shared_state,)
    )
    app.pipeline_process.start()
    return jsonify({"status": "started"})

@app.route('/api/stop', methods=['POST'])
def stop_pipeline():
    """
    API endpoint to force-stop any running builds or measurements.
    Instantly updates UI state and then performs cleanup.
    
    Returns:
        Response: JSON status (force stopped).
    """
    # 1. Kill the local management process immediately
    if app.pipeline_process and app.pipeline_process.is_alive():
        print(f"Force stopping local pipeline process {app.pipeline_process.pid}...")
        kill_child_processes(app.pipeline_process.pid)
        app.pipeline_process.terminate()
        app.pipeline_process.join(timeout=1)
        if app.pipeline_process.is_alive():
            os.kill(app.pipeline_process.pid, signal.SIGKILL)

    # 2. IMMEDIATELY update shared state so UI reflects 'Idle' without waiting for SSH
    app.shared_state["is_running"] = False
    app.shared_state["current_task"] = None
    app.shared_state["detailed_status"] = "Idle (Cleaning up...)"

    # 3. Perform remote cleanup as a single combined command
    try:
        pipeline = ChromiumPipeline()
        if pipeline.use_ssh:
            print("Attempting one-shot remote process cleanup...")
            remote_path = pipeline.ssh_config.get('chromium_path')
            # Combine all cleanup tasks into a single shell execution to minimize SSH overhead/timeouts
            cleanup_cmd = "pkill -f chrome || true; pkill -f chromedriver || true"
            if remote_path:
                cleanup_cmd += f"; rm {os.path.join(remote_path, 'remote_agent.py')} || true"

            # Use short timeout and 1 retry
            pipeline.run_command(cleanup_cmd, timeout=5, max_retries=1)
    except Exception as e:
        print(f"Remote cleanup skipped: {e}")
    
    app.shared_state["detailed_status"] = "Idle"
    return jsonify({"status": "force stopped"})

@app.route('/api/status', methods=['GET'])
def get_status():
    """
    API endpoint to fetch the current live status for the dashboard UI.
    
    Returns:
        Response: JSON containing is_running, current_task, and detailed_status.
    """
    return jsonify({
        "is_running": app.shared_state["is_running"],
        "current_task": app.shared_state["current_task"],
        "detailed_status": app.shared_state.get("detailed_status", "Idle")
    })

@app.route('/api/results', methods=['GET'])
def get_results():
    """
    API endpoint to fetch all historical measurement results from test_results.json.
    
    Returns:
        Response: JSON list of all recorded results.
    """
    if os.path.exists('test_results.json'):
        with open('test_results.json', 'r') as f:
            try: return jsonify(json.load(f))
            except: return jsonify([])
    return jsonify([])

if __name__ == '__main__':
    # Initialize shared memory manager for IPC
    multiprocessing.freeze_support()
    manager = multiprocessing.Manager()
    app.shared_state = manager.dict({
        "is_running": False,
        "current_task": None,
        "detailed_status": "Idle"
    })
    
    os.makedirs('static', exist_ok=True)
    
    url = "http://localhost:5000"
    print(f" * Dashboard is available at: {url}")
    
    # Cross-platform browser auto-open
    try:
        import webbrowser
        if os.path.exists('/proc/version') and 'microsoft' in open('/proc/version').read().lower():
            os.system(f'powershell.exe -NoProfile -Command "Start-Process \'{url}\'" > /dev/null 2>&1')
        else:
            webbrowser.open(url)
    except Exception as e:
        print(f" * Could not open browser automatically: {e}")
    
    # Launch Flask server
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
