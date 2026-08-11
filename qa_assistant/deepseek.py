"""DeepSeek API 对接模块：账户余额查询。

使用 Python 标准库 urllib 发起请求，无第三方依赖。
API Key 优先级：环境变量 DEEPSEEK_API_KEY > 配置文件 ~/.deepseek_qa_config.json。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
CONFIG_PATH = Path.home() / ".deepseek_qa_config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("读取配置文件失败: %s", exc)
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_api_key(*, force_prompt: bool = False) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key and not force_prompt:
        return key
    cfg = load_config()
    if cfg.get("api_key") and not force_prompt:
        return cfg["api_key"]
    key = input("请输入 DeepSeek API Key: ").strip()
    cfg["api_key"] = key
    save_config(cfg)
    logger.info("API Key 已保存到 %s", CONFIG_PATH)
    return key


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"请求失败 HTTP {exc.code}: {body}") from exc


def fetch_balance(api_key: str | None = None, base_url: str = DEFAULT_BASE_URL) -> dict:
    """查询 DeepSeek 账户余额。"""
    api_key = api_key or get_api_key()
    url = f"{base_url.rstrip('/')}/user/balance"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    return _get_json(url, headers)


def format_balance(info: dict) -> str:
    lines = []
    for item in info.get("balance_infos", []):
        currency = item.get("currency", "?")
        total = item.get("total_balance", 0)
        granted = item.get("granted_balance", 0)
        topped = item.get("topped_up_balance", 0)
        lines.append(f"{currency}: 总余额 {total}，充值余额 {topped}，赠送余额 {granted}")
    if not lines:
        lines.append(str(info))
    return "\n".join(lines)
