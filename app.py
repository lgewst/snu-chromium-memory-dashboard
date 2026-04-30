import multiprocessing
import os
import signal
import psutil
import threading
import time
import json
from flask import Flask, jsonify, send_from_directory, request, render_template
from pipeline import ChromiumPipeline

app = Flask(__name__)
app.json.sort_keys = False # Preserve original key order from the file
app.shared_state = None
app.pipeline_process = None

vitals_cache = {
    "data": {"status": "disconnected"},
    "last_updated": 0,
    "last_client_request": 0
}

def background_vitals_updater():
    """
    스마트 백그라운드 리소스 수집기 (Lazy Polling 적용)
    클라이언트(대시보드)가 활성화되어 있을 때만 3초 주기로 데이터를 수집하며,
    요청이 없으면 유휴 상태(Sleep)로 전환하여 서버 부하와 SSH 오버헤드를 방지합니다.
    """
    global vitals_cache
    pipeline = ChromiumPipeline() # 무한 루프 밖에서 한 번만 인스턴스화
    
    # cpu_percent 초기화 (첫 호출은 0.0 반환 방지)
    psutil.cpu_percent(interval=None) 

    while True:
        now = time.time()
        
        # [핵심 최적화] 프론트엔드 요청이 30초 이상 없으면 아무것도 안 하고 대기 (Idle Mode)
        # 탭을 닫았거나 브라우저를 최소화했을 때 불필요한 원격 서버 SSH 폭격을 막아줍니다.
        if now - vitals_cache["last_client_request"] > 30:
            time.sleep(2) 
            continue

        settings = ChromiumPipeline.load_settings()
        is_server_mode = settings.get('use_ssh', False)

        try:
            if is_server_mode:
                # 파이프라인의 내부 설정만 최신화 (인스턴스를 매번 새로 만들지 않음)
                pipeline.settings = settings 
                pipeline.ssh_config = settings.get('ssh_config', {})
                
                data = pipeline.get_remote_vitals()
                if data:
                    vitals_cache["data"] = data
            else:
                # 로컬 수집 로직
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory()
                swap = psutil.swap_memory()
                disk = psutil.disk_usage(settings.get('local_chromium_path', os.getcwd()) or '/')
                load = os.getloadavg() if hasattr(os, 'getloadavg') else (0.0, 0.0, 0.0)

                vitals_cache["data"] = {
                    "status": "connected",
                    "cpu_percent": round(cpu),
                    "ram_curr_gb": round(ram.used / (1024**3), 1),
                    "ram_tot_gb": round(ram.total / (1024**3), 1),
                    "swap_curr_gb": round(swap.used / (1024**3), 1),
                    "swap_tot_gb": round(swap.total / (1024**3), 1),
                    "load_avg": f"{load[0]:.2f}, {load[1]:.2f}, {load[2]:.2f}",
                    "disk_used_gb": round(disk.used / (1024**3)),
                    "disk_tot_gb": round(disk.total / (1024**3))
                }
            
            vitals_cache["last_updated"] = time.time()
            
        except Exception as e:
            print(f"Vitals update error: {e}")
            vitals_cache["data"] = {"status": "disconnected"}

        # 작업 완료 후 3초 휴식
        time.sleep(3)

@app.route('/api/vitals', methods=['GET'])
def get_vitals():
    """
    프론트엔드에서 시스템 상태를 요청할 때 호출되는 API.
    요청이 들어올 때마다 클라이언트 활성 시간을 갱신하여 백그라운드 스레드를 깨웁니다.
    """
    global vitals_cache
    
    # 클라이언트가 살아있음을 백그라운드 스레드에 알림 (Heartbeat)
    vitals_cache["last_client_request"] = time.time() 
    
    # 캐시가 10초 이상 갱신되지 않았다는 것은, 연결에 문제가 생겼거나 스레드가 죽었음을 의미
    if time.time() - vitals_cache["last_updated"] > 10:
        return jsonify({"status": "disconnected", "reason": "cache_stale"})
        
    return jsonify(vitals_cache["data"])

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

@app.route('/tasks')
def tasks_page():
    """
    Serves the task management page.
    
    Returns:
        str: Rendered tasks.html template.
    """
    return render_template('tasks.html')

@app.route('/statistics')
def statistics_page():
    """
    Serves the statistics analysis page.
    
    Returns:
        str: Rendered statistics.html template.
    """
    return render_template('statistics.html')

@app.route('/details')
def details_page():
    """
    Serves the detailed data view page.
    
    Returns:
        str: Rendered details.html template.
    """
    return render_template('details.html')

@app.route('/api/features', methods=['GET'])
def get_features():
    """
    API endpoint to fetch all defined memory features from memory_features.json.
    
    Returns:
        Response: JSON list of all features.
    """
    if os.path.exists('memory_features.json'):
        with open('memory_features.json', 'r') as f:
            try: return jsonify(json.load(f))
            except: return jsonify([])
    return jsonify([])

@app.route('/api/features', methods=['POST'])
def save_features():
    """Updates the entire memory_features.json file and syncs group_id to results."""
    try:
        features = request.json
        with open('memory_features.json', 'w') as f:
            json.dump(features, f, indent=2)
        
        # Sync group_id to test_results.json if it exists
        if os.path.exists('test_results.json'):
            with open('test_results.json', 'r') as f:
                results = json.load(f)
            
            # Create a mapping of id -> group_id from the new features list
            group_mapping = {str(feat['id']): feat.get('group_id') for feat in features}
            
            updated = False
            for res in results:
                res_id = str(res['id'])
                if res_id in group_mapping:
                    new_group = group_mapping[res_id]
                    if res.get('group_id') != new_group:
                        res['group_id'] = new_group
                        updated = True
            
            if updated:
                with open('test_results.json', 'w') as f:
                    json.dump(results, f, indent=2)
                    
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/features/reorder', methods=['POST'])
def reorder_features():
    """Updates the order of features based on frontend drag-and-drop."""
    return save_features()

@app.route('/api/patches', methods=['GET'])
def list_patches():
    """Lists all .patch files in the default or custom patch directory."""
    settings = ChromiumPipeline.load_settings()
    patch_dir = settings.get('custom_patch_dir')
    
    # Fallback to local 'patches' directory if no custom dir is set
    if not patch_dir or not os.path.isabs(patch_dir):
        patch_dir = os.path.join(os.getcwd(), 'patches')
    
    patches = []
    if os.path.exists(patch_dir):
        try:
            for f in os.listdir(patch_dir):
                if f.endswith('.patch'):
                    patches.append({
                        "name": f,
                        "full_path": os.path.join(patch_dir, f),
                        "is_absolute": os.path.isabs(settings.get('custom_patch_dir', ''))
                    })
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return jsonify({"patch_dir": patch_dir, "patches": sorted(patches, key=lambda x: x['name'])})

@app.route('/api/target_urls', methods=['GET'])
def get_target_urls():
    """
    API endpoint to fetch the list of target URLs for measurement.
    
    Returns:
        Response: JSON list of URLs.
    """
    if os.path.exists('target_urls.json'):
        with open('target_urls.json', 'r') as f:
            try: return jsonify(json.load(f))
            except: return jsonify([])
    return jsonify([])

@app.route('/api/target_urls', methods=['POST'])
def save_target_urls():
    """
    API endpoint to update target_urls.json with a new list of URLs.
    
    Returns:
        Response: JSON status (success/error).
    """
    try:
        urls = request.json
        if not isinstance(urls, list):
            return jsonify({"status": "error", "message": "Expected a list of URLs"}), 400
            
        with open('target_urls.json', 'w') as f:
            json.dump(urls, f, indent=2)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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
    API endpoint to initiate the background measurement process.
    Validates if memory_features.json exists and contains tasks.

    Returns:
        Response: JSON status (started/error/already running).
    """
    if app.shared_state["is_running"]:
        return jsonify({"status": "already running"}), 400

    # Validate tasks before starting
    if not os.path.exists('memory_features.json'):
        return jsonify({"status": "error", "message": "No tasks defined (memory_features.json missing)"}), 400

    try:
        with open('memory_features.json', 'r') as f:
            tasks = json.load(f)
            if not tasks or len(tasks) == 0:
                return jsonify({"status": "error", "message": "No tasks to do"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to read tasks: {str(e)}"}), 400

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

    # Vitals thread
    vitals_thread = threading.Thread(target=background_vitals_updater, daemon=True)
    vitals_thread.start()
    
    # Launch Flask server
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
