# -*- coding: utf-8 -*-
"""鼠标/键盘控制: 基于 pyautogui, 封装右键/拖拽/关闭等常用操作。"""
import ctypes
import time

import pyautogui

# 停止热键虚拟键码(F1~F12, ESC 等)
VK = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B, "ESC": 0x1B,
}


class MouseController:
    def __init__(self, config, logger=None):
        self.logger = logger
        p = config["params"]
        self.click_delay = float(p.get("click_delay", 0.4))
        self.pre_click_delay = float(p.get("pre_click_delay", 0.3))
        self.drag_duration = float(p.get("drag_duration", 0.5))
        self.stop_key = p.get("stop_key", "F12")
        self.dry_run = bool(p.get("dry_run", False))

        # 安全机制: 鼠标移到屏幕左上角即触发 FailSafe 异常, 防止失控
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = self.click_delay

        self._init_dpi_awareness()

    @staticmethod
    def _init_dpi_awareness():
        """设为系统 DPI 感知, 保证 pyautogui 与 mss 坐标一致。"""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    # -------------------------------------------------- 基础操作
    def _guard(self, func, *args, **kwargs):
        """dry_run 时跳过真实鼠标操作。"""
        if self.dry_run:
            if self.logger:
                self.logger.debug("dry-run: 跳过 %s%s", func.__name__, args[:2])
            return
        return func(*args, **kwargs)

    def _correct_drift(self, x, y, tol=4, attempts=3):
        """游戏存在鼠标漂移: 移动到目标后校验光标位置, 被拉走就移回来。"""
        corrected = False
        for _ in range(attempts):
            cx, cy = self.cursor_pos()
            if abs(cx - x) <= tol and abs(cy - y) <= tol:
                if corrected and self.logger:
                    self.logger.debug("光标漂移已校正: 目标(%d, %d) 实际(%d, %d)",
                                      x, y, cx, cy)
                return
            pyautogui.moveTo(x, y)
            time.sleep(0.03)
            corrected = True
        if self.logger:
            self.logger.warning("光标漂移未能校正: 目标(%d, %d) 实际(%d, %d)",
                                x, y, cx, cy)

    def move(self, x, y):
        def _do():
            self._correct_drift(x, y)
            if self.logger:
                self.logger.debug("移动鼠标 -> (%d, %d)", x, y)

        self._guard(_do)

    def click(self, x, y, button="left", clicks=1, interval=0.1):
        def _do():
            # 先移动到目标并校验(游戏会漂移光标), 等待游戏识别悬停状态,
            # 点击前再校正一次, 然后立即在当前(已校正)位置点击, 缩短漂移窗口。
            self._correct_drift(x, y)
            if self.pre_click_delay > 0:
                time.sleep(self.pre_click_delay)
            self._correct_drift(x, y)
            pyautogui.click(button=button, clicks=clicks, interval=interval)
            if self.logger:
                self.logger.debug("点击 (%d, %d) 方式=%s", x, y, button)

        self._guard(_do)

    def right_click(self, x, y):
        self.click(x, y, button="right", clicks=1)

    def double_click(self, x, y):
        self.click(x, y, button="left", clicks=2)

    def drag(self, x1, y1, x2, y2, duration=None):
        """从起点拖拽到终点(按住左键移动再松开)。"""
        duration = duration if duration is not None else self.drag_duration

        def _do():
            pyautogui.moveTo(x1, y1, duration=max(0.05, duration * 0.2))
            pyautogui.mouseDown(x1, y1)
            pyautogui.moveTo(x2, y2, duration=duration, tween=pyautogui.easeOutQuad)
            pyautogui.mouseUp(x2, y2)

        self._guard(_do)

    def press_key(self, key):
        self._guard(pyautogui.press, key)

    def press_hotkey(self, key):
        """按快捷键, 支持组合键, 如 "ctrl+b"。逐个按下再逐个抬起, 提高游戏识别率。"""
        if not key:
            return
        parts = [p.strip() for p in str(key).split("+")]

        def _do():
            if len(parts) > 1:
                for k in parts:
                    pyautogui.keyDown(k)
                    time.sleep(0.05)
                time.sleep(0.05)
                for k in reversed(parts):
                    pyautogui.keyUp(k)
                    time.sleep(0.05)
            else:
                pyautogui.press(parts[0])

        self._guard(_do)

    def press_esc(self):
        self.press_key("esc")

    # -------------------------------------------------- 停止检测
    def stop_requested(self):
        """按住配置的停止键(如 F12)返回 True。"""
        if not self.stop_key:
            return False
        vk = VK.get(str(self.stop_key).upper())
        if vk is None:
            return False
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)

    @staticmethod
    def cursor_pos():
        return pyautogui.position()

    @staticmethod
    def get_cursor_pos():
        pt = pyautogui.position()
        return int(pt.x), int(pt.y)
