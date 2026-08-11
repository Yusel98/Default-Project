"""根据截图识别问题，并从本地知识库检索答案。

支持两种模式：
    单次模式：  python answer_image.py -i "截图.jpg"            （处理一张截图）
    监控模式：  python answer_image.py --watch                 （检测到截图被新内容覆盖后自动识别）

用法：
    python answer_image.py
    python answer_image.py -i "截图路径.jpg" -k knowledge --top-k 5
    python answer_image.py --watch
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.dont_write_bytecode = True  # 不写 __pycache__，长时运行避免磁盘缓存堆积

from ocr import OCREngine
from overlay import clean_lines, parse_question
from search import load_documents, question_cover, search

# Windows 下 stdout 默认可能是 GBK，强制 UTF-8 输出（配合 BAT 的 chcp 65001）
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

DEFAULT_IMAGE = Path(r"D:\Program Files (x86)\梦幻西游\screen\jd\screenshot.jpg")

try:
    import main as _ui
except ImportError:  # 兼容：单独运行时若同目录有 main.py 则复用其终端美化
    _ui = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="截图识别 → 本地知识库检索")
    parser.add_argument("-i", "--image", default=str(DEFAULT_IMAGE), help="截图路径")
    parser.add_argument("-k", "--knowledge", default="knowledge", help="知识库目录")
    parser.add_argument("--top-k", type=int, default=5, help="返回候选数")
    parser.add_argument("--similarity", type=float, default=0.1,
                        help="识别题目与题库题干匹配度低于此值时视为非答题画面，不显示结果 (默认 0.1)")
    parser.add_argument("--watch", action="store_true",
                        help="监控模式：截图被新内容覆盖后自动识别")
    parser.add_argument("--interval", type=float, default=0.1, help="监控检测间隔秒数")
    return parser.parse_args()


def _load_assets(knowledge: str):
    """加载并返回可复用的 OCR 引擎与知识库（监控模式只加载一次）。"""
    engine = OCREngine(lang="ch")
    docs = load_documents(knowledge)
    if not docs:
        print(f"知识库为空: {knowledge}")
    return engine, docs


def process(image_path: str, engine, docs, top_k: int, similarity: float = 0.1) -> None:
    # 用 imdecode 兼容含中文/空格的路径
    data = np.fromfile(image_path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        print(f"无法读取截图: {image_path}")
        return

    lines = clean_lines(engine.recognize_lines(img))
    if not lines:
        print("未识别到任何文字。")
        return

    question, option_texts, _ = parse_question(lines)
    if not question:
        print("未识别到有效题目。识别到的行：")
        for text, _box in lines:
            print("  ", text)
        return

    hits = search(docs, question, top_k=top_k)

    similar = question_cover(question, hits[0].snippet) if hits else 0.0
    if similar < similarity:
        print(
            f"[提示] 匹配度 {similar * 100:.0f}% 低于阈值 {similarity * 100:.0f}%，"
            "当前画面可能不是答题界面，忽略识别结果。"
        )
        return

    if _ui is not None:
        _ui.report(question, hits)
    else:
        print("\n[知识库答案]")
        print(hits[0].snippet if hits else "未找到答案。")


def _file_hash(path: str) -> str | None:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _stat_tuple(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def watch(image_path: str, engine, docs, top_k: int, interval: float,
          similarity: float = 0.1) -> None:
    # 用 stat(size+mtime_ns) 快速判断文件是否有写入，确认后再用 md5 复核，
    # 避免每个轮询周期都读整个文件，缩短响应时间。
    last_stat = _stat_tuple(image_path)
    last_hash = _file_hash(image_path)
    print(f"监控中: {image_path}")
    print("检测到新截图覆盖后自动识别，按 Ctrl+C 退出。")
    while True:
        time.sleep(interval)
        cur_stat = _stat_tuple(image_path)
        if cur_stat is None or cur_stat == last_stat:
            continue
        last_stat = cur_stat
        time.sleep(0.1)  # 等文件写完，避免读到半截
        cur_hash = _file_hash(image_path)
        if cur_hash is None or cur_hash == last_hash:
            continue
        last_hash = cur_hash
        print(f"\n[{time.strftime('%H:%M:%S')}] 检测到新截图，开始识别...")
        process(image_path, engine, docs, top_k, similarity)


def main() -> None:
    args = parse_args()
    if _ui is not None:
        _ui._enable_ansi()

    engine, docs = _load_assets(args.knowledge)
    print(f"OCR 引擎: {engine.engine_name}")
    if args.watch:
        watch(args.image, engine, docs, args.top_k, args.interval, args.similarity)
    else:
        print(f"读取截图: {args.image}")
        process(args.image, engine, docs, args.top_k, args.similarity)


if __name__ == "__main__":
    main()