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
    Executes high-frequency memory measurements using Selenium.
    Follows a two-phase protocol:
    Phase 1: Stabilization (X seconds, measures X+1 times)
    Phase 2: Evaluation (Y seconds, measures Y times)
    Peak value is the maximum of Phase 2.
    
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
        list: A list of result dictionaries containing peak and all captured samples.
    """
    results = []
    print(f"Starting precision measurement: {repeats} repeats, {len(target_urls)} URLs, Stabilization: {stabilization_sec}s, Evaluation: {evaluation_sec}s at {interval}s interval")
    sys.stdout.flush()
    
    for i in range(repeats):
        print(f"Iteration {i+1}/{repeats}...")
        sys.stdout.flush()
        iter_res = {"iteration": i + 1, "urls": {}}
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
            for url in target_urls:
                print(f"  Visiting: {url}")
                sys.stdout.flush()
                driver.get(url)
                
                all_measurements = []
                evaluation_measurements = []
                
                start_time = time.time()
                total_duration = stabilization_sec + evaluation_sec
                
                points_collected = 0
                expected_points = int((total_duration / interval) + 1)
                
                while points_collected < expected_points:
                    current_elapsed = points_collected * interval
                    # Wait until it's time for the next measurement
                    while time.time() - start_time < current_elapsed:
                        time.sleep(0.01)
                        
                    mem = get_mem(driver.service.process.pid)
                    if mem > 0:
                        all_measurements.append(mem)
                        
                        # If we are in evaluation phase (after stabilization_sec)
                        if current_elapsed > stabilization_sec - (interval / 2):
                            evaluation_measurements.append(mem)
                            
                    points_collected += 1
                
                if all_measurements:
                    peak_mem = max(evaluation_measurements) if evaluation_measurements else max(all_measurements)
                    iter_res["urls"][url] = {
                        "peak": peak_mem,
                        "all": all_measurements,
                        "phase1_count": len(all_measurements) - len(evaluation_measurements),
                        "phase2_count": len(evaluation_measurements)
                    }
                    print(f"  Peak Memory (Evaluation Phase): {peak_mem:.2f} MB")
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
