# -*- coding: utf-8 -*-
"""主流程控制器 + 命令行入口。

架构分层:
  ScreenCapture  -> 截图(固定区域)
  OCRService     -> 固定区域识别 + 纠错 + 地点提取
  MouseController-> pyautogui 封装(右键/停止键)
  Warehouse      -> 仓库操作(选中标签/右键道具栏入库)
  MainController -> 上述模块编排, 手动分类入库(sort)
"""
import argparse
import json
import logging
import sys
import time

import pyautogui

from .config_manager import CONFIG_FILE, ensure_config, load_config, save_config
from .logger import setup_logger
from .mouse_controller import MouseController
from .ocr_service import OCRService
from .screen_capture import ScreenCapture
from .warehouse import Warehouse, WarehouseFullError


class MainController:
    """手动分类入库: 用户自行打开仓库/开封, 机器人负责悬停识别并存入对应仓库。"""

    def __init__(self, config_path=None, dry_run=None, engine=None):
        config_path = config_path or CONFIG_FILE
        ensure_config(config_path)
        self.config = load_config(config_path)
        if dry_run is not None:
            self.config["params"]["dry_run"] = dry_run
        if engine:
            self.config["params"]["ocr_engine"] = engine

        self.logger = setup_logger()
        self.screen = ScreenCapture()
        self.mouse = MouseController(self.config, self.logger)
        self.ocr = OCRService(self.config, self.screen, self.logger)
        self.warehouse = Warehouse(self.config, self.mouse, self.ocr, self.logger)

    # -------------------------------------------------- 悬停读坐标
    def _save_debug_shot(self, left, top, width, height, tag):
        """把指定区域截图存到 debug_save_dir, 用于排查识别失败。"""
        try:
            d = self.config["params"].get("debug_save_dir") or "debug"
            os.makedirs(d, exist_ok=True)
            img = self.screen.grab(left, top, width, height)
            path = os.path.join(d, "%s_%s.png" % (
                tag, time.strftime("%H%M%S")))
            self.screen.save(img, path)
            self.logger.info("已保存调试截图: %s", path)
        except Exception:
            pass

    def _read_location_by_hover(self, x, y, save_debug=True):
        """仓库界面下, 鼠标悬停到宝图位置, 识别悬停提示的坐标文字。返回地点或 None。

        带重试: 提示框未弹出/OCR 偶发失败时, 移开再移回触发提示框重新渲染,
        最多重试 retry_max 次; 仍失败则保存调试截图并返回 None。
        """
        p = self.config["params"]
        coords = self.config["coords"]
        left, top, w, h = coords["hover_coord_region"]
        retry_max = max(1, int(p.get("retry_max", 3)))
        retry_interval = float(p.get("retry_interval", 1.0))
        hover_delay = float(p.get("hover_delay", 1.2))
        jiggle = int(p.get("hover_jiggle", 60))

        self.mouse.move(x, y)
        time.sleep(hover_delay)
        for attempt in range(1, retry_max + 1):
            texts = self.ocr.recognize_region_texts(left, top, w, h)
            # 悬停提示固定含"坐标+地点", 强制带坐标关键字, 避免误抓仓库名
            loc = self.ocr.extract_location(texts, require_coord=True)
            if loc:
                return loc
            if self.logger:
                self.logger.warning(
                    "悬停第 %d/%d 次未识别到地点, 区域文字: %s",
                    attempt, retry_max, "".join(texts)[:150])
            if attempt < retry_max:
                # 移开再移回, 触发提示框重新渲染; 短暂等待再重试
                self.mouse.move(x, y + jiggle)
                time.sleep(0.3)
                self.mouse.move(x, y)
                time.sleep(hover_delay)
            time.sleep(retry_interval)
        if save_debug:
            self._save_debug_shot(left, top, w, h, "hover_fail")
        return None

    def _deposit_from(self, from_pos, location):
        """把 from_pos 处的地图放入对应仓库。返回 ok / skip / full。"""
        warehouse_name = self.ocr.resolve_warehouse(location)
        if not warehouse_name:
            self.logger.warning("地点[%s]未配置对应仓库, 跳过", location)
            return "skip"
        self.logger.info("坐标[%s] -> 应存入%s", location, warehouse_name)
        try:
            self.warehouse.deposit(from_pos, warehouse_name)
        except WarehouseFullError as e:
            self.logger.error("%s, 终止流程", e)
            return "full"
        except KeyError as e:
            self.logger.error("%s, 跳过该藏宝图", e)
            return "skip"
        except Exception as e:
            self.logger.exception("存入仓库失败: %s", e)
            return "skip"
        self.logger.info("识别到坐标[%s]，成功存入%s", location, warehouse_name)
        return "ok"

    # -------------------------------------------------- 悬停入库
    def _deposit_all(self):
        """在仓库界面下悬停道具栏每张宝图, 识别地点并存入对应仓库。返回成功数。"""
        coords = self.config["coords"]
        wh_positions = coords.get("backpack_in_warehouse_slots") or []
        done = 0
        for i, (x, y) in enumerate(wh_positions):
            if self.mouse.stop_requested():
                self.logger.info("检测到停止键, 提前结束")
                break
            location = self._read_location_by_hover(x, y)
            if location is None:
                self.logger.warning("仓库内位置%d 悬停未识别到坐标, 跳过", i + 1)
                continue
            result = self._deposit_from((x, y), location)
            if result == "stop":
                break
            if result == "full":
                break
            if result == "ok":
                done += 1
        self.logger.info("本批次入库完成, 成功 %d/%d", done, len(wh_positions))
        return done


# =============================================================
# 命令行入口
# =============================================================
def _reconfigure_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _set_console_debug():
    """把控制台日志级别降到 DEBUG, 用于 --verbose 输出点击坐标等细节。"""
    lg = logging.getLogger("treasure_sorter")
    for h in lg.handlers:
        if isinstance(h, logging.StreamHandler):
            h.setLevel(logging.DEBUG)


def _parse_region(text):
    try:
        parts = [int(x) for x in str(text).replace(" ", ",").split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError("区域格式: left,top,width,height")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("区域格式: left,top,width,height")
    return parts


def _pick_point(prompt):
    """交互式取点: 移动鼠标到目标位置, 回车记录。"""
    print(prompt)
    while True:
        raw = input("  移动鼠标到目标点后按回车 / s 跳过 / q 退出 > ").strip().lower()
        if raw in ("q", "quit"):
            raise SystemExit(0)
        if raw == "s":
            return None
        x, y = MouseController.get_cursor_pos()
        return (x, y)


def _regularize_points(points, y_tolerance=12):
    """把散点按行聚类, 每行 Y 取均值、X 按平均间距重建为等差序列。

    用于把手工录的格子坐标修正到标准网格上(游戏格子是等距的)。
    保持输入顺序不变(格子序号与位置对应关系不变)。
    """
    if not points:
        return points

    # 1) 按 Y 容差聚类成行
    rows = []
    for p in points:
        for row in rows:
            if abs(row[0][1] - p[1]) <= y_tolerance:
                row.append(p)
                break
        else:
            rows.append([p])

    # 2) 每行: 统一 Y + X 等差
    grid = {}
    for row in rows:
        row.sort(key=lambda p: p[0])
        ys = [p[1] for p in row]
        y = int(round(sum(ys) / len(ys)))
        xs = [p[0] for p in row]
        n = len(row)
        if n >= 2:
            gaps = [xs[i + 1] - xs[i] for i in range(n - 1)]
            spacing = int(round(sum(gaps) / len(gaps))) or 1
            # 以中位对齐量确定整行起点, 使网格整体贴合录制点
            anchors = [xs[i] - i * spacing for i in range(n)]
            anchor = int(round(sum(anchors) / n))
            new_xs = [anchor + i * spacing for i in range(n)]
        else:
            new_xs = xs
        grid[id(row)] = (y, new_xs)

    # 3) 按原顺序输出
    result = []
    for p in points:
        for row in rows:
            if p in row:
                y, xs = grid[id(row)]
                idx = row.index(p)
                result.append([xs[idx], y])
                break
        else:
            result.append(list(p))
    return result


def cmd_sort(args):
    """手动分类入口: 仓库/开封由用户自行完成, 机器人只做悬停识别点位并分类入库。

    流程: 用户手动打开仓库并把已开封宝图放进背包(仓库界面可见) ->
    机器人逐个悬停识别地点 -> 点击同名仓库标签 -> 右键该格存入。
    """
    if args.verbose:
        _set_console_debug()
    app = MainController(args.config, dry_run=args.dry_run, engine=args.engine)

    if not app.config["coords"].get("backpack_in_warehouse_slots"):
        app.logger.error("未配置仓库界面道具栏坐标(backpack_in_warehouse_slots), "
                         "请先运行 gridgen/calibrate")
        return
    tab_region = app.config["coords"].get("warehouse_tab_region") or [0, 0, 0, 0]
    if not (tab_region[2] > 0 and tab_region[3] > 0):
        app.logger.error("未配置仓库标签区域(warehouse_tab_region), OCR 无法定位仓库, "
                         "请先运行 calibrate")
        return
    hover = app.config["coords"].get("hover_coord_region") or [0, 0, 0, 0]
    if not (hover[2] > 0 and hover[3] > 0):
        app.logger.error("未配置悬停坐标提示区域(hover_coord_region), 请先运行 calibrate")
        return

    delay = float(app.config["params"].get("manual_open_ready_delay", 8.0))
    app.logger.info("请手动打开仓库并确认已开封宝图在背包可见, %.0f 秒后开始分类存入...",
                    delay)
    time.sleep(delay)
    try:
        n = app._deposit_all()
        app.logger.info("分类存入完成, 成功 %d 张", n)
    except pyautogui.FailSafeException:
        app.logger.error("触发 FailSafe(鼠标移到屏幕左上角), 已停止")
    except Exception as e:
        app.logger.exception("分类存入出错: %s", e)


def cmd_testclick(args):
    """对固定坐标连续点击并打印光标位置, 检验游戏是否对点击加偏差(反作弊)。

    用法示例(点 A 仓库标签 10 次):
      python main.py testclick --x 1301 --y 912 --count 10 --delay 0.8
    观察: 若每次游戏里选中的都是同一仓库, 说明点击层无偏差;
          若时对时错, 说明游戏侧对鼠标点击加了随机偏差。
    """
    cfg = load_config(args.config)
    screen = ScreenCapture()
    ocr = OCRService(cfg, screen, None) if args.ocr_region else None
    x, y, n = args.x, args.y, args.count
    delay = float(args.delay)
    print("测试: 对固定坐标 (%d, %d) %s点击 %d 次, 间隔 %.2fs"
          % (x, y, args.button, n, delay))
    for i in range(1, n + 1):
        pyautogui.moveTo(x, y)
        time.sleep(0.25)
        p1 = pyautogui.position()
        pyautogui.click(x, y, button=args.button)
        time.sleep(0.1)
        p2 = pyautogui.position()
        line = "#%02d 点击前光标(%d,%d) 点击后(%d,%d)" % (
            i, int(p1.x), int(p1.y), int(p2.x), int(p2.y))
        if ocr:
            left, top, w, h = args.ocr_region
            texts = ocr.recognize_region_texts(left, top, w, h)
            line += "  OCR: %s" % "".join(texts)[:60]
        print(line)
        time.sleep(delay)


def cmd_config(args):
    cfg = load_config(args.config)
    print(json.dumps(cfg, ensure_ascii=False, indent=2))


def cmd_capture(args):
    screen = ScreenCapture()
    if args.region:
        img = screen.grab_region(args.region)
    else:
        img = screen.grab_region([0, 0, 0, 0])
    import os
    out = args.out or os.path.join(os.path.dirname(args.config), "capture.png")
    screen.save(img, out)
    print("截图已保存:", out)


def cmd_ocr(args):
    """调试: 识别指定区域并打印, 展示纠错与地点提取结果。"""
    from .config_manager import load_config
    cfg = load_config(args.config)
    if args.engine:
        cfg["params"]["ocr_engine"] = args.engine
    screen = ScreenCapture()
    ocr = OCRService(cfg, screen, None)
    if args.region:
        left, top, w, h = args.region
    else:
        left, top, w, h = cfg["coords"]["coord_text_region"]
        # coord_text_region 未配置时, 回退到悬停识别区域(分类真正用的区域)
        if w <= 0 or h <= 0:
            left, top, w, h = cfg["coords"].get("hover_coord_region") or [0, 0, 0, 0]
    items = ocr.recognize_items(left, top, w, h)
    print("识别到 %d 行文字:" % len(items))
    for it in items:
        print("  (%5d, %5d)  %.3f  %s" % (
            left + it["center"][0], top + it["center"][1], it["score"], it["text"]))
    texts = [it["text"] for it in items]
    loc = ocr.extract_location(texts)
    print("纠错后文本:", ocr.correct_text("".join(texts)))
    print("提取地点:", loc, "-> 仓库:", ocr.resolve_warehouse(loc) if loc else None)
    if args.out:
        import cv2
        import numpy as np
        img = screen.grab(left, top, w, h)
        for it in items:
            box = it["box"]
            if box:
                pts = np.array([[int(p[0]), int(p[1])] for p in box], dtype=np.int32)
                cv2.polylines(img, [pts], True, (0, 0, 255), 2)
        screen.save(img, args.out)
        print("标注图已保存:", args.out)


def cmd_calibrate(args):
    """交互式校准坐标。可用 --only 只校准指定项。"""
    cfg = load_config(args.config)
    coords = cfg["coords"]
    params = cfg["params"]

    only = [s.strip() for s in (args.only or "").split(",") if s.strip()]

    sections = [
        # (键, 标签, 类型, 操作提示)
        ("backpack_in_warehouse_slots", "coords.backpack_in_warehouse_slots (仓库界面道具栏20格)", "points",
         "仓库界面已打开(道具栏已自带显示)。录的是道具栏里放宝图的20个格子"),
        ("hover_coord_region", "coords.hover_coord_region (悬停坐标提示区域)", "region",
         "把鼠标悬停在一张宝图上, 等坐标提示出现, 框选该提示文字区域"),
        ("warehouse_tab_region", "coords.warehouse_tab_region (仓库标签区域)", "region",
         "仓库界面已打开, 框选所有仓库标签所在的大致区域(OCR 会在此区域里找对应地点名的标签)"),
        ("warehouse_full_text_region", "coords.warehouse_full_text_region (仓库已满提示区域)", "region",
         "(可选) 打开仓库后, 框选「仓库已满」提示出现的区域; 全0表示不检测"),
    ]

    if only:
        sections = [s for s in sections if s[0] in only]

    print("开始坐标校准(屏幕绝对像素)。共 %d 项。" % len(sections))
    try:
        for key, label, kind, hint in sections:
            print("-- " + label)
            print("  操作提示: " + hint)
            if kind == "point":
                pt = _pick_point("  把鼠标移到目标点后按回车 / s 跳过 / q 退出 > ")
                if pt:
                    coords[key] = list(pt)
            elif kind == "region":
                p1 = _pick_point("  区域左上角: 把鼠标移到左上角后按回车 / s 跳过 > ")
                p2 = _pick_point("  区域右下角: 把鼠标移到右下角后按回车 / s 跳过 > ")
                if p1 and p2:
                    coords[key] = [p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1]]
            elif kind == "points":
                arr = coords.setdefault(key, [])
                arr.clear()
                print("  依次把鼠标移到每个格子上按回车, b 撤销上一个, d 完成, q 退出。")
                while True:
                    raw = input("    第%d个: 回车记录 / b 撤销 / d 完成 / q 退出 > "
                                % (len(arr) + 1)).strip().lower()
                    if raw == "q":
                        raise SystemExit(0)
                    if raw == "b":
                        if arr:
                            removed = arr.pop()
                            print("    已撤销: (%d, %d)" % tuple(removed))
                        else:
                            print("    没有可撤销的记录了")
                        continue
                    if raw == "d":
                        break
                    x, y = MouseController.get_cursor_pos()
                    arr.append([int(x), int(y)])
                    print("    记录: (%d, %d)" % (x, y))
            # 每完成一项立即保存, 防止中途退出丢失
            save_config(cfg, args.config)
    except SystemExit:
        # 按 q 退出前也保存已录的部分
        save_config(cfg, args.config)
        raise
    save_config(cfg, args.config)
    print("校准完成, 已保存到:", args.config)


def cmd_regularize(args):
    """把手工录的格子坐标规整到标准网格上(同排 Y 统一, X 等差)。"""
    cfg = load_config(args.config)
    coords = cfg["coords"]
    for key in ("backpack_in_warehouse_slots",):
        if coords.get(key):
            before = list(coords[key])
            coords[key] = _regularize_points(coords[key])
            changed = sum(1 for a, b in zip(before, coords[key]) if a != b)
            print("已规整 %s: %d 点, %d 点被修正" % (key, len(coords[key]), changed))
            print("  修正后: %s" % json.dumps(coords[key], ensure_ascii=False))
    save_config(cfg, args.config)
    print("已保存到:", args.config)


def cmd_gridgen(args):
    """用锚点生成标准网格坐标: 只需录 3 个点(首行首格/首行二格/二行首格),
    其余按 X/Y 等差自动生成, 避免逐格手工录制引入抖动。
    """
    cfg = load_config(args.config)
    key = args.key
    if key not in cfg["coords"]:
        print("未知坐标键: %s, 可用: %s"
              % (key, ", ".join(sorted(cfg["coords"].keys()))))
        return

    # 行结构: 默认等行等列; 可用 --rowlen 指定每行个数, 如 "6,6,6,2"
    if args.rowlen:
        rowlens = [int(x) for x in str(args.rowlen).split(",") if x.strip()]
        rows = len(rowlens)
        cols = max(rowlens)
        total = sum(rowlens)
    else:
        rows, cols = max(1, args.rows), max(1, args.cols)
        rowlens = [cols] * rows
        total = rows * cols

    print("移动鼠标到格子中心后按回车记录锚点(可 s 跳过 / q 退出)。")
    p00 = _pick_point("  第1行第1格(锚点1) > ")
    if not p00:
        print("锚点1缺失, 取消")
        return
    p01 = _pick_point("  第1行第2格(锚点2, 求X间距) > ")
    if not p01:
        print("锚点2缺失, 取消")
        return
    p10 = None
    if rows > 1:
        p10 = _pick_point("  第2行第1格(锚点3, 求Y间距) > ")
        if not p10:
            print("锚点3缺失, 取消")
            return

    dx = p01[0] - p00[0]
    dy = (p10[1] - p00[1]) if p10 else 0
    if dx == 0:
        print("X间距为0, 锚点2可能没录准, 取消")
        return
    print("X间距=%d, Y间距=%d, 行结构=%s" % (dx, dy, rowlens))

    points = []
    for r, rlen in enumerate(rowlens):
        for c in range(rlen):
            points.append([p00[0] + c * dx, p00[1] + r * dy])

    cfg["coords"][key] = points
    print("已生成 %s: %s" % (key, json.dumps(points, ensure_ascii=False)))
    save_config(cfg, args.config)
    print("已保存到:", args.config)


def main(argv=None):
    _reconfigure_stdout()
    parser = argparse.ArgumentParser(description="藏宝图自动分类入库工具")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser(
        "run", help="手动分类入库(旧命令, 别名 sort): 用户自行开仓/开封, 机器人只悬停识别并存入")
    p_run.add_argument("--config", default=CONFIG_FILE)
    p_run.add_argument("--engine", choices=["rapidocr", "paddle"])
    p_run.add_argument("--dry-run", action="store_true", help="只识别不动鼠标")
    p_run.add_argument("--verbose", action="store_true", help="控制台输出点击/移动坐标等细节")
    p_run.set_defaults(func=cmd_sort)

    p_sort = sub.add_parser(
        "sort", help="手动分类入库: 用户自行打开仓库/开场, 机器人只悬停识别并分类存入")
    p_sort.add_argument("--config", default=CONFIG_FILE)
    p_sort.add_argument("--engine", choices=["rapidocr", "paddle"])
    p_sort.add_argument("--dry-run", action="store_true", help="只识别不动鼠标")
    p_sort.add_argument("--verbose", action="store_true", help="控制台输出点击/移动坐标等细节")
    p_sort.set_defaults(func=cmd_sort)

    p_tc = sub.add_parser("testclick", help="对固定坐标连点N次, 检验游戏是否对点击加偏差")
    p_tc.add_argument("--config", default=CONFIG_FILE)
    p_tc.add_argument("--x", type=int, required=True, help="点击X")
    p_tc.add_argument("--y", type=int, required=True, help="点击Y")
    p_tc.add_argument("--count", type=int, default=10, help="点击次数")
    p_tc.add_argument("--delay", type=float, default=0.8, help="每次间隔(秒)")
    p_tc.add_argument("--button", default="left", help="left/right")
    p_tc.add_argument("--ocr-region", type=_parse_region, default=None,
                      help="每次点击后OCR该区域(如仓库名显示区), 用于判断选中了哪个仓库")
    p_tc.set_defaults(func=cmd_testclick)

    p_cfg = sub.add_parser("config", help="查看/生成配置文件")
    p_cfg.add_argument("--config", default=CONFIG_FILE)
    p_cfg.set_defaults(func=cmd_config)

    p_cap = sub.add_parser("capture", help="截图保存(确认区域用)")
    p_cap.add_argument("--config", default=CONFIG_FILE)
    p_cap.add_argument("--region", type=_parse_region, default=None)
    p_cap.add_argument("--out", default=None)
    p_cap.set_defaults(func=cmd_capture)

    p_ocr = sub.add_parser("ocr", help="识别指定/配置区域文字(调试用)")
    p_ocr.add_argument("--config", default=CONFIG_FILE)
    p_ocr.add_argument("--region", type=_parse_region, default=None)
    p_ocr.add_argument("--engine", choices=["rapidocr", "paddle"])
    p_ocr.add_argument("--out", default=None, help="保存带识别框的标注图")
    p_ocr.set_defaults(func=cmd_ocr)

    p_cal = sub.add_parser("calibrate", help="交互式校准坐标")
    p_cal.add_argument("--config", default=CONFIG_FILE)
    p_cal.add_argument("--only", default=None,
                       help="只校准指定项, 逗号分隔。可选: "
                            "hover_coord_region,backpack_in_warehouse_slots,"
                            "warehouse_deposit_slots,warehouse_full_text_region")
    p_cal.set_defaults(func=cmd_calibrate)

    p_reg = sub.add_parser("regularize", help="把格子坐标规整到标准网格(同排Y统一,X等差)")
    p_reg.add_argument("--config", default=CONFIG_FILE)
    p_reg.set_defaults(func=cmd_regularize)

    p_gg = sub.add_parser("gridgen", help="用3个锚点生成标准网格坐标(只需精确录3个点)")
    p_gg.add_argument("--config", default=CONFIG_FILE)
    p_gg.add_argument("--key", required=True,
                      help="坐标键: backpack_in_warehouse_slots")
    p_gg.add_argument("--rows", type=int, default=4, help="行数(默认4)")
    p_gg.add_argument("--cols", type=int, default=5, help="列数(默认5)")
    p_gg.set_defaults(func=cmd_gridgen)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return
    args.func(args)
