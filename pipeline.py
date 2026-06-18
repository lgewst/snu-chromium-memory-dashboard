import json
import os
import subprocess
import time
import psutil
import logging
import threading
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import paramiko

# Setup file logging for deep debugging
logging.basicConfig(
    filename='pipeline_debug.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class CDPMetricsCollector:
    """
    Asynchronously collects CDP performance metrics in a separate thread.
    This prevents CDP latency from blocking the main memory measurement loop.
    """
    def __init__(self, driver):
        self.driver = driver
        self.last_metrics = {}
        self.running = False
        self.thread = None

    def _collect_loop(self):
        while self.running:
            try:
                cdp_metrics = self.driver.execute_cdp_cmd('Performance.getMetrics', {})
                self.last_metrics = {m['name']: m['value'] for m in cdp_metrics['metrics']}
            except:
                # If CDP fails or driver is closed, we just keep the last known good metrics
                pass
            time.sleep(0.1) # Minimum interval between CDP polls

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._collect_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)

    def get_latest(self):
        return self.last_metrics

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
        self.settings_path = settings_path
        self.state = state
        self.settings = self.load_settings(settings_path)
        
        self.use_ssh = self.settings.get('use_ssh', False)
        self.local_chromium_path = self.settings.get('local_chromium_path', '../chromium')
        self.local_depot_tools = self.settings.get('depot_tools_path', '')
        self.ssh_config = self.settings.get('ssh_config', {})
        self.target_urls = self._load_json('target_urls.json')

        self._persistent_ssh = None
        
        # Initialize patch states tracking if not exists
        if 'patch_states' not in self.settings:
            self.settings['patch_states'] = {}
        
        if self.settings.get('debug', True):
            log_mode = 'w' if self.settings.get('refresh_log', True) else 'a'
            logging.basicConfig(
                filename='pipeline_debug.log',
                filemode=log_mode,
                level=logging.DEBUG,
                format='%(asctime)s - %(levelname)s - %(message)s',
                force=True
            )
            logging.info(f"Pipeline initialized. use_ssh: {self.use_ssh}, current_key: {self._get_state_key()}")

    def _get_state_key(self):
        """
        Generates a unique key for the current execution environment.
        Returns 'local' or 'ssh:{host}:{user}'.
        """
        if not self.use_ssh:
            return "local"
        host = self.ssh_config.get('host', 'unknown')
        user = self.ssh_config.get('user', 'unknown')
        return f"ssh:{host}:{user}"

    @property
    def current_patch_id(self):
        """Returns the currently recorded patch for the current environment."""
        return self.settings['patch_states'].get(self._get_state_key())

    @current_patch_id.setter
    def current_patch_id(self, value):
        """Updates and persists the patch state for the current environment."""
        key = self._get_state_key()
        self.settings['patch_states'][key] = value
        self._save_settings()

    def _save_settings(self):
        """
        Persists the current internal settings (like patch_states) back to settings.json.
        """
        try:
            with open(self.settings_path, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save settings: {e}")

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
            tuple: (bool success, float elapsed_time_seconds, str output)
        """
        if self.use_ssh and self.ssh_config.get('host'):
            return self._run_ssh_command(cmd, timeout=timeout, max_retries=max_retries)
        else:
            # Local Execution with PATH environment setup
            env = os.environ.copy()
            if self.local_depot_tools:
                env["PATH"] = self.local_depot_tools + os.pathsep + env["PATH"]
            
            start_time = time.time()
            captured_output = []
            
            # Start process
            process = subprocess.Popen(
                cmd, shell=True, cwd=cwd, env=env, 
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                text=True, bufsize=1, universal_newlines=True
            )

            # Stream stdout in real-time
            for line in process.stdout:
                print(line, end='')
                captured_output.append(line)
            
            # Capture remaining output and stderr
            stdout_rem, stderr = process.communicate()
            if stdout_rem: captured_output.append(stdout_rem)
            if stderr: 
                print(stderr, end='')
                captured_output.append(stderr)
                
            full_output = "".join(captured_output)
            return process.returncode == 0, time.time() - start_time, full_output

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
            tuple: (bool success, float elapsed_time_seconds, str output)
        """
        ssh = self._get_ssh_client(timeout=timeout, max_retries=max_retries)
        if not ssh: return False, 0, "Failed to establish SSH connection."

        try:
            start_time = time.time()
            remote_path = self.ssh_config.get('chromium_path') or self.local_chromium_path
            remote_depot = self.ssh_config.get('depot_tools_path', '')
            env_setup = f"export PATH=$PATH:{remote_depot} " if remote_depot else ""
            full_cmd = f"cd {remote_path} && {env_setup} && {cmd}"

            stdin, stdout, stderr = ssh.exec_command(full_cmd)
            
            captured_output = []
            # Stream stdout
            for line in stdout:
                print(line, end='')
                captured_output.append(line)
            
            # Read stderr
            err_output = stderr.read().decode('utf-8')
            if err_output:
                print(err_output, end='')
                captured_output.append(err_output)

            exit_status = stdout.channel.recv_exit_status()
            ssh.close()
            
            full_output = "".join(captured_output)
            return exit_status == 0, time.time() - start_time, full_output
        except Exception as e:
            if 'ssh' in locals() and ssh: ssh.close()
            return False, 0, f"SSH command error: {str(e)}"

    def build_chromium(self, build_flags):
        """
        Automates the GN generation and Ninja compilation process.

        Args:
            build_flags (list): List of GN build arguments.

        Returns:
            tuple: (bool success, float build_time_seconds, str log)
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
        success, _, gen_log = self.run_command(f"gn gen {build_path} --args='{gn_args}'", cwd=cwd)
        if not success: 
            return False, 0, f"GN Gen Failed:\n{gen_log}"

        self._update_status(f"Running autoninja for {build_path} (chrome + chromedriver)...")
        j_val = self.settings.get('autoninja_j', 0)
        j_flag = f" -j {j_val}" if j_val and j_val > 0 else ""
        success, build_time, build_log = self.run_command(f"autoninja -C {build_path}{j_flag} chrome chromedriver", cwd=cwd)
        
        full_log = f"GN Gen Output:\n{gen_log}\n\nNinja Build Output:\n{build_log}"
        return success, build_time, full_log

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
        Executes high-frequency memory measurements locally using Selenium and 
        Asynchronous CDP collection, matching the protocol in remote_agent.py.
        
        This implementation restarts the browser for every URL to ensure isolation and 
        uses a dedicated thread for CDP metrics to keep the measurement loop precise.
        
        Args:
            runtime_flags (list): List of Chrome command-line arguments.
            repeats (int): Number of measurement iterations per URL.

        Returns:
            list: A list of iteration results containing peak, samples, and metadata.
        """
        self._update_status(f"Measuring memory locally (URL Isolation, Async CDP): {repeats} repeats")
        results = []
        build_path = self.settings.get('build_path', 'out/Default')
        chrome_exe = os.path.join(self.local_chromium_path, build_path, "chrome")
        chromedriver_exe = os.path.join(self.local_chromium_path, build_path, "chromedriver")
        stabilization_sec = float(self.settings.get('stabilization_seconds', 20))
        evaluation_sec = float(self.settings.get('evaluation_seconds', 20))
        interval = float(self.settings.get('measurement_interval', 1.0))
        
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
            
            for url in self.target_urls:
                self._update_status(f"Iter {i+1}/{repeats}: {url}")
                
                # Setup fresh driver for each URL
                opts = Options()
                opts.binary_location = chrome_exe
                if self.settings.get('headless', True):
                    opts.add_argument("--headless=new")
                opts.add_argument("--no-sandbox")
                opts.add_argument("--disable-dev-shm-usage")
                for f in runtime_flags:
                    opts.add_argument(f)
                
                try:
                    from selenium.webdriver.chrome.service import Service
                    svc = Service(executable_path=chromedriver_exe)
                    driver = webdriver.Chrome(service=svc, options=opts)
                    driver.execute_cdp_cmd('Performance.enable', {})
                    
                    collector = CDPMetricsCollector(driver)
                    collector.start()
                    
                    driver.get(url)
                    
                    all_samples = []
                    evaluation_rss = []
                    evaluation_pss = []
                    
                    start_time = time.time()
                    
                    # --- Phase 1: Stabilization ---
                    phase1_expected = int(stabilization_sec / interval) + 1
                    for j in range(phase1_expected):
                        current_target = start_time + (j * interval)
                        wait = current_target - time.time()
                        if wait > 0:
                            time.sleep(wait)
                        
                        actual_elapsed = round(time.time() - start_time, 2)
                        rss_mb, pss_mb = self._get_mem(driver.service.process.pid)
                        if rss_mb > 0:
                            cdp_data = collector.get_latest()
                            sample = self._capture_sample(rss_mb, pss_mb, actual_elapsed, cdp_data)
                            all_samples.append(sample)
                    
                    phase1_final_count = len(all_samples)

                    # --- Phase 2: Evaluation ---
                    phase2_expected = int(evaluation_sec / interval)
                    phase2_start_target = start_time + stabilization_sec + interval
                    for j in range(phase2_expected):
                        current_target = phase2_start_target + (j * interval)
                        wait = current_target - time.time()
                        if wait > 0:
                            time.sleep(wait)
                        
                        actual_elapsed = round(time.time() - start_time, 2)
                        rss_mb, pss_mb = self._get_mem(driver.service.process.pid)
                        if rss_mb > 0:
                            cdp_data = collector.get_latest()
                            sample = self._capture_sample(rss_mb, pss_mb, actual_elapsed, cdp_data)
                            all_samples.append(sample)
                            evaluation_rss.append(rss_mb)
                            evaluation_pss.append(pss_mb)
                    
                    if all_samples:
                        peak_rss = max(evaluation_rss) if evaluation_rss else max([s['rss'] for s in all_samples])
                        peak_pss = max(evaluation_pss) if evaluation_pss else max([s['pss'] for s in all_samples])
                        iteration_result["urls"][url] = {
                            "peak": peak_rss,
                            "peak_pss": peak_pss,
                            "samples": all_samples,
                            "phase1_count": phase1_final_count,
                            "phase2_count": len(evaluation_rss)
                        }
                    
                    collector.stop()
                    driver.quit()
                    
                except Exception as e:
                    self._update_status(f"    Error during {url}: {e}")
                    try: driver.quit()
                    except: pass
                
                # Brief cool-down between URLs
                time.sleep(1)
            
            # Global cleanup between iterations
            os.system("pkill -f chrome || true")
            os.system("pkill -f chromedriver || true")

            if iteration_result["urls"]:
                results.append(iteration_result)
        return results

    def _capture_sample(self, rss_mb, pss_mb, actual_elapsed, cdp_data=None):
        """
        Creates a sample snapshot using system memory and provided CDP data.

        Args:
            rss_mb (float): The total system-level RSS memory usage in Megabytes.
            pss_mb (float): The total system-level PSS memory usage in Megabytes.
            actual_elapsed (float): The actual time in seconds since the page load started.
            cdp_data (dict, optional): The latest metrics dictionary from CDP.

        Returns:
            dict: A sample dictionary containing:
                - timestamp: Wall-clock time of capture.
                - elapsed: Actual seconds since the page load began.
                - rss: System-level RSS memory usage (MB).
                - pss: System-level PSS memory usage (MB).
                - js_heap_used: Memory used by the V8 JS engine (MB).
                - js_heap_total: Total memory allocated for the V8 JS engine (MB).
                - layout_duration: Cumulative time spent on layout operations (ms).
                - task_duration: Total time spent executing all tasks (ms).
                - script_duration: Cumulative time spent executing scripts (ms).
                - nodes: Number of DOM nodes in the document.
                - documents: Number of documents in the process tree.
        """
        metrics = cdp_data if cdp_data else {}
        return {
            "timestamp": time.time(),
            "elapsed": actual_elapsed,
            "rss": rss_mb,
            "pss": pss_mb,
            "js_heap_used": metrics.get('JSHeapUsedSize', 0) / (1024 * 1024),
            "js_heap_total": metrics.get('JSHeapTotalSize', 0) / (1024 * 1024),
            "layout_duration": metrics.get('LayoutDuration', 0),
            "task_duration": metrics.get('TaskDuration', 0),
            "script_duration": metrics.get('ScriptDuration', 0),
            "nodes": metrics.get('Nodes', 0),
            "documents": metrics.get('Documents', 0)
        }

    def _get_mem(self, pid):
        """
        Calculates the total RSS and PSS memory usage of a process and all its recursive children.
        
        Args:
            pid (int): The process ID of the parent process.
            
        Returns:
            tuple: (total_rss_mb, total_pss_mb) - Total memory usage in Megabytes (MB).
        """
        total_rss = 0
        total_pss = 0
        try:
            parent = psutil.Process(pid)
            # Get memory for parent
            info = parent.memory_full_info()
            total_rss += info.rss
            total_pss += getattr(info, 'pss', info.rss) # Fallback to RSS if PSS not available
            
            # Get memory for all children
            for child in parent.children(recursive=True):
                try:
                    c_info = child.memory_full_info()
                    total_rss += c_info.rss
                    total_pss += getattr(c_info, 'pss', c_info.rss)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return total_rss / (1024 * 1024), total_pss / (1024 * 1024)
        except:
            return 0, 0

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
        dep_check_cmd = "python3 -c 'import selenium, psutil' 2>/dev/null || python3 -m pip install selenium psutil --user"
        ssh.exec_command(dep_check_cmd)

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

    def _resolve_patch_path(self, patch_val):
        """
        Determines the full local path for a patch file.
        If patch_val is just a filename, looks in the 'patches/' directory.
        Otherwise, treats it as an absolute path.

        Args:
            patch_val (str): The patch filename or path.

        Returns:
            str|None: Full path to the patch file, or None if not found.
        """
        if not patch_val: return None
        
        # If it's a relative filename, look in the patches/ directory
        if not os.path.isabs(patch_val):
            potential_path = os.path.join(os.getcwd(), "patches", patch_val)
            if os.path.exists(potential_path):
                return potential_path
        
        # Check if it's an absolute path that exists
        if os.path.exists(patch_val):
            return patch_val
            
        return None

    def _manage_patches(self, patch_val):
        """
        Applies or reverts patches to the Chromium source tree.
        Optimizes by skipping re-application if the same patch is already active.
        Supports both local and remote (SSH) git repositories.

        Args:
            patch_val (str|None): The path/ID of the target patch.

        Returns:
            bool: True if patching succeeded or was skipped (already applied).
        """
        target_patch_path = self._resolve_patch_path(patch_val)
        
        # 1. Skip if already applied
        if self.current_patch_id == target_patch_path:
            self._update_status(f"Patch {patch_val or 'None'} already applied. Skipping.")
            return True

        self._update_status(f"Managing patches: Transitioning from {self.current_patch_id} to {target_patch_path}")

        # 2. Cleanup: Hard reset source tree to a clean state
        # This removes any previous patches or uncommitted changes.
        cleanup_cmd = "git checkout -- . && git clean -df"
        cwd = self.ssh_config.get('chromium_path') if self.use_ssh else self.local_chromium_path
        
        success, _, _ = self.run_command(cleanup_cmd, cwd=cwd)
        if not success:
            self._update_status("Error: Failed to reset Chromium source tree.")
            self.current_patch_id = None # Unknown state
            return False

        # 3. Apply new patch if provided
        if target_patch_path:
            if self.use_ssh:
                # SSH: Upload local patch to remote temporary location and apply
                ssh = self._get_ssh_client()
                if not ssh: return False
                try:
                    sftp = ssh.open_sftp()
                    remote_patch_tmp = f"/tmp/{os.path.basename(target_patch_path)}"
                    sftp.put(target_patch_path, remote_patch_tmp)
                    sftp.close()
                    
                    apply_cmd = f"git apply {remote_patch_tmp} && rm {remote_patch_tmp}"
                    success, _, _ = self._run_ssh_command(apply_cmd)
                    ssh.close()
                except Exception as e:
                    self._update_status(f"SSH Patch transfer failed: {e}")
                    ssh.close()
                    return False
            else:
                # Local: Apply directly
                success, _, _ = self.run_command(f"git apply {target_patch_path}", cwd=cwd)
            
            if not success:
                self._update_status(f"Error: Failed to apply patch {patch_val}")
                self.current_patch_id = None
                return False

        self.current_patch_id = target_patch_path
        return True

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
        
        # Patch Management
        patch_val = feature.get('patch')
        if not self._manage_patches(patch_val):
            self._update_status(f"Task {feature['id']}: Patching Failed. Skipping task.")
            return None
        self._update_status(f"Task {feature['id']}: Starting Build")
        build_success, build_time, build_log = self.build_chromium(feature.get('build_flags', []))
        
        # Save build log (both success and failure)
        self.save_build_log(feature['id'], build_success, build_time, build_log, feature.get('build_flags', []))

        if not build_success: 
            self._update_status(f"Task {feature['id']}: Build Failed")
            # Clear state on build failure to be safe
            self.current_patch_id = None
            return None

        repeats = self.settings.get('default_repeats', 5)
        self._update_status(f"Task {feature['id']}: Starting Measurement")
        memory_results = self.measure_memory(feature.get('runtime_flags', []), repeats=repeats)

        if not memory_results:
            self._update_status(f"Task {feature['id']}: Measurement Failed")
            return None

        # Build final result object
        result = {
            "id": feature['id'],
            "group_id": feature.get('group_id'),
            "timestamp": time.time(),
            "build_time": build_time,
            "build_flags": feature.get('build_flags', []),
            "runtime_flags": feature.get('runtime_flags', []),
            "patch": feature.get('patch'),
            "memory_results": memory_results
        }
        
        self.save_result(result)
        self._update_status(f"Task {feature['id']}: Completed Successfully")
        return result

    def save_build_log(self, feature_id, success, build_time, log, build_flags):
        """
        Records the outcome of a build attempt, including the full console output.
        
        Args:
            feature_id (str): ID of the task.
            success (bool): Whether the build finished successfully.
            build_time (float): Duration of the build.
            log (str): The captured stdout/stderr from the build process.
            build_flags (list): The GN arguments used.
        """
        log_entry = {
            "id": feature_id,
            "success": success,
            "build_time": build_time,
            "log": log,
            "build_flags": build_flags,
            "timestamp": time.time()
        }
        
        history = self._load_json('build_logs.json')
        if not isinstance(history, list):
            history = []
            
        history.append(log_entry)
        
        try:
            with open('build_logs.json', 'w') as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save build log: {e}")

    def clear_build_logs(self):
        """
        Deletes the build_logs.json file to clear all build history.
        """
        if os.path.exists('build_logs.json'):
            try:
                os.remove('build_logs.json')
                return True
            except Exception as e:
                logging.error(f"Failed to clear build logs: {e}")
                return False
        return True

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

    def _get_persistent_ssh(self):
        """
        [NEW] 기존에 연결된 SSH 세션이 살아있다면 재사용하고, 죽었으면 새로 연결합니다.
        매번 발생하는 수백 ms 단위의 SSH Handshake 오버헤드를 완벽히 제거합니다.
        """
        if self._persistent_ssh:
            transport = self._persistent_ssh.get_transport()
            if transport and transport.is_active():
                return self._persistent_ssh
                
        # 세션이 없거나 끊어졌다면 새로 연결
        self._persistent_ssh = self._get_ssh_client(timeout=3, max_retries=1)
        return self._persistent_ssh

    def get_remote_vitals(self):
        """
        원격 서버의 시스템 상태를 수집합니다. (지속형 SSH 세션 적용)
        """
        if not self.use_ssh or not self.ssh_config:
            return None

        cmd = "cat /proc/loadavg; echo '---'; free -m; echo '---'; df -BG /; echo '---'; top -bn1 | grep 'Cpu(s)'"
        
        try:
            # 1. 매번 새로 연결하지 않고, 열려있는 세션을 가져옵니다.
            ssh = self._get_persistent_ssh()
            if not ssh:
                return {"status": "disconnected"}

            # 2. 명령어 실행 (이미 뚫려있는 파이프를 통해 텍스트만 쏜살같이 오갑니다)
            stdin, stdout, stderr = ssh.exec_command(cmd)
            output = stdout.read().decode('utf-8').strip()
            
            # [CRITICAL] 여기서 ssh.close()를 절대 하지 않습니다! 다음 3초 뒤에 또 써야 하니까요.

            if not output:
                return {"status": "disconnected"}

            parts = output.split('---')
            if len(parts) < 4: return {"status": "disconnected"}

            # ... (이하 기존 파싱 로직과 동일) ...
            load_avg = " ".join(parts[0].strip().split()[:3]).replace(" ", ", ")
            
            # Memory parsing
            mem_lines = parts[1].strip().split('\n')
            ram_tot = ram_curr = swap_tot = swap_curr = 0
            for line in mem_lines:
                if line.startswith('Mem:'):
                    cols = line.split()
                    ram_tot = round(int(cols[1]) / 1024, 1)
                    ram_curr = round(int(cols[2]) / 1024, 1)
                elif line.startswith('Swap:'):
                    cols = line.split()
                    swap_tot = round(int(cols[1]) / 1024, 1)
                    swap_curr = round(int(cols[2]) / 1024, 1)

            # Disk parsing
            disk_lines = parts[2].strip().split('\n')
            disk_tot = disk_used = 0
            if len(disk_lines) > 1:
                cols = disk_lines[1].split()
                disk_tot = int(cols[1].replace('G', ''))
                disk_used = int(cols[2].replace('G', ''))

            # CPU parsing
            cpu_line = parts[3].strip()
            cpu_percent = 0
            if cpu_line:
                idle_str = [x for x in cpu_line.split(',') if 'id' in x]
                if idle_str:
                    idle_val = float(idle_str[0].strip().split()[0])
                    cpu_percent = round(100.0 - idle_val)

            return {
                "status": "connected",
                "cpu_percent": cpu_percent,
                "ram_curr_gb": ram_curr,
                "ram_tot_gb": ram_tot,
                "swap_curr_gb": swap_curr,
                "swap_tot_gb": swap_tot,
                "load_avg": load_avg,
                "disk_used_gb": disk_used,
                "disk_tot_gb": disk_tot
            }
            
        except Exception as e:
            print(f"Failed to fetch remote vitals: {e}")
            # 네트워크가 끊어졌다면 다음 시도를 위해 세션을 초기화합니다.
            if self._persistent_ssh:
                self._persistent_ssh.close()
                self._persistent_ssh = None
            return {"status": "disconnected"}


