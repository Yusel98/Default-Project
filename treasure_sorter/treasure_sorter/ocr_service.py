# -*- coding: utf-8 -*-
"""OCR 服务: 固定区域截图识别 + 文本纠错 + 地点提取。

设计要点:
  1. 因为坐标/地点文字出现在"固定位置", 封装 recognize_region_texts() 直接识别固定区域;
  2. OCR 输出先经纠错表(ocr_corrections)替换, 再通过正则分词, 最后与 locations 映射匹配;
  3. 引擎支持 rapidocr(默认)与 paddle, 加载失败自动回退。
"""
import re

import cv2

from .screen_capture import ScreenCapture


class OCRService:
    def __init__(self, config, screen=None, logger=None):
        self.logger = logger
        self.screen = screen or ScreenCapture()
        p = config["params"]
        self.engine_name = p.get("ocr_engine", "rapidocr")
        self.scale = float(p.get("ocr_scale", 1.0))
        self.confidence = float(p.get("ocr_confidence", 0.0))
        self.corrections = config.get("ocr_corrections", {})
        self.locations = config.get("locations", {})
        self.noise_words = config.get("noise_words", [])
        self._engine, self._kind = self._load_engine()

    # -------------------------------------------------- 引擎加载
    def _load_engine(self):
        """加载 OCR 引擎, 失败自动回退到 rapidocr。"""
        try:
            if self.engine_name == "paddle":
                from paddleocr import PaddleOCR
                engine = PaddleOCR(
                    lang="ch",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
                return engine, "paddle"
            from rapidocr_onnxruntime import RapidOCR
            return RapidOCR(), "rapidocr"
        except Exception as e:
            if self.logger:
                self.logger.warning("OCR 引擎加载失败(%s), 自动回退 rapidocr", e)
            from rapidocr_onnxruntime import RapidOCR
            return RapidOCR(), "rapidocr"

    # -------------------------------------------------- 固定区域识别
    def recognize_items(self, left, top, width, height):
        """识别固定区域, 返回 [{"text": str, "score": float, "box": [...], "center": (x,y)}]"""
        img = self.screen.grab(left, top, width, height)
        if self.scale != 1.0:
            img = cv2.resize(img, None, fx=self.scale, fy=self.scale,
                             interpolation=cv2.INTER_LINEAR)

        items = []
        if self._kind == "rapidocr":
            result, _ = self._engine(img)
            for box, text, score in (result or []):
                items.append({
                    "text": str(text),
                    "score": float(score),
                    "box": self._unscale(box),
                })
        else:  # paddle
            results = self._engine.predict(img)
            for r in results:
                texts = r.get("rec_texts") or []
                scores = r.get("rec_scores") or []
                polys = r.get("rec_polys") or r.get("rec_boxes") or []
                for t, s, poly in zip(texts, scores, polys):
                    box = [[float(p[0]), float(p[1])] for p in poly]
                    items.append({
                        "text": str(t),
                        "score": float(s),
                        "box": self._unscale(box),
                    })

        items = [it for it in items if it["score"] >= self.confidence]
        for it in items:
            it["center"] = self._center(it["box"])
        return items

    def recognize_region_texts(self, left, top, width, height):
        """识别固定区域, 只返回文本列表(供地点提取)。"""
        items = self.recognize_items(left, top, width, height)
        return [it["text"] for it in items]

    def _unscale(self, box):
        if box is None:
            return None
        return [[p[0] / self.scale, p[1] / self.scale] for p in box]

    @staticmethod
    def _center(box):
        if not box:
            return (0, 0)
        n = len(box)
        return (sum(p[0] for p in box) / n, sum(p[1] for p in box) / n)

    # -------------------------------------------------- 文本纠错与地点提取
    def correct_text(self, text):
        """按纠错表替换常见 OCR 误识别词。"""
        for wrong, right in self.corrections.items():
            text = text.replace(wrong, right)
        return text

    @staticmethod
    def _tokenize(text):
        """正则分词: 提取中文词、数字坐标等有意义的片段。"""
        return re.findall(r"[0-9]+(?:[,，:：]?[0-9]+)*|[\u4e00-\u9fff]+", text)

    def _coord_normalized(self, text):
        """去空格与中英文括号, 消除 OCR 在"坐标/地点"中间插入的空格(如"坐 标""江南野 外")。"""
        norm = re.sub(r"\s+", "", text)
        return re.sub(r"[【】\[\]{}（）()]", "", norm)

    def _location_after_coord(self, text):
        """在文本里找"坐标"关键字, 取其后最先出现的 16 个地点名之一。

        关键: 先去掉所有空格/括号后再匹配, 否则 OCR 把"坐标"拆成"坐 标"、
        把"江南野外"拆成"江南野 外"会导致找不到。提示框里"坐标"紧跟着的就是
        真正地点; 末尾"可快速转移"里出现的同名仓库名靠后, 取最靠前者即可避开。
        """
        norm = self._coord_normalized(text)
        idx = norm.find("坐标")
        if idx < 0:
            return None
        tail = norm[idx + len("坐标"):]
        best, best_pos = None, -1
        for key in self.locations:
            if not key:
                continue
            p = tail.find(key)
            if p >= 0 and (best is None or p < best_pos or
                           (p == best_pos and len(key) > len(best))):
                best, best_pos = key, p
        return best

    def extract_location(self, texts, require_coord=False):
        """从识别文本中提取地点/坐标信息, 返回匹配到的 locations 键或 None。

        识别方案(按优先级):
          1) 找"坐标"关键字, 取其后连续 2~4 个汉字作为地点名(悬停提示是固定"坐标+地点");
          2) (require_coord=False 时) 剔除干扰词并归一化后做整串子串匹配;
          3) 正则分词后逐 token 匹配;
          4) 模糊匹配兜底。

        require_coord=True 时(悬停读提示): 必须命中"坐标"关键字才返回,
        否则一律 None。这样能区分"悬停提示(带坐标)"与"仓库标签(纯地点名)",
        避免把仓库名误当宝图坐标。
        """
        combined = "".join(texts)
        combined = self.correct_text(combined)

        # 悬停提示固定带"坐标+地点": 去空格/括号后取"坐标"后最先出现的地点名
        loc = self._location_after_coord(combined)
        if loc:
            return loc
        if require_coord:
            return None

        # 剔除常见干扰词(如"使用""前往"等 UI 文字)
        for w in self.noise_words:
            combined = combined.replace(w, "")

        # 归一化: 去掉空白与中英文括号, 让 "[123,456]" / "123, 456" 等可对齐 locations 键
        norm = re.sub(r"\s+", "", combined)
        norm = re.sub(r"[\[\]{}()（）【】]", "", norm)

        # 2) 整串子串匹配(最准)
        for key in self.locations:
            if key in norm:
                return key

        # 3) 正则分词后逐 token 匹配(容忍 OCR 把多个字拼/拆在一起)
        for token in self._tokenize(norm):
            for key in self.locations:
                if key and (key in token or token in key):
                    return key

        # 4) 模糊匹配兜底(字符重叠度)
        return self._fuzzy(norm)

    def _fuzzy(self, name):
        """按公共字符数匹配最接近的地点键, 未达到阈值返回 None。"""
        best_key, best_score = None, 0
        for key in self.locations:
            if not key:
                continue
            score = self._overlap(name, key)
            if score > best_score:
                best_key, best_score = key, score
        if best_key:
            # 至少需要 max(2, 较短串长度-1) 个公共字符
            need = max(2, min(len(name), len(best_key)) - 1)
            if best_score >= need:
                return best_key
        return None

    @staticmethod
    def _overlap(a, b):
        """两个字符串公共字符数(用 Counter 交集, 考虑重复字符)。"""
        from collections import Counter
        return sum((Counter(a) & Counter(b)).values())

    def resolve_warehouse(self, location_key):
        """地点键 -> 仓库名称, 不存在返回 None。"""
        return self.locations.get(location_key)

    # -------------------------------------------------- 区域定位
    @staticmethod
    def _is_coord_tooltip(text):
        """判断文本是否为"坐标提示"(坐标+地点)而非纯地点: 含"坐标"关键字、
        形如 123,456 的数字对、或 (数字 的括号坐标, 都是提示特征。"""
        t = "".join(text.split())
        if "坐标" in t:
            return True
        if re.search(r"\d+[\s，,、:：]?\s*\d+", t):
            return True
        if re.search(r"[（(]\s*\d", t):
            return True
        return False

    def find_text_position(self, target, left, top, width, height):
        """在指定区域内扫描文字, 返回与 target 最匹配文字的中心(屏幕绝对坐标)。

        返回 (x, y, matched_text), 未找到时返回 (None, None, None)。
        用于动态定位仓库标签: 每个仓库标签已是地点名, 直接在这块区域里现读坐标,
        因此不受游戏鼠标漂移/固定坐标偏差影响。

        区分标记: 仓库标签是"纯地点名"(短); 悬停提示是"坐标+地点"(含坐标数字/括号,
        明显更长)。匹配时优先长度贴近地点名的纯标签, 避免把"坐标提示"误当仓库标签。
        """
        items = self.recognize_items(left, top, width, height)
        tg = "".join(str(target).split())
        best_item, best_text, best_val = None, "", -10 ** 9
        for it in items:
            c = "".join(self.correct_text(it["text"]).split())
            if not c:
                continue
            overlap = self._overlap(c, tg)
            val = overlap
            # 纯地点名(仓库标签)优先: 坐标提示是长尾样式, 重罚
            if self._is_coord_tooltip(c):
                val -= 1000
            # 长度越贴近目标名越优(仓库标签通常只含地点名)
            val -= abs(len(c) - len(tg)) * 5
            if val > best_val:
                best_val, best_item, best_text = val, it, c

        if best_item is None:
            return None, None, best_text
        need = max(2, min(len(best_text), len(tg)) - 1)
        if self._overlap(best_text, tg) < need:
            return None, None, best_text
        cx, cy = best_item["center"]
        return int(cx + left), int(cy + top), best_text
