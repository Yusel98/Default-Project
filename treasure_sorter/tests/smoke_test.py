# -*- coding: utf-8 -*-
"""离线冒烟测试: 用合成画面验证手动分类入库流程(悬停读坐标 -> 识别地点 -> 存入), 不操作真实鼠标。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import treasure_sorter.controller as ctrl
import treasure_sorter.screen_capture as sc
from treasure_sorter.mouse_controller import MouseController
from treasure_sorter.ocr_service import OCRService
from treasure_sorter.warehouse import Warehouse

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = r"C:\Windows\Fonts\msyh.ttc"


def make_config():
    return {
        # 恒等映射: 地点名 == 仓库标签名(用户把仓库改成地点名)
        "locations": {
            "星穹之路": "星穹之路", "落霞峰": "落霞峰", "长安": "长安",
            "建邺城": "建邺城", "北俱芦洲": "北俱芦洲", "朱紫国": "朱紫国",
            "墨家村": "墨家村",
        },
        "ocr_corrections": {"星育": "星穹", "星函": "星穹", "路霞": "落霞", "建邮": "建邺"},
        "noise_words": ["藏宝图", "使用", "前往", "坐标"],
        "coords": {
            "hover_coord_region": [0, 0, 800, 100],
            "warehouse_tab_region": [0, 150, 800, 120],
            "backpack_in_warehouse_slots": [[200, 600], [400, 600], [600, 600]],
            "warehouse_full_text_region": [0, 0, 0, 0],
        },
        "params": {
            "ocr_engine": "rapidocr", "ocr_scale": 1.0, "ocr_confidence": 0.0,
            "retry_max": 2, "retry_interval": 0.05, "timeout_per_item": 15.0,
            "hover_delay": 0.05, "hover_jiggle": 60, "click_delay": 0.0,
            "pre_click_delay": 0.0, "after_right_click_delay": 0.05,
            "drag_duration": 0.1, "warehouse_full_keyword": "已满",
            "manual_open_ready_delay": 0.0, "stop_key": "F12",
            "dry_run": True, "debug_save_dir": "debug",
        },
    }


class FakeMouse(MouseController):
    """记录操作, 并把移动映射到道具栏槽位, 不真实操作系统鼠标/键盘。"""

    def __init__(self, config, logger=None):
        super().__init__(config, logger)
        self.cfg = config
        self.clicks = []
        self.current_wh_index = -1

    def click(self, x, y, button="left", clicks=1, interval=0.1):
        x, y = int(x), int(y)
        self.clicks.append((x, y, button))

    def right_click(self, x, y):
        self.click(x, y, "right")

    def move(self, x, y):
        x, y = int(x), int(y)
        for i, (sx, sy) in enumerate(self.cfg["coords"]["backpack_in_warehouse_slots"]):
            if abs(sx - x) < 5 and abs(sy - y) < 5:
                self.current_wh_index = i

    def drag(self, x1, y1, x2, y2, duration=None):
        self.clicks.append((int(x1), int(y1), "drag"))

    def stop_requested(self):
        return False


class FakeScreen(sc.ScreenCapture):
    """合成画面: 按当前悬停的道具栏槽位渲染坐标文字; 仓库标签区域渲染地点名标签。"""

    def __init__(self, mouse_ref, tip_by_wh, depleted=None, tab_labels=None):
        self.mouse_ref = mouse_ref
        self.tip_by_wh = tip_by_wh
        self.depleted = set() if depleted is None else depleted
        self.regions = {}
        # 仓库标签: {名称: (相对x,y)}
        self.tab_labels = tab_labels or {
            "星穹之路": (120, 20), "落霞峰": (300, 20), "长安": (500, 20),
        }

    def set_region(self, name, region):
        self.regions[name] = tuple(region)

    def _render(self, img, text, pos=(20, 20)):
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        d.text(pos, text, font=ImageFont.truetype(FONT, 36), fill=(10, 10, 10))
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    def grab(self, left, top, width, height):
        key = (int(left), int(top), int(width), int(height))
        img = np.full((int(height), int(width), 3), 245, np.uint8)
        if self.regions.get("hover") == key:
            i = self.mouse_ref.current_wh_index
            if i not in self.depleted:
                text = self.tip_by_wh.get(i, "")
                if text:
                    img = self._render(img, "坐标" + text)
        elif self.regions.get("tab") == key:
            for name, (tx, ty) in self.tab_labels.items():
                img = self._render(img, name, (tx, ty))
        return img


def test_ocr_extract():
    ocr = OCRService(make_config(), FakeScreen(None, {}), None)
    assert ocr.extract_location(["星育之路"]) == "星穹之路", "纠错星育->星穹 失败"
    assert ocr.extract_location(["落霞峰, 前往"]) == "落霞峰", "落霞峰提取失败"
    assert ocr.extract_location(["长安"]) == "长安", "长安提取失败"
    assert ocr.extract_location(["未知地点"]) is None, "未知地点不应匹配"
    # 纠错表: 建邮 -> 建邺
    assert ocr.extract_location(["坐标建邮城（81，105）"]) == "建邺城", "建邮纠错失败"
    assert ocr.resolve_warehouse("星穹之路") == "星穹之路", "恒等映射失败"
    # 区分"坐标提示"与"纯地点名": 提示含坐标/数字/括号, 仓库标签是纯名
    assert ocr._is_coord_tooltip("坐标建邺城(123,105)") is True, "坐标提示应为true"
    assert ocr._is_coord_tooltip("建邺城 81,105") is True, "含数字对应为true"
    assert ocr._is_coord_tooltip("建邺城") is False, "纯地点名应为false"
# require_coord: 悬停提示必须带"坐标"; 纯地点名(仓库标签)不认
    assert ocr.extract_location(["坐标建邺城（81，105）"], require_coord=True) == "建邺城"
    assert ocr.extract_location(["建邺城"], require_coord=True) is None, "纯地点名在require_coord下应忽略"
    # OCR 会在"坐标"/"地点"中间插空格、夹括号, 必须去空格/括号后取"坐标"后最先出现的点
    assert ocr.extract_location(["坐标】北俱 芦洲（169，145）挖掘时..."],
                                require_coord=True) == "北俱芦洲", "含空格地点解析失败"
    assert ocr.extract_location(["【坐 标】朱紫国（71.116）道具..."],
                                require_coord=True) == "朱紫国", "坐标中间插空格失败"
    assert ocr.extract_location(["藏宝图地图。坐标】墨家村（73.78）鼠标右击道具可快速转移江南野外建邺城..."],
                                require_coord=True) == "墨家村", "应取坐标后最靠前的地点(墨家村)"
    print("test_ocr_extract OK")


def test_sort_flow():
    """悬停道具栏格子 -> OCR 读地点 -> 存入对应地点名的仓库。"""
    cfg = make_config()
    tip_by_wh = {0: "星穹之路（1,2）", 1: "落霞峰（3,4）", 2: "长安（5,6）"}

    ctrl.load_config = lambda *a, **k: cfg
    ctrl.MouseController = FakeMouse

    app = ctrl.MainController(dry_run=True)
    app.screen = FakeScreen(app.mouse, tip_by_wh)
    app.screen.set_region("hover", cfg["coords"]["hover_coord_region"])
    app.screen.set_region("tab", cfg["coords"]["warehouse_tab_region"])
    app.ocr = OCRService(cfg, app.screen, app.logger)
    app.ocr.screen = app.screen
    app.warehouse = Warehouse(cfg, app.mouse, app.ocr, app.logger)

    # OCR 定位仓库标签: 在标签区域找到"星穹之路"并返回其在屏幕上的位置
    lt, tp, w, h = cfg["coords"]["warehouse_tab_region"]
    tx, ty, matched = app.ocr.find_text_position("星穹之路", lt, tp, w, h)
    print("OCR定位仓库标签:", tx, ty, matched)
    assert matched and (tx, ty) is not None, "OCR 未定位到仓库标签"
    assert abs(tx - (lt + 120)) < 80 and abs(ty - (tp + 20)) < 60, (tx, ty)

    deposits = []
    original_deposit = Warehouse.deposit

    def fake_deposit(self, from_pos, warehouse_name):
        deposits.append((tuple(from_pos), warehouse_name))

    Warehouse.deposit = fake_deposit
    try:
        n = app._deposit_all()
    finally:
        Warehouse.deposit = original_deposit

    print("存入记录:", deposits)
    assert deposits == [
        ((200, 600), "星穹之路"),
        ((400, 600), "落霞峰"),
        ((600, 600), "长安"),
    ], deposits
    assert n == 3, "应成功存入 3 张, 实际 %d" % n
    print("test_sort_flow OK")


def test_grid_snap():
    """OCR 文字中心吸附到最近网格格子中心, 文字抖动时不会点到相邻(含上下)仓库。"""
    cfg = make_config()
    cfg["coords"]["warehouse_tab_region"] = [0, 0, 400, 200]
    cfg["params"]["tab_grid_rows"] = 4
    cfg["params"]["tab_grid_cols"] = 4
    wh = Warehouse(cfg, None, None, None)

    lt, tp, w, h = cfg["coords"]["warehouse_tab_region"]
    cell_w, cell_h = w // 4, h // 4

    # 目标文字中心偏向格子(0,0)右/下边缘, 吸附后仍落在(0,0)格子中心, 不会滑到邻居
    cx, cy = wh._snap_to_cell(lt + cell_w - 2, tp + cell_h - 2, lt, tp, w, h)
    assert (cx, cy) == (lt + cell_w // 2, tp + cell_h // 2), (cx, cy)
    # 目标文字中心在(1,1)格子内部偏左上, 仍正确归属(1,1)
    cx2, cy2 = wh._snap_to_cell(lt + cell_w + cell_w // 2, tp + cell_h + cell_h // 2,
                                lt, tp, w, h)
    assert (cx2, cy2) == (lt + cell_w + cell_w // 2, tp + cell_h + cell_h // 2), (cx2, cy2)
    # 文字中心非常接近两行边界上方: 必须归到上一行(0,1), 不点到下方邻居 → 用户"点到上面仓库"的修复
    cx3, cy3 = wh._snap_to_cell(lt + cell_w * 3 + 1, tp + cell_h * 3 + cell_h - 1,
                                lt, tp, w, h)
    assert (cx3, cy3) == (lt + cell_w * 3 + cell_w // 2, tp + cell_h * 3 + cell_h // 2), (cx3, cy3)
    print("test_grid_snap OK")


if __name__ == "__main__":
    test_ocr_extract()
    test_sort_flow()
    test_grid_snap()