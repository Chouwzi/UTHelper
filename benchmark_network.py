import time
import asyncio
import httpx
import requests
from concurrent.futures import ThreadPoolExecutor

urls = [
    "https://courses.ut.edu.vn",
    "https://portal.ut.edu.vn",
    "https://courses.ut.edu.vn/login/index.php"
] * 4  # 12 requests

def fetch_requests(url):
    try:
        r = requests.get(url, timeout=5)
        return r.status_code
    except Exception as e:
        return str(e)

def run_threads():
    start = time.time()
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch_requests, urls))
    print(f"[Requests + ThreadPool] Hoàn thành 12 requests. Thời gian: {time.time() - start:.3f}s")

async def fetch_httpx(client, url):
    try:
        r = await client.get(url, timeout=5)
        return r.status_code
    except Exception as e:
        return str(e)

async def run_httpx():
    start = time.time()
    async with httpx.AsyncClient() as client:
        tasks = [fetch_httpx(client, url) for url in urls]
        results = await asyncio.gather(*tasks)
    print(f"[HTTPX Asyncio] Hoàn thành 12 requests. Thời gian: {time.time() - start:.3f}s")


if __name__ == "__main__":
    print("Mô phỏng Benchmark Network: Threading vs Asyncio")
    run_threads()
    asyncio.run(run_httpx())
