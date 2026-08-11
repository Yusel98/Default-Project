# -*- coding: utf-8 -*-
"""截图模块: 基于 mss 的高速屏幕捕获, 返回 BGR 格式 numpy 数组(兼容 cv2/OCR)。"""
import cv2
import mss
import numpy as np


class ScreenCapture:
    def __init__(self):
        self._sct = mss.mss()

    @staticmethod
    def screen_size():
        with mss.mss() as sct:
            m = sct.monitors[0]
            return int(m["width"]), int(m["height"])

    def grab(self, left, top, width, height):
        """截取指定屏幕区域(绝对坐标), 返回 BGR 图像。宽高 <=0 时截全屏。"""
        left, top = int(left), int(top)
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            width, height = self.screen_size()
        shot = self._sct.grab({
            "left": left, "top": top,
            "width": width, "height": height,
        })
        return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)

    def grab_region(self, region):
        """region = [left, top, width, height], width/height 为 0 时截全屏。"""
        left, top, w, h = region
        if w <= 0 or h <= 0:
            sw, sh = self.screen_size()
            w, h = sw, sh
        return self.grab(left, top, w, h)

    def save(self, img, path):
        cv2.imwrite(path, img)

    def save_with_points(self, img, points, path, color=(0, 0, 255)):
        """把目标点用圆点画在图上(用于校准核对), 再保存。points 为绝对坐标。"""
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        draw = img.copy()
        for (x, y) in points:
            cv2.circle(draw, (int(x), int(y)), 6, color, 2)
        cv2.imwrite(path, draw)
