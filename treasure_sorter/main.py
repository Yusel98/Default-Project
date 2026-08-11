# -*- coding: utf-8 -*-
# =============================================================
# 依赖安装命令(Windows):
#   pip install pyautogui mss opencv-python numpy rapidocr-onnxruntime
#
# 说明:
#   本程序用于 PC 客户端游戏内"藏宝图"道具的自动分类与入库。
#   OCR 引擎默认使用 rapidocr(轻量、免翻墙、对中文支持好)。
#   如需使用 PaddleOCR: 请先修复环境(pip install --upgrade pandas)
#   然后将 config.json 中 ocr_engine 改为 "paddle"。
#
# 常用命令:
#   python main.py calibrate   交互式校准所有坐标
#   python main.py ocr         识别坐标区域文字(调试)
#   python main.py run         运行自动化流程
#   python main.py config      查看/生成配置
# =============================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from treasure_sorter.controller import main  # noqa: E402

if __name__ == "__main__":
    main()
