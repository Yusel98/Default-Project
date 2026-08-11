# -*- coding: utf-8 -*-
"""仓库操作: 手动分类入库场景下, 只负责"点击仓库标签选中 + 右键道具栏格子存入"。
"""
import time

from .mouse_controller import MouseController


class WarehouseFullError(Exception):
    """仓库已满异常。"""


class Warehouse:
    def __init__(self, config, mouse, ocr=None, logger=None):
        self.config = config
        self.mouse = mouse
        self.ocr = ocr
        self.logger = logger
        self.coords = config["coords"]
        self.params = config["params"]

    # -------------------------------------------------- 入库
    def deposit(self, from_pos, warehouse_name):
        """把仓库界面道具栏 from_pos 处的宝图存入 warehouse_name 仓库。

        方式: 用 OCR 在仓库标签区域实时定位仓库名标签(标签已改成地点名),
        点击其中心选中, 再右键道具栏格子即可存入。
        抛出 WarehouseFullError 表示该仓库已满。
        """
        if not self.ocr:
            raise KeyError("未配置 OCR, 无法定位仓库标签")

        region = self.coords.get("warehouse_tab_region") or [0, 0, 0, 0]
        left, top, w, h = region
        if not (w > 0 and h > 0):
            raise KeyError("未配置仓库标签区域(warehouse_tab_region)")

        x, y, matched = self.ocr.find_text_position(warehouse_name, left, top, w, h)
        if x is None or y is None:
            raise KeyError("OCR 未在仓库标签区域识别到[%s](读到: %s)"
                           % (warehouse_name, matched))

        # 1) 把 OCR 结果吸附到最近的网格格子中心再点击, 避免文字 bbox 抖动点到相邻仓库。
        #    OCR 只用来确认"是哪个仓库(index)", 点击点用规则格子的中心(稳定), 不直接用文字中心。
        cx, cy = self._snap_to_cell(x, y, left, top, w, h)
        self.mouse.click(cx, cy)
        time.sleep(self.params.get("after_right_click_delay", 0.6))
        # 2) 右键道具栏格子, 存入选中仓库
        self.mouse.right_click(from_pos[0], from_pos[1])
        time.sleep(self.params.get("after_right_click_delay", 0.6))

        if self._is_full():
            raise WarehouseFullError("仓库[%s]已满" % warehouse_name)

        if self.logger:
            self.logger.info("OCR定位仓库标签[%s]@(%d, %d)->格子中心(%d, %d), 右键道具栏 (%s, %s) 存入",
                             matched, x, y, cx, cy, from_pos[0], from_pos[1])

    def _snap_to_cell(self, x, y, left, top, w, h):
        """把 OCR 文字中心 (x, y) 吸附到最近网格格子中心, 返回 (cx, cy)。

        网格按 tab_grid_rows x tab_grid_cols 把 warehouse_tab_region 均匀切分;
        未配置行列(<=0)时直接返回原文字中心, 保持旧行为。行列配置由用户在 calibrate 时填入。
        """
        rows = int(self.params.get("tab_grid_rows", 0) or 0)
        cols = int(self.params.get("tab_grid_cols", 0) or 0)
        if rows <= 0 or cols <= 0 or w <= 0 or h <= 0:
            return x, y
        rx = min(max(x - left, 0), w - 1)
        ry = min(max(y - top, 0), h - 1)
        cell_w, cell_h = w / cols, h / rows
        c = min(int(rx // cell_w), cols - 1)
        r = min(int(ry // cell_h), rows - 1)
        return int(left + (c + 0.5) * cell_w), int(top + (r + 0.5) * cell_h)

    def _is_full(self):
        """通过 OCR 检测"仓库已满"提示(区域可配置, 全 0 表示不检测)。"""
        region = self.coords.get("warehouse_full_text_region")
        if not region or not self.ocr:
            return False
        left, top, w, h = region
        if w <= 0 or h <= 0:
            return False
        texts = self.ocr.recognize_region_texts(left, top, w, h)
        kw = self.params.get("warehouse_full_keyword", "已满")
        hit = any(kw in t for t in texts)
        if hit and self.logger:
            self.logger.warning("检测到仓库已满提示: %s", texts)
        return hit