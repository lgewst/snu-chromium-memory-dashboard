import json
import os
import subprocess
import time
import psutil
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import paramiko

# Setup file logging for deep debugging
logging.basicConfig(
    filename='pipeline_debug.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class ChromiumPipeline:
    """
    Orchestrates the build and memory measurement process for Chromium.
    Supports both local execution and remote execution via SSH.
    """
    @staticmethod
    def load_settings(settings_path='settings.json'):
        """
        Loads configuration from a JSON file and merges it with default values.
        Ensures all required keys exist by performing a recursive deep merge.
        If the file doesn't exist or is missing fields, it updates the disk with defaults.

        Args:
            settings_path (str): The path to the settings JSON file. Defaults to 'settings.json'.

        Returns:
            dict: A complete settings dictionary containing both user-defined and default values.
        """
        default_settings = {
            "local_chromium_path": os.path.abspath(os.path.join(os.getcwd(), "../chromium/src")), # Local path to Chromium 'src'
            "depot_tools_path": os.path.abspath(os.path.join(os.getcwd(), "../depot_tools")), # Local path to depot_tools
            "build_path": "out/Default", # Default build output directory
            "headless": True, # Run browser without visible UI
            "use_ssh": False, # Enable remote execution via SSH
            "debug": True, # Write detailed logs to pipeline_debug.log
            "refresh_log": True, # Clear log file on startup
            "ssh_config": {
                "host": "1.1.1.1", # Remote server IP
                "port": 22, # SSH port
                "user": "username", # SSH username
                "password": "", # SSH password
                "chromium_path": "/home/username/chromium/src", # Remote Chromium 'src' path
                "build_path": "out/Default", # Remote build directory
                "depot_tools_path": "/home/username/depot_tools" # Remote depot_tools path
            },
            "default_repeats": 5, # Number of repeated measurements per flag combination
            "stabilization_seconds": 20, # Stabilization phase (Phase 1)
            "evaluation_seconds": 20, # Evaluation phase (Phase 2)
            "measurement_interval": 1.0 # Interval (seconds) between high-frequency memory captures
        }
        
        settings = {}
        if not os.path.exists(settings_path):
            settings = default_settings
            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=2)
        else:
            try:
                with open(settings_path, 'r') as f:
                    settings = json.load(f)
            except:
                settings = {}

            # Self-updating logic for settings consistency
            changed = False
            def deep_merge(target, default):
                updated = False
                for key, val in default.items():
                    if key not in target:
                        target[key] = val
                        updated = True
                    elif isinstance(val, dict) and isinstance(target[key], dict):
                        if deep_merge(target[key], val):
                            updated = True
                return updated

            if deep_merge(settings, default_settings):
                with open(settings_path, 'w') as f:
                    json.dump(settings, f, indent=2)
        
        return settings

    def __init__(self, settings_path='settings.json', state=None):
        """
        Initializes the Chromium measurement pipeline and handles configuration merging.
        
        Args:
            settings_path (str): The relative or absolute path to the settings.json file.
            state (multiprocessing.managers.DictProxy, optional): A shared dictionary object 
                provided by the Flask app to track real-time pipeline status for the frontend.
        """
        self.state = state
        self.settings = self.load_settings(settings_path)
        
        self.use_ssh = self.settings.get('use_ssh', False)
        self.local_chromium_path = self.settings.get('local_chromium_path', '../chromium')
        self.local_depot_tools = self.settings.get('depot_tools_path', '')
        self.ssh_config = self.settings.get('ssh_config', {})
        self.target_urls = self._load_json('target_urls.json')
        
        if self.settings.get('debug', True):
            log_mode = 'w' if self.settings.get('refresh_log', True) else 'a'
            logging.basicConfig(
                filename='pipeline_debug.log',
                filemode=log_mode,
                level=logging.DEBUG,
                format='%(asctime)s - %(levelname)s - %(message)s',
                force=True
            )
            logging.info(f"Pipeline initialized. use_ssh: {self.use_ssh}")

    def _update_status(self, msg):
        """
        Broadcasts a status message to the Flask shared state and logs it.

        Args:
            msg (str): The descriptive status message to display.
        """
        if self.state is not None:
            self.state["detailed_status"] = msg
        if self.settings.get('debug', True):
            logging.info(f"[Status Update] {msg}")
        print(f"[Status] {msg}")

    def _load_json(self, path):
        """
        Helper method to safely parse a JSON file.

        Args:
            path (str): Filepath to the target JSON.

        Returns:
            dict|list: Parsed JSON content, or an empty dictionary on failure.
        """
        if os.path.exists(path):
            with open(path, 'r') as f:
                try: return json.load(f)
                except: return {}
        return {}

    def run_command(self, cmd, cwd=None, timeout=None, max_retries=None):
        """
        Executes a shell command either locally or on a remote SSH host.

        Args:
            cmd (str): The raw shell command string.
            cwd (str, optional): Local directory where the command should be run.
            timeout (int, optional): Override default SSH connection timeout.
            max_retries (int, optional): Override default SSH retry count.

        Returns:
            tuple: (bool success, float elapsed_time_seconds)
        """
        if self.use_ssh and self.ssh_config.get('host'):
            return self._run_ssh_command(cmd, timeout=timeout, max_retries=max_retries)
        else:
            # Local Execution with PATH environment setup
            env = os.environ.copy()
            if self.local_depot_tools:
                env["PATH"] = self.local_depot_tools + os.pathsep + env["PATH"]
            start_time = time.time()
            process = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in process.stdout:
                print(line, end='')
            stdout, stderr = process.communicate()
            return process.returncode == 0, time.time() - start_time

    def _get_ssh_client(self, timeout=None, max_retries=None):
        """
        Internal factory to create a connected SSH client with automatic retries.

        Args:
            timeout (int, optional): Override default connection timeout.
            max_retries (int, optional): Override default retry limit.

        Returns:
            paramiko.SSHClient|None: An active SSH connection or None if failed.
        """
        retries = max_retries if max_retries is not None else 5
        conn_timeout = timeout if timeout is not None else 20

        for attempt in range(retries):
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                ssh.connect(
                    self.ssh_config['host'], 
                    self.ssh_config.get('port', 22), 
                    self.ssh_config['user'], 
                    self.ssh_config['password'],
                    timeout=conn_timeout, banner_timeout=30, auth_timeout=30
                )
                return ssh
            except Exception as e:
                ssh.close()
                print(f"SSH Connection attempt ({attempt + 1}/{retries}) failed: {e}")
                if attempt < retries - 1: time.sleep(3)
        return None

    def _run_ssh_command(self, cmd, timeout=None, max_retries=None):
        """
        Runs a command on the remote host via an established SSH session.

        Args:
            cmd (str): The command string to be executed remotely.
            timeout (int, optional): Connection timeout.
            max_retries (int, optional): Connection retry limit.

        Returns:
            tuple: (bool success, float elapsed_time_seconds)
        """
        ssh = self._get_ssh_client(timeout=timeout, max_retries=max_retries)
        if not ssh: return False, 0

        try:
            start_time = time.time()
            remote_path = self.ssh_config.get('chromium_path') or self.local_chromium_path
            remote_depot = self.ssh_config.get('depot_tools_path', '')
            env_setup = f"export PATH=$PATH:{remote_depot} " if remote_depot else ""
            full_cmd = f"cd {remote_path} && {env_setup} && {cmd}"

            stdin, stdout, stderr = ssh.exec_command(full_cmd)
            for line in stdout:
                print(line, end='')
            exit_status = stdout.channel.recv_exit_status()
            ssh.close()
            return exit_status == 0, time.time() - start_time
        except Exception as e:
            print(f"SSH Command execution failed: {e}")
            return False, 0

    def build_chromium(self, build_flags):
        """
        Automates the GN generation and Ninja compilation process.

        Args:
            build_flags (list): List of GN build arguments.

        Returns:
            tuple: (bool success, float build_time_seconds)
        """
        self._update_status("Starting Chromium Build...")
        gn_args = ' '.join(build_flags)

        if self.use_ssh:
            build_path = self.ssh_config.get('build_path', 'out/Default')
            cwd = self.ssh_config.get('chromium_path')
        else:
            build_path = self.settings.get('build_path', 'out/Default')
            cwd = self.local_chromium_path

        self._update_status(f"Running gn gen for {build_path}...")
        success, _ = self.run_command(f"gn gen {build_path} --args='{gn_args}'", cwd=cwd)
        if not success: return False, 0

        self._update_status(f"Running autoninja for {build_path} (chrome + chromedriver)...")
        return self.run_command(f"autoninja -C {build_path} chrome chromedriver", cwd=cwd)

    def measure_memory(self, runtime_flags, repeats=5):
        """
        Entry point for memory measurement. Decides between local and remote measurement.

        Args:
            runtime_flags (list): Flags to pass to the Chromium binary.
            repeats (int): Number of measurement iterations.

        Returns:
            list: URL-specific memory usage data per iteration.
        """
        if self.use_ssh:
            return self._measure_memory_remote(runtime_flags, repeats)
        else:
            return self._measure_memory_local(runtime_flags, repeats)

    def _measure_memory_local(self, runtime_flags, repeats):
        """
        Orchestrates high-frequency Selenium measurements locally using polling and CDP.
        
        Follows a robust two-phase measurement protocol:
        1. Phase 1 (Stabilization): Measures for X seconds (default 20s). 
           Uses a 'skip-behind' strategy: if a measurement (CDP call) takes too long, 
           it skips the next scheduled points to avoid bursting and reduce CPU load 
           during heavy page initialization.
        2. Phase 2 (Evaluation): Measures for Y points (default 20). 
           Uses a 'catch-up' strategy: ensures exactly Y points are collected even 
           if some measurements are delayed, providing a consistent dataset for peak analysis.

        Args:
            runtime_flags (list): List of Chrome command-line arguments.
            repeats (int): Number of measurement iterations per URL.

        Returns:
            list: A list of iteration results, where each contains 'peak' (max RSS of Phase 2),
                  'samples' (all data points), and phase counts.
        """
        self._update_status(f"Measuring memory locally (Repeats: {repeats})...")
        results = []
        build_path = self.settings.get('build_path', 'out/Default')
        chrome_exe = os.path.join(self.local_chromium_path, build_path, "chrome")
        stabilization_sec = float(self.settings.get('stabilization_seconds', 20))
        evaluation_sec = float(self.settings.get('evaluation_seconds', 20))
        interval = float(self.settings.get('measurement_interval', 1.0))
        
        # Collect device metadata
        import platform
        device_info = {
            "os": platform.system(),
            "os_release": platform.release(),
            "cpu": platform.processor(),
            "machine": platform.machine(),
            "python_version": platform.python_version()
        }

        for i in range(repeats):
            iteration_result = {"iteration": i + 1, "urls": {}, "metadata": device_info}
            options = Options()
            options.binary_location = chrome_exe
            if self.settings.get('headless', True): options.add_argument("--headless=new")
            for flag in runtime_flags: options.add_argument(flag)
            
            try:
                driver = webdriver.Chrome(options=options)
                # Enable CDP Performance Domain
                driver.execute_cdp_cmd('Performance.enable', {})
                
                for url in self.target_urls:
                    self._update_status(f"Local Measure Iter {i+1}/{repeats}: {url}")
                    driver.get(url)
                    
                    all_samples = []
                    evaluation_rss = []
                    
                    start_time = time.time()
                    
                    # --- Phase 1: Stabilization (Skip-behind to avoid bursting) ---
                    current_target = start_time
                    while time.time() - start_time <= stabilization_sec and current_target - start_time <= stabilization_sec:
                        wait = current_target - time.time()
                        
                        if wait < -interval/2:
                            # Behind schedule: Adjust target to current time to prevent bursts
                            current_target = time.time()
                        elif wait > 0:
                            time.sleep(wait)
                        
                        current_target += interval
                        actual_elapsed = round(time.time() - start_time, 2)
                        
                        # Measure
                        rss_bytes = self._get_total_memory(driver.service.process.pid)
                        if rss_bytes > 0:
                            sample = self._capture_sample(driver, rss_bytes / (1024 * 1024), actual_elapsed)
                            all_samples.append(sample)
                    
                    phase1_final_count = len(all_samples)
                    
                    # --- Phase 2: Evaluation (Catch-up to ensure fixed count) ---
                    phase2_expected = int(evaluation_sec / interval)
                    # Align Phase 2 timeline strictly with the initial start_time
                    phase2_base_time = start_time + stabilization_sec
                    for j in range(phase2_expected):
                        # Targets: start + stabilization + 1s, 2s, ..., 20s
                        current_target = phase2_base_time + ((j + 1) * interval)
                        wait = current_target - time.time()
                        if wait > 0:
                            time.sleep(wait)
                        
                        actual_elapsed = round(time.time() - start_time, 2)
                        rss_bytes = self._get_total_memory(driver.service.process.pid)
                        if rss_bytes > 0:
                            rss_mb = rss_bytes / (1024 * 1024)
                            sample = self._capture_sample(driver, rss_mb, actual_elapsed)
                            all_samples.append(sample)
                            evaluation_rss.append(rss_mb)
                    
                    if all_samples:
                        peak_rss = max(evaluation_rss) if evaluation_rss else max([s['rss'] for s in all_samples])
                        iteration_result["urls"][url] = {
                            "peak": peak_rss,
                            "samples": all_samples,
                            "phase1_count": phase1_final_count,
                            "phase2_count": len(evaluation_rss)
                        }
                driver.quit()
            except Exception as e:
                print(f"Driver Error: {e}")
                if 'driver' in locals(): driver.quit()
            
            os.system("pkill -f chrome || true")

            if iteration_result["urls"]:
                results.append(iteration_result)
        return results

    def _capture_sample(self, driver, rss_mb, actual_elapsed):
        """
        Captures a comprehensive snapshot of memory and performance metrics via CDP.
        
        Executes the 'Performance.getMetrics' command to retrieve internal browser 
        metrics such as V8 JS Heap usage and execution durations for layout/scripts.

        Args:
            driver (webdriver.Chrome): The active Selenium driver instance.
            rss_mb (float): The total system-level RSS memory usage in Megabytes.
            actual_elapsed (float): The actual time in seconds since the page load started.

        Returns:
            dict: A sample dictionary containing:
                - timestamp: Wall-clock time of capture.
                - elapsed: Actual seconds since the page load began.
                - rss: System-level memory usage (MB).
                - js_heap_used: Memory used by the V8 JS engine (MB).
                - js_heap_total: Total memory allocated for the V8 JS engine (MB).
                - layout_duration: Cumulative time spent on layout operations (ms).
                - task_duration: Total time spent executing all tasks (ms).
                - script_duration: Cumulative time spent executing scripts (ms).
                - nodes: Number of DOM nodes in the document.
                - documents: Number of documents in the process tree.
        """
        try:
            cdp_metrics = driver.execute_cdp_cmd('Performance.getMetrics', {})
            metrics_dict = {m['name']: m['value'] for m in cdp_metrics['metrics']}
        except:
            metrics_dict = {}

        return {
            "timestamp": time.time(),
            "elapsed": actual_elapsed,
            "rss": rss_mb,
            "js_heap_used": metrics_dict.get('JSHeapUsedSize', 0) / (1024 * 1024),
            "js_heap_total": metrics_dict.get('JSHeapTotalSize', 0) / (1024 * 1024),
            "layout_duration": metrics_dict.get('LayoutDuration', 0),
            "task_duration": metrics_dict.get('TaskDuration', 0),
            "script_duration": metrics_dict.get('ScriptDuration', 0),
            "nodes": metrics_dict.get('Nodes', 0),
            "documents": metrics_dict.get('Documents', 0)
        }

    def _get_total_memory(self, pid):
        """
        Recursively calculates the RSS memory usage of a process tree.

        Args:
            pid (int): Parent process ID.

        Returns:
            int: Total memory usage in bytes.
        """
        try:
            parent = psutil.Process(pid)
            total = parent.memory_info().rss
            for child in parent.children(recursive=True):
                total += child.memory_info().rss
            return total
        except: return 0

    def _measure_memory_remote(self, runtime_flags, repeats):
        """
        Remote execution strategy involving Python check, dependency install, and agent deployment.

        Args:
            runtime_flags (list): Command-line flags for the remote Chromium.
            repeats (int): Number of measurement iterations.

        Returns:
            list: Aggregated results parsed from the remote agent.
        """
        self._update_status(f"Preparing remote server for measurement...")
        ssh = self._get_ssh_client()
        if not ssh: return []

        # Check Python3 existence
        stdin, stdout, stderr = ssh.exec_command("python3 --version")
        if stdout.channel.recv_exit_status() != 0:
            self._update_status("Error: python3 not found on remote. Please install python3 and pip.")
            ssh.close()
            return []

        remote_path = self.ssh_config.get('chromium_path')
        build_path = self.ssh_config.get('build_path', 'out/Default')
        chrome_exe = os.path.join(remote_path, build_path, "chrome")
        chromedriver_exe = os.path.join(remote_path, build_path, "chromedriver")
        
        # Dependency installation with robust pathing
        self._update_status(f"Checking remote dependencies...")
        dep_install_cmd = "python3 -m pip install selenium psutil --user"
        ssh.exec_command(dep_install_cmd)

        try:
            with open('remote_agent.py', 'r') as f:
                remote_script_content = f.read()
            replacements = {
                "{{TARGET_URLS}}": json.dumps(self.target_urls),
                "{{RUNTIME_FLAGS}}": json.dumps(runtime_flags),
                "{{REPEATS}}": str(repeats),
                "{{STABILIZATION_SEC}}": str(self.settings.get('stabilization_seconds', 20)),
                "{{EVALUATION_SEC}}": str(self.settings.get('evaluation_seconds', 20)),
                "{{INTERVAL}}": str(self.settings.get('measurement_interval', 1.0)),
                "{{CHROME_EXE}}": chrome_exe,
                "{{CHROMEDRIVER_EXE}}": chromedriver_exe
            }
            for placeholder, value in replacements.items():
                remote_script_content = remote_script_content.replace(placeholder, value)
            
            sftp = ssh.open_sftp()
            remote_agent_path = os.path.join(remote_path, "remote_agent.py")
            with sftp.file(remote_agent_path, 'w') as f:
                f.write(remote_script_content)
            sftp.close()

            self._update_status(f"Executing remote measurement agent...")
            cmd = f"python3 {remote_agent_path} 2>&1"
            stdin, stdout, stderr = ssh.exec_command(f"cd {remote_path} && {cmd}")
            
            output = ""
            is_collecting_result = False
            for line in stdout:
                if "---RESULT_START---" in line:
                    is_collecting_result = True
                    print("[Remote] Results data block started...")
                    continue
                if "---RESULT_END---" in line:
                    is_collecting_result = False
                    print(f"[Remote] Results data block ended (Total: {len(output)} bytes)")
                    continue
                
                if is_collecting_result:
                    output += line
                else:
                    # Print regular logs but skip the huge JSON block
                    print(f"[Remote] {line}", end='')
                    if "Iteration" in line or "Visiting" in line:
                        self._update_status(line.strip())

            # Final cleanup
            self._update_status(f"Cleaning up remote agent script...")
            ssh.exec_command(f"rm {remote_agent_path}")
            ssh.close()

            if output.strip():
                return json.loads(output.strip())
            return []
        except Exception as e:
            print(f"Remote measurement failed: {e}")
            return []

    def run_pipeline(self, feature):
        """
        Coordinates the full build-measure pipeline for a single feature.

        Args:
            feature (dict): Configuration from memory_features.json.

        Returns:
            dict|None: Results object or None on validation failure.
        """
        if not self.use_ssh and not os.path.exists(self.local_chromium_path):
            raise FileNotFoundError(f"Chromium path not found: {self.local_chromium_path}")
        
        self._update_status(f"Task {feature['id']}: Starting Build")
        build_success, build_time = self.build_chromium(feature.get('build_flags', []))
        if not build_success: 
            self._update_status(f"Task {feature['id']}: Build Failed")
            return None

        repeats = self.settings.get('default_repeats', 5)
        self._update_status(f"Task {feature['id']}: Starting Measurement")
        memory_results = self.measure_memory(feature.get('runtime_flags', []), repeats=repeats)

        if not memory_results:
            self._update_status(f"Task {feature['id']}: Measurement failed. Not saving.")
            return None

        self._update_status(f"Task {feature['id']}: Completed")
        final_result = {
            "id": feature['id'],
            "build_time": build_time,
            "build_flags": feature.get('build_flags', []),
            "runtime_flags": feature.get('runtime_flags', []),
            "memory_results": memory_results,
            "timestamp": time.time()
        }
        self.save_result(final_result)
        return final_result

    def save_result(self, result):
        """
        Appends results to the local historical results file.

        Args:
            result (dict): Data to persist.
        """
        history = []
        if os.path.exists('test_results.json'):
            with open('test_results.json', 'r') as f:
                try: history = json.load(f)
                except: history = []
        history.append(result)
        with open('test_results.json', 'w') as f:
            json.dump(history, f, indent=2)
