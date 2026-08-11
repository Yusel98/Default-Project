"""本地知识库检索模块。

从 knowledge/ 目录读取文本类文件，按段落切分成片段，
对问题文本做关键词打分，返回得分最高的若干片段作为答案候选。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_EXTS = {".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".log"}

# 常见停用词（中英文）
STOPWORDS = set(
    """的 了 和 是 在 有 我 你 他 她 它 这 那 与 及 或 就 都 而 之 个 吗 呢 吧 啊
    a an the is are was were be to of in on for and or with from by at it this that
    what how why when where who which do does did can could will would should may
    what is how to""".split()
)

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")  # 连续中文
_WORD_RUN = re.compile(r"[A-Za-z0-9_]{2,}")     # 英文/数字词


@dataclass
class Hit:
    file: str
    score: float
    snippet: str


def _tokenize(text: str) -> list:
    """把文本拆成可打分的词。

    中文连续段拆成字符 bigram（如“太阳系”-> 太阳、阳系），保证和知识库
    片段能模糊匹配；英文按单词切分。
    """
    tokens = []
    for run in _CJK_RUN.findall(text):
        if len(run) >= 2:
            tokens.append(run)
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    tokens += _WORD_RUN.findall(text.lower())
    return [t for t in tokens if t not in STOPWORDS]


def _chunk_text(text: str) -> list:
    """按空行切成段落片段（同一段落的问题和答案保持在一起，匹配更准）。"""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def load_documents(knowledge_dir: str | Path) -> dict[str, list[str]]:
    """读取知识库目录下所有支持格式文件，返回 {文件名: [片段, ...]}。"""
    root = Path(knowledge_dir)
    docs: dict[str, list[str]] = {}
    if not root.is_dir():
        logger.warning("知识库目录不存在: %s", root)
        return docs
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            chunks = _chunk_text(text)
            if chunks:
                docs[str(path.relative_to(root))] = chunks
        except OSError as exc:
            logger.warning("读取 %s 失败: %s", path, exc)
    return docs


# 建一次索引缓存，避免每题都全量扫描+重复 lower；只保留最近一份，防止长时运行内存膨胀
_index_cache: dict = {}


def _get_index(docs: dict[str, list[str]]):
    """为文档集构建倒排索引：term -> [(file_i, chunk_i), ...]，并缓存 chunk 小写文本。

    缓存只保留最近一次调用对应的索引；docs 重建后旧索引立即释放，避免内存累积。
    """
    key = id(docs)
    entry = _index_cache.get(key)
    if entry is not None:
        return entry
    files = list(docs.keys())
    lower_cache: dict = {}
    inverted: dict = {}
    for fi, file in enumerate(files):
        for ci, chunk in enumerate(docs[file]):
            lower = chunk.lower()
            lower_cache[(fi, ci)] = lower
            for term in set(_tokenize(lower)):
                inverted.setdefault(term, []).append((fi, ci))
    entry = (files, lower_cache, inverted)
    if len(_index_cache) > 1:
        _index_cache.clear()
    _index_cache[key] = entry
    return entry


def snippet_question(snippet: str) -> str:
    """从知识库片段中提取题干（“答案”标记之前的部分）。"""
    i = snippet.find("题目")
    m = re.search(r"答案[:：]", snippet)
    stem = snippet[: m.start()] if m else snippet
    return (stem[i:] if i != -1 else stem).strip()


def _norm_for_cover(text: str) -> str:
    return re.sub(r"\s+", "", text).strip("，。？！：")


def question_cover(question: str, snippet: str) -> float:
    """识别题目文本与题库题干的匹配度（0~1）。

    统计题库片段题干里的词有多少比例出现在识别文本中（长词权重更高）。
    用于识别区域并非答题界面、或识别内容与题库对不上时，不显示识别结果。
    """
    kb_terms = _tokenize(_norm_for_cover(snippet_question(snippet)))
    if not kb_terms:
        return 0.0
    i = question.find("题目")
    q = question[i:] if i != -1 else question
    q_terms = set(_tokenize(_norm_for_cover(q)))
    total = sum(len(t) for t in kb_terms)
    hit = sum(len(t) for t in kb_terms if t in q_terms)
    return hit / total


def search(docs: dict[str, list[str]], query: str, top_k: int = 5) -> list[Hit]:
    """在文档片段中检索与 query 最匹配的片段。

    使用倒排索引只扫描 query 命中过的片段，避免全库逐段打分。
    """
    terms = _tokenize(query)
    if not terms:
        return []
    files, lower_cache, inverted = _get_index(docs)

    candidates: set = set()
    for term in terms:
        candidates.update(inverted.get(term, ()))

    # 为命中的片段打分：得分 = 命中词数加权（长词权重更高）
    scored: list[Hit] = []
    for key in candidates:
        fi, ci = key
        lower = lower_cache[key]
        score = 0.0
        for term in terms:
            freq = lower.count(term)
            if freq:
                weight = len(term)  # 更长的匹配词更有信息量
                score += weight * (1 + math.log1p(freq))
        if score > 0:
            scored.append(Hit(file=files[fi], score=score, snippet=docs[files[fi]][ci]))

    scored.sort(key=lambda h: h.score, reverse=True)
    # 有 IDF 时更精确，这里保持轻量：简单长度惩罚防止过长片段霸榜
    return scored[:top_k]
