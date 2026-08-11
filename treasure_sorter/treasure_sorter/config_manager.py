# -*- coding: utf-8 -*-
"""配置管理: 读取/生成/保存 JSON 配置。

坐标均为屏幕绝对坐标(像素):
  - 点:   [x, y]
  - 区域: [left, top, width, height]
"""
import copy
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# 默认配置, 会被 config.json 覆盖
DEFAULTS = {
    # 地点/坐标 -> 仓库名称 映射(核心映射表, 按需修改)
    "locations": {
        "江南野外": "江南野外",
        "建邺城": "建邺城",
        "东海湾": "东海湾",
        "傲来国": "傲来国",
        "女儿村": "女儿村",
        "花果山": "花果山",
        "北俱芦洲": "北俱芦洲",
        "长寿郊外": "长寿郊外",
        "狮驼岭": "狮驼岭",
        "普陀山": "普陀山",
        "五庄观": "五庄观",
        "朱紫国": "朱紫国",
        "麒麟山": "麒麟山",
        "大唐国境": "大唐国境",
        "大唐境外": "大唐境外",
        "墨家村": "墨家村",
    },
    # OCR 常见误识别纠错表: {错误词: 正确词}, 命中后先替换再匹配
    "ocr_corrections": {
        "建邮": "建邺",
        "建鄂": "建邺",
        "奥来国": "傲来国",
        "北惧芦洲": "北俱芦洲",
        "长郊": "长寿郊外",
        "普坨山": "普陀山",
    },
    # 坐标区域内的 UI 文本, 全部交给坐标信息, 用于提示框文字拼接前的排除
    "noise_words": ["藏宝图", "使用", "前往", "坐标", "地点", "寻宝"],

"coords": {
        "hover_coord_region": [0, 0, 0, 0], # 仓库界面下, 鼠标悬停宝图时坐标提示出现的固定区域
        "warehouse_tab_region": [0, 0, 0, 0], # 仓库标签横排/多行所在的固定区域(OCR 定位标签用)
        "backpack_in_warehouse_slots": [],  # 仓库界面下背包(道具栏)格子坐标 [[x, y], ...]
        "warehouse_full_text_region": [0, 0, 0, 0],  # "仓库已满"提示出现的区域(全 0 表示不检测)
    },

    "params": {
        "ocr_engine": "rapidocr",      # rapidocr / paddle
        "ocr_scale": 2.0,              # OCR 前放大倍数, 小字建议 2.0
        "ocr_confidence": 0.3,         # 过滤识别置信度过低的文字
        "retry_max": 2,                # 悬停识别为空的单张重试次数
        "deposit_retry": 1,            # 存入后是否再悬停校验并重试(1=存完即过)
        "retry_interval": 0.5,         # 重试间隔(秒)
        "timeout_per_item": 30.0,      # 单张藏宝图最长处理时间(秒)
        "hover_delay": 0.9,            # 悬停宝图后等待坐标提示出现的时长(秒)
        "hover_jiggle": 60,            # 识别失败时, 鼠标移开的像素(触发提示框重渲染)
        "click_delay": 0.3,            # 点击之间的默认停顿(秒)
        "pre_click_delay": 0.2,        # 鼠标定位到位后、按下之前的停顿(提高触发成功率)
        "after_right_click_delay": 0.4,  # 点击仓库标签/右键存入后的等待(秒)
        "drag_duration": 0.5,          # 拖拽动画时长(秒)
        "warehouse_full_keyword": "已满",  # 仓库满提示关键字
        "tab_grid_rows": 3,      # 仓库标签网格行数(用于把 OCR 结果吸附到格子中心)
        "tab_grid_cols": 6,      # 仓库标签网格列数(点击格子中心, 避免点到相邻标签)
        "manual_open_ready_delay": 8.0, # 手动打开仓库后, 程序等待的秒数
        "stop_key": "F12",             # 运行中按住该键可停止
        "dry_run": False,              # 调试: 只识别与计算, 不操作鼠标
        "debug_save_dir": "debug",     # 识别失败时保存悬停区域截图到该目录, 便于排查
    },
}


def load_config(path=CONFIG_FILE):
    """读取配置, 缺失项用默认值补齐。"""
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass

    cfg = copy.deepcopy(DEFAULTS)
    if isinstance(data, dict):
        for k in ("locations", "ocr_corrections", "coords", "params"):
            if k in data and isinstance(data[k], dict):
                cfg[k].update(data[k])
        if "noise_words" in data and isinstance(data["noise_words"], list):
            cfg["noise_words"] = data["noise_words"]
    return cfg


def save_config(cfg, path=CONFIG_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def ensure_config(path=CONFIG_FILE):
    """不存在则生成默认配置。"""
    if not os.path.exists(path):
        save_config(load_config(), path)
    return path
