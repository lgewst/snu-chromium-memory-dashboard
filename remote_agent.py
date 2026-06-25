import json
import time
import os
import sys
import threading
import subprocess

def _install_package(package):
    try:
        __import__(package)
    except ImportError:
        print(f"Installing {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

_install_package('psutil')
_install_package('selenium')

import psutil
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

def get_mem(pid):
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

def cleanup():
    """
    Forcefully terminates any remaining chrome or chromedriver processes 
    on the remote system to ensure a clean state for the next iteration.
    """
    os.system("pkill -f chrome || true")
    os.system("pkill -f chromedriver || true")

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

def capture_sample(rss_mb, pss_mb, actual_elapsed, cdp_data=None):
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

def run_measurement(target_urls, runtime_flags, repeats, stabilization_sec, evaluation_sec, interval, chrome_exe, chromedriver_exe):
    """
    Executes high-frequency memory measurements using Selenium and Asynchronous CDP collection.
    
    This implementation restarts the browser for every URL to ensure isolation and 
    uses a dedicated thread for CDP metrics to keep the measurement loop precise.
    
    Args:
        target_urls (list): List of website URLs to measure.
        runtime_flags (list): Command-line arguments for the Chrome binary.
        repeats (int): Number of measurement iterations per URL.
        stabilization_sec (float): Phase 1 duration in seconds.
        evaluation_sec (float): Phase 2 duration in seconds.
        interval (float): Target time in seconds between captures.
        chrome_exe (str): Absolute path to the Chrome binary.
        chromedriver_exe (str): Absolute path to the ChromeDriver binary.
        
    Returns:
        list: A list of iteration results containing peak, samples, and metadata.
    """
    import platform
    device_info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "cpu": platform.processor(),
        "machine": platform.machine(),
        "python_version": platform.python_version()
    }

    results = []
    print(f"Starting precision measurement (Async CDP, URL Isolation): {repeats} repeats")
    sys.stdout.flush()
    
    for i in range(repeats):
        print(f"Iteration {i+1}/{repeats}...")
        sys.stdout.flush()
        iter_res = {"iteration": i + 1, "urls": {}, "metadata": device_info}
        
        for url in target_urls:
            # Setup fresh driver for each URL
            opts = Options()
            opts.binary_location = chrome_exe
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            for f in runtime_flags:
                opts.add_argument(f)
            
            try:
                print(f"  Starting browser for: {url}")
                sys.stdout.flush()
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
                    rss_mb, pss_mb = get_mem(driver.service.process.pid)
                    if rss_mb > 0:
                        cdp_data = collector.get_latest()
                        sample = capture_sample(rss_mb, pss_mb, actual_elapsed, cdp_data)
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
                    rss_mb, pss_mb = get_mem(driver.service.process.pid)
                    if rss_mb > 0:
                        cdp_data = collector.get_latest()
                        sample = capture_sample(rss_mb, pss_mb, actual_elapsed, cdp_data)
                        all_samples.append(sample)
                        evaluation_rss.append(rss_mb)
                        evaluation_pss.append(pss_mb)
                
                if all_samples:
                    peak_rss = max(evaluation_rss) if evaluation_rss else max([s['rss'] for s in all_samples])
                    peak_pss = max(evaluation_pss) if evaluation_pss else max([s['pss'] for s in all_samples])
                    iter_res["urls"][url] = {
                        "peak": peak_rss,
                        "peak_pss": peak_pss,
                        "samples": all_samples,
                        "phase1_count": phase1_final_count,
                        "phase2_count": len(evaluation_rss)
                    }
                    print(f"    Peak RSS: {peak_rss:.2f} MB, Peak PSS: {peak_pss:.2f} MB")
                
                collector.stop()
                driver.quit()
                
            except Exception as e:
                print(f"    Error during {url}: {e}")
                sys.stdout.flush()
                try: driver.quit()
                except: pass
            
            # Brief cool-down between URLs
            time.sleep(1)
        
        cleanup()
        if iter_res["urls"]:
            results.append(iter_res)
            
    return results

if __name__ == "__main__":
    # Placeholder values to be replaced by the pipeline before uploading.
    TARGET_URLS = {{TARGET_URLS}}
    RUNTIME_FLAGS = {{RUNTIME_FLAGS}}
    REPEATS = {{REPEATS}}
    STABILIZATION_SEC = {{STABILIZATION_SEC}}
    EVALUATION_SEC = {{EVALUATION_SEC}}
    INTERVAL = {{INTERVAL}}
    CHROME_EXE = "{{CHROME_EXE}}"
    CHROMEDRIVER_EXE = "{{CHROMEDRIVER_EXE}}"
    
    results = run_measurement(TARGET_URLS, RUNTIME_FLAGS, REPEATS, float(STABILIZATION_SEC), float(EVALUATION_SEC), float(INTERVAL), CHROME_EXE, CHROMEDRIVER_EXE)
    
    # Send the structured result back to the host via standard output
    print("---RESULT_START---")
    print(json.dumps(results))
    print("---RESULT_END---")
    sys.stdout.flush()
