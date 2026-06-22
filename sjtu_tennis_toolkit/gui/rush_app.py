"""Rush booking tkinter GUI."""

from __future__ import annotations

import datetime as dt
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from sjtu_tennis_toolkit.browser.rusher import RushBooker
from sjtu_tennis_toolkit.config import (
    is_rush_start_allowed,
    load_rate_limit_cooldown,
    parse_rush_config,
    rush_config_label,
    rush_deadline_datetime,
    rush_release_datetime,
    rush_target_date,
    rush_time_options,
)
from sjtu_tennis_toolkit.models import RushConfig, Slot, VENUES


class RushApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("交我办网球场抢场器")
        self.geometry("720x500")
        self.minsize(680, 460)

        self.events: queue.Queue = queue.Queue()
        self.booker: RushBooker | None = None
        self.booker_thread: threading.Thread | None = None
        self.ordered = False

        self.date_var = tk.StringVar(value=rush_target_date().isoformat())
        self.time_var = tk.StringVar(value="21:00-22:00")
        self.venue_var = tk.StringVar(value=VENUES[0].name)
        self.court_var = tk.StringVar(value="1")
        self.release_time_var = tk.StringVar(value="12:00:00")
        self.status_var = tk.StringVar(value="未开始")

        self._build_ui()
        self.after(200, self._drain_events)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        form = ttk.LabelFrame(root, text="抢场条件", padding=12)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        ttk.Label(form, text="日期").grid(row=0, column=0, padx=(0, 8), pady=6, sticky="w")
        self.date_entry = ttk.Entry(form, textvariable=self.date_var, width=18, state="readonly")
        self.date_entry.grid(row=0, column=1, padx=(0, 16), pady=6, sticky="ew")

        ttk.Label(form, text="时间").grid(row=0, column=2, padx=(0, 8), pady=6, sticky="w")
        self.time_combo = ttk.Combobox(
            form,
            textvariable=self.time_var,
            values=rush_time_options(),
            state="readonly",
            width=16,
        )
        self.time_combo.grid(row=0, column=3, pady=6, sticky="ew")

        ttk.Label(form, text="场馆").grid(row=1, column=0, padx=(0, 8), pady=6, sticky="w")
        self.venue_combo = ttk.Combobox(
            form,
            textvariable=self.venue_var,
            values=tuple(venue.name for venue in VENUES),
            state="readonly",
            width=18,
        )
        self.venue_combo.grid(row=1, column=1, padx=(0, 16), pady=6, sticky="ew")

        ttk.Label(form, text="场地号").grid(row=1, column=2, padx=(0, 8), pady=6, sticky="w")
        self.court_combo = ttk.Combobox(
            form,
            textvariable=self.court_var,
            values=tuple(str(court) for court in range(1, 9)),
            state="readonly",
            width=8,
        )
        self.court_combo.grid(row=1, column=3, pady=6, sticky="ew")

        ttk.Label(form, text="开始抢场").grid(row=2, column=0, padx=(0, 8), pady=6, sticky="w")
        self.release_time_entry = ttk.Entry(form, textvariable=self.release_time_var, width=18)
        self.release_time_entry.grid(row=2, column=1, padx=(0, 16), pady=6, sticky="ew")

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X, pady=(12, 8))

        self.start_button = ttk.Button(buttons, text="开始抢场", command=self.start_rush)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(buttons, text="停止抢场", command=self.stop_rush, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=8)

        status = ttk.Label(root, textvariable=self.status_var)
        status.pack(fill=tk.X, pady=(0, 8))

        self.log = scrolledtext.ScrolledText(root, height=18, wrap=tk.WORD, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True)

        self._append_log("设置开始抢场时间后点击开始。程序会提前打开网页等待登录，并在指定时间刷新抢场。")

    def start_rush(self) -> None:
        self.date_var.set(rush_target_date().isoformat())
        now = dt.datetime.now()

        try:
            config = self.current_config()
        except ValueError as exc:
            messagebox.showerror("输入有误", str(exc))
            return

        if not is_rush_start_allowed(now, config.release_time):
            deadline = rush_deadline_datetime(now, config.release_time)
            message = f"抢场时间已过。本次抢场截止时间为 {deadline.strftime('%H:%M:%S')}。"
            self.status_var.set("抢场失败")
            self._append_log(message)
            messagebox.showwarning("抢场时间已过", message)
            return

        cooldown_until = load_rate_limit_cooldown()
        if cooldown_until:
            messagebox.showerror(
                "今日请求已达上限",
                f"学校系统已经提示请求次数超过限制。\n\n建议不要继续刷新，请在 {cooldown_until.strftime('%Y-%m-%d %H:%M')} 后再试。",
            )
            self._append_log(f"今日请求已达上限，已阻止启动抢场。可在 {cooldown_until.strftime('%Y-%m-%d %H:%M')} 后再试。")
            return

        if self.booker_thread and self.booker_thread.is_alive():
            return

        self.ordered = False
        self.booker = RushBooker(self.current_config, self.events)
        self.booker_thread = threading.Thread(target=self.booker.run, daemon=True)
        self.booker_thread.start()

        self._set_inputs_enabled(False)
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        release_at = rush_release_datetime(now, config.release_time)
        self.status_var.set(
            f"等待 {config.release_time.strftime('%H:%M:%S')}"
            if now < release_at
            else "抢场中"
        )
        self._append_log(f"开始抢场：{rush_config_label(config)}")

    def current_config(self) -> RushConfig:
        venue_key = self._venue_key_from_name(self.venue_var.get())
        return parse_rush_config(
            self.time_var.get(),
            venue_key,
            self.court_var.get(),
            release_time_text=self.release_time_var.get(),
        )

    def stop_rush(self) -> None:
        if self.booker:
            self.booker.stop()
        self.stop_button.configure(state=tk.DISABLED)
        self.status_var.set("正在停止")

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._append_log(str(payload))
            elif kind == "status":
                self.status_var.set(str(payload))
            elif kind == "error":
                self._append_log(str(payload))
                messagebox.showerror("抢场错误", str(payload))
                self._reset_controls("抢场失败")
            elif kind == "failed":
                self._append_log(str(payload))
                self.status_var.set("抢场失败")
            elif kind == "ordered":
                self._handle_ordered(payload)
            elif kind == "stopped":
                self._append_log(str(payload))
                if not self.ordered:
                    self._reset_controls("已停止" if self.status_var.get() != "抢场失败" else "抢场失败")
                else:
                    self._reset_controls("已下单")

        self.after(200, self._drain_events)

    def _handle_ordered(self, slot: Slot) -> None:
        self.ordered = True
        message = f"已提交订单：{slot.venue} {slot.date.isoformat()} {slot.hour} {slot.court}"
        self.status_var.set("已下单")
        self._append_log(message)
        messagebox.showinfo("已下单", message)

    def _reset_controls(self, status: str) -> None:
        self._set_inputs_enabled(True)
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.booker = None
        self.booker_thread = None
        self.status_var.set(status)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        combo_state = "readonly" if enabled else tk.DISABLED
        self.date_entry.configure(state="readonly")
        self.time_combo.configure(state=combo_state)
        self.venue_combo.configure(state=combo_state)
        self.court_combo.configure(state=combo_state)
        self.release_time_entry.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def _append_log(self, message: str) -> None:
        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _venue_key_from_name(self, venue_name: str) -> str:
        for venue in VENUES:
            if venue.name == venue_name:
                return venue.key
        raise ValueError(f"未知场馆：{venue_name}")

    def destroy(self) -> None:
        if self.booker:
            self.booker.stop()
        super().destroy()


def main() -> None:
    RushApp().mainloop()


if __name__ == "__main__":
    main()
