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

def run_measurement(target_urls, runtime_flags, repeats, stabilization_sec, evaluation_sec, interval, chrome_exe, chromedriver_exe):
    """
    Executes high-frequency memory measurements using Selenium and CDP.
    Follows a two-phase protocol:
    Phase 1: Stabilization (X seconds, measures X+1 times)
    Phase 2: Evaluation (Y seconds, measures Y times)
    Peak value is the maximum RSS of Phase 2.
    
    Args:
        target_urls (list): List of website URLs to measure.
        runtime_flags (list): Command-line arguments for the Chrome binary.
        repeats (int): Number of times to repeat the measurement for each URL.
        stabilization_sec (float): Phase 1 duration.
        evaluation_sec (float): Phase 2 duration.
        interval (float): Time in seconds between consecutive memory captures.
        chrome_exe (str): Absolute path to the Chrome binary.
        chromedriver_exe (str): Absolute path to the ChromeDriver binary.
        
    Returns:
        list: A list of result dictionaries containing peak, samples, and metadata.
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
                total_duration = stabilization_sec + evaluation_sec
                
                points_collected = 0
                expected_points = int((total_duration / interval) + 1)
                
                while points_collected < expected_points:
                    current_target_time = start_time + (points_collected * interval)
                    
                    # Wait until it's time for the next measurement
                    time_to_wait = current_target_time - time.time()
                    if time_to_wait > 0:
                        time.sleep(time_to_wait)

                    current_elapsed = points_collected * interval
                    
                    # 1. Measure System Memory (RSS)
                    rss_mb = get_mem(driver.service.process.pid)
                    if rss_mb > 0:
                        # 2. Measure CDP Performance Metrics
                        try:
                            cdp_metrics = driver.execute_cdp_cmd('Performance.getMetrics', {})
                            metrics_dict = {m['name']: m['value'] for m in cdp_metrics['metrics']}
                        except:
                            metrics_dict = {}

                        sample = {
                            "timestamp": time.time(),
                            "elapsed": current_elapsed,
                            "rss": rss_mb,
                            "js_heap_used": metrics_dict.get('JSHeapUsedSize', 0) / (1024 * 1024),
                            "js_heap_total": metrics_dict.get('JSHeapTotalSize', 0) / (1024 * 1024),
                            "layout_duration": metrics_dict.get('LayoutDuration', 0),
                            "task_duration": metrics_dict.get('TaskDuration', 0),
                            "script_duration": metrics_dict.get('ScriptDuration', 0),
                            "nodes": metrics_dict.get('Nodes', 0),
                            "documents": metrics_dict.get('Documents', 0)
                        }
                        all_samples.append(sample)
                        
                        if current_elapsed > stabilization_sec + (interval / 2):
                            evaluation_rss.append(rss_mb)
                            
                    points_collected += 1
                
                if all_samples:
                    peak_rss = max(evaluation_rss) if evaluation_rss else max([s['rss'] for s in all_samples])
                    iter_res["urls"][url] = {
                        "peak": peak_rss,
                        "samples": all_samples,
                        "phase1_count": len(all_samples) - len(evaluation_rss),
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
