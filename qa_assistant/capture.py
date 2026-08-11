"""屏幕 / 摄像头画面采集模块。

提供两个采集源：
- capture_screen(region): 使用 mss 抓取屏幕指定区域
- capture_camera(index):  使用 OpenCV 抓取摄像头画面
"""

from __future__ import annotations

import atexit

import numpy as np

_sct = None
_cap = None
_cap_index = None


def _mss():
    """惰性创建并复用 mss 抓屏连接（避免每帧都新建/销毁）。"""
    global _sct
    if _sct is None:
        import mss

        _sct = mss.mss()
        atexit.register(_sct.close)
    return _sct


def _camera(index: int):
    """惰性创建并复用指定编号的摄像头句柄。"""
    global _cap, _cap_index
    if _cap is None or _cap_index != index:
        import cv2

        _cap = cv2.VideoCapture(index)
        _cap_index = index
        atexit.register(_cap.release)
    return _cap


def capture_screen(region: tuple | None = None) -> np.ndarray:
    """抓取屏幕区域，返回 BGR 格式的 numpy 数组。

    region: (left, top, width, height)，为 None 时抓取整屏。
    """
    sct = _mss()
    monitor = (
        {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
        if region
        else sct.monitors[0]
    )
    frame = np.array(sct.grab(monitor), dtype=np.uint8)
    # mss 输出 BGRA，转成 BGR
    return frame[..., :3]


def capture_camera(index: int = 0) -> np.ndarray:
    """抓取一帧摄像头画面，返回 BGR 格式的 numpy 数组。"""
    cap = _camera(index)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError(f"无法从摄像头 {index} 读取画面")
    return frame
