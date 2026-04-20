import time
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from bs4 import BeautifulSoup

def dummy_work(html_content):
    soup = BeautifulSoup(html_content, "lxml")
    # Simulate finding some assignments
    nodes = soup.find_all("div")
    return len(nodes)

if __name__ == '__main__':
    # Add project root to sys.path
    # project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    # if project_root not in sys.path:
    #     sys.path.append(project_root)

    multiprocessing.freeze_support()
    
    html_content = "<div><p>Assignment 1</p></div>" * 1000

    print("----- BENCHMARKING PARSING -----")
    
    # Process Pool Test
    print("Testing ProcessPoolExecutor...")
    start_time = time.time()
    with ProcessPoolExecutor(max_workers=2) as pool:
        future1 = pool.submit(dummy_work, html_content)
        future2 = pool.submit(dummy_work, html_content)
        res1 = future1.result()
        res2 = future2.result()
    process_duration = time.time() - start_time
    print(f"ProcessPoolExecutor took {process_duration:.4f} seconds.")

    # Thread Pool Test
    print("Testing ThreadPoolExecutor...")
    start_time = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future1 = pool.submit(dummy_work, html_content)
        future2 = pool.submit(dummy_work, html_content)
        res1 = future1.result()
        res2 = future2.result()
    thread_duration = time.time() - start_time
    print(f"ThreadPoolExecutor took {thread_duration:.4f} seconds.")
    
    print(f"Difference: ThreadPool was {process_duration/thread_duration:.2f} times faster.")
