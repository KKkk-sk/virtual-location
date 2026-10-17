"""iPhone 虚拟定位控制台（Tkinter 图形界面）。

运行：
    .venv\\Scripts\\python.exe fakegps_gui.py

界面上所有耗时操作（建隧道、设坐标）都丢到后台线程，避免窗口假死。
"""

from __future__ import annotations

import queue
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import core


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.session = core.LocationSession()
        self.devices: list[core.Device] = []
        self.selected: core.Device | None = None
        self.busy = False
        # 后台线程不能直接碰 tkinter（会报 "main thread is not in main loop"），
        # 所以结果一律经队列交给主线程的轮询函数处理。
        self._results: queue.Queue = queue.Queue()

        root.title("iPhone 虚拟定位")
        root.geometry("780x660")
        root.minsize(700, 580)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._refresh_favorites()
        self.root.after(100, self._poll_results)
        self.root.after(200, self.refresh_devices)

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        # ---- 设备 ----
        dev_box = ttk.LabelFrame(outer, text=" 设备 ", padding=10)
        dev_box.pack(fill="x")
        self.device_var = tk.StringVar(value="正在检测…")
        ttk.Label(dev_box, textvariable=self.device_var, foreground="#0a7a3d").pack(side="left")

        self.btn_refresh = ttk.Button(dev_box, text="刷新", width=7, command=self.refresh_devices)
        self.btn_doctor = ttk.Button(dev_box, text="体检", width=7, command=self.show_doctor)
        self.btn_image = ttk.Button(dev_box, text="挂载镜像", width=10, command=self.mount_image)
        self.btn_devmode = ttk.Button(dev_box, text="开发者模式", width=11, command=self.setup_developer_mode)
        for btn in (self.btn_refresh, self.btn_doctor, self.btn_image, self.btn_devmode):
            btn.pack(side="right", padx=2)

        # ---- 位置输入 ----
        pos_box = ttk.LabelFrame(outer, text=" 位置 ", padding=10)
        pos_box.pack(fill="x", pady=(10, 0))

        row1 = ttk.Frame(pos_box)
        row1.pack(fill="x")
        ttk.Label(row1, text="预设地点").pack(side="left")
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(
            row1, textvariable=self.preset_var, state="readonly", width=26,
            values=[p[0] for p in core.PRESETS],
        )
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

        # ---- 操作 ----
        act = ttk.Frame(outer)
        act.pack(fill="x", pady=(10, 0))
        self.btn_set = ttk.Button(act, text="设置虚拟位置", command=self.set_location, width=18)
        self.btn_set.pack(side="left")
        self.btn_clear = ttk.Button(act, text="恢复真实定位", command=self.restore, width=18)
        self.btn_clear.pack(side="left", padx=8)

        # ---- 收藏 ----
        fav_box = ttk.LabelFrame(outer, text=" 收藏夹 ", padding=10)
        fav_box.pack(fill="x", pady=(10, 0))
        fav_wrap = ttk.Frame(fav_box)
        fav_wrap.pack(fill="x")
        self.fav_list = tk.Listbox(fav_wrap, height=4, activestyle="dotbox")
        self.fav_list.pack(side="left", fill="both", expand=True)
        self.fav_list.bind("<Double-Button-1>", self._use_favorite)
        scroll = ttk.Scrollbar(fav_wrap, orient="vertical", command=self.fav_list.yview)
        scroll.pack(side="left", fill="y")
        self.fav_list.configure(yscrollcommand=scroll.set)

        fav_btns = ttk.Frame(fav_wrap)
        fav_btns.pack(side="left", padx=(8, 0), fill="y")
        ttk.Button(fav_btns, text="保存当前", width=10, command=self.save_favorite).pack(pady=1)
        ttk.Button(fav_btns, text="使用选中", width=10, command=self._use_favorite).pack(pady=1)
        ttk.Button(fav_btns, text="删除选中", width=10, command=self.delete_favorite).pack(pady=1)

        # ---- 日志 ----
        log_box = ttk.LabelFrame(outer, text=" 日志 ", padding=8)
        log_box.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(log_box, height=10, wrap="word", font=("Consolas", 9),
                           state="disabled", background="#1e1e1e", foreground="#d6d6d6",
                           insertbackground="#d6d6d6")
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        log_scroll.pack(side="left", fill="y")
        self.log.configure(yscrollcommand=log_scroll.set)

        # ---- 状态栏 ----
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=(8, 4)).pack(fill="x", side="bottom")

    # --------------------------------------------------------------- 工具 --
    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_set, self.btn_clear, self.btn_refresh,
                    self.btn_doctor, self.btn_image, self.btn_devmode):
            btn.configure(state=state)
        if status:
            self._set_status(status)

    def _async(self, fn, on_done) -> None:
        """后台执行 fn，结果（或异常）放进队列，由主线程轮询取出。"""
        def worker() -> None:
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - 需要把任何错误回传给界面
                result = exc
            self._results.put((on_done, result))
        threading.Thread(target=worker, daemon=True).start()

    def _poll_results(self) -> None:
        """在主线程里消费后台结果。只有这里才允许操作控件。"""
        while True:
            try:
                on_done, result = self._results.get_nowait()
            except queue.Empty:
                break
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001 - 单个回调出错不该拖垮界面
                self._log(f"界面回调出错：{exc}")
        self.root.after(100, self._poll_results)

    def _current_udid(self) -> str | None:
        return self.selected.udid if self.selected else None

    # --------------------------------------------------------------- 设备 --
    def refresh_devices(self) -> None:
        if self.busy:
            return
        self._set_status("正在检测设备…")
        self._async(core.list_devices, self._on_devices)

    def _on_devices(self, result) -> None:
        if isinstance(result, Exception):
            self.device_var.set("检测失败")
            self._log(f"检测设备出错：{result}")
            self._set_status("检测失败")
            return
        devices, error = result
        self.devices = devices
        if devices:
            self.selected = devices[0]
            self.device_var.set(self.selected.label())
            self._log(f"检测到设备：{self.selected.label()}  UDID={self.selected.udid}")
            self._set_status("设备就绪，可以设置位置")
        else:
            self.selected = None
            self.device_var.set("未检测到设备")
            self._log(error)
            self._set_status("未连接设备")

    def show_doctor(self) -> None:
        self._set_busy(True, "正在体检传输层…")
        self._async(core.doctor_report, self._on_doctor)

    def _on_doctor(self, result) -> None:
        self._set_busy(False, "体检完成")
        self._log("--- 传输层体检 ---")
        self._log(result if isinstance(result, str) else f"出错：{result}")

    def mount_image(self) -> None:
        udid = self._current_udid()
        if not udid:
            messagebox.showwarning("没有设备", "先连接 iPhone 并点「刷新」。")
            return
        self._set_busy(True, "正在挂载开发者镜像（可能需下载，请稍候）…")
        self._async(lambda: core.mount_developer_image(udid), self._on_mount)

    def _on_mount(self, result) -> None:
        self._set_busy(False, "镜像挂载完成")
        if isinstance(result, Exception):
            self._log(f"挂载镜像出错：{result}")
            return
        code, output = result
        self._log(f"挂载开发者镜像（退出码 {code}）：{output or '无输出'}")

    def setup_developer_mode(self) -> None:
        udid = self._current_udid()
        if not udid:
            messagebox.showwarning("没有设备", "先连接 iPhone 并点「刷新」。")
            return
        self._set_busy(True, "正在检查开发者模式…")
        self._async(lambda: self._prepare_developer_mode(udid), self._on_devmode)

    @staticmethod
    def _prepare_developer_mode(udid: str) -> str:
        state, raw = core.developer_mode_status(udid)
        lines = [f"当前状态输出：{raw or '（无输出）'}"]
        if state is True:
            lines.append("开发者模式已经是开启状态。")
            return "\n".join(lines)
        code, output = core.enable_developer_mode(udid)
        lines.append(f"尝试开启开发者模式（退出码 {code}）：{output or '无输出'}")
        lines.append("如果手机弹窗或提示重启，请按提示操作；重启后在"
                     "「设置 → 隐私与安全性 → 开发者模式」确认已打开。")
        return "\n".join(lines)

    def _on_devmode(self, result) -> None:
        self._set_busy(False, "开发者模式检查完成")
        self._log("--- 开发者模式 ---")
        self._log(result if isinstance(result, str) else f"出错：{result}")

    # --------------------------------------------------------------- 定位 --
    def _read_coords(self) -> tuple[float, float] | None:
        try:
            lat = float(self.lat_var.get().strip())
            lon = float(self.lon_var.get().strip())
        except ValueError:
            messagebox.showerror("坐标格式不对", "纬度和经度都要填数字，例如：\n31.23966\n121.49000")
            return None
        if not -90 <= lat <= 90:
            messagebox.showerror("纬度超范围", f"纬度必须在 -90 ~ 90 之间，当前是 {lat}")
            return None
        if not -180 <= lon <= 180:
            messagebox.showerror("经度超范围", f"经度必须在 -180 ~ 180 之间，当前是 {lon}")
            return None
        return lat, lon

    def set_location(self) -> None:
        if self.busy:
            return
        coords = self._read_coords()
        if coords is None:
            return
        lat, lon = coords
        udid = self._current_udid()
        self._set_busy(True, "正在建隧道并设置位置（首次约 10 秒）…")
        self._log(f"设置虚拟位置：{lat:.6f}, {lon:.6f}")
        self._async(lambda: self.session.start(lat, lon, udid), self._on_set)

    def _on_set(self, result) -> None:
        if isinstance(result, Exception):
            self._set_busy(False, "设置失败")
            self._log(f"设置失败：{result}")
            messagebox.showerror("设置失败", str(result))
            return
        lat, lon = self.session.latitude, self.session.longitude
        self._set_busy(False, f"正在模拟定位：{lat:.5f}, {lon:.5f}")
        self._log(f"成功。当前模拟坐标 {lat:.6f}, {lon:.6f}（拔线或点「恢复」即还原）")

    def restore(self) -> None:
        if self.busy:
            return
        udid = self._current_udid()
        self._set_busy(True, "正在恢复真实定位…")
        self._async(lambda: self.session.stop(clear=True, udid=udid), self._on_restore)

    def _on_restore(self, result) -> None:
        self._set_busy(False, "已恢复真实定位")
        if isinstance(result, Exception):
            self._log(f"恢复时出错：{result}")
            return
        code, output = result
        self._log(f"已恢复真实定位（clear 退出码 {code}）")
        if code != 0 and output:
            hint = core.explain_error(output)
            self._log(f"清除输出：{output}")
            if hint:
                self._log(f"提示：{hint}")

    # --------------------------------------------------------------- 预设 --
    def _apply_preset(self, _event=None) -> None:
        name = self.preset_var.get()
        for preset_name, lat, lon in core.PRESETS:
            if preset_name == name:
                self.lat_var.set(f"{lat:.5f}")
                self.lon_var.set(f"{lon:.5f}")
                self._set_status(f"已填入 {preset_name}")
                return

    def open_map(self) -> None:
        coords = self._read_coords()
        if coords is None:
            return
        lat, lon = coords
        url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}"
        webbrowser.open(url)
        self._log(f"已在浏览器打开地图：{url}")

    # ------------------------------------------------------------- 收藏夹 --
    def _refresh_favorites(self) -> None:
        self.fav_list.delete(0, "end")
        for item in core.load_favorites():
            self.fav_list.insert(
                "end", f"{item.get('name')}   ({item.get('lat')}, {item.get('lon')})"
            )

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
        self._log(f"已收藏「{name}」：{lat:.6f}, {lon:.6f}")
        self._set_status(f"已收藏 {name}")

    def _selected_favorite(self) -> dict | None:
        selection = self.fav_list.curselection()
        if not selection:
            messagebox.showinfo("未选中", "先在列表里选一个收藏坐标。")
            return None
        index = selection[0]
        items = core.load_favorites()
        return items[index] if 0 <= index < len(items) else None

    def _use_favorite(self, _event=None) -> None:
        item = self._selected_favorite()
        if not item:
            return
        self.lat_var.set(f"{float(item.get('lat', 0)):.5f}")
        self.lon_var.set(f"{float(item.get('lon', 0)):.5f}")
        self._set_status(f"已载入收藏 {item.get('name')}")

    def delete_favorite(self) -> None:
        item = self._selected_favorite()
        if not item:
            return
        if not messagebox.askokcancel("删除收藏", f"确定删除「{item.get('name')}」？"):
            return
        core.remove_favorite(str(item.get("name")))
        self._refresh_favorites()
        self._log(f"已删除收藏「{item.get('name')}」")

    # --------------------------------------------------------------- 退出 --
    def on_close(self) -> None:
        # 守护进程是独立进程，关界面不影响它 —— 定位会继续保持。
        # 要恢复真实位置请显式点「恢复真实定位」或运行 fakegps.py clear。
        if self.session.active:
            keep = messagebox.askokcancel(
                "退出界面",
                "关闭界面后，模拟定位仍会在后台保持（守护进程不受影响）。\n\n"
                "要恢复真实位置：点「恢复真实定位」或运行  python fakegps.py clear\n\n"
                "确定退出吗？",
            )
            if not keep:
                return
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
