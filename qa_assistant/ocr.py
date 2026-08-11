"""OCR 识别模块。

优先使用 RapidOCR（CPU 上快、模型随包内置）；未安装时回退到 PaddleOCR，
最后回退到 pytesseract。全部不可用时抛出 RuntimeError 并提示安装命令。
"""

from __future__ import annotations

import logging
import shutil

import numpy as np

logger = logging.getLogger(__name__)


def _poly_to_box(points) -> tuple | None:
    """把 OCR 输出的 4 点多边形/矩形转成 (x1, y1, x2, y2)，无效时返回 None。"""
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim < 2 or pts.shape[1] < 2:
        return None
    xs, ys = pts[:, 0], pts[:, 1]
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def _paddle_ocr_available() -> bool:
    try:
        import paddleocr  # noqa: F401

        return True
    except ImportError:
        return False


def _rapid_available() -> bool:
    try:
        import rapidocr  # noqa: F401

        return True
    except ImportError:
        return False


def _tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


class OCREngine:
    """统一的 OCR 封装，自动选择可用引擎。"""

    def __init__(self, lang: str = "ch", prefer: str | None = None):
        self._paddle = None
        self._rapid = None
        self._engine = None
        self.lang = lang
        if prefer is None:
            if _rapid_available():
                prefer = "rapid"
            elif _paddle_ocr_available():
                prefer = "paddle"
            elif _tesseract_available():
                prefer = "tesseract"
            else:
                raise RuntimeError(
                    "未检测到可用的 OCR 引擎。请安装任一：\n"
                    "  pip install rapidocr\n"
                    "  pip install paddleocr paddlepaddle\n"
                    "  或安装 Tesseract-OCR（并把 tesseract 加入 PATH）"
                )
        self._engine_name = prefer

    @property
    def engine_name(self) -> str:
        return self._engine_name

    def recognize_lines(self, image: np.ndarray) -> list:
        """返回按行聚合的识别结果，每项为 (文本, 包围盒(x1, y1, x2, y2))。"""
        if self._engine_name == "rapid":
            return self._recognize_lines_rapid(image)
        if self._engine_name == "paddle":
            return self._recognize_lines_paddle(image)
        return self._recognize_lines_tesseract(image)

    def _init_rapid(self) -> None:
        if self._rapid is None:
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            from rapidocr import RapidOCR

            self._rapid = RapidOCR()

    def _recognize_lines_rapid(self, image: np.ndarray) -> list:
        self._init_rapid()
        res = self._rapid(image)
        if res is None:
            return []
        boxes = getattr(res, "boxes", None)
        texts = getattr(res, "txts", None)
        if boxes is None or texts is None:
            return []
        lines = []
        for text, box in zip(texts, boxes):
            bbox = _poly_to_box(box)
            if bbox is not None:
                lines.append((str(text), bbox))
        # 按“先上后左”排序，保持题干/选项的视觉顺序
        lines.sort(key=lambda line: (line[1][1], line[1][0]))
        return lines

    def _init_paddle(self) -> None:
        if self._paddle is None:
            import paddleocr
            from paddleocr import PaddleOCR

            if hasattr(paddleocr, "__version__") and paddleocr.__version__.startswith("3"):
                # paddleocr 3.x 移除了 use_angle_cls/show_log 等参数；
                # enable_mkldnn=False 规避 paddlepaddle 3.3 的 onednn 崩溃问题
                # 默认 PP-OCRv6_medium 在 CPU 上单帧约 4~5 秒，改用 PP-OCRv4_mobile 约 2 秒
                self._paddle = PaddleOCR(
                    lang=self.lang,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    enable_mkldnn=False,
                    text_detection_model_name="PP-OCRv4_mobile_det",
                    text_recognition_model_name="PP-OCRv4_mobile_rec",
                )
            else:
                self._paddle = PaddleOCR(
                    use_angle_cls=True,
                    lang=self.lang,
                    show_log=False,
                    use_gpu=False,
                )

    def _recognize_lines_paddle(self, image: np.ndarray) -> list:
        self._init_paddle()
        lines = []
        if hasattr(self._paddle, "predict"):
            # paddleocr 3.x：predict 返回 dict 风格结果（OCRResult），
            # rec_texts / rec_polys 都是它的键，不能 getattr 访问
            for res in self._paddle.predict(image) or []:
                if not hasattr(res, "get"):
                    continue
                texts = list(res.get("rec_texts") or [])
                polys = list(res.get("rec_polys") or [])
                for text, poly in zip(texts, polys):
                    bbox = _poly_to_box(poly)
                    if bbox is not None:
                        lines.append((text, bbox))
        else:
            # paddleocr 2.x：ocr 返回 [(框四点, (文本, 置信度)), ...]
            result = self._paddle.ocr(image, cls=True)
            for page in result or []:
                for item in page or []:
                    bbox = _poly_to_box(item[0])   # 4 点多边形 [[x, y], ...]
                    text = item[1][0]
                    if bbox is not None:
                        lines.append((text, bbox))
        # 按“先上后左”排序，保持题干/选项的视觉顺序
        lines.sort(key=lambda line: (line[1][1], line[1][0]))
        return lines

    def _recognize_lines_tesseract(self, image: np.ndarray) -> list:
        import pytesseract
        from pytesseract import Output

        tesseract_lang = "chi_sim" if self.lang.startswith("ch") else self.lang
        data = pytesseract.image_to_data(image, lang=tesseract_lang, output_type=Output.DICT)
        groups: dict = {}
        n = len(data["text"])
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            entry = groups.setdefault(key, {"words": [], "x1": 1e9, "y1": 1e9, "x2": 0, "y2": 0})
            entry["words"].append(text)
            x, y = data["left"][i], data["top"][i]
            w, h = data["width"][i], data["height"][i]
            entry["x1"] = min(entry["x1"], x)
            entry["y1"] = min(entry["y1"], y)
            entry["x2"] = max(entry["x2"], x + w)
            entry["y2"] = max(entry["y2"], y + h)
        lines = []
        for key in sorted(groups):
            e = groups[key]
            lines.append((" ".join(e["words"]), (e["x1"], e["y1"], e["x2"], e["y2"])))
        return lines
