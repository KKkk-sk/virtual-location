"""命令行入口 —— 图形界面不好用时用它，也方便排查问题。

    python fakegps.py devices            列出已连接设备
    python fakegps.py doctor             传输层体检（缺什么一目了然）
    python fakegps.py set 31.23966 121.49000    设置虚拟位置（Ctrl+C 恢复）
    python fakegps.py clear              清除模拟定位
    python fakegps.py devmode            查看/尝试开启开发者模式
    python fakegps.py mount              挂载开发者镜像
    python fakegps.py gui                打开图形界面

注意：`set` 必须保持命令常驻——一旦它退出（或拔掉数据线），
手机就立刻回到真实位置。这是 iOS 开发者定位模拟的机制决定的。
"""

from __future__ import annotations

import argparse
import sys
import time

import core


def cmd_devices(_args: argparse.Namespace) -> int:
    devices, error = core.list_devices()
    if not devices:
        print(error)
        return 1
    print(f"检测到 {len(devices)} 台设备：")
    for index, device in enumerate(devices, 1):
        print(f"  {index}. {device.label()}")
        print(f"     UDID: {device.udid}")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    print(core.doctor_report())
    return 0


def cmd_devmode(args: argparse.Namespace) -> int:
    state, raw = core.developer_mode_status(args.udid)
    print(raw or "（无输出）")
    if state is True:
        print("开发者模式：已开启")
        return 0
    print("开发者模式：未开启或状态未知，尝试开启…")
    code, output = core.enable_developer_mode(args.udid)
    print(output or "（无输出）")
    if code != 0:
        hint = core.explain_error(output)
        print(f"\n提示：{hint}" if hint else "\n提示：请在手机上手动开启开发者模式。")
        return 1
    print("若手机提示需要重启，请按提示操作。")
    return 0


def cmd_mount(args: argparse.Namespace) -> int:
    print("正在挂载开发者镜像（首次可能需要下载，请稍候）…")
    code, output = core.mount_developer_image(args.udid)
    print(output or "（无输出）")
    return 0 if code == 0 else 1


def cmd_set(args: argparse.Namespace) -> int:
    """设置位置后立刻返回 —— 位置由常驻守护进程保持，不需要挂住这个命令。"""
    session = core.LocationSession()
    started = time.time()
    try:
        session.start(args.latitude, args.longitude, args.udid, settle=args.settle)
    except core.LocationError as exc:
        print(f"设置失败：{exc}", file=sys.stderr)
        return 1

    elapsed = (time.time() - started) * 1000
    print(f"已设置虚拟位置：{args.latitude}, {args.longitude}（耗时 {elapsed:.0f} ms）")
    print("位置由后台守护进程保持，这个命令可以直接退出，不用 Ctrl+C。")
    print("恢复真实定位：python fakegps.py clear      停止守护进程：python fakegps.py stop")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    status = core.daemon_status()
    if not status:
        print("守护进程未运行（下次 set 会自动拉起，首次约十几秒）")
        return 1
    if not status.get("ready"):
        print("守护进程正在建立隧道，请稍候…")
        return 1

    mode = status.get("mode")
    latitude = status.get("lat")
    longitude = status.get("lon")

    if mode == "track":
        info = status.get("track") or {}
        distance = info.get("distance_m", 0)
        laps = info.get("laps_done", 0)
        pace = info.get("pace_sec_per_km", 0)
        print("模式：校园跑")
        print(f"  已跑 {distance:.0f} m / {laps:.2f} 圈")
        print(f"  配速 {pace / 60:.2f} 分/公里，第 {info.get('lane', 1)} 道"
              f"（单圈 {info.get('lap_length_m', 0):.1f} m）")
    else:
        print("模式：静态位置")

    if latitude is None:
        print("当前没有在模拟定位")
    else:
        print(f"当前坐标：{latitude:.6f}, {longitude:.6f}")
    print(f"运行时长：{status.get('uptime')} 秒")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from track import UESTC_TRACK, format_pace, parse_pace

    pace = parse_pace(args.pace)
    speed = 1000.0 / pace
    lap = UESTC_TRACK.lap_length_for_lane
    print(f"校园跑模式：配速 {format_pace(pace)} /km，即 {speed:.3f} m/s")
    print(f"  跑道：电子科大清水河校区田径场，第 {args.lane} 道，单圈 {lap:.1f} m")

    try:
        core.start_track_run(
            pace,
            laps=args.laps,
            lane=args.lane,
            reverse=args.reverse,
            interval=args.interval,
            settle=args.settle,
        )
    except core.LocationError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    if args.laps:
        print(f"已开始移动：跑 {args.laps:g} 圈后自动停下（约 {args.laps * lap / speed / 60:.1f} 分钟）")
    else:
        print("已开始移动：一直循环跑（约 2.4 分钟一圈）")
    print("查看进度：python fakegps.py status")
    print("停止跑步：python fakegps.py stoprun      恢复真实定位：python fakegps.py clear")
    return 0


def cmd_stoprun(_args: argparse.Namespace) -> int:
    code, output = core.stop_track_run()
    print(output)
    return 0 if code == 0 else 1


def cmd_stop(_args: argparse.Namespace) -> int:
    code, output = core.stop_daemon()
    print(output)
    return 0 if code == 0 else 1


def cmd_clear(args: argparse.Namespace) -> int:
    code, output = core.clear_location(args.udid)
    print(output or f"已清除模拟定位（退出码 {code}）")
    if code != 0:
        hint = core.explain_error(output)
        if hint:
            print(f"\n提示：{hint}")
    return 0 if code == 0 else 1


def cmd_gui(_args: argparse.Namespace) -> int:
    import fakegps_gui
    fakegps_gui.main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="iPhone 虚拟定位工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("devices", help="列出已连接设备").set_defaults(func=cmd_devices)
    sub.add_parser("doctor", help="传输层体检").set_defaults(func=cmd_doctor)
    sub.add_parser("gui", help="打开图形界面").set_defaults(func=cmd_gui)
    sub.add_parser("status", help="查看当前模拟状态").set_defaults(func=cmd_status)
    sub.add_parser("stop", help="停止后台守护进程（恢复真实定位）").set_defaults(func=cmd_stop)
    sub.add_parser("stoprun", help="停止校园跑（位置停在当前点）").set_defaults(func=cmd_stoprun)

    for name, func, help_text in (
        ("devmode", cmd_devmode, "查看/开启开发者模式"),
        ("mount", cmd_mount, "挂载开发者镜像"),
        ("clear", cmd_clear, "清除模拟定位（守护进程保持运行）"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--udid", default=None, help="指定设备（多台手机时用）")
        p.set_defaults(func=func)

    set_parser = sub.add_parser("set", help="设置虚拟位置（毫秒级切换）")
    set_parser.add_argument("latitude", type=float, help="纬度 -90~90")
    set_parser.add_argument("longitude", type=float, help="经度 -180~180")
    set_parser.add_argument("--udid", default=None, help="指定设备")
    set_parser.add_argument("--settle", type=float, default=90.0,
                            help="首次启动守护进程的等待上限（秒）")
    set_parser.set_defaults(func=cmd_set)

    run_parser = sub.add_parser("run", help="校园跑模式：沿 400 米跑道按配速移动")
    run_parser.add_argument("--pace", default="6:00",
                            help="配速，如 6:00 表示 6 分/公里（默认 6:00）")
    run_parser.add_argument("--laps", type=float, default=None,
                            help="跑几圈后自动停（默认一直循环）")
    run_parser.add_argument("--lane", type=int, default=1, help="第几道（默认 1 道）")
    run_parser.add_argument("--reverse", action="store_true", help="反向跑")
    run_parser.add_argument("--interval", type=float, default=1.0,
                            help="坐标更新间隔，秒（默认 1.0，接近真实 GPS 频率）")
    run_parser.add_argument("--settle", type=float, default=90.0,
                            help="首次启动守护进程的等待上限（秒）")
    run_parser.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
