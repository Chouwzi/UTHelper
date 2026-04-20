import asyncio
import threading
import time

def simulate_zombie_loop():
    print("\n--- Test 1: Sleep dài (Zombie Loop) ---")
    page_alive = threading.Event()
    page_alive.set()
    
    async def loop_old():
        while page_alive.is_set():
            print("[Old] Đang ngủ 5 giây...")
            await asyncio.sleep(5)  # Giả sử đây là 60 phút
            if not page_alive.is_set(): break
            print("[Old] Load data!")
            
    async def main_old():
        task = asyncio.create_task(loop_old())
        await asyncio.sleep(1)
        print(">>> App bị tắt bởi user. Clear event.")
        page_alive.clear()
        start = time.time()
        await task # Chờ task kết thúc (để xem nó có tắt liền không)
        print(f"Task tắt sau {time.time() - start:.2f}s từ khi close app. ==> RẤT CHẬM!")
        
    asyncio.run(main_old())

def simulate_fixed_loop():
    print("\n--- Test 2: Sleep chia nhỏ (Wait_for logic) ---")
    page_alive = threading.Event()
    page_alive.set()
    
    async def loop_new():
        while page_alive.is_set():
            print("[New] Bắt đầu chu kỳ đợi 5s...")
            slept = 0
            while slept < 5 and page_alive.is_set():
                await asyncio.sleep(0.5)
                slept += 0.5
            if not page_alive.is_set(): break
            print("[New] Load data!")
            
    async def main_new():
        task = asyncio.create_task(loop_new())
        await asyncio.sleep(1)
        print(">>> App bị tắt bởi user. Clear event.")
        page_alive.clear()
        start = time.time()
        await task # Chờ task kết thúc
        print(f"Task tắt sau {time.time() - start:.2f}s từ khi close app. ==> KHÓA THOÁT NHANH CHÓNG!")
        
    asyncio.run(main_new())

if __name__ == '__main__':
    simulate_zombie_loop()
    simulate_fixed_loop()
