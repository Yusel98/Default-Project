"""实时识别问题，从本地知识库获取答案的主程序。

用法示例：
    python main.py --source screen --interval 2 --knowledge knowledge
    python main.py --source camera --camera 0
    python main.py --balance
    python main.py --set-key
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # 不写 __pycache__，长时运行避免磁盘缓存堆积

import cv2
import numpy as np

from capture import capture_camera, capture_screen
from deepseek import DEFAULT_BASE_URL, fetch_balance, format_balance, get_api_key
from ocr_client import OCRClient
from overlay import (
    Overlay,
    clean_lines,
    find_answer_line,
    parse_question,
    pick_option,
    pick_region,
    primary_monitor_rect,
)
from search import load_documents, question_cover, search

REGION_FILE = Path(__file__).resolve().parent / "region_config.json"
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

# 两次 OCR 之间的最小间隔（秒），避免画面动画导致连续 OCR 空转、浪费 CPU
MIN_OCR_GAP = 0.5


def load_app_config() -> dict:
    """读取 config.json（识别间隔等）。"""
    cfg = {"interval": 0.3}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update(data)
    except (OSError, ValueError):
        pass
    return cfg


def save_app_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("qa_assistant")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="屏幕/摄像头识别问题 → 本地知识库找答案")
    parser.add_argument("--source", choices=["screen", "camera"], default="screen",
                        help="画面来源：screen=屏幕截图，camera=摄像头")
    parser.add_argument("--region", nargs=4, type=int, metavar=("LEFT", "TOP", "W", "H"),
                        help="屏幕区域 (left top width height)，缺省拖拽选择")
    parser.add_argument("--camera", type=int, default=0, help="摄像头编号")
    parser.add_argument("--interval", type=float, default=None,
                        help="轮询间隔秒数（默认读 config.json；运行中 F2 调慢 / F3 调快）")
    parser.add_argument("--knowledge", default="knowledge", help="知识库目录")
    parser.add_argument("--top-k", type=int, default=5, help="返回本地知识库答案候选数")
    parser.add_argument("--similarity", type=float, default=0.1,
                        help="识别题目与题库题干匹配度低于此值时视为非答题画面，不显示结果 (默认 0.1)")
    parser.add_argument("--lang", default="ch", help="OCR 语言 (paddle: ch/en；tesseract: chi_sim/eng)")
    parser.add_argument("--no-select", action="store_true", help="不在屏幕上框选正确选项")
    parser.add_argument("--select-color", default="red", help="框选颜色 (默认 red)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"DeepSeek API 地址 (默认 {DEFAULT_BASE_URL})")
    parser.add_argument("--balance", action="store_true", help="查询 DeepSeek 账户余额后退出")
    parser.add_argument("--set-key", action="store_true", help="配置 DeepSeek API Key 后退出")
    return parser.parse_args()


def grab_frame(args: argparse.Namespace, region: tuple | None = None):
    if args.source == "screen":
        return capture_screen(region)
    return capture_camera(args.camera)


def load_saved_region() -> tuple[int, int, int, int] | None:
    """读取上次保存的答题区域 (left, top, width, height)。"""
    try:
        data = json.loads(REGION_FILE.read_text(encoding="utf-8"))
        r = data.get("region")
        if r and len(r) == 4:
            return tuple(int(x) for x in r)
    except (OSError, ValueError):
        pass
    return None


def save_saved_region(region: tuple[int, int, int, int]) -> None:
    REGION_FILE.write_text(
        json.dumps({"region": list(region)}, ensure_ascii=False), encoding="utf-8"
    )


def resolve_region(args: argparse.Namespace) -> tuple[int, int, int, int] | None:
    """确定识别区域：命令行 --region > 每次启动拖拽选择 > 上次保存(取消时回退)。"""
    if args.region:
        return tuple(args.region)
    if args.source == "screen":
        rect = pick_region(primary_monitor_rect())
        if rect:
            save_saved_region(rect)
            return rect
    # 取消选择时回退到上次保存的区域，再不行就用全屏
    return load_saved_region()


def to_thumb(frame) -> np.ndarray:
    """把整帧压缩成 80x60 缩略图，用于快速判断画面是否变化。"""
    return cv2.resize(frame, (80, 60), interpolation=cv2.INTER_AREA)


def frame_changed(prev_thumb, cur_thumb, threshold: float = 3.0) -> bool:
    """画面是否发生明显变化。静止画面返回 False，可跳过昂贵的 OCR。"""
    if prev_thumb is None:
        return True
    return float(cv2.absdiff(prev_thumb, cur_thumb).mean()) > threshold


# ---- 终端彩色输出 ----
_ANSI_BOLD = "\033[1m"
_ANSI_DIM = "\033[2m"
_ANSI_CYAN = "\033[36m"
_ANSI_YELLOW = "\033[33m"
_ANSI_GREEN = "\033[32m"
_ANSI_RED = "\033[31m"
_ANSI_RESET = "\033[0m"


def _use_color() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


def _enable_ansi() -> None:
    """在 Windows 控制台开启 ANSI 转义码支持（Windows 10+）。"""
    if os.name != "nt" or not _use_color():
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        for handle in (ctypes.c_void_p(-11), ctypes.c_void_p(-12)):  # stdout/stderr
            mode = ctypes.c_uint32()
            if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                k32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: BLE001
        pass


def _paint(code: str, text: str) -> str:
    return f"{code}{text}{_ANSI_RESET}" if _use_color() else text


def report(question: str, hits) -> None:
    line = _paint(_ANSI_DIM, "─" * 60)
    print()
    print(line)
    if hits:
        print(_paint(_ANSI_CYAN + _ANSI_BOLD, "  知识库"))
        for sl in hits[0].snippet.splitlines():
            if sl.startswith("答案"):
                print(_paint(_ANSI_GREEN, f"  {sl}"))
            else:
                print(_paint(_ANSI_YELLOW, f"  {sl}"))
    else:
        print(_paint(_ANSI_RED, "  未获取到答案。"))
    print(line)


def main() -> None:
    args = parse_args()
    _enable_ansi()

    if args.set_key:
        get_api_key(force_prompt=True)
        return
    if args.balance:
        print(format_balance(fetch_balance(base_url=args.base_url)))
        return

    ocr = OCRClient(lang=args.lang)
    logger.info("OCR 子进程就绪（识别崩溃会自动重启）")

    docs = load_documents(args.knowledge)
    logger.info("加载知识库 %s，共 %d 个文件", args.knowledge, len(docs))
    if not docs:
        logger.warning("知识库为空，请把文档放入 %s 目录（txt/md/csv/json/py 等）", args.knowledge)

    region = resolve_region(args)
    if region:
        logger.info("答题区域: left=%d top=%d width=%d height=%d", *region)

    app_cfg = load_app_config()
    interval = args.interval if args.interval is not None else float(app_cfg.get("interval", 2.0))

    overlay = None
    if args.source == "screen" and not args.no_select:
        rect = region if region else primary_monitor_rect()
        overlay = Overlay(rect)

        def adjust_interval(delta: float) -> None:
            nonlocal interval
            interval = round(max(0.1, interval + delta), 1)
            app_cfg["interval"] = interval
            save_app_config(app_cfg)
            logger.info("识别间隔调整为 %.1f 秒", interval)

        overlay.root.bind("<F2>", lambda _e: adjust_interval(0.5))
        overlay.root.bind("<F3>", lambda _e: adjust_interval(-0.5))
        logger.info("已开启框选模式，按 F10 显示/隐藏红框，F2 调慢 / F3 调快，Ctrl+C 退出")

    logger.info("识别间隔 %.1f 秒", interval)
    last_question = ""
    last_boxes: list = []
    last_drawn_question = ""

    def mark_option(question: str, box, color: str) -> None:
        nonlocal last_boxes, last_drawn_question
        last_boxes = [box]
        last_drawn_question = question
        overlay.draw(last_boxes, color=color)

    prev_thumb = None
    last_ocr_at = 0.0
    while True:
        try:
            frame = grab_frame(args, region)
            thumb = to_thumb(frame)
            if not frame_changed(prev_thumb, thumb):
                # 画面静止：拉长轮询间隔，空闲时大幅降低抓屏频率与功耗
                time.sleep(0.2)
                continue
            prev_thumb = thumb
            now = time.monotonic()
            remain = MIN_OCR_GAP - (now - last_ocr_at)
            if remain > 0:
                # 一次睡满剩余节流时间，避免以高频率空转抓屏
                time.sleep(remain)
                continue
            last_ocr_at = now
            if overlay:
                overlay.clear()

            lines = clean_lines(ocr.recognize_lines(frame))
            del frame  # OCR 完成后立即释放原图，降低持续运行的峰值内存
            question, option_texts, option_boxes = parse_question(lines)

            if not question:
                continue
            if question == last_question:
                if overlay and last_boxes and question == last_drawn_question:
                    overlay.draw(last_boxes, color=args.select_color)
                time.sleep(interval)
                continue

            last_question = question

            hits = search(docs, question, top_k=args.top_k)

            similar = question_cover(question, hits[0].snippet) if hits else 0.0
            if similar < args.similarity:
                if overlay:
                    last_boxes = []
                    last_drawn_question = ""
                    overlay.clear()
                continue

            report(question, hits)

            if overlay:
                if hits:
                    idx = pick_option(hits[0].snippet, option_texts)
                    if idx is not None:
                        mark_option(question, option_boxes[idx], args.select_color)
                    else:
                        box = find_answer_line(hits[0].snippet, lines)
                        if box:
                            mark_option(question, box, args.select_color)
                        else:
                            last_boxes = []
                            last_drawn_question = ""
                            overlay.clear()
                else:
                    last_boxes = []
                    last_drawn_question = ""
                    overlay.clear()
        except KeyboardInterrupt:
            logger.info("已退出")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("本轮处理失败: %s", exc)
        time.sleep(0.05)
        if overlay:
            overlay.refresh()
    ocr.shutdown()
    if overlay:
        overlay.destroy()


if __name__ == "__main__":
    main()
