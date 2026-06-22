"""PC version tkinter GUI — PCApp class."""

import datetime as dt
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from sjtu_tennis_toolkit.alarm import Alarm
from sjtu_tennis_toolkit.config import (
    config_label,
    default_date_range_text,
    load_pc_rate_limit_cooldown,
    parse_config,
)
from sjtu_tennis_toolkit.constants import DEFAULT_CHECK_INTERVAL_SECONDS
from sjtu_tennis_toolkit.models import MonitorConfig, Slot, VENUES
from sjtu_tennis_toolkit.pc.monitor import PCVenueMonitor


class PCApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("交我办PC版网球场空位警报器")
        self.geometry("720x520")
        self.minsize(680, 480)

        self.events: queue.Queue = queue.Queue()
        self.monitor: PCVenueMonitor | None = None
        self.monitor_thread: threading.Thread | None = None
        self.alarm = Alarm()
        self.alert_popup_open = False
        self.alert_window: tk.Toplevel | None = None
        self.config_change_after_id: str | None = None

        self.date_var = tk.StringVar(value=default_date_range_text())
        self.start_var = tk.StringVar(value="17:00")
        self.end_var = tk.StringVar(value="22:00")
        self.interval_var = tk.StringVar(value=str(DEFAULT_CHECK_INTERVAL_SECONDS))
        self.venue_vars = {
            venue.key: tk.BooleanVar(value=True)
            for venue in VENUES
        }
        self.status_var = tk.StringVar(value="未开始")
        for var in (self.date_var, self.start_var, self.end_var, self.interval_var, *self.venue_vars.values()):
            var.trace_add("write", self._schedule_config_change)

        self._build_ui()
        self.after(200, self._drain_events)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        form = ttk.LabelFrame(root, text="PC版监控条件", padding=12)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        form.columnconfigure(5, weight=1)

        ttk.Label(form, text="日期").grid(row=0, column=0, padx=(0, 8), pady=6, sticky="w")
        ttk.Entry(form, textvariable=self.date_var, width=24).grid(row=0, column=1, padx=(0, 16), pady=6, sticky="ew")

        ttk.Label(form, text="开始").grid(row=0, column=2, padx=(0, 8), pady=6, sticky="w")
        ttk.Entry(form, textvariable=self.start_var, width=10).grid(row=0, column=3, padx=(0, 16), pady=6, sticky="ew")

        ttk.Label(form, text="结束").grid(row=0, column=4, padx=(0, 8), pady=6, sticky="w")
        ttk.Entry(form, textvariable=self.end_var, width=10).grid(row=0, column=5, pady=6, sticky="ew")

        ttk.Label(form, text="刷新间隔").grid(row=1, column=0, padx=(0, 8), pady=6, sticky="w")
        interval_field = ttk.Frame(form)
        interval_field.grid(row=1, column=1, padx=(0, 16), pady=6, sticky="w")
        ttk.Entry(interval_field, textvariable=self.interval_var, width=8).pack(side=tk.LEFT)
        ttk.Label(interval_field, text="秒").pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(form, text="场馆").grid(row=2, column=0, padx=(0, 8), pady=6, sticky="w")
        venue_options = ttk.Frame(form)
        venue_options.grid(row=2, column=1, columnspan=5, pady=6, sticky="w")
        for venue in VENUES:
            ttk.Checkbutton(
                venue_options,
                text=venue.name,
                variable=self.venue_vars[venue.key],
            ).pack(side=tk.LEFT, padx=(0, 18))



        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X, pady=(12, 8))

        self.start_button = ttk.Button(buttons, text="开始PC版监控", command=self.start_monitoring)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(buttons, text="停止监控", command=self.stop_monitoring, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=8)
        self.stop_alarm_button = ttk.Button(buttons, text="停止报警", command=self.stop_alarm, state=tk.DISABLED)
        self.stop_alarm_button.pack(side=tk.LEFT)

        status = ttk.Label(root, textvariable=self.status_var)
        status.pack(fill=tk.X, pady=(0, 8))

        self.log = scrolledtext.ScrolledText(root, height=18, wrap=tk.WORD, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True)

        self._append_log(
            "PC版会打开桌面交我办并监控所选网球场。"
            "使用 Androws Android 自动化接口（ADB）运行。"
            "PC版只报警，不自动下单。"
        )

    def start_monitoring(self) -> None:
        cooldown_until = load_pc_rate_limit_cooldown()
        if cooldown_until:
            messagebox.showerror(
                "PC版今日请求已达上限",
                f"学校系统已经提示请求次数超过限制。\n\n建议不要继续刷新，请在 {cooldown_until.strftime('%Y-%m-%d %H:%M')} 后再试。",
            )
            self._append_log(f"PC版今日请求已达上限，已阻止启动监控。可在 {cooldown_until.strftime('%Y-%m-%d %H:%M')} 后再试。")
            return

        try:
            config = self.current_config()
        except ValueError as exc:
            messagebox.showerror("输入有误", str(exc))
            return

        if self.monitor_thread and self.monitor_thread.is_alive():
            return

        self.monitor = PCVenueMonitor(self.current_config, self.events)
        self.monitor_thread = threading.Thread(target=self.monitor.run, daemon=True)
        self.monitor_thread.start()

        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("PC版监控中")
        self._append_log(f"开始PC版监控：{config_label(config)}，发现空场即报警，不自动下单。")

    def current_config(self) -> MonitorConfig:
        venue_keys = tuple(key for key, var in self.venue_vars.items() if var.get())
        return parse_config(
            self.date_var.get(),
            self.start_var.get(),
            self.end_var.get(),
            venue_keys,
            self.interval_var.get(),
            False,
        )

    def _schedule_config_change(self, *_args) -> None:
        if self.config_change_after_id:
            self.after_cancel(self.config_change_after_id)
        self.config_change_after_id = self.after(500, self._apply_config_change)

    def _apply_config_change(self) -> None:
        self.config_change_after_id = None
        if not (self.monitor_thread and self.monitor_thread.is_alive() and self.monitor):
            return
        if self.monitor.stop_event.is_set():
            return

        try:
            config = self.current_config()
        except ValueError as exc:
            self.status_var.set("PC版监控条件有误")
            self._append_log(f"PC版监控条件暂未生效：{exc}")
            self.monitor.wake()
            return

        self.status_var.set("PC版监控中")
        self._append_log(f"已修改PC版监控条件，立即检查：{config_label(config)}")
        self.stop_alarm()
        self.monitor.wake()

    def stop_monitoring(self) -> None:
        if self.monitor:
            self.monitor.stop()
        self.stop_alarm()
        self.stop_button.configure(state=tk.DISABLED)
        self.status_var.set("正在停止")

    def stop_alarm(self) -> None:
        self.alarm.stop()
        self.stop_alarm_button.configure(state=tk.DISABLED)
        self.alert_popup_open = False
        if self.alert_window and self.alert_window.winfo_exists():
            self.alert_window.destroy()
        self.alert_window = None
        if self.status_var.get() == "发现空场":
            self.status_var.set("PC版监控中")

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._append_log(str(payload))
            elif kind == "error":
                self._append_log(str(payload))
                messagebox.showerror("PC版监控错误", str(payload))
                self._reset_controls()
            elif kind == "available":
                self._handle_available(payload)
            elif kind == "stopped":
                self._append_log(str(payload))
                self._reset_controls()

        self.after(200, self._drain_events)

    def _handle_available(self, slots: list[Slot]) -> None:
        lines = [f"{slot.venue} {slot.date.isoformat()} {slot.hour} {slot.court}" for slot in slots]
        message = "PC版发现可预约网球场：\n" + "\n".join(lines)
        self.status_var.set("发现空场")
        self._append_log(message)
        self.alarm.start()
        self.stop_alarm_button.configure(state=tk.NORMAL)

        if not self.alert_popup_open:
            self.alert_popup_open = True
            self.after(0, lambda: self._show_availability_alert(message))

    def _show_availability_alert(self, message: str) -> None:
        if self.alert_window and self.alert_window.winfo_exists():
            self.alert_window.lift()
            self.alert_window.focus_force()
            return

        popup = tk.Toplevel(self)
        self.alert_window = popup
        popup.title("PC版发现空场")
        popup.geometry("440x260")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.protocol("WM_DELETE_WINDOW", self.stop_alarm)

        container = ttk.Frame(popup, padding=18)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="PC版发现可预约网球场", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        ttk.Label(container, text=message, justify=tk.LEFT).pack(anchor="w", fill=tk.X, pady=(12, 16))
        ttk.Label(container, text="铃声会一直响，直到点击\u201c确定\u201d。").pack(anchor="w", pady=(0, 16))

        ttk.Button(container, text="确定", command=self.stop_alarm).pack(anchor="e")

        popup.transient(self)
        popup.grab_set()
        popup.lift()
        popup.focus_force()

    def _reset_controls(self) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.monitor = None
        self.monitor_thread = None
        if self.status_var.get() != "发现空场":
            self.status_var.set("未开始")

    def _append_log(self, message: str) -> None:
        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def destroy(self) -> None:
        self.stop_monitoring()
        super().destroy()


def main() -> None:
    PCApp().mainloop()


if __name__ == "__main__":
    main()
