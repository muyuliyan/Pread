import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter

DEFAULT_KEYS = {"reader": "P", "lookup": "K", "translate": "T", "explain": "E", "note": "N"}
CTRL_SHIFT = 0x0002 | 0x0004
NO_REPEAT = 0x4000
WM_HOTKEY = 0x0312


class GlobalHotkeys(QAbstractNativeEventFilter):
    def __init__(self, app, window, actions, keys):
        super().__init__()
        self.app = app
        self.registered = {}
        self.failed = []
        if sys.platform != "win32":
            return
        self.hwnd = int(window.winId())
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self.user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        for identifier, (name, action) in enumerate(actions.items(), 1):
            key = keys.get(name, DEFAULT_KEYS[name])
            if key is False:
                continue
            if not isinstance(key, str) or len(key) != 1 or key.upper() not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
                self.failed.append(name)
                continue
            if self.user32.RegisterHotKey(self.hwnd, identifier, CTRL_SHIFT | NO_REPEAT, ord(key.upper())):
                self.registered[identifier] = action
            else:
                self.failed.append(name)
        if self.registered:
            app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if self.registered and bytes(event_type) in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}:
            event = wintypes.MSG.from_address(int(message))
            if event.message == WM_HOTKEY and event.hwnd == self.hwnd and event.wParam in self.registered:
                self.registered[event.wParam]()
                return True, 0
        return False, 0

    def close(self):
        if self.registered:
            self.app.removeNativeEventFilter(self)
            for identifier in self.registered:
                self.user32.UnregisterHotKey(self.hwnd, identifier)
            self.registered.clear()
