"""OCR 独立进程模块。

paddleocr 在某些环境下首次推理可能原生崩溃（无 Python 报错直接退出）。
把 OCR 放到子进程里运行，子进程崩了主程序不会死，还能自动重启。

对外接口：OCRClient.recognize_lines(bgr_image) -> list[(text, (x1,y1,x2,y2))]
"""

from __future__ import annotations

import multiprocessing as mp
import queue


def _worker(lang: str, in_q, out_q) -> None:  # noqa: ANN001
    """子进程主循环：接收 (seq, BGR 数组) -> 返回 (seq, lines) 或 (seq, 异常信息)。"""
    from ocr import OCREngine

    engine = OCREngine(lang=lang)
    while True:
        try:
            seq, frame = in_q.get()
        except (EOFError, OSError, KeyboardInterrupt):
            break
        try:
            lines = engine.recognize_lines(frame)
            out_q.put((seq, lines))
        except Exception as exc:  # noqa: BLE001
            out_q.put((seq, ("__error__", repr(exc))))


class OCRClient:
    """OCR 客户端：内部维护一个子进程，崩溃后自动重建。"""

    def __init__(self, lang: str = "ch"):
        self.lang = lang
        self._in: mp.Queue | None = None
        self._out: mp.Queue | None = None
        self._proc: mp.Process | None = None
        self._seq = 0
        self._spawn()

    def _spawn(self) -> None:
        self._in = mp.Queue(maxsize=4)
        self._out = mp.Queue(maxsize=4)
        self._proc = mp.Process(
            target=_worker, args=(self.lang, self._in, self._out), daemon=True
        )
        self._proc.start()

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def _restart(self) -> None:
        for q in (self._in, self._out):
            try:
                q.close()
            except Exception:  # noqa: BLE001
                pass
        self._spawn()

    def recognize_lines(self, frame) -> list:
        while True:
            if not self._alive():
                self._restart()
            self._seq += 1
            seq = self._seq
            self._in.put((seq, frame))
            try:
                got_seq, result = self._out.get(timeout=30)
            except queue.Empty:
                # 子进程可能卡死，重启后重试一次
                self._restart()
                continue
            if got_seq != seq:
                self._restart()
                continue
            if isinstance(result, tuple) and result and result[0] == "__error__":
                raise RuntimeError(result[1])
            return result

    def shutdown(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(timeout=3)
