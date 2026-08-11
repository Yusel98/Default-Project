"""框选模块：解析题干与选项、把正确选项在屏幕上用红框标出。

依赖 tkinter（Python 自带）。透明色使用 magenta，Windows 下该区域可点击穿透。
"""

from __future__ import annotations

import difflib
import re
import tkinter as tk

# 选项开头：A. A、① 1. 等
OPTION_RE = re.compile(r"^\s*([A-Ha-h①②③④⑤⑥⑦⑧1-8])\s*[.、．:：)）]?")

# 游戏 HUD / 截图水印等非题目内容，识别后直接剔除，避免污染题干与匹配度
NOISE_RE = re.compile(
    r"图片编号|无与伦比|\[\w{4,}\]|^\d{1,2}:\d{2}:\d{2}$|"
    r"^[子丑寅卯辰巳午未申酉戌亥]时\([昼夜]\)"
)


def clean_lines(lines: list[tuple[str, Box]]) -> list[tuple[str, Box]]:
    """过滤 HUD 噪声行（时钟、坐标、水印等），保留纯题目/选项行。"""
    return [(t, b) for t, b in lines if t and not NOISE_RE.search(t)]
# 知识库片段里显式给出的答案，如 “答案：B” “答案:B”
ANSWER_LINE_RE = re.compile(r"答案[:：]?\s*([A-Ha-h①②③④⑤⑥⑦⑧1-8])")

Box = tuple[int, int, int, int]  # (x1, y1, x2, y2)


def parse_question(lines: list[tuple[str, Box]]) -> tuple[str, list[str], list[Box]]:
    """把识别行拆成题干和选项。

    返回 (question, option_texts, option_boxes)。以选项前缀开头的短行归为选项，
    其余行拼接为题干。
    """
    question_parts: list[str] = []
    option_texts: list[str] = []
    option_boxes: list[Box] = []
    for text, box in lines:
        if OPTION_RE.match(text) and len(text) < 80:
            option_texts.append(text)
            option_boxes.append(box)
        else:
            question_parts.append(text)
    question = " ".join(question_parts).strip()
    return question, option_texts, option_boxes


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _strip_option_prefix(text: str) -> str:
    """剥离选项前缀“A. ”“1、”等；选项本身就是一个字符（如数字“3”）时原样保留。

    分隔符必须存在，避免把“10级、20级”里的“1”误当选项序号剥掉。
    """
    m = re.match(r"^\s*[A-Ha-h①②③④⑤⑥⑦⑧1-8]\s*[.、．:：)）]\s*", text)
    if m:
        return text[m.end():]
    if re.fullmatch(r"\s*[A-Ha-h①②③④⑤⑥⑦⑧1-8]\s*", text):
        return text
    return text


def _letter_index(text: str) -> int | None:
    """把选项字母/序号转成 0 起始下标，非选项开头返回 None。"""
    m = OPTION_RE.match(text.strip())
    if not m:
        return None
    ch = m.group(1).lower()
    if ch in "abcdefgh":
        return ord(ch) - ord("a")
    if ch in "①②③④⑤⑥⑦⑧":
        return "①②③④⑤⑥⑦⑧".index(ch)
    if ch in "12345678":
        return int(ch) - 1
    return None


def pick_option(snippet: str, option_texts: list[str]) -> int | None:
    """在选项里找出与知识库答案最匹配的那一个。

    匹配优先级：
    1. 答案文字与某选项文本完全一致 -> 直接命中（对“答案：3”“答案：0级、10级”这类
       短/数字答案最可靠，可避免与相似选项如“10级、20级”混淆）；
    2. 答案文字包含/相似于某选项文本（子串打分）；
    3. 显式字母答案“答案：B”或片段首字母（仅作兜底，防止 OCR 把数字误识为字母时框错）。
    """
    if not snippet or not option_texts:
        return None

    am = re.search(r"答案[:：]\s*(\S.*)$", snippet)
    target = _norm(am.group(1)) if am else _norm(snippet)
    if not target:
        return None

    opts = [_norm(_strip_option_prefix(o)) for o in option_texts]

    # 1) 完全一致
    for i, opt in enumerate(opts):
        if opt and opt == target:
            return i

    # 2) 子串 / 模糊匹配：完整度优先
    best_idx: int | None = None
    best_score = -1.0
    for i, opt in enumerate(opts):
        if not opt:
            continue
        ratio = difflib.SequenceMatcher(None, target, opt).ratio()
        if opt in target:
            # 选项只是答案的一部分（如“灌溉工程”之于“防洪灌溉工程”），
            # 只按“覆盖了答案的比例”给分，避免压过只差一两个字的完整选项
            score = len(opt) / max(len(target), 1)
        elif target in opt:
            score = len(target) / max(len(opt), 1)
        else:
            score = ratio
        if score > best_score:
            best_idx, best_score = i, score
    if best_idx is not None and best_score >= 0.2:
        return best_idx

    # 3) 显式字母答案 / 片段首字母兜底
    m = ANSWER_LINE_RE.search(snippet)
    if m:
        idx = _letter_index(m.group(1))
        if idx is not None and idx < len(option_texts):
            return idx
    # snippet 里没有“答案：xxx”标记时才把整段当字母答案，
    # 避免把“答案：珐琅彩瓷瓶”这类题目首字符是数字的片段误当选项序号
    if not am:
        direct = _letter_index(snippet.strip())
        if direct is not None and direct < len(option_texts):
            return direct
    return None


def extract_answer(snippet: str) -> str:
    """从知识库片段里提取“答案：xxx”的文字；没有则返回整段。"""
    m = re.search(r"答案[:：]\s*(\S[^\n]*)", snippet)
    return m.group(1).strip() if m else snippet.strip()


def _line_candidates(lines: list[tuple[str, Box]]) -> list[tuple[str, tuple[Box, Box] | Box]]:
    """把 OCR 行合并成“逻辑选项”候选。

    该界面的选项过长时会在同一列内折行（第二行紧贴其下、水平范围相近），
    单独一行只含答案的一部分，需把紧邻的上下两行拼起来才算完整选项。
    布局是 2 列多选选项（每列 1~2 行），OCR 输出顺序是每行的左→右，因此
    续行不一定紧跟在主行之后，这里对每个主行向后扫描找“在其正下方且同列
    重叠”的一行合并。返回每段文本及其包围盒（合并项为 (主行框, 续行框)）。
    """
    merged: list[tuple[str, tuple[Box, Box] | Box]] = []
    vs: list[str] = [_norm(t) for t, _ in lines]
    used: set[int] = set()
    n = len(lines)
    for i, box in enumerate((_[1] for _ in lines)):
        if i in used:
            continue
        t = vs[i]
        attach = None
        if t:
            for j in range(i + 1, n):
                if j in used:
                    continue
                b2 = lines[j][1]
                gap = max(b2[1] - box[3], 0)
                overlap = max(0, min(box[2], b2[2]) - max(box[0], b2[0]))
                if 0 <= gap <= 12 and overlap > 0 and vs[j]:
                    attach = j
                    break
        if attach is not None:
            used.add(attach)
            merged.append((t + vs[attach], (box, lines[attach][1])))
        else:
            merged.append((t, box))
    return merged


def _score_against(target: str, text: str) -> float:
    """按目标答案给一段文本打分，与 pick_option 的子串/模糊规则一致。"""
    if not text:
        return 0.0
    if text == target:
        return 2.0
    if target in text:
        return 1.5
    if text in target:
        return len(text) / max(len(target), 1)
    return difflib.SequenceMatcher(None, target, text).ratio()


def find_answer_line(snippet: str, lines: list[tuple[str, Box]]) -> Box | None:
    """在全部识别行里找包含答案文字的那一行，返回其包围盒。

    用于选项没被识别成“选项行”（如选项不带字母前缀、字母被 OCR 分开识别）时的回退。
    打分原则：完整包含答案文字（等于或包含 target）得分最高；
    只覆盖答案一部分的短行得分要低，避免误框。选项折行时把两行合并成一个
    候选再打分，保证长选项（如“京剧、越剧、黄梅戏、评剧、豫剧”）能整段命中。
    """
    target = _norm(extract_answer(snippet))
    if not target:
        return None
    best_box: Box | None = None
    best_score = 0.0
    for cand_text, cand_box in _line_candidates(lines):
        score = _score_against(target, cand_text)
        if score > best_score:
            best_box, best_score = cand_box, score
    if best_box is None or best_score < 0.6:
        return None
    if isinstance(best_box, tuple) and len(best_box) == 2 and isinstance(best_box[0], tuple):
        (b1, b2) = best_box
        return (
            min(b1[0], b2[0]),
            min(b1[1], b2[1]),
            max(b1[2], b2[2]),
            max(b1[3], b2[3]),
        )
    return best_box


class Overlay:
    """透明置顶窗口，用于在屏幕上框出正确选项。"""

    def __init__(self, rect: tuple[int, int, int, int]):
        left, top, width, height = rect
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.wm_attributes("-transparentcolor", "magenta")
        except tk.TclError:
            pass
        self.root.geometry(f"{width}x{height}+{left}+{top}")
        self.canvas = tk.Canvas(self.root, bg="magenta", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._visible = True
        self.root.bind("<F10>", lambda _e: self.toggle())
        self.root.update()
        self._enable_click_through()

    def _enable_click_through(self) -> None:
        """让整个窗口（含红框）鼠标点击穿透到下层游戏，不影响点击操作。仅 Windows。"""
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_LAYERED
            )
        except Exception:  # noqa: BLE001
            pass

    def toggle(self) -> None:
        self._visible = not self._visible
        if self._visible:
            self.root.deiconify()
        else:
            self.root.withdraw()

    def draw(self, boxes: list[Box], color: str = "red") -> None:
        self.canvas.delete("all")
        for (x1, y1, x2, y2) in boxes:
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=4)
        self.root.update()

    def refresh(self) -> None:
        self.root.update()

    def clear(self) -> None:
        self.canvas.delete("all")
        self.root.update()

    def destroy(self) -> None:
        self.root.destroy()


def primary_monitor_rect() -> tuple[int, int, int, int]:
    """主显示器的屏幕区域 (left, top, width, height)。"""
    import mss

    with mss.mss() as sct:
        m = sct.monitors[1]
        return (m["left"], m["top"], m["width"], m["height"])


def pick_region(monitor_rect: tuple[int, int, int, int]) -> Box | None:
    """让用户在屏幕上拖拽框选出答题区域。

    返回 (left, top, width, height)，按 Esc 取消时返回 None。
    """
    left, top, width, height = monitor_rect
    result: list = []

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.35)
    except tk.TclError:
        pass
    root.geometry(f"{width}x{height}+{left}+{top}")
    canvas = tk.Canvas(root, bg="black", cursor="crosshair", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        width // 2,
        40,
        text="在答题区域上按住左键拖拽框选，松开确认；右键取消重选；Esc 退出",
        fill="white",
        font=("Microsoft YaHei", 18),
    )
    state: dict = {"x1": None, "y1": None, "rect": None}

    def on_press(e) -> None:
        state["x1"] = e.x_root - left
        state["y1"] = e.y_root - top
        if state["rect"] is not None:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(
            state["x1"], state["y1"], state["x1"], state["y1"],
            outline="#ff5a5a", width=2, fill="#ffdddd",
        )

    def on_cancel(_e) -> None:
        if state["rect"] is not None:
            canvas.delete(state["rect"])
        state["rect"] = None
        state["x1"] = None
        state["y1"] = None

    def on_drag(e) -> None:
        if state["rect"] is not None:
            canvas.coords(state["rect"], state["x1"], state["y1"],
                          e.x_root - left, e.y_root - top)

    def on_release(e) -> None:
        x1, y1 = state["x1"], state["y1"]
        x2, y2 = e.x_root - left, e.y_root - top
        if x1 is None or abs(x2 - x1) < 30 or abs(y2 - y1) < 30:
            return
        result.append((min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)))
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<ButtonPress-3>", on_cancel)
    root.bind("<Escape>", lambda _e: root.destroy())
    root.mainloop()
    return result[0] if result else None
