"""2026 淘宝任务助手。基于 coin11-tb 的文案和按行配对方法改写。

来源及改动见 SOURCES.md，许可证见 LICENSE。默认只预览，不点击。
"""

import argparse
from datetime import datetime
import logging
from pathlib import Path
import re
from threading import Event, Thread
import time
import xml.etree.ElementTree as ET

TB_APP = "com.taobao.taobao"
ALIPAY_APP = "com.eg.android.AlipayGphone"
PROGRESS = re.compile(r"[（(]\s*(\d+)\s*/\s*(\d+)\s*[)）]")
PANEL = re.compile(r"做任务赚(?:能量|体力)")
GO = {"去完成", "去逛逛", "去浏览", "逛一逛", "去看看", "逛一下"}
CLAIM = {"领取奖励", "立即领取", "立即领", "去领取"}
BROWSE = re.compile(r"浏览|逛|看看")
SEARCH = re.compile(r"搜一搜|搜索")
ALIPAY_VISIT = re.compile(r"支付宝|蚂蚁庄园|蚂蚁森林|芭芭农场")
SKIP = re.compile(
    r"下单|购买|支付|充值|开通|订阅|捐|助力|邀请|分享|拉好友|加购|收藏|关注|评价|"
    r"安装|下载|桌面|权限|授权|游戏|玩一|试玩|通关|消消乐|农场|蚂蚁|支付宝|闲鱼|"
    r"点淘|一淘|饿了么|闪购|微博|百度|菜鸟|快手|信用|贷款|借款|理财|基金|保险|会员|签到|夺宝|抽奖|转盘"
)
INTERACTIVE_TASK = re.compile(r"游戏|玩一|试玩|通关|消消乐|签到|夺宝|抽奖|转盘")
BLOCKED_DESTINATION = re.compile(r"微博|快手")
VERIFY = re.compile(r"验证码|滑动验证|拖动.*滑块|按住.*滑块|向右滑|安全验证|人机验证|身份验证|点选验证|请依次点击|请完成验证|操作频繁|访问受限|异常访问|登录验证|请先登录")
GAME = re.compile(r"升级赛车拿钱|加速升级|跳一跳拿钱")
ENERGY = re.compile(r"剩余\s*(\d+)\s*(?:能量|体力)")
UPGRADE_POPUP = re.compile(r"确认选择|解锁新(?:尾翼|车身|车饰|赛车)|选择.{0,8}(?:款式|车型|车身)|(?:送你|赠送|恭喜获得|领取成功).{0,24}(?:能量|体力)|恭喜升级")
LOG = logging.getLogger("taobao_2026")
LOG.addHandler(logging.NullHandler())
LOG_DIR = Path(__file__).resolve().parent / "logs"


class TaskSkipped(RuntimeError):
    """当前任务无法完成，返回列表后继续其他任务。"""


def configure_logging():
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"run-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    print(f"运行日志：{path}", flush=True)


def task_row(nodes, button):
    bx1, by1, _, by2 = button["bounds"]
    cy = (by1 + by2) / 2
    rows = [n for n in nodes if PROGRESS.search(n["text"])
            and n["parent"] == button["parent"]
            and n["bounds"][2] <= bx1 + 10
            and n["bounds"][1] <= cy <= n["bounds"][3]]
    return rows[0] if len(rows) == 1 else None


def parse_nodes(xml):
    nodes = []
    root = ET.fromstring(xml)
    parents = {child: index for index, parent in enumerate(root.iter()) for child in parent}
    for el in root.iter("node"):
        a = el.attrib
        match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", a.get("bounds", ""))
        if not match or a.get("enabled", "true") == "false" or a.get("visible-to-user", "true") == "false":
            continue
        bounds = tuple(map(int, match.groups()))
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        nodes.append({
            "text": (a.get("text") or a.get("content-desc", "")).strip(),
            "class": a.get("class", ""),
            "clickable": a.get("clickable") == "true",
            "bounds": bounds,
            "parent": parents.get(el),
            "description": a.get("content-desc", "").strip(),
            "resource_id": a.get("resource-id", ""),
        })
    return nodes


def is_panel(nodes):
    return any(PANEL.fullmatch(n["text"]) for n in nodes)


def task_candidates(nodes):
    """任务标题和按钮必须属于同一容器且位于同一行；未知行跳过。"""
    tasks = []
    panel_bottom = max((n["bounds"][3] for n in nodes if PANEL.fullmatch(n["text"])), default=0)
    for button in nodes:
        label = button["text"]
        if label not in GO | CLAIM or button["class"] != "android.widget.Button":
            continue
        if button["bounds"][1] < panel_bottom:
            continue
        # 不猜测多个重叠文案中哪一个是标题。
        row = task_row(nodes, button)
        if row is None:
            continue
        text = row["text"]
        # 同行独立副标题也参与过滤，避免遗漏外部应用或额外操作。
        text += " " + " ".join(n["text"] for n in nodes if n is not row
                               and n["parent"] == row["parent"]
                               and n["class"] != "android.widget.Image"
                               and n["bounds"][2] <= button["bounds"][0] + 10
                               and row["bounds"][1] <= n["bounds"][1] < n["bounds"][3] <= row["bounds"][3])
        text = text.strip()
        progress = PROGRESS.search(text)
        title = text[:progress.start()].strip()
        alipay_visit = bool(ALIPAY_VISIT.search(text))
        pailitao = "拍立淘" in text
        # 支付宝夺宝/游戏只进入短停；普通游戏和来源不明的签到不执行。
        filtered_text = ALIPAY_VISIT.sub("", text)
        if alipay_visit:
            filtered_text = INTERACTIVE_TASK.sub("", filtered_text)
        if SKIP.search(filtered_text):
            continue
        if not (BROWSE.search(text) or SEARCH.search(text) or alipay_visit or pailitao):
            continue
        done, total = map(int, progress.groups())
        if total == 0 or (label in GO and done >= total):
            continue
        tasks.append({
            "title": title,
            "details": text,
            "progress": (done, total),
            "action": ("领取" if label in CLAIM else "跳转停留6秒" if alipay_visit
                       else "拍立淘停留6秒" if pailitao else "搜索后浏览" if SEARCH.search(text) else "浏览"),
            "bounds": button["bounds"],
        })
    return tasks


def click_task(d, task):
    # 点击前重新核对身份、进度、整行文案及坐标，变化时回到自动扫描。
    nodes = read_screen(d)
    if not is_panel(nodes) or task not in task_candidates(nodes):
        LOG.info("task_changed_before_click title=%s", task["title"])
        return False
    point = center(d, task["bounds"])
    LOG.info("task_click title=%s bounds=%s", task["title"], task["bounds"])
    d.click(*point)
    return True


def check_task_destination(nodes):
    if not is_panel(nodes) and any(BLOCKED_DESTINATION.search(n["text"]) for n in nodes):
        raise TaskSkipped("进入了微博或快手页面，未继续浏览。")


def read_screen(d, allowed_packages=(TB_APP,)):
    info = d.info
    if not info.get("screenOn", False):
        raise RuntimeError("屏幕已熄灭。请手动解锁后重新运行。")
    if info.get("currentPackageName") not in allowed_packages:
        raise RuntimeError("前台已离开淘宝或本任务允许的应用，请手动返回任务列表后重新运行。")
    xml = d.dump_hierarchy()
    for el in ET.fromstring(xml).iter("node"):
        if el.get("visible-to-user", "true") == "false":
            continue
        text = el.get("text", "") + " " + el.get("content-desc", "")
        if VERIFY.search(text):
            LOG.warning("verification_detected source=hierarchy")
            raise RuntimeError("发现验证或访问限制，已停止。请在手机上手动处理。")
        if re.search(r"提交订单|确认订单|确认支付|收银台|立即付款", text):
            raise RuntimeError("进入了订单或支付页面，已停止，请手动返回。")
    nodes = parse_nodes(xml)
    if not nodes:
        raise RuntimeError("读不到页面控件。请等待页面加载；纯画布页面需要另行适配。")
    return nodes


def center(d, bounds):
    width, height = d.window_size()
    x1, y1, x2, y2 = bounds
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise TaskSkipped("按钮不完整或位于屏幕外，跳过当前任务。")
    return (x1 + x2) // 2, (y1 + y2) // 2


def is_search_entry(nodes):
    texts = {n["text"] for n in nodes}
    return ("搜索" in texts and any(n["class"] == "android.widget.EditText" for n in nodes)
            and ("搜索后浏览立得奖励" in texts or {"历史搜索", "搜索发现"} <= texts))


def is_search_results(nodes):
    if is_panel(nodes) or is_search_entry(nodes):
        return False
    fields = [n for n in nodes if n["class"] == "android.widget.EditText" and n["text"]]
    return (len(fields) == 1 and any(n["text"] == "搜索" for n in nodes)
            and any(n["class"] == "android.widget.Button" and re.search(r"[¥￥]\s*\d", n["text"])
                    and n["bounds"][1] >= fields[0]["bounds"][3] for n in nodes))


def search_and_wait(d, nodes):
    fields = [n for n in nodes if n["class"] == "android.widget.EditText"]
    buttons = [n for n in nodes if n["class"] == "android.widget.Button" and n["text"] == "搜索"]
    if not is_search_entry(nodes) or len(fields) != 1 or len(buttons) != 1:
        raise TaskSkipped("搜索页控件不明确，未开始浏览。")
    history = [n for n in nodes if n["text"] == "历史搜索"]
    discovery = [n for n in nodes if n["text"] == "搜索发现"]
    choices = []
    if len(history) == len(discovery) == 1:
        choices = [n for n in nodes if n["class"] == "android.view.View" and n["clickable"]
                   and re.fullmatch(r"[\w· -]{1,40}", n["text"]) and not SKIP.search(n["text"])
                   and history[0]["bounds"][3] <= (n["bounds"][1] + n["bounds"][3]) / 2
                   and n["bounds"][3] <= discovery[0]["bounds"][1]]
    if choices:
        choice = min(choices, key=lambda n: (n["bounds"][1], n["bounds"][0]))
        print("搜索：使用历史词条，等待商品结果。", flush=True)
        LOG.info("search_submit source=history")
        d.click(*center(d, choice["bounds"]))
    else:
        print("搜索：输入收纳盒，等待商品结果。", flush=True)
        d(className="android.widget.EditText").set_text("收纳盒")
        # 输入可能调起键盘并改变按钮位置，重新检查后再提交。
        nodes = read_screen(d)
        buttons = [n for n in nodes if n["class"] == "android.widget.Button" and n["text"] == "搜索"]
        if len(buttons) != 1:
            raise TaskSkipped("输入后无法确认搜索按钮。")
        LOG.info("search_submit source=input")
        d.click(*center(d, buttons[0]["bounds"]))
    for _ in range(5):
        if is_search_results(read_screen(d)):
            LOG.info("search_results_ready")
            return
        time.sleep(0.5)
    raise TaskSkipped("未确认商品搜索结果，未开始浏览，不重复搜索。")


def browse_and_return(d, seconds, search_required=False):
    for _ in range(5):
        nodes = read_screen(d)
        check_task_destination(nodes)
        if not is_panel(nodes):
            if is_search_entry(nodes):
                search_and_wait(d, nodes)
                break
            if not search_required or is_search_results(nodes):
                break
        time.sleep(0.5)
    else:
        if search_required:
            raise TaskSkipped("未识别到搜索页或商品搜索结果，未开始浏览。")
        raise TaskSkipped("点击后未进入浏览页。")
    width, height = d.window_size()
    LOG.info("browse_start seconds=%s", seconds)
    stop = Event()
    failures = []

    def monitor():
        try:
            while not stop.is_set():
                nodes = read_screen(d)
                check_task_destination(nodes)
                if is_panel(nodes):
                    stop.set()
                    return
                stop.wait(0.25)
        except Exception as exc:
            failures.append(exc)
            stop.set()

    watcher = Thread(target=monitor, daemon=True)
    watcher.start()
    swipes = 0
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline and not stop.is_set():
            # 完整页面检查在后台进行，避免无线查询打断连续滑动。
            info = d.info
            if not info.get("screenOn") or info.get("currentPackageName") != TB_APP:
                raise RuntimeError("浏览中屏幕已关闭或已离开淘宝，停止操作。")
            if stop.is_set():
                break
            d.swipe(width * 0.45, height * 0.76, width * 0.45, height * 0.35, 0.6)
            swipes += 1
    finally:
        stop.set()
        watcher.join(timeout=12)
    if watcher.is_alive():
        raise RuntimeError("验证检查未及时返回，已停止，请检查连接。")
    if failures:
        raise failures[0]
    LOG.info("browse_swipes_finished count=%s", swipes)
    return_to_tasks(d)


def visit_and_return(d, seconds, allow_alipay=False):
    allowed = (TB_APP, ALIPAY_APP) if allow_alipay else (TB_APP,)
    stop = Event()
    failures = []
    entered_at = [None]
    on_panel = [True]

    def monitor():
        try:
            while not stop.is_set():
                nodes = read_screen(d, allowed_packages=allowed)
                check_task_destination(nodes)
                on_panel[0] = is_panel(nodes)
                if not on_panel[0] and entered_at[0] is None:
                    entered_at[0] = time.monotonic()
                stop.wait(0.25)
        except Exception as exc:
            failures.append(exc)
            stop.set()

    watcher = Thread(target=monitor, daemon=True)
    watcher.start()
    launch_deadline = time.monotonic() + 15
    logged = False
    try:
        while True:
            if failures:
                raise failures[0]
            info = d.info
            package = info.get("currentPackageName")
            if not info.get("screenOn") or package not in allowed:
                raise RuntimeError("短停期间屏幕关闭或进入未允许的应用，停止操作。")
            now = time.monotonic()
            # 跨应用到达可快速确认，避免界面树查询延长支付宝停留。
            if package == ALIPAY_APP and entered_at[0] is None:
                entered_at[0] = now
            if entered_at[0] is not None:
                if not logged:
                    LOG.info("short_visit_start seconds=%s allow_alipay=%s", seconds, allow_alipay)
                    logged = True
                if now >= entered_at[0] + seconds:
                    break
            elif now >= launch_deadline:
                raise TaskSkipped("点击后未确认短停任务页面。")
            stop.wait(0.1)
        if failures:
            raise failures[0]
        stop.set()
        LOG.info("short_visit_return elapsed=%.3f", time.monotonic() - entered_at[0])
        # 到时先发出返回指令，后台查询的收尾不占用目标页面停留时间。
        if package == ALIPAY_APP:
            d.app_start(TB_APP, stop=False)
            resume_deadline = time.monotonic() + 5
            while d.info.get("currentPackageName") != TB_APP:
                if time.monotonic() >= resume_deadline:
                    raise RuntimeError("已发出返回指令，但淘宝未及时回到前台。")
                time.sleep(0.1)
        elif not on_panel[0]:
            d.press("back")
    finally:
        stop.set()
        watcher.join(timeout=12)
    if watcher.is_alive():
        raise RuntimeError("短停验证检查未及时返回，已停止，请检查连接。")
    if failures:
        raise failures[0]
    return_to_tasks(d)


def return_to_tasks(d):
    for _ in range(6):
        nodes = read_screen(d)
        if is_panel(nodes):
            return nodes
        buttons = [n for n in nodes if n["class"] == "android.widget.Button"
                   and n["text"] in {"去赚能量", "去赚体力"}]
        if len(buttons) == 1:
            d.click(*center(d, buttons[0]["bounds"]))
        else:
            d.press("back")
        time.sleep(2)
    raise RuntimeError("未能返回任务列表，已停止。请手动打开列表后重试。")


def run_tasks(d):
    attempted = set()
    empty_pages = 0
    started = time.monotonic()
    clicks = 0
    for _ in range(60):
        if time.monotonic() - started > 20 * 60:
            break
        # 每项之后重新预览，不复用上一屏的任务顺序或按钮坐标。
        nodes = return_to_tasks(d)
        tasks = task_candidates(nodes)
        pending = [t for t in tasks if (t["title"], t["progress"], t["action"]) not in attempted]
        print(f"自动预览：当前识别 {len(tasks)} 项，本轮可执行 {len(pending)} 项。", flush=True)
        LOG.info("auto_preview recognized=%s pending=%s", len(tasks), len(pending))
        chosen = pending[0] if pending else None
        if chosen is None:
            empty_pages += 1
            if empty_pages >= 4:
                print("连续多屏没有新的可执行任务，本轮结束。")
                break
            width, height = d.window_size()
            d.swipe(width * 0.5, height * 0.80, width * 0.5, height * 0.55, 0.5)
            time.sleep(2)
            continue
        empty_pages = 0
        key = (chosen["title"], chosen["progress"], chosen["action"])
        # 任务秒数可能出现在进度之后，使用整行文案解析。
        seconds = re.search(r"(\d+)\s*秒", chosen["details"])
        duration = max(18, int(seconds.group(1)) + 3) if seconds else 18
        print(f"{chosen['action']}：{chosen['title']} {chosen['progress']}", flush=True)
        LOG.info("task_start action=%s progress=%s title=%s", chosen["action"], chosen["progress"], chosen["title"])
        try:
            if chosen["action"] in {"浏览", "搜索后浏览"} and duration > 120:
                raise TaskSkipped("该任务等待时间超过两分钟。")
            if not click_task(d, chosen):
                print("任务列表已变化，未点击，自动重新扫描。", flush=True)
                continue
            attempted.add(key)
            clicks += 1
            if chosen["action"] == "跳转停留6秒":
                visit_and_return(d, 6, allow_alipay=True)
            elif chosen["action"] == "拍立淘停留6秒":
                visit_and_return(d, 6, allow_alipay=False)
            elif chosen["action"] != "领取":
                if chosen["action"] == "搜索后浏览":
                    browse_and_return(d, duration, search_required=True)
                else:
                    browse_and_return(d, duration)
            else:
                time.sleep(2)
        except TaskSkipped as exc:
            attempted.add(key)
            print(f"跳过：{exc} 自动返回列表，继续下一项。", flush=True)
            LOG.info("task_skipped reason=%s", exc)
            return_to_tasks(d)
            continue
        after = return_to_tasks(d)
        matches = []
        for n in after:
            progress = PROGRESS.search(n["text"])
            if progress and n["text"][:progress.start()].strip() == chosen["title"]:
                value = tuple(map(int, progress.groups()))
                if value[1] == chosen["progress"][1]:
                    matches.append(value)
        updated = matches[0] if len(matches) == 1 else None
        LOG.info("task_progress before=%s after=%s", chosen["progress"], updated)
        if updated is None:
            print("原任务已不在当前列表或文案已更新，自动重新预览。", flush=True)
        elif updated[0] <= chosen["progress"][0]:
            print("该项暂未增加进度，本轮先跳过，继续其他任务。", flush=True)
        else:
            print(f"进度已更新：{chosen['progress']} → {updated}，自动重新预览。", flush=True)
    print(f"本轮点击 {clicks} 次。点击次数不代表到账金币，请在淘宝核对奖励。")
    LOG.info("task_run_finished clicks=%s", clicks)


def game_state(nodes):
    buttons = [n for n in nodes if n["class"] == "android.widget.Button" and GAME.search(n["text"])]
    if len(buttons) != 1:
        raise RuntimeError("未识别到唯一的赛车/跳一跳按钮，请关闭弹窗、收起任务列表后重试。")
    match = ENERGY.search(buttons[0]["text"])
    if not match:
        raise RuntimeError("无法读取按钮上的剩余能量，本轮停止。")
    return buttons[0], int(match.group(1))


def upgrade_popup(nodes):
    labels = {"关闭", "关闭按钮", "关闭弹窗", "关闭弹层", "取消", "×", "✕", "✖", "x", "close"}
    closes = set()
    for n in nodes:
        if not (n["clickable"] or n["class"] in {"android.widget.Button", "android.widget.Image", "android.widget.ImageView"}):
            continue
        if (n["text"].lower() in labels or n["description"].lower() in labels
                or re.search(r"(?:^|[/_])(?:close|dismiss)(?:_btn|_button|_icon)?$", n["resource_id"], re.I)):
            closes.add(n["bounds"])
    pending = bool(closes) or any(UPGRADE_POPUP.search(n["text"] + " " + n["description"])
                                  or n["class"] == "android.app.Dialog" for n in nodes)
    return closes, pending


def prepare_upgrade(d):
    tried = set()
    backed = False
    handled = False
    # 最多四次关闭/恢复操作，每次之后重新读屏确认。
    for attempt in range(5):
        nodes = read_screen(d)
        if is_panel(nodes):
            raise RuntimeError("请先收起任务列表，回到显示剩余能量的赛车页面。")
        closes, pending = upgrade_popup(nodes)
        if not pending:
            if handled and not any(n["class"] == "android.widget.Button" and GAME.search(n["text"]) for n in nodes):
                # 关闭或返回退出活动时，只通过明确的淘金币入口恢复原页面。
                entries = [n for n in nodes if n["clickable"] and
                           (n["text"] in {"领淘金币", "淘金币"} or re.fullmatch(r"淘金币抵(?:[¥￥]\d+(?:\.\d+)?)?", n["text"]))]
                if not entries:
                    entries = [n for n in nodes if n["clickable"] and n["text"] == "我的淘宝"]
                if len(entries) == 1 and entries[0]["text"] not in tried and attempt < 4:
                    tried.add(entries[0]["text"])
                    LOG.info("upgrade_reopen entry=%s", entries[0]["text"])
                    d.click(*center(d, entries[0]["bounds"]))
                    time.sleep(1)
                    continue
                raise RuntimeError("弹窗返回后未确认赛车页面，已停止升级。")
            button, energy = game_state(nodes)
            return button, energy, handled
        if attempt == 4:
            break
        point = None
        if len(closes) == 1:
            bounds = next(iter(closes))
            if bounds not in tried:
                try:
                    point = center(d, bounds)
                except TaskSkipped:
                    pass  # 不完整的关闭按钮改用返回键。
        if point is not None:
            tried.add(bounds)
            LOG.info("upgrade_popup_close bounds=%s", bounds)
            print("关闭升级弹窗，确认页面后继续。", flush=True)
            d.click(*point)
        elif not backed:
            backed = True
            LOG.info("upgrade_popup_back")
            print("未找到可用的关闭按钮，返回赛车页面后继续。", flush=True)
            d.press("back")
        else:
            break
        handled = True
        time.sleep(1)
    raise RuntimeError("弹窗关闭或页面恢复未成功，已停止，避免重复点击。")


def upgrade(d):
    previous = None
    unchanged = 0
    for _ in range(60):
        button, energy, handled = prepare_upgrade(d)
        if handled:
            previous, unchanged = None, 0
        print(f"剩余能量/体力：{energy}", flush=True)
        LOG.info("upgrade_energy remaining=%s", energy)
        if energy < 10:
            print("剩余不足 10，结束升级。")
            return
        unchanged = unchanged + 1 if previous is not None and energy >= previous else 0
        if unchanged >= 3:
            raise RuntimeError("连续三轮能量没有减少，已停止。请检查是否被弹窗遮挡。")
        previous = energy
        x, y = center(d, button["bounds"])
        if energy >= 50:
            d.long_click(x, y, 3)
        else:
            d.click(x, y)
        for _ in range(5):
            time.sleep(1)
            if upgrade_popup(read_screen(d))[1]:
                break
    print("已达到 60 轮上限，请手动核对活动进度。")


def main():
    parser = argparse.ArgumentParser(description="2026 淘宝金币任务助手；默认预览，Ctrl+C 停止。")
    parser.add_argument("--serial", help="adb devices 中的设备序列号，多设备时必填")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="自动执行可识别的浏览、搜索及短停任务，每项后重新扫描")
    mode.add_argument("--upgrade", action="store_true", help="在赛车首页消耗活动能量/体力")
    args = parser.parse_args()
    configure_logging()
    LOG.info("run_start mode=%s", "tasks" if args.run else "upgrade" if args.upgrade else "preview")
    try:
        import adbutils
        import uiautomator2 as u2
    except ImportError as exc:
        raise RuntimeError("缺少依赖，请先运行 install.cmd 创建本目录的独立环境。") from exc
    devices = adbutils.adb.device_list()
    serials = [device.serial for device in devices]
    LOG.info("connected_device_count=%s", len(serials))
    if args.serial:
        if args.serial not in serials:
            raise RuntimeError("指定设备未连接或未授权，请检查 adb devices。")
        serial = args.serial
    elif len(serials) == 1:
        serial = serials[0]
    else:
        raise RuntimeError(f"需连接并授权一台安卓设备。当前设备：{serials}；多设备请加 --serial。")
    d = u2.connect_usb(serial)
    print(f"已连接：{serial}。请保持手机解锁，Ctrl+C 可停止。", flush=True)
    if args.run:
        run_tasks(d)
    elif args.upgrade:
        upgrade(d)
    else:
        nodes = read_screen(d)
        if not is_panel(nodes):
            raise RuntimeError("预览前，请手动进入淘宝的金币任务列表。")
        tasks = task_candidates(nodes)
        for task in tasks:
            print(f"将{task['action']}：{task['title']} {task['progress']}")
        print(f"当前屏幕识别 {len(tasks)} 项；预览未点击。执行请加 --run。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        LOG.info("stopped_by_keyboard")
        print("\n已按 Ctrl+C 停止。")
    except Exception as exc:
        LOG.error("run_stopped reason=%s", exc)
        print(f"停止：{exc}")
        raise SystemExit(1)
