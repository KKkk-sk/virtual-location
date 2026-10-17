"""Android 虚拟定位命令行。

    python fakegps_android.py devices                列出已连接手机
    python fakegps_android.py setup                  一键准备（装 App + 授权 + 设为模拟位置应用）
    python fakegps_android.py set 35.1595 126.8526   设置位置并回读确认
    python fakegps_android.py get                    读取当前模拟坐标
    python fakegps_android.py stop                   停止模拟，恢复真实定位
    python fakegps_android.py run --pace 6:00        校园跑模式
    python fakegps_android.py gui                    打开图形界面
"""

from __future__ import annotations

import argparse
import sys
import time

import core_android as core


def cmd_devices(_args: argparse.Namespace) -> int:
    devices, error = core.list_devices()
    if not devices:
        print(error)
        return 1
    print(f"检测到 {len(devices)} 台可用设备：")
    for device in devices:
        print(f"  {device.label()}  序列号 {device.serial}")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    try:
        for line in core.setup_device(args.serial):
            print(line)
    except core.AdbError as exc:
        print(f"准备失败：{exc}", file=sys.stderr)
        return 1
    print("\n准备完成。接着可以：python fakegps_android.py set 30.6570 104.0660")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    started = time.time()
    try:
        latitude, longitude = core.set_location_verified(args.latitude, args.longitude, args.serial)
    except core.AdbError as exc:
        print(f"设置失败：{exc}", file=sys.stderr)
        return 1
    print(f"已设置并回读确认：{latitude:.6f}, {longitude:.6f}"
          f"（耗时 {(time.time() - started) * 1000:.0f} ms）")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    location = core.get_location(args.serial)
    if location is None:
        print("读不到当前坐标。可能 App 没在运行，或还没执行过 setup。")
        return 1
    latitude, longitude, altitude = location
    print(f"当前模拟坐标：{latitude:.6f}, {longitude:.6f}（海拔 {altitude:.0f} m）")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    code, output = core.stop_mock(args.serial)
    print("已停止模拟，手机回到真实定位。")
    return 0 if code == 0 else 1


def cmd_run(args: argparse.Namespace) -> int:
    from track import UESTC_TRACK, format_pace, parse_pace

    pace = parse_pace(args.pace)
    runner = core.TrackRunner()
    try:
        runner.start(pace, laps=args.laps, lane=args.lane, reverse=args.reverse,
                     interval=args.interval, serial=args.serial)
    except core.AdbError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    lap = UESTC_TRACK.lap_length_for_lane
    speed = 1000.0 / pace
    print(f"校园跑模式：{format_pace(pace)} /km（{speed:.3f} m/s），第 {args.lane} 道，单圈 {lap:.1f} m")
    print(f"跑道中心 {UESTC_TRACK.center_lat:.6f}, {UESTC_TRACK.center_lon:.6f}")
    print("按 Ctrl+C 停止跑步。\n")

    try:
        while runner.running:
            status = runner.status()
            print(f"\r已跑 {status['distance_m']:>7.1f} m / {status['laps_done']:.2f} 圈", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止…")
    runner.stop()

    if runner.error:
        print(f"\n运行出错：{runner.error}")
        return 1
    status = runner.status()
    print(f"\n已停止：共 {status['distance_m']:.0f} m / {status['laps_done']:.2f} 圈")
    return 0


def cmd_gui(_args: argparse.Namespace) -> int:
    import android_gui
    android_gui.main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Android 虚拟定位工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("devices", help="列出已连接设备").set_defaults(func=cmd_devices)
    sub.add_parser("get", help="读取当前模拟坐标").set_defaults(func=cmd_get)
    sub.add_parser("stop", help="停止模拟，恢复真实定位").set_defaults(func=cmd_stop)
    sub.add_parser("gui", help="打开图形界面").set_defaults(func=cmd_gui)

    for name, func, help_text in (
        ("setup", cmd_setup, "一键准备（安装 App + 授权）"),
        ("set", cmd_set, "设置位置并回读确认"),
        ("run", cmd_run, "校园跑模式"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--serial", default=None, help="指定设备序列号（多台手机时用）")
        p.set_defaults(func=func)

    set_parser = sub.choices["set"]
    set_parser.add_argument("latitude", type=float, help="纬度 -90~90")
    set_parser.add_argument("longitude", type=float, help="经度 -180~180")

    run_parser = sub.choices["run"]
    run_parser.add_argument("--pace", default="6:00", help="配速，如 6:00 表示 6 分/公里")
    run_parser.add_argument("--laps", type=float, default=None, help="跑几圈后停（默认一直跑）")
    run_parser.add_argument("--lane", type=int, default=1, help="第几道（默认 1）")
    run_parser.add_argument("--reverse", action="store_true", help="反向跑")
    run_parser.add_argument("--interval", type=float, default=1.0, help="坐标更新间隔秒")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
