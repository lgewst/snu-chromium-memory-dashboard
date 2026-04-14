import json
import time
import psutil
import os
import sys

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
except ImportError as e:
    print(f"---ERROR---: Required Python module missing: {e}. Please run 'pip install selenium psutil' on the remote server.")
    sys.exit(1)

def get_mem(pid):
    """
    Calculates the total RSS memory usage of a process and all its recursive children.
    
    Args:
        pid (int): The process ID of the parent process.
        
    Returns:
        float: Total memory usage in Megabytes (MB).
    """
    try:
        parent = psutil.Process(pid)
        total = parent.memory_info().rss
        for child in parent.children(recursive=True):
            total += child.memory_info().rss
        return total / (1024 * 1024)  # Convert to MB
    except:
        return 0

def cleanup():
    """
    Forcefully terminates any remaining chrome or chromedriver processes 
    on the remote system to ensure a clean state for the next iteration.
    """
    os.system("pkill -f chrome || true")
    os.system("pkill -f chromedriver || true")

def capture_sample(driver, rss_mb, actual_elapsed):
    """
    Captures a comprehensive snapshot of memory and performance metrics via CDP.
    
    Executes the 'Performance.getMetrics' command to retrieve internal browser 
    metrics such as V8 JS Heap usage and execution durations for layout/scripts.

    Args:
        driver (webdriver.Chrome): The active Selenium driver instance.
        rss_mb (float): The total system-level RSS memory usage in Megabytes.
        actual_elapsed (float): The actual time in seconds since the page load started.

    Returns:
        dict: A sample dictionary containing system memory and CDP metrics.
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

def run_measurement(target_urls, runtime_flags, repeats, stabilization_sec, evaluation_sec, interval, chrome_exe, chromedriver_exe):
    """
    Executes high-frequency memory measurements using Selenium and CDP.
    
    Follows a robust two-phase measurement protocol:
    1. Phase 1 (Stabilization): Skip-behind strategy to avoid catch-up bursts.
    2. Phase 2 (Evaluation): Catch-up strategy to ensure exactly N measurements.
    
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
    print(f"Starting precision measurement: {repeats} repeats, {len(target_urls)} URLs, Stabilization: {stabilization_sec}s, Evaluation: {evaluation_sec}s at {interval}s interval")
    sys.stdout.flush()
    
    for i in range(repeats):
        print(f"Iteration {i+1}/{repeats}...")
        sys.stdout.flush()
        iter_res = {"iteration": i + 1, "urls": {}, "metadata": device_info}
        opts = Options()
        opts.binary_location = chrome_exe
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        for f in runtime_flags:
            opts.add_argument(f)
        
        try:
            svc = Service(executable_path=chromedriver_exe)
            driver = webdriver.Chrome(service=svc, options=opts)
            # Enable CDP Performance Domain
            driver.execute_cdp_cmd('Performance.enable', {})

            for url in target_urls:
                print(f"  Visiting: {url}")
                sys.stdout.flush()
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
                    rss_mb = get_mem(driver.service.process.pid)
                    if rss_mb > 0:
                        sample = capture_sample(driver, rss_mb, actual_elapsed)
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
                    rss_mb = get_mem(driver.service.process.pid)
                    if rss_mb > 0:
                        sample = capture_sample(driver, rss_mb, actual_elapsed)
                        all_samples.append(sample)
                        evaluation_rss.append(rss_mb)
                
                if all_samples:
                    peak_rss = max(evaluation_rss) if evaluation_rss else max([s['rss'] for s in all_samples])
                    iter_res["urls"][url] = {
                        "peak": peak_rss,
                        "samples": all_samples,
                        "phase1_count": phase1_final_count,
                        "phase2_count": len(evaluation_rss)
                    }
                    print(f"  Peak RSS (Evaluation Phase): {peak_rss:.2f} MB")
                else:
                    print(f"  Memory: Failed to measure")
                sys.stdout.flush()
            driver.quit()
        except Exception as e:
            print(f"Iteration {i+1} error: {e}")
            sys.stdout.flush()
        
        cleanup()
        if iter_res["urls"]:
            results.append(iter_res)
    return results

if __name__ == "__main__":
    # Placeholder values to be replaced by the pipeline before uploading.
    # These are dynamically populated by ChromiumPipeline._measure_memory_remote.
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
