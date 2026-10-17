"""Android 虚拟定位控制台（Tkinter）。

运行：python android_gui.py
"""

from __future__ import annotations

import queue
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import core_android as core


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.devices: list[core.Device] = []
        self.selected: core.Device | None = None
        self.runner = core.TrackRunner()
        self.busy = False
        # 后台线程不能直接碰 tkinter，结果统一经队列交给主线程
        self._results: queue.Queue = queue.Queue()

        root.title("安卓虚拟定位")
        root.geometry("800x700")
        root.minsize(720, 640)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._refresh_favorites()
        self.root.after(100, self._poll_results)
        self.root.after(200, self.refresh_devices)
        self.root.after(500, self._tick_status)

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        dev_box = ttk.LabelFrame(outer, text=" 设备 ", padding=10)
        dev_box.pack(fill="x")
        self.device_var = tk.StringVar(value="正在检测…")
        ttk.Label(dev_box, textvariable=self.device_var, foreground="#0a7a3d").pack(side="left")
        self.btn_refresh = ttk.Button(dev_box, text="刷新", width=7, command=self.refresh_devices)
        self.btn_setup = ttk.Button(dev_box, text="一键准备", width=10, command=self.setup_device)
        self.btn_setup.pack(side="right", padx=2)
        self.btn_refresh.pack(side="right", padx=2)

        pos_box = ttk.LabelFrame(outer, text=" 位置 ", padding=10)
        pos_box.pack(fill="x", pady=(10, 0))
        row1 = ttk.Frame(pos_box)
        row1.pack(fill="x")
        ttk.Label(row1, text="预设地点").pack(side="left")
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(row1, textvariable=self.preset_var, state="readonly",
                                       width=28, values=[p[0] for p in core.PRESETS])
        self.preset_box.pack(side="left", padx=6)
        self.preset_box.bind("<<ComboboxSelected>>", self._apply_preset)
        ttk.Button(row1, text="填入坐标", width=10, command=self._apply_preset).pack(side="left")

        row2 = ttk.Frame(pos_box)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="纬度").pack(side="left")
        self.lat_var = tk.StringVar(value=f"{core.PRESETS[0][1]:.5f}")
        ttk.Entry(row2, textvariable=self.lat_var, width=14).pack(side="left", padx=(4, 12))
        ttk.Label(row2, text="经度").pack(side="left")
        self.lon_var = tk.StringVar(value=f"{core.PRESETS[0][2]:.5f}")
        ttk.Entry(row2, textvariable=self.lon_var, width=14).pack(side="left", padx=(4, 12))
        ttk.Button(row2, text="在地图查看", width=12, command=self.open_map).pack(side="left")

        act = ttk.Frame(outer)
        act.pack(fill="x", pady=(10, 0))
        self.btn_set = ttk.Button(act, text="设置位置（回读确认）", command=self.set_location, width=22)
        self.btn_set.pack(side="left")
        self.btn_get = ttk.Button(act, text="回读当前", command=self.read_current, width=12)
        self.btn_get.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(act, text="停止模拟（恢复真实）", command=self.stop_mock, width=20)
        self.btn_stop.pack(side="left")

        track_box = ttk.LabelFrame(outer, text=" 校园跑模式（电子科大清水河田径场） ", padding=10)
        track_box.pack(fill="x", pady=(10, 0))
        trow = ttk.Frame(track_box)
        trow.pack(fill="x")
        ttk.Label(trow, text="配速").pack(side="left")
        self.pace_var = tk.StringVar(value="6:00")
        ttk.Entry(trow, textvariable=self.pace_var, width=8).pack(side="left", padx=(4, 4))
        ttk.Label(trow, text="分/公里").pack(side="left", padx=(0, 12))
        ttk.Label(trow, text="圈数").pack(side="left")
        self.laps_var = tk.StringVar(value="")
        ttk.Entry(trow, textvariable=self.laps_var, width=6).pack(side="left", padx=(4, 4))
        ttk.Label(trow, text="（留空=一直跑）").pack(side="left", padx=(0, 12))
        ttk.Label(trow, text="第几道").pack(side="left")
        self.lane_var = tk.StringVar(value="1")
        ttk.Spinbox(trow, from_=1, to=8, textvariable=self.lane_var, width=4).pack(side="left", padx=4)

        trow2 = ttk.Frame(track_box)
        trow2.pack(fill="x", pady=(8, 0))
        self.btn_run = ttk.Button(trow2, text="开始校园跑", command=self.start_run, width=14)
        self.btn_run.pack(side="left")
        self.btn_runstop = ttk.Button(trow2, text="停止跑步", command=self.stop_run, width=12)
        self.btn_runstop.pack(side="left", padx=6)
        self.track_var = tk.StringVar(value="未运行")
        ttk.Label(trow2, textvariable=self.track_var).pack(side="left", padx=8)

        fav_box = ttk.LabelFrame(outer, text=" 收藏夹 ", padding=10)
        fav_box.pack(fill="x", pady=(10, 0))
        wrap = ttk.Frame(fav_box)
        wrap.pack(fill="x")
        self.fav_list = tk.Listbox(wrap, height=4, activestyle="dotbox")
        self.fav_list.pack(side="left", fill="both", expand=True)
        self.fav_list.bind("<Double-Button-1>", self._use_favorite)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.fav_list.yview)
        scroll.pack(side="left", fill="y")
        self.fav_list.configure(yscrollcommand=scroll.set)
        btns = ttk.Frame(wrap)
        btns.pack(side="left", padx=(8, 0), fill="y")
        ttk.Button(btns, text="保存当前", width=10, command=self.save_favorite).pack(pady=1)
        ttk.Button(btns, text="使用选中", width=10, command=self._use_favorite).pack(pady=1)

        log_box = ttk.LabelFrame(outer, text=" 日志 ", padding=8)
        log_box.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(log_box, height=9, wrap="word", font=("Consolas", 9), state="disabled",
                           background="#1e1e1e", foreground="#d6d6d6", insertbackground="#d6d6d6")
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        log_scroll.pack(side="left", fill="y")
        self.log.configure(yscrollcommand=log_scroll.set)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w",
                  padding=(8, 4)).pack(fill="x", side="bottom")

    # --------------------------------------------------------------- 工具 --
    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _async(self, fn, on_done) -> None:
        def worker() -> None:
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                result = exc
            self._results.put((on_done, result))
        threading.Thread(target=worker, daemon=True).start()

    def _poll_results(self) -> None:
        while True:
            try:
                on_done, result = self._results.get_nowait()
            except queue.Empty:
                break
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001
                self._log(f"界面回调出错：{exc}")
        self.root.after(100, self._poll_results)

    def _tick_status(self) -> None:
        """定时刷新校园跑进度。"""
        if self.runner.running:
            status = self.runner.status()
            self.track_var.set(f"已跑 {status['distance_m']:.0f} m / {status['laps_done']:.2f} 圈")
            if status.get("error"):
                self._log(f"跑步出错：{status['error']}")
                self.runner.stop()
        elif self.runner.distance_m > 0 and self.track_var.get().startswith("已跑"):
            status = self.runner.status()
            self.track_var.set(f"已停止：{status['distance_m']:.0f} m / {status['laps_done']:.2f} 圈")
        self.root.after(500, self._tick_status)

    def _current_serial(self) -> str | None:
        return self.selected.serial if self.selected else None

    def _read_coords(self) -> tuple[float, float] | None:
        try:
            lat = float(self.lat_var.get().strip())
            lon = float(self.lon_var.get().strip())
        except ValueError:
            messagebox.showerror("坐标格式不对", "纬度和经度都要填数字，例如：\n30.6570\n104.0660")
            return None
        if not -90 <= lat <= 90:
            messagebox.showerror("纬度超范围", f"纬度必须在 -90 ~ 90 之间，当前 {lat}")
            return None
        if not -180 <= lon <= 180:
            messagebox.showerror("经度超范围", f"经度必须在 -180 ~ 180 之间，当前 {lon}")
            return None
        return lat, lon

    # --------------------------------------------------------------- 设备 --
    def refresh_devices(self) -> None:
        self.status_var.set("正在检测设备…")
        self._async(core.list_devices, self._on_devices)

    def _on_devices(self, result) -> None:
        if isinstance(result, Exception):
            self.device_var.set("检测失败")
            self._log(f"检测设备出错：{result}")
            return
        devices, error = result
        self.devices = devices
        if devices:
            self.selected = devices[0]
            self.device_var.set(self.selected.label())
            self._log(f"检测到设备：{self.selected.label()}  序列号 {self.selected.serial}")
            self.status_var.set("设备就绪")
        else:
            self.selected = None
            self.device_var.set("未检测到设备")
            self._log(error)
            self.status_var.set("未连接设备")

    def setup_device(self) -> None:
        serial = self._current_serial()
        if not serial:
            messagebox.showwarning("没有设备", "先连上手机并点「刷新」。")
            return
        self._log("开始一键准备（安装 App + 授权）…")
        self._async(lambda: core.setup_device(serial), self._on_setup)

    def _on_setup(self, result) -> None:
        if isinstance(result, Exception):
            self._log(f"准备失败：{result}")
            messagebox.showerror("准备失败", str(result))
            return
        for line in result:
            self._log(line)
        self.status_var.set("准备完成")

    # --------------------------------------------------------------- 定位 --
    def set_location(self) -> None:
        coords = self._read_coords()
        if coords is None:
            return
        lat, lon = coords
        serial = self._current_serial()
        self.status_var.set("正在下发坐标并回读确认…")
        self._log(f"设置位置：{lat:.6f}, {lon:.6f}")
        self._async(lambda: core.set_location_verified(lat, lon, serial), self._on_set)

    def _on_set(self, result) -> None:
        if isinstance(result, Exception):
            self.status_var.set("设置失败")
            self._log(f"设置失败：{result}")
            messagebox.showerror("设置失败", str(result))
            return
        lat, lon = result
        self.status_var.set(f"已生效：{lat:.5f}, {lon:.5f}")
        self._log(f"设备回读确认：{lat:.6f}, {lon:.6f}（真的生效了）")

    def read_current(self) -> None:
        serial = self._current_serial()
        self._async(lambda: core.get_location(serial), self._on_get)

    def _on_get(self, result) -> None:
        if isinstance(result, Exception) or result is None:
            self._log("读不到当前坐标：确认 App 已安装并运行过（先点「一键准备」）。")
            return
        lat, lon, alt = result
        self.lat_var.set(f"{lat:.5f}")
        self.lon_var.set(f"{lon:.5f}")
        self._log(f"当前模拟坐标：{lat:.6f}, {lon:.6f}（海拔 {alt:.0f} m）")

    def stop_mock(self) -> None:
        serial = self._current_serial()
        self._log("正在停止模拟…")
        self._async(lambda: core.stop_mock(serial), self._on_stop)

    def _on_stop(self, result) -> None:
        self.runner.stop()
        self.status_var.set("已停止模拟")
        self._log("已停止模拟，手机回到真实定位。")

    # ------------------------------------------------------------- 校园跑 --
    def start_run(self) -> None:
        from track import parse_pace

        serial = self._current_serial()
        if not serial:
            messagebox.showwarning("没有设备", "先连上手机并点「刷新」。")
            return
        try:
            pace = parse_pace(self.pace_var.get().strip())
        except ValueError:
            messagebox.showerror("配速格式不对", "配速写成 6:00 这样，表示 6 分 00 秒每公里。")
            return
        laps_text = self.laps_var.get().strip()
        laps = float(laps_text) if laps_text else None
        lane = int(self.lane_var.get() or 1)

        try:
            self.runner.start(pace, laps=laps, lane=lane, serial=serial)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("启动失败", str(exc))
            return
        self._log(f"校园跑开始：配速 {pace / 60:.2f} 分/公里，第 {lane} 道"
                  + (f"，{laps:g} 圈" if laps else "，一直跑"))
        self.status_var.set("校园跑进行中")

    def stop_run(self) -> None:
        self.runner.stop()
        self._log("校园跑已停止（位置停在当前点）")
        self.status_var.set("校园跑已停止")

    # --------------------------------------------------------------- 预设 --
    def _apply_preset(self, _event=None) -> None:
        for name, lat, lon in core.PRESETS:
            if name == self.preset_var.get():
                self.lat_var.set(f"{lat:.5f}")
                self.lon_var.set(f"{lon:.5f}")
                self.status_var.set(f"已填入 {name}")
                return

    def open_map(self) -> None:
        coords = self._read_coords()
        if coords is None:
            return
        lat, lon = coords
        webbrowser.open(f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}")

    # ------------------------------------------------------------- 收藏夹 --
    def _refresh_favorites(self) -> None:
        self.fav_list.delete(0, "end")
        for item in core.load_favorites():
            self.fav_list.insert("end", f"{item.get('name')}   ({item.get('lat')}, {item.get('lon')})")

    def save_favorite(self) -> None:
        coords = self._read_coords()
        if coords is None:
            return
        lat, lon = coords
        name = simpledialog.askstring("保存到收藏夹", "给这个坐标起个名字：",
                                      initialvalue=self.preset_var.get() or f"{lat:.4f},{lon:.4f}")
        if not name:
            return
        core.upsert_favorite(name.strip(), lat, lon)
        self._refresh_favorites()
        self._log(f"已收藏「{name}」")

    def _selected_favorite(self) -> dict | None:
        selection = self.fav_list.curselection()
        if not selection:
            return None
        items = core.load_favorites()
        index = selection[0]
        return items[index] if 0 <= index < len(items) else None

    def _use_favorite(self, _event=None) -> None:
        item = self._selected_favorite()
        if not item:
            return
        self.lat_var.set(f"{float(item.get('lat', 0)):.5f}")
        self.lon_var.set(f"{float(item.get('lon', 0)):.5f}")
        self.status_var.set(f"已载入收藏 {item.get('name')}")

    # --------------------------------------------------------------- 退出 --
    def on_close(self) -> None:
        if self.runner.running:
            if not messagebox.askokcancel("退出", "校园跑还在进行，退出会停止跑步（位置停在当前点）。继续吗？"):
                return
            self.runner.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
