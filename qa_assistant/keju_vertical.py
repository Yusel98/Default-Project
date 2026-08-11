"""竖排选项答题模式专用工具（梦幻西游另一种答题界面）。

与 main.py 的不同点：
- 4 个选项竖着排列、没有 A/B/C/D 字母前缀；
- 选项位置按“题目下方的短行簇”几何规则定位，不走字母前缀解析；
- 屏幕画面清晰，无需放大，OCR 直接识别。

用法：
    python keju_vertical.py                      # 实时模式，启动时拖拽框选答题区域
    python keju_vertical.py --image "1.jpg"      # 识别一张截图
    python keju_vertical.py --region 100 200 500 400
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time

import numpy as np

sys.dont_write_bytecode = True  # 不写 __pycache__，长时运行避免磁盘缓存堆积

import main as _main
from capture import capture_screen
from ocr import OCREngine
from ocr_client import OCRClient
from overlay import Overlay, clean_lines, extract_answer, pick_region, primary_monitor_rect
from search import load_documents, question_cover, search

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("keju_vertical")

# 选项文本一般较短；超过该长度更可能属于题目/题干（长句）
MAX_OPT_CHARS = 18

# 两次 OCR 之间的最小间隔（秒），避免画面动画导致连续 OCR 空转、浪费 CPU
MIN_OCR_GAP = 0.5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="竖排选项（无字母前缀）答题识别")
    parser.add_argument("--image", help="截图文件路径；给出则只识别这一张并退出")
    parser.add_argument("--region", nargs=4, type=int, metavar=("LEFT", "TOP", "W", "H"),
                        help="屏幕区域 (left top width height)，缺省拖拽选择")
    parser.add_argument("--interval", type=float, default=0.3,
                        help="轮询间隔秒数 (仅实时)")
    parser.add_argument("--knowledge", default="knowledge", help="知识库目录")
    parser.add_argument("--top-k", type=int, default=5, help="返回本地知识库答案候选数")
    parser.add_argument("--similarity", type=float, default=0.1,
                        help="识别题目与题库题干匹配度低于此值时视为非答题画面，不显示结果 (默认 0.1)")
    parser.add_argument("--no-select", action="store_true", help="不框选正确选项")
    parser.add_argument("--select-color", default="red", help="框选颜色 (默认 red)")
    parser.add_argument("--lang", default="ch", help="OCR 语言")
    return parser.parse_args()


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def parse_vertical(lines: list[tuple[str, tuple[int, int, int, int]]]):
    """把无字母前缀的 OCR 行拆成题干和竖排选项。

    返回 (question, options)，options 为 [(text, (x1,y1,x2,y2)), ...]。
    规则：找出竖直间隔最大的那道“缝”，缝下方的一簇短行当作选项，
    其余（含题目）拼成题干。
    """
    rows = [(t, b) for t, b in lines if t.strip()]
    rows.sort(key=lambda r: (r[1][1], r[1][0]))  # 先按 y，再按 x
    n = len(rows)
    if n == 0:
        return "", []
    if n == 1:
        return rows[0][0], []

    # 计算相邻行之间的竖直间隔
    gaps = []
    for i in range(n - 1):
        gaps.append(rows[i + 1][1][1] - rows[i][1][3])  # 下一行 top - 本行 bottom

    # 找到最大间隔；其下方即选项块
    split = int(max(range(n - 1), key=lambda i: gaps[i])) + 1
    options = rows[split:]
    question_parts = [t for t, _ in rows[:split]]

    # 校验：选项应≥2 行、都是短行且数量范围合理，否则放弃切分
    if (
        len(options) >= 2
        and len(options) <= 6
        and all(len(_norm(t)) <= MAX_OPT_CHARS + 3 for t, _ in options)
    ):
        question = " ".join(question_parts).strip()
    else:
        question = " ".join(t for t, _ in rows).strip()
        options = []
    return question, options


def pick_option_index(snippet: str,
                      options: list[tuple[str, tuple[int, int, int, int]]]) -> int | None:
    """在竖排选项里找出与知识库答案最匹配的一个下标；无可靠命中返回 None。

    评分原则与 find_answer_line 一致：完整命中 > 行包含答案 > 答案包含行(短行) > 模糊。
    """
    target = _norm(extract_answer(snippet))
    if not target or not options:
        return None
    best_idx: int | None = None
    best_score = 0.0
    for i, (text, _box) in enumerate(options):
        t = _norm(text)
        if not t:
            continue
        if t == target:
            score = 2.0
        elif target in t:
            score = 1.5
        elif t in target:
            score = len(t) / max(len(target), 1)
        else:
            from difflib import SequenceMatcher

            score = SequenceMatcher(None, target, t).ratio()
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx if best_score >= 0.6 else None


def report(question: str, hits) -> None:
    _main._enable_ansi()
    _main.report(question, hits)


def resolve_region(args: argparse.Namespace) -> tuple[int, int, int, int] | None:
    if args.region:
        return tuple(args.region)
    if not args.image:
        rect = pick_region(primary_monitor_rect())
        if rect:
            _main.save_saved_region(rect)
            return rect
    return _main.load_saved_region()


def _imread(path: str) -> np.ndarray | None:
    """兼容中文路径图片读取。"""
    import cv2

    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def run_single_image(eng: OCREngine, img: np.ndarray, docs, top_k: int) -> None:
    """截图模式：打印识别结果、知识库答案和应框选的选项。"""
    lines = clean_lines(eng.recognize_lines(img))
    question, options = parse_vertical(lines)
    print("题干:", question)
    print("识别到的选项：")
    for i, (t, b) in enumerate(options):
        print(f"  [{i}] {t}  box={b}")
    if not question:
        print("未识别到有效题目。")
        return
    hits = search(docs, question, top_k=top_k)
    report(question, hits)
    idx = pick_option_index(hits[0].snippet, options) if hits else None
    if idx is not None:
        print(f"\n应选第 {idx} 个选项：{options[idx][0]}  位置 {options[idx][1]}")
    else:
        print("\n未找到足以置信的正确选项（可能选项没被 OCR 完整识别）。")


def main() -> None:
    args = parse_args()
    _main._enable_ansi()

    docs = load_documents(args.knowledge)
    logger.info("加载知识库 %s，共 %d 个文件", args.knowledge, len(docs))

    if args.image:
        eng = OCREngine(lang=args.lang)
        img = _imread(args.image)
        if img is None:
            print(f"无法读取截图: {args.image}")
            return
        run_single_image(eng, img, docs, args.top_k)
        return

    # ---- 实时模式 ----
    region = resolve_region(args)
    if region:
        logger.info("答题区域: left=%d top=%d width=%d height=%d", *region)

    ocr = OCRClient(lang=args.lang)
    logger.info("OCR 子进程就绪（识别崩溃会自动重启）")
    interval = args.interval

    overlay = None
    if not args.no_select and region:
        overlay = Overlay(region)
        overlay.root.bind("<F10>", lambda _e: overlay.toggle())
        logger.info("开启框选模式，F10 显示/隐藏红框，Ctrl+C 退出")

    last_question = ""
    last_box: tuple | None = None
    last_drawn_question = ""
    prev_thumb = None
    last_ocr_at = 0.0

    while True:
        try:
            frame = capture_screen(region)
            thumb = _main.to_thumb(frame)
            if not _main.frame_changed(prev_thumb, thumb):
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
            question, options = parse_vertical(lines)
            if not question:
                last_box = None
                time.sleep(0.05)
                continue
            if question == last_question:
                if overlay and last_box and question == last_drawn_question:
                    overlay.draw([last_box], color=args.select_color)
                time.sleep(interval)
                continue
            last_question = question

            hits = search(docs, question, top_k=args.top_k)

            similar = question_cover(question, hits[0].snippet) if hits else 0.0
            if similar < args.similarity:
                last_box = None
                continue

            report(question, hits)

            if overlay:
                idx = pick_option_index(hits[0].snippet, options) if hits else None
                if idx is not None:
                    last_box = options[idx][1]
                    last_drawn_question = question
                    overlay.draw([last_box], color=args.select_color)
                else:
                    last_box = None
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