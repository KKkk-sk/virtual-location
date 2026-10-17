"""Android 虚拟定位核心 —— 通过 adb 驱动开源的 ADB Mock GPS。

原理（Android 的硬性规则）：
    Android 不允许普通程序直接改定位，必须有一个 App 扮演"模拟位置提供者"。
    所以本工具用 adb 安装 ADB Mock GPS（开源，minSdk 31 → Android 12 及以上都能用），
    再把它的 mock_location 权限打开，之后发一条广播就能改坐标：

        adb shell am broadcast -a com.adbmockgps.SET_LOCATION \
            --es lat "35.1595" --es lon "126.8526" -f 0x01000000

    比 iOS 省事的地方：
      - 定位一旦设上就**一直保持**，断开数据线也不影响（App 在前台服务里持续推送）；
      - 可以用 GET_LOCATION **回读**当前坐标，确认真的生效了。

    `-f 0x01000000` 是 FLAG_INCLUDE_STOPPED_PACKAGES，少了它广播到不了未运行的 App，
    这是这套方案最常见的坑。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
APK_PATH = PROJECT_DIR / "vendor" / "ADB.Mock.GPS-release.apk"
FAVORITES_PATH = PROJECT_DIR / "favorites.json"

PACKAGE = "com.adbmockgps"
ACTION_SET = "com.adbmockgps.SET_LOCATION"
ACTION_GET = "com.adbmockgps.GET_LOCATION"
BROADCAST_FLAGS = ["-f", "0x01000000"]

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# 常见坐标，方便直接试
PRESETS: list[tuple[str, float, float]] = [
    ("电子科大清水河·田径场", 30.7462577, 103.9292042),
    ("成都·天府广场", 30.65700, 104.06600),
    ("北京·天安门", 39.90750, 116.39139),
    ("上海·外滩", 31.23966, 121.49000),
    ("广州·珠江新城", 23.12190, 113.32440),
    ("深圳·市民中心", 22.54460, 114.05780),
    ("杭州·西湖", 30.24280, 120.14910),
    ("香港·中环", 22.28190, 114.15800),
    ("台北·101", 25.03390, 121.56540),
    ("东京·涩谷", 35.65800, 139.70160),
    ("首尔·光州", 35.15950, 126.85260),
    ("纽约·时代广场", 40.75800, -73.98550),
    ("伦敦·大本钟", 51.50070, -0.12460),
    ("巴黎·埃菲尔铁塔", 48.85840, 2.29450),
]


class AdbError(RuntimeError):
    """adb 操作失败。"""


# --------------------------------------------------------------------------- #
# adb 定位与调用
# --------------------------------------------------------------------------- #
def adb_path() -> Path:
    """优先用安装脚本下载到 runtime 里的 adb，其次找 vendor，最后找 PATH。"""
    for candidate in (
        PROJECT_DIR / "runtime" / "platform-tools" / "adb.exe",
        PROJECT_DIR / "vendor" / "platform-tools" / "adb.exe",
    ):
        if candidate.exists():
            return candidate
    import shutil

    found = shutil.which("adb")
    if found:
        return Path(found)
    raise AdbError(
        "找不到 adb。请先双击运行「安装.bat」，它会自动下载 Android 平台工具。"
    )


def run_adb(args: list[str], timeout: float = 30, serial: str | None = None) -> tuple[int, str]:
    """执行一条 adb 命令，返回 (退出码, 输出)。"""
    command = [str(adb_path())]
    if serial:
        command += ["-s", serial]
    command += args
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return 124, f"adb 命令超时（{timeout:.0f} 秒）"
    except OSError as exc:
        return 127, f"无法启动 adb：{exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


# --------------------------------------------------------------------------- #
# 设备
# --------------------------------------------------------------------------- #
@dataclass
class Device:
    serial: str
    state: str
    model: str = ""

    def label(self) -> str:
        return f"{self.model or self.serial}（{self.state}）"


def list_devices() -> tuple[list[Device], str]:
    """返回 (在线设备列表, 出错原因)。只返回 state=device 的可用设备。"""
    code, output = run_adb(["devices", "-l"], timeout=25)
    if code != 0:
        return [], output or "adb 执行失败"

    devices: list[Device] = []
    for line in output.splitlines()[1:]:          # 第一行是 "List of devices attached"
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial, state = parts[0], parts[1] if len(parts) > 1 else "unknown"
        model = ""
        for part in parts[2:]:
            if part.startswith("model:"):
                model = part.split(":", 1)[1]
        if state == "device":
            devices.append(Device(serial, state, model))

    if devices:
        return devices, ""

    # 区分几种常见的"连了但不可用"
    if "unauthorized" in output:
        return [], "手机还没授权这台电脑：解锁手机，在「允许 USB 调试吗？」弹窗里点「允许」。"
    if "offline" in output:
        return [], "设备处于 offline 状态：拔掉数据线重插，或在手机上撤销 USB 调试授权后重新允许。"
    return [], (
        "没检测到安卓设备。请确认：\n"
        "  ① 数据线已连接（要能传数据，不能是纯充电线）；\n"
        "  ② 手机已开启「开发者选项 → USB 调试」；\n"
        "  ③ 手机上弹出「允许 USB 调试」时点了「允许」。"
    )


def describe_device(serial: str) -> Device:
    devices, _ = list_devices()
    for device in devices:
        if device.serial == serial:
            return device
    raise AdbError("指定的设备不在线")


# --------------------------------------------------------------------------- #
# 安装与授权
# --------------------------------------------------------------------------- #
def is_app_installed(serial: str | None = None) -> bool:
    code, output = run_adb(["shell", "pm", "list", "packages"], timeout=25, serial=serial)
    return code == 0 and PACKAGE in output


def install_app(serial: str | None = None) -> tuple[int, str]:
    """安装 ADB Mock GPS（已装则覆盖安装，不影响数据）。"""
    if not APK_PATH.exists():
        raise AdbError(f"找不到 APK：{APK_PATH}\n请确认 vendor 目录里有 ADB.Mock.GPS-release.apk")
    return run_adb(["install", "-r", str(APK_PATH)], timeout=180, serial=serial)


def grant_permissions(serial: str | None = None) -> list[str]:
    """授予定位、通知权限，并把本 App 设为模拟位置提供者。

    appops 设 mock_location 等效于在开发者选项里手动选「选择模拟位置信息应用」，
    所以这一步可以完全自动化，不需要用户去点菜单。
    """
    steps = [
        (["shell", "pm", "grant", PACKAGE, "android.permission.ACCESS_FINE_LOCATION"], "定位权限"),
        (["shell", "pm", "grant", PACKAGE, "android.permission.ACCESS_COARSE_LOCATION"], "粗略定位权限"),
        (["shell", "pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS"], "通知权限"),
        (["shell", "appops", "set", PACKAGE, "android:mock_location", "allow"], "模拟位置权限"),
    ]
    results = []
    for args, name in steps:
        code, output = run_adb(args, timeout=30, serial=serial)
        ok = code == 0 or "Unknown operation" in output or "already" in output.lower()
        results.append(f"  {'OK ' if ok else '跳过'} {name}" + ("" if ok else f"：{output[:60]}"))
    return results


def setup_device(serial: str | None = None) -> list[str]:
    """一键准备：装 App → 授权 → 设为模拟位置应用。"""
    log: list[str] = []
    log.append("1) 检查 App 是否已安装…")
    if is_app_installed(serial):
        log.append("   已安装，跳过")
    else:
        log.append("   正在安装 ADB Mock GPS…")
        code, output = install_app(serial)
        log.append(f"   adb install 退出码 {code}")
        if code != 0:
            log.append(f"   输出：{output[:200]}")
            raise AdbError("安装 APK 失败。请确认手机允许安装未知来源应用，或手动在手机上安装 vendor 目录里的 APK。")
    log.append("2) 授予权限并设为模拟位置应用…")
    log.extend(grant_permissions(serial))
    log.append("3) 完成。")
    return log


# --------------------------------------------------------------------------- #
# 设置 / 读取 / 停止
# --------------------------------------------------------------------------- #
_LOCATION_RE = re.compile(r'data="?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:,\s*(-?\d+(?:\.\d+)?))?')


def set_location(latitude: float, longitude: float, altitude: float | None = None,
                 serial: str | None = None) -> tuple[int, str]:
    """下发一个模拟坐标。"""
    if not -90 <= latitude <= 90:
        raise AdbError(f"纬度必须在 -90~90 之间，收到 {latitude}")
    if not -180 <= longitude <= 180:
        raise AdbError(f"经度必须在 -180~180 之间，收到 {longitude}")

    args = ["shell", "am", "broadcast", "-a", ACTION_SET,
            "--es", "lat", f"{latitude:.7f}",
            "--es", "lon", f"{longitude:.7f}"]
    if altitude is not None:
        args += ["--es", "alt", f"{altitude:.1f}"]
    args += BROADCAST_FLAGS
    return run_adb(args, timeout=30, serial=serial)


def get_location(serial: str | None = None) -> tuple[float, float, float] | None:
    """回读当前模拟坐标。读不到返回 None。"""
    args = ["shell", "am", "broadcast", "-a", ACTION_GET, *BROADCAST_FLAGS]
    code, output = run_adb(args, timeout=30, serial=serial)
    if code != 0:
        return None
    match = _LOCATION_RE.search(output)
    if not match:
        return None
    latitude = float(match.group(1))
    longitude = float(match.group(2))
    altitude = float(match.group(3)) if match.group(3) else 0.0
    return latitude, longitude, altitude


def set_location_verified(latitude: float, longitude: float, serial: str | None = None,
                          attempts: int = 3) -> tuple[float, float]:
    """设置并回读校验，返回设备实际生效的坐标。

    这是 Android 版比 iOS 版强的一点：能确认真的生效了，而不是只看命令有没有报错。
    """
    last_output = ""
    for _ in range(attempts):
        code, output = set_location(latitude, longitude, serial=serial)
        last_output = output
        if code != 0:
            raise AdbError(f"下发坐标失败：{output[:300]}")
        confirmed = get_location(serial)
        if confirmed is not None:
            return confirmed[0], confirmed[1]
        time.sleep(0.4)
    raise AdbError(
        "已发出坐标但读不回结果，可能是 App 被系统停用了。\n"
        "请在手机上打开一次 ADB Mock GPS，确认它在运行，并允许通知。\n"
        f"最近输出：{last_output[:300]}"
    )


def stop_mock(serial: str | None = None) -> tuple[int, str]:
    """停止模拟：强制停止 App 并收回模拟位置权限，手机回到真实定位。"""
    run_adb(["shell", "appops", "set", PACKAGE, "android:mock_location", "deny"], timeout=25, serial=serial)
    return run_adb(["shell", "am", "force-stop", PACKAGE], timeout=25, serial=serial)


def appops_status(serial: str | None = None) -> str:
    code, output = run_adb(["shell", "appops", "get", PACKAGE, "android:mock_location"],
                           timeout=25, serial=serial)
    return output or f"查询失败（退出码 {code}）"


# --------------------------------------------------------------------------- #
# 校园跑模式
# --------------------------------------------------------------------------- #
class TrackRunner:
    """在后台线程里按配速沿跑道持续推送坐标。

    Android 端没有常驻守护进程的必要：每个点都是独立的 adb 广播，
    而且 App 会一直保持最后一次设的坐标。
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.distance_m = 0.0
        self.laps_done = 0.0
        self.last_position: tuple[float, float] | None = None
        self.error: str | None = None
        self._pace = 0.0
        self._lane = 1
        self._lap_length = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, pace_sec_per_km: float, laps: float | None = None, lane: int = 1,
              reverse: bool = False, interval: float = 1.0,
              center: tuple[float, float] | None = None,
              bearing_deg: float | None = None, serial: str | None = None) -> None:
        from track import UESTC_TRACK

        if self.running:
            raise AdbError("校园跑已经在运行")

        track = UESTC_TRACK
        if center or bearing_deg is not None or lane != 1:
            from track import Track

            track = Track(
                center_lat=center[0] if center else UESTC_TRACK.center_lat,
                center_lon=center[1] if center else UESTC_TRACK.center_lon,
                bearing_deg=bearing_deg if bearing_deg is not None else UESTC_TRACK.bearing_deg,
                lane=lane,
            )

        self._stop.clear()
        self.error = None
        self.distance_m = 0.0
        self.laps_done = 0.0
        self._pace = pace_sec_per_km
        self._lane = lane
        self._lap_length = track.lap_length_for_lane
        speed = 1000.0 / pace_sec_per_km

        def worker() -> None:
            started = time.monotonic()
            lap_limit = float(laps) if laps else None
            try:
                while not self._stop.is_set():
                    elapsed = time.monotonic() - started
                    distance = speed * elapsed
                    if reverse:
                        distance = -distance
                    if lap_limit is not None and abs(distance) >= lap_limit * self._lap_length:
                        distance = lap_limit * self._lap_length * (-1 if reverse else 1)
                        latitude, longitude = track.position(distance)
                        set_location(latitude, longitude, serial=serial)
                        self.last_position = (latitude, longitude)
                        self.distance_m = abs(distance)
                        self.laps_done = lap_limit
                        return
                    latitude, longitude = track.position(distance)
                    set_location(latitude, longitude, serial=serial)
                    self.last_position = (latitude, longitude)
                    self.distance_m = abs(distance)
                    self.laps_done = abs(distance) / self._lap_length
                    self._stop.wait(interval)
            except Exception as exc:  # noqa: BLE001 - 线程内异常要能反馈给界面
                self.error = f"{type(exc).__name__}: {exc}"

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def status(self) -> dict:
        return {
            "running": self.running,
            "distance_m": round(self.distance_m, 1),
            "laps_done": round(self.laps_done, 3),
            "lap_length_m": round(self._lap_length, 1),
            "pace_sec_per_km": self._pace,
            "lane": self._lane,
            "lat": self.last_position[0] if self.last_position else None,
            "lon": self.last_position[1] if self.last_position else None,
            "error": self.error,
        }


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
    FAVORITES_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_favorite(name: str, latitude: float, longitude: float) -> list[dict]:
    items = [d for d in load_favorites() if d.get("name") != name]
    items.append({"name": name, "lat": latitude, "lon": longitude})
    items.sort(key=lambda d: str(d.get("name", "")))
    save_favorites(items)
    return items
