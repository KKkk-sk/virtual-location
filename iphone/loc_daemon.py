"""常驻定位守护进程 —— 隧道和 DVT 会话只建立一次，之后切换坐标是毫秒级。

为什么需要它：
    原来每次设置位置都要起一个新的 `pymobiledevice3 ... simulate-location set` 进程，
    它要重新建立用户态隧道并连接 DVT（10-20 秒）。这段空窗期里手机没有任何模拟在生效，
    于是立刻掉回真实定位 —— 表现就是"切换慢、定位不稳、老是回到成都"。
    改成常驻进程后，隧道只建一次，切换只剩一次 DTX 调用。

协议：监听 127.0.0.1:<port>，一行一个 JSON 请求，回一行 JSON。
    {"cmd": "ping"}                    -> {"ok": true, "ready": true, "lat": .., "lon": ..}
    {"cmd": "set", "lat": 35.1, "lon": 126.8} -> {"ok": true}
    {"cmd": "clear"}                   -> {"ok": true}
    {"cmd": "quit"}                    -> {"ok": true}，随后进程退出

另外每 --keepalive 秒会把当前坐标重发一次，防止设备侧意外丢失模拟状态。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any, Optional

DEFAULT_PORT = 47823
DEFAULT_KEEPALIVE = 20.0


def log(message: str) -> None:
    """守护进程的日志走 stderr（客户端会把它重定向到 logs/loc_daemon.log）。"""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


class LocationDaemon:
    def __init__(self, port: int, udid: Optional[str], keepalive: float) -> None:
        self.port = port
        self.udid = udid
        self.keepalive = keepalive

        self.sim: Any = None
        self.ready = False
        self.latitude: Optional[float] = None
        self.longitude: Optional[float] = None
        self.started_at = time.time()
        self.stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

        # 校园跑模式状态
        self.mode = "idle"                                   # idle / static / track
        self.track_task: Optional[asyncio.Task] = None
        self.track_stats: dict[str, Any] = {}

    # ------------------------------------------------------------------ 设备 --
    async def connect(self) -> None:
        """建立用户态隧道 + DVT + LocationSimulation 通道（只做一次）。"""
        from pymobiledevice3.remote import userspace_tunnel
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

        log(f"正在建立用户态隧道（udid={self.udid or '第一台 USB 设备'}）…")
        started = time.time()
        rsd = await userspace_tunnel.establish_userspace_rsd(serial=self.udid)
        log(f"隧道已建立，用时 {time.time() - started:.1f}s")

        log("正在连接 DVT…")
        dvt = DvtProvider(rsd)
        await dvt.__aenter__()
        sim = LocationSimulation(dvt)
        await sim.__aenter__()

        self.sim = sim
        self.ready = True
        log(f"DVT 就绪，总用时 {time.time() - started:.1f}s，开始接受命令")

    async def apply(self, latitude: float, longitude: float) -> None:
        async with self._lock:
            await self.sim.set(latitude, longitude)
            self.latitude, self.longitude = latitude, longitude

    async def clear(self) -> None:
        async with self._lock:
            await self.sim.clear()
            self.latitude = self.longitude = None

    # -------------------------------------------------------- 校园跑模式 --
    async def stop_track(self) -> None:
        """停掉校园跑循环（切换到静态位置或清除定位时都会调用）。"""
        task, self.track_task = self.track_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self.mode = "idle"

    async def track_loop(self, config: dict) -> None:
        """按配速沿 400 米跑道循环移动位置。"""
        from track import Track

        pace = float(config["pace_sec_per_km"])
        interval = max(float(config.get("interval", 1.0)), 0.2)
        lane = int(config.get("lane", 1))
        laps = config.get("laps")
        reverse = bool(config.get("reverse", False))

        track = Track(
            center_lat=float(config["center_lat"]),
            center_lon=float(config["center_lon"]),
            bearing_deg=float(config.get("bearing_deg", 0.0)),
            lane=lane,
        )
        speed = 1000.0 / pace            # 6:00/km → 2.778 m/s
        lap_length = track.lap_length_for_lane
        total_laps = float(laps) if laps else None

        self.mode = "track"
        self.track_stats = {
            "pace_sec_per_km": pace,
            "speed_mps": round(speed, 3),
            "lap_length_m": round(lap_length, 2),
            "lane": lane,
            "distance_m": 0.0,
            "laps_done": 0.0,
            "reverse": reverse,
        }
        log(f"校园跑模式启动：第 {lane} 道，配速 {pace / 60:.2f} 分/公里，"
            f"单圈 {lap_length:.1f} m，速度 {speed:.3f} m/s")

        started = time.monotonic()
        try:
            while True:
                elapsed = time.monotonic() - started
                distance = speed * elapsed
                if reverse:
                    distance = -distance

                if total_laps is not None and abs(distance) >= total_laps * lap_length:
                    distance = total_laps * lap_length * (-1 if reverse else 1)
                    latitude, longitude = track.position(distance)
                    await self.apply(latitude, longitude)
                    self.track_stats.update(distance_m=round(abs(distance), 1),
                                            laps_done=total_laps)
                    log(f"已跑完 {total_laps:g} 圈（{abs(distance):.0f} m），停在终点")
                    break

                latitude, longitude = track.position(distance)
                await self.apply(latitude, longitude)
                self.track_stats.update(
                    distance_m=round(abs(distance), 1),
                    laps_done=round(abs(distance) / lap_length, 3),
                    elapsed_s=round(elapsed, 2),
                    lat=latitude,
                    lon=longitude,
                )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            log("校园跑模式已停止")
            raise
        finally:
            if self.mode == "track":
                self.mode = "static"

    # ------------------------------------------------------------------ 协议 --
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        response: dict[str, Any]
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=15)
            request = json.loads(raw.decode("utf-8") or "{}")
            command = request.get("cmd")
            response = await self.dispatch(command, request)
        except asyncio.TimeoutError:
            response = {"ok": False, "error": "timeout"}
        except Exception as exc:  # noqa: BLE001 - 任何异常都回给客户端
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            try:
                writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
            writer.close()

    async def dispatch(self, command: Any, request: dict) -> dict:
        if command == "ping":
            return {
                "ok": True,
                "ready": self.ready,
                "lat": self.latitude,
                "lon": self.longitude,
                "mode": self.mode,
                "track": self.track_stats,
                "uptime": round(time.time() - self.started_at, 1),
            }

        if not self.ready:
            return {"ok": False, "error": "守护进程还在建立隧道，请稍候"}

        if command == "set":
            await self.stop_track()
            latitude = float(request["lat"])
            longitude = float(request["lon"])
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                return {"ok": False, "error": "坐标超出范围"}
            await self.apply(latitude, longitude)
            self.mode = "static"
            log(f"已设置 {latitude:.6f}, {longitude:.6f}")
            return {"ok": True, "lat": latitude, "lon": longitude}

        if command == "track":
            await self.stop_track()
            self.track_task = asyncio.create_task(self.track_loop(request))
            return {"ok": True, "mode": "track", "track": self.track_stats}

        if command == "stoprun":
            await self.stop_track()
            log("校园跑已停止，位置保持不变")
            return {"ok": True}

        if command == "clear":
            await self.stop_track()
            await self.clear()
            self.mode = "idle"
            log("已清除模拟定位")
            return {"ok": True}

        if command == "quit":
            await self.stop_track()
            log("收到退出命令")
            self.stop_event.set()
            return {"ok": True}

        return {"ok": False, "error": f"未知命令 {command!r}"}

    # ------------------------------------------------------------------ 保活 --
    async def keepalive_loop(self) -> None:
        """定期重发当前坐标，抵消设备侧偶发的模拟状态丢失。"""
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.keepalive)
                return  # 被要求退出
            except asyncio.TimeoutError:
                pass
            if not self.ready or self.latitude is None:
                continue
            if self.mode == "track":
                continue  # 校园跑模式每秒都在推新坐标，不需要保活重发
            try:
                await self.apply(self.latitude, self.longitude)
            except Exception as exc:  # noqa: BLE001 - 保活失败不该让进程退出
                log(f"保活重发失败：{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ 入口 --
    async def run(self) -> None:
        server = await asyncio.start_server(self.handle, "127.0.0.1", self.port)
        log(f"已监听 127.0.0.1:{self.port}")
        keepalive_task = asyncio.create_task(self.keepalive_loop())

        connect_task = asyncio.create_task(self._connect_guarded())

        async with server:
            await self.stop_event.wait()

        keepalive_task.cancel()
        connect_task.cancel()
        log("守护进程退出")

    async def _connect_guarded(self) -> None:
        try:
            await self.connect()
        except Exception as exc:  # noqa: BLE001 - 失败也要让客户端能拿到原因
            log(f"建立连接失败：{type(exc).__name__}: {exc}")
            self.ready = False
            self.stop_event.set()


def main() -> int:
    parser = argparse.ArgumentParser(description="iPhone 虚拟定位守护进程（内部使用）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--udid", default=None)
    parser.add_argument("--keepalive", type=float, default=DEFAULT_KEEPALIVE,
                        help="每隔多少秒重发一次当前坐标（0 表示关闭）")
    args = parser.parse_args()

    daemon = LocationDaemon(args.port, args.udid, max(args.keepalive, 0.0))
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
