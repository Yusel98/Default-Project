# -*- coding: utf-8 -*-
"""日志模块: 统一控制台 + 文件双通道日志。"""
import logging
import os

CONSOLE_LEVEL = logging.INFO     # 控制台只打印关键信息
FILE_LEVEL = logging.DEBUG       # 文件记录全部细节


def setup_logger(name="treasure_sorter", log_file="treasure_sorter.log",
                 console_level=CONSOLE_LEVEL, file_level=FILE_LEVEL):
    """初始化并返回日志对象(幂等: 已初始化则直接返回)。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # 控制台输出
    sh = logging.StreamHandler()
    sh.setLevel(console_level)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # 文件输出(utf-8, 避免中文乱码)
    try:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(file_level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass

    logger.propagate = False
    return logger
