import ctypes
from ctypes import wintypes
import time

kernel32 = ctypes.windll.kernel32

class DEBUG_EVENT(ctypes.Structure):
    pass # We just need it to capture

class OUTPUT_DEBUG_STRING_INFO(ctypes.Structure):
    _fields_ = [
        ("lpDebugStringData", wintypes.LPSTR),
        ("fUnicode", wintypes.WORD),
        ("nDebugStringLength", wintypes.WORD)
    ]

# Actually, listening to ETW is hard in pure python.
# A simpler way is to just run a loop polling `OutputDebugString` using the dbgeng API.
# Wait, `DebugActiveProcess` lets us attach as a debugger and receive debug events!
