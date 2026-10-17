"""虚拟定位核心逻辑 —— 封装 pymobiledevice3 的命令行。

三条关键事实（读源码 + 官方 --help 实测确认，不是猜的）：

1. iOS 17+ 的定位模拟走 DVT 服务，必须要有一条到手机的 RSD 隧道。
   `--userspace` 是纯 Python 实现的用户态隧道，官方原文 "NO root/admin is required"，
   所以本工具**不需要管理员权限**，也不需要装 Xcode（Windows 上本来也没有）。

2. `simulate-location set` 设完坐标后会停在 `input("Press ENTER to exit>")`
   （见 pymobiledevice3/osu/win_util.py）。所以这里把它当"长驻会话"用：
   **进程活着 = 正在模拟；杀掉进程 = 恢复真实定位**。

3. Windows 上必须先装苹果的 Apple Mobile Device 驱动（装 iTunes 会带），
   否则 usbmuxd 连不上，所有命令都会报 "Failed to connect to usbmuxd socket"。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
FAVORITES_PATH = PROJECT_DIR / "favorites.json"

# 不弹黑框（Windows）
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 内置坐标，省得每次去查
PRESETS: list[tuple[str, float, float]] = [
    ("北京 · 天安门", 39.90750, 116.39139),
    ("上海 · 外滩", 31.23966, 121.49000),
    ("广州 · 珠江新城", 23.12190, 113.32440),
    ("深圳 · 市民中心", 22.54460, 114.05780),
    ("杭州 · 西湖", 30.24280, 120.14910),
    ("成都 · 天府广场", 30.65700, 104.06600),
    ("西安 · 钟楼", 34.26100, 108.94230),
    ("重庆 · 解放碑", 29.55770, 106.57710),
    ("香港 · 中环", 22.28190, 114.15800),
    ("台北 · 101", 25.03390, 121.56540),
    ("东京 · 涩谷", 35.65800, 139.70160),
    ("首尔 · 江南", 37.49790, 127.02760),
    ("新加坡 · 滨海湾", 1.28310, 103.86040),
    ("纽约 · 时代广场", 40.75800, -73.98550),
    ("洛杉矶 · 好莱坞", 34.09280, -118.32870),
    ("伦敦 · 大本钟", 51.50070, -0.12460),
    ("巴黎 · 埃菲尔铁塔", 48.85840, 2.29450),
    ("悉尼 · 歌剧院", -33.85680, 151.21530),
    ("迪拜 · 哈利法塔", 25.19720, 55.27440),
    ("莫斯科 · 红场", 55.75390, 37.62040),
]


class LocationError(RuntimeError):
    """定位操作失败（消息会直接显示给用户）。"""


# --------------------------------------------------------------------------- #
# 进程调用
# --------------------------------------------------------------------------- #
def python_exe() -> str:
    """优先用项目自带 venv 的解释器，保证 pymobiledevice3 一定在。"""
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONHOME", None)
    return env


def _base_cmd() -> list[str]:
    return [python_exe(), "-m", "pymobiledevice3"]


def run(args: list[str], timeout: float = 60) -> tuple[int, str]:
    """执行一条 pymobiledevice3 子命令，返回 (退出码, 合并后的输出)。"""
    try:
        proc = subprocess.run(
            _base_cmd() + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_child_env(),
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（{timeout:.0f} 秒未完成）：{' '.join(args)}"
    except OSError as exc:
        return 127, f"无法启动 Python 进程：{exc}"
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def _tunnel_options(udid: str | None) -> list[str]:
    """需要 RSD 隧道的命令用这组选项。"""
    opts = ["--userspace"]
    if udid:
        opts += ["--udid", udid]
    return opts


def _classic_options(udid: str | None) -> list[str]:
    """走经典 lockdown 的命令（如 amfi）用这组，不要加隧道参数。"""
    return ["--udid", udid] if udid else []


def explain_error(text: str) -> str:
    """把工具报错翻译成人话 + 下一步该干什么。"""
    low = text.lower()
    if "usbmuxd" in low:
        return "连不上苹果手机驱动（usbmuxd）。请先安装 iTunes 或 Apple Devices，再插线连接手机。"
    if "passcode" in low:
        return (
            "手机设了锁屏密码，命令行无法代你开启开发者模式（苹果的安全限制）。\n"
            "请在手机上手动操作：设置 → 隐私与安全性 → 开发者模式 → 打开 → 重启手机 → 解锁后确认。"
        )
    if "developer mode" in low or "developermode" in low:
        return "iPhone 的开发者模式没开：设置 → 隐私与安全性 → 开发者模式 → 打开并重启手机。"
    if "mount" in low and ("image" in low or "ddi" in low):
        return "开发者镜像没挂载。点「挂载开发者镜像」后再试。"
    if "trust" in low or "pair" in low or "pairing" in low:
        return "手机没信任这台电脑：解锁手机，在弹出的对话框点「信任」并输入锁屏密码。"
    if "no device" in low or "not found" in low or "unable to find" in low:
        return "没检测到设备。检查数据线、手机是否解锁、是否点过「信任」。"
    if "unauthorized" in low or "lockdown" in low:
        return "设备未授权或未解锁，请解锁手机后重试。"
    return ""


# --------------------------------------------------------------------------- #
# 设备
# --------------------------------------------------------------------------- #
@dataclass
class Device:
    udid: str
    name: str = ""
    version: str = ""
    model: str = ""

    def label(self) -> str:
        head = self.name or "iPhone"
        if self.version:
            head += f" · iOS {self.version}"
        if self.model:
            head += f"（{self.model}）"
        return head


def _extract_json(text: str):
    """输出里夹着日志行，抠出第一个完整的 JSON 数组/对象。"""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def list_devices() -> tuple[list[Device], str]:
    """返回 (设备列表, 出错原因)。列表非空时原因为空字符串。"""
    code, output = run(["usbmux", "list"], timeout=45)
    payload = _extract_json(output)

    devices: list[Device] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                devices.append(Device(udid=item))
            elif isinstance(item, dict):
                udid = item.get("UniqueDeviceID") or item.get("SerialNumber") or ""
                if udid:
                    devices.append(
                        Device(
                            udid=udid,
                            name=str(item.get("DeviceName") or ""),
                            version=str(item.get("ProductVersion") or ""),
                            model=str(item.get("ProductType") or ""),
                        )
                    )

    if devices:
        return devices, ""

    if "usbmuxd" in output.lower():
        return [], (
            "连不上 usbmuxd（苹果设备驱动）。\n\n"
            "需要先装驱动：\n"
            "  winget install --id Apple.iTunes --source winget\n\n"
            "装完插上数据线，解锁 iPhone 并点「信任」。"
        )
    if code != 0:
        return [], output[:800] or f"命令执行失败（退出码 {code}）"
    return [], "未检测到设备。请确认：① 数据线已连接；② 手机已解锁；③ 点过「信任」。"


# --------------------------------------------------------------------------- #
# 开发者模式 / 开发者镜像
# --------------------------------------------------------------------------- #
def developer_mode_status(udid: str | None = None) -> tuple[bool | None, str]:
    """查询开发者模式。返回 (状态, 原始输出)，状态解析不出来时给 None。"""
    _, output = run(["amfi", "developer-mode-status", *_classic_options(udid)], timeout=60)
    low = output.lower()
    if "true" in low:
        return True, output
    if "false" in low:
        return False, output
    return None, output


def reveal_developer_mode(udid: str | None = None) -> tuple[int, str]:
    """让「开发者模式」开关出现在设置里（新手机第一次需要）。"""
    return run(["amfi", "reveal-developer-mode", *_classic_options(udid)], timeout=60)


def enable_developer_mode(udid: str | None = None) -> tuple[int, str]:
    code, output = reveal_developer_mode(udid)
    code2, output2 = run(["amfi", "enable-developer-mode", *_classic_options(udid)], timeout=90)
    return code2 or code, (output + "\n" + output2).strip()


def mount_developer_image(udid: str | None = None, timeout: float = 300) -> tuple[int, str]:
    """挂载 DeveloperDiskImage（首次用 DVT 服务前必须做，可能会下载镜像）。"""
    return run(["mounter", "auto-mount", *_tunnel_options(udid)], timeout=timeout)


# --------------------------------------------------------------------------- #
# 定位模拟会话
# --------------------------------------------------------------------------- #
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

DAEMON_SCRIPT = PROJECT_DIR / "loc_daemon.py"
DAEMON_LOG = PROJECT_DIR / "logs" / "loc_daemon.log"
DAEMON_PORT = int(os.environ.get("FAKEGPS_DAEMON_PORT", "47823"))


def _daemon_request(payload: dict, timeout: float = 20.0) -> dict | None:
    """向守护进程发一条 JSON 请求；连不上或解析失败返回 None。"""
    try:
        with socket.create_connection(("127.0.0.1", DAEMON_PORT), timeout=timeout) as sock:
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            sock.settimeout(timeout)
            buffer = b""
            while not buffer.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
        return json.loads(buffer.decode("utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return None


def _daemon_ping(timeout: float = 2.0) -> dict | None:
    return _daemon_request({"cmd": "ping"}, timeout=timeout)


def daemon_running() -> bool:
    """守护进程是否在跑且隧道已就绪。"""
    ping = _daemon_ping(timeout=1.5)
    return bool(ping and ping.get("ready"))


def daemon_status() -> dict | None:
    return _daemon_ping(timeout=2.0)


def _log_tail(lines: int = 12) -> str:
    try:
        content = DAEMON_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])
    except OSError:
        return ""


def start_daemon(udid: str | None = None) -> None:
    """拉起守护进程。刻意脱离当前终端/父进程，关掉命令行窗口也不会影响它。"""
    DAEMON_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(DAEMON_LOG, "a", encoding="utf-8")
    cmd = [python_exe(), str(DAEMON_SCRIPT), "--port", str(DAEMON_PORT)]
    if udid:
        cmd += ["--udid", udid]
    subprocess.Popen(
        cmd,
        cwd=str(PROJECT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        close_fds=True,
        env=_child_env(),
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )


def ensure_daemon(udid: str | None = None, wait: float = 90.0) -> tuple[bool, str]:
    """确保守护进程在跑且隧道就绪。首次约十几秒，之后就一直是秒开。"""
    ping = _daemon_ping()
    if ping and ping.get("ready"):
        return True, ""
    if ping is None:  # 完全连不上 => 还没起来
        start_daemon(udid)

    deadline = time.time() + wait
    while time.time() < deadline:
        ping = _daemon_ping(timeout=2.0)
        if ping and ping.get("ready"):
            return True, ""
        time.sleep(0.5)

    detail = _log_tail()
    return False, (
        "守护进程启动超时。常见原因：设备未连接/未信任、开发者模式未开、开发者镜像未挂载。\n"
        f"日志：{DAEMON_LOG}" + (f"\n\n--- 最近日志 ---\n{detail}" if detail else "")
    )


def stop_daemon() -> tuple[int, str]:
    response = _daemon_request({"cmd": "quit"}, timeout=10)
    if response and response.get("ok"):
        return 0, "守护进程已退出"
    return 1, "守护进程未在运行"


class LocationSession:
    """通过常驻守护进程控制定位。

    隧道和 DVT 会话由 loc_daemon.py **只建立一次**，之后切换坐标只是给它发一条命令
    （实测 50-70 毫秒）。中间没有任何空窗，所以不会再出现"切完掉回真实位置"的情况。
    """

    def __init__(self) -> None:
        self.latitude: float | None = None
        self.longitude: float | None = None
        self.udid: str | None = None

    # -- 状态 --------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.latitude is not None and daemon_running()

    def output(self) -> str:
        return json.dumps(daemon_status() or {}, ensure_ascii=False)

    # -- 公开接口 ----------------------------------------------------------
    def start(self, latitude: float, longitude: float, udid: str | None = None,
              settle: float = 90.0) -> None:
        """设置并保持位置。settle 是首次启动守护进程的等待上限。"""
        if not -90 <= latitude <= 90:
            raise LocationError(f"纬度必须在 -90~90 之间，收到 {latitude}")
        if not -180 <= longitude <= 180:
            raise LocationError(f"经度必须在 -180~180 之间，收到 {longitude}")

        ok, message = ensure_daemon(udid, wait=settle)
        if not ok:
            raise LocationError(message)

        response = _daemon_request({"cmd": "set", "lat": latitude, "lon": longitude}, timeout=25)
        if not response or not response.get("ok"):
            raise LocationError(f"设置失败：{(response or {}).get('error', '守护进程无响应')}")

        self.latitude, self.longitude, self.udid = latitude, longitude, udid

    def stop(self, clear: bool = True, udid: str | None = None) -> tuple[int, str]:
        """clear=True 只清除坐标（守护进程留着，下次切换仍是秒开）；False 则整个退出。"""
        self.latitude = self.longitude = None
        if not clear:
            return stop_daemon()
        return clear_location(udid or self.udid)


def clear_location(udid: str | None = None, timeout: float = 30) -> tuple[int, str]:
    """清除模拟定位，让手机回到真实坐标。优先走守护进程，没起来就退回一次性命令。"""
    if daemon_running():
        response = _daemon_request({"cmd": "clear"}, timeout=timeout)
        if response and response.get("ok"):
            return 0, "已清除模拟定位（守护进程保持运行，下次切换仍秒开）"
        return 1, json.dumps(response or {}, ensure_ascii=False)
    return run(
        ["developer", "dvt", "simulate-location", "clear", *_tunnel_options(udid)],
        timeout=timeout,
    )


# --------------------------------------------------------------------------- #
# 校园跑模式
# --------------------------------------------------------------------------- #
def start_track_run(
    pace_sec_per_km: float,
    laps: float | None = None,
    lane: int = 1,
    reverse: bool = False,
    interval: float = 1.0,
    center: tuple[float, float] | None = None,
    bearing_deg: float | None = None,
    udid: str | None = None,
    settle: float = 90.0,
) -> dict:
    """启动校园跑模式：沿 400 m 跑道按配速循环移动位置。

    移动由守护进程内的一个异步循环完成（每秒推一次新坐标），
    所以这个函数发完命令就返回，关掉终端也不会中断跑步。
    """
    from track import UESTC_TRACK

    ok, message = ensure_daemon(udid, wait=settle)
    if not ok:
        raise LocationError(message)

    payload = {
        "cmd": "track",
        "center_lat": float(center[0] if center else UESTC_TRACK.center_lat),
        "center_lon": float(center[1] if center else UESTC_TRACK.center_lon),
        "bearing_deg": float(
            bearing_deg if bearing_deg is not None else UESTC_TRACK.bearing_deg
        ),
        "pace_sec_per_km": float(pace_sec_per_km),
        "lane": int(lane),
        "reverse": bool(reverse),
        "interval": float(interval),
    }
    if laps:
        payload["laps"] = float(laps)

    response = _daemon_request(payload, timeout=30)
    if not response or not response.get("ok"):
        raise LocationError(f"启动校园跑失败：{(response or {}).get('error', '守护进程无响应')}")
    return response


def stop_track_run() -> tuple[int, str]:
    """停止校园跑（位置停在当前点，不会跳回原点）。"""
    response = _daemon_request({"cmd": "stoprun"}, timeout=15)
    if response and response.get("ok"):
        return 0, "校园跑已停止（位置停在当前点）"
    return 1, "校园跑未在运行"


# --------------------------------------------------------------------------- #
# 收藏夹
# --------------------------------------------------------------------------- #
def load_favorites() -> list[dict]:
    if not FAVORITES_PATH.exists():
        return []
    try:
        data = json.loads(FAVORITES_PATH.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_favorites(items: list[dict]) -> None:
    FAVORITES_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def upsert_favorite(name: str, latitude: float, longitude: float) -> list[dict]:
    """同名覆盖，否则追加。"""
    items = [d for d in load_favorites() if d.get("name") != name]
    items.append({"name": name, "lat": latitude, "lon": longitude})
    items.sort(key=lambda d: str(d.get("name", "")))
    save_favorites(items)
    return items


def remove_favorite(name: str) -> list[dict]:
    items = [d for d in load_favorites() if d.get("name") != name]
    save_favorites(items)
    return items


def doctor_report() -> str:
    """传输层体检，出问题时用来看清楚缺什么。"""
    return run(["doctor"], timeout=90)[1]
