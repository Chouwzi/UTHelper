import psutil
import subprocess
import time
import os
import signal

EXE_PATH = r"build\windows\UTHelper.exe"

def get_process_tree(pid):
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return []
    children = parent.children(recursive=True)
    children.append(parent)
    return children

def measure_performance():
    print(f"Starting {EXE_PATH}...")
    start_time = time.time()
    
    # Start the process
    proc = subprocess.Popen([EXE_PATH])
    pid = proc.pid
    
    print(f"Process started with PID: {pid}")
    
    # Wait for the app to initialize its sub-processes (e.g. Flet client, Python backend)
    time.sleep(5)
    
    # Get all related processes
    procs = get_process_tree(pid)
    print(f"Tracking {len(procs)} processes in the tree.")
    
    # Initialize CPU counters
    for p in procs:
        try:
            p.cpu_percent()
        except Exception:
            pass

    memory_samples = []
    cpu_samples = []
    
    print("Monitoring for 15 seconds...")
    try:
        for _ in range(15):
            time.sleep(1)
            
            # Re-evaluate tree in case of short-lived child processes
            procs = get_process_tree(pid)
            if not procs:
                print("App exited prematurely.")
                break
                
            total_mem_mb = 0
            total_cpu = 0
            
            for p in procs:
                try:
                    # rss: Resident Set Size (Physical Memory)
                    mem_info = p.memory_info()
                    total_mem_mb += mem_info.rss / (1024 * 1024)
                    total_cpu += p.cpu_percent()
                except psutil.NoSuchProcess:
                    continue
            
            memory_samples.append(total_mem_mb)
            cpu_samples.append(total_cpu)
            print(f"Current Memory: {total_mem_mb:.2f} MB | Current CPU: {total_cpu:.2f}%")
            
    except KeyboardInterrupt:
        pass
        
    print("\n--- Performance Report ---")
    if memory_samples:
        avg_mem = sum(memory_samples) / len(memory_samples)
        peak_mem = max(memory_samples)
        avg_cpu = sum(cpu_samples) / len(cpu_samples)
        
        print(f"Average Memory Usage (RSS): {avg_mem:.2f} MB")
        print(f"Peak Memory Usage (RSS):    {peak_mem:.2f} MB")
        print(f"Average CPU Usage:          {avg_cpu:.2f} % (Note: multi-core %)")
    else:
        print("No samples collected.")
        
    print("Killing process tree...")
    for p in get_process_tree(pid):
        try:
            p.kill()
        except:
            pass
    
    proc.wait()
    print("Done.")

if __name__ == "__main__":
    measure_performance()
