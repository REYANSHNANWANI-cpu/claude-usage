"""Cross-platform active window and foreground process detector."""
import os
import platform
import subprocess
import sys
from typing import Dict, Optional, Tuple

CURRENT_OS = platform.system().lower()

if CURRENT_OS == "windows":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Configure ctypes function signatures
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD


def get_foreground_window_windows() -> Dict[str, Optional[str]]:
    """Retrieve foreground window title and process information on Windows."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"title": "", "process_name": "", "pid": None, "exe": ""}

    # Extract Window Title
    length = 1024
    buffer = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buffer, length)
    title = buffer.value.strip()

    # Extract Process ID
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    target_pid = int(pid.value)

    process_name = ""
    exe_path = ""
    try:
        import psutil
        proc = psutil.Process(target_pid)
        process_name = proc.name()
        try:
            exe_path = proc.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    except Exception:
        pass

    return {
        "title": title,
        "process_name": process_name,
        "pid": target_pid,
        "exe": exe_path,
    }


def get_foreground_window_macos() -> Dict[str, Optional[str]]:
    """Retrieve foreground window title and process information on macOS via AppleScript."""
    script = """
    global frontApp, frontAppName, windowTitle
    set windowTitle to ""
    tell application "System Events"
        set frontApp to first application process whose frontmost is true
        set frontAppName to name of frontApp
        tell frontApp
            if (count of windows) > 0 then
                set windowTitle to name of front window
            end if
        end tell
    end tell
    return frontAppName & "|||" & windowTitle
    """
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            parts = proc.stdout.strip().split("|||")
            proc_name = parts[0] if len(parts) > 0 else ""
            title = parts[1] if len(parts) > 1 else ""
            return {"title": title, "process_name": proc_name, "pid": None, "exe": ""}
    except Exception:
        pass

    return {"title": "", "process_name": "", "pid": None, "exe": ""}


def get_foreground_window_linux() -> Dict[str, Optional[str]]:
    """Retrieve active window title on Linux using xdotool."""
    try:
        res = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if res.returncode == 0:
            return {"title": res.stdout.strip(), "process_name": "", "pid": None, "exe": ""}
    except Exception:
        pass
    return {"title": "", "process_name": "", "pid": None, "exe": ""}


def get_active_window_info() -> Dict[str, Optional[str]]:
    """
    Cross-platform query returning dictionary with:
    {'title': str, 'process_name': str, 'pid': Optional[int], 'exe': str}
    """
    if CURRENT_OS == "windows":
        return get_foreground_window_windows()
    elif CURRENT_OS == "darwin":
        return get_foreground_window_macos()
    else:
        return get_foreground_window_linux()
