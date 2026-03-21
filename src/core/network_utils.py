import time
import functools
import logging
import random
from typing import Callable

logger = logging.getLogger(__name__)

def retry_with_backoff(retries: int = 3, backoff_in_seconds: int = 1):
    """
    Decorator for retrying a function with exponential backoff.
    Ideal for networking requests to Moodle.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            x = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if x == retries:
                        logger.error(f"Function {func.__name__} failed after {retries} retries: {str(e)}")
                        raise
                    
                    # Exponential backoff with jitter
                    # (2^x * backoff_in_seconds) + random jitter
                    sleep_time = (backoff_in_seconds * (2 ** x) + 
                                 random.uniform(0, 1))
                    
                    logger.warning(f"Retry {x + 1}/{retries} for {func.__name__} in {sleep_time:.2f}s due to: {str(e)}")
                    time.sleep(sleep_time)
                    x += 1
        return wrapper
    return decorator
