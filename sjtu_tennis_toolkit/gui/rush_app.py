"""Rush booking tkinter GUI."""

from __future__ import annotations

import datetime as dt
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from sjtu_tennis_toolkit.browser.rusher import RushBooker
from sjtu_tennis_toolkit.config import (
    HUXIAOMING_COURT_SCOPE_OPTIONS,
    MAX_RUSH_TIME_SLOTS,
    is_rush_start_allowed,
    load_rate_limit_cooldown,
    parse_rush_config,
    rush_allowed_courts,
    rush_config_label,
    rush_deadline_datetime,
    rush_release_datetime,
    rush_target_date,
    rush_time_options,
)
from sjtu_tennis_toolkit.models import RushConfig, Slot, VENUES


RUSH_TIME_LABELS = (
    "第一时间",
    "第二时间",
    "第三时间",
    "第四时间",
    "第五时间",
    "第六时间",
    "第七时间",
)


class RushApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("交我办网球场抢场器")
        self.geometry("760x680")
        self.minsize(720, 600)

        self.events: queue.Queue = queue.Queue()
        self.booker: RushBooker | None = None
        self.booker_thread: threading.Thread | None = None
        self.ordered = False
        self.inputs_enabled = True

        self.date_var = tk.StringVar(value=rush_target_date().isoformat())
        self.time_options = rush_time_options()
        self.time_vars = [tk.StringVar(value="21:00-22:00")]
        self.time_combos: list[ttk.Combobox] = []
        self.venue_var = tk.StringVar(value=VENUES[0].name)
        self.huxiaoming_scope_var = tk.StringVar(value="全部都要")
        self.court_var = tk.StringVar(value="1")
        self.release_time_var = tk.StringVar(value="12:00:00")
        self.status_var = tk.StringVar(value="未开始")

        self._build_ui()
        self.venue_var.trace_add("write", self._update_venue_controls)
        self.huxiaoming_scope_var.trace_add("write", self._update_venue_controls)
        self._update_venue_controls()
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

        ttk.Label(form, text="开始抢场").grid(row=0, column=2, padx=(0, 8), pady=6, sticky="w")
        self.release_time_entry = ttk.Entry(form, textvariable=self.release_time_var, width=18)
        self.release_time_entry.grid(row=0, column=3, pady=6, sticky="ew")

        ttk.Label(form, text="场馆").grid(row=1, column=0, padx=(0, 8), pady=6, sticky="w")
        self.venue_combo = ttk.Combobox(
            form,
            textvariable=self.venue_var,
            values=tuple(venue.name for venue in VENUES),
            state="readonly",
            width=18,
        )
        self.venue_combo.grid(row=1, column=1, padx=(0, 16), pady=6, sticky="ew")

        ttk.Label(form, text="场地范围").grid(row=1, column=2, padx=(0, 8), pady=6, sticky="w")
        self.huxiaoming_scope_combo = ttk.Combobox(
            form,
            textvariable=self.huxiaoming_scope_var,
            values=HUXIAOMING_COURT_SCOPE_OPTIONS,
            state=tk.DISABLED,
            width=16,
        )
        self.huxiaoming_scope_combo.grid(row=1, column=3, pady=6, sticky="ew")

        ttk.Label(form, text="场地号").grid(
            row=2,
            column=0,
            padx=(0, 8),
            pady=9,
            sticky="nw",
        )
        self.court_combo = ttk.Combobox(
            form,
            textvariable=self.court_var,
            values=tuple(str(court) for court in range(1, 9)),
            state="readonly",
            width=8,
        )
        self.court_combo.grid(
            row=2,
            column=1,
            padx=(0, 16),
            pady=6,
            sticky="new",
        )

        time_panel = ttk.Frame(form)
        time_panel.grid(row=2, column=2, columnspan=2, pady=6, sticky="nsew")
        time_panel.columnconfigure(0, weight=1)

        self.time_rows_frame = ttk.Frame(time_panel)
        self.time_rows_frame.grid(row=0, column=0, sticky="ew")
        self.time_rows_frame.columnconfigure(1, weight=1)

        time_buttons = ttk.Frame(time_panel)
        time_buttons.grid(row=1, column=0, pady=(4, 0), sticky="w")
        self.add_time_button = ttk.Button(
            time_buttons,
            text="新增时间",
            command=self._add_time,
        )
        self.add_time_button.pack(side=tk.LEFT)
        self.delete_time_button = ttk.Button(
            time_buttons,
            text="删除时间",
            command=self._delete_time,
        )
        self.delete_time_button.pack(side=tk.LEFT, padx=(8, 0))
        self._render_time_rows()

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

        self._append_log(
            "默认只有第一时间，可依次新增到第七时间，并从末尾删除。程序按时间序号依次尝试；"
            "胡晓明网球场可限定室外场（1-5、8）、室内场（6、7）或全部场地。"
        )

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
            self.time_vars[0].get(),
            venue_key,
            self.court_var.get(),
            release_time_text=self.release_time_var.get(),
            huxiaoming_court_scope=self.huxiaoming_scope_var.get(),
            time_range_texts=tuple(var.get() for var in self.time_vars),
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
        self.inputs_enabled = enabled
        combo_state = "readonly" if enabled else tk.DISABLED
        self.date_entry.configure(state="readonly")
        self.venue_combo.configure(state=combo_state)
        self.court_combo.configure(state=combo_state)
        for combo in self.time_combos:
            combo.configure(state=combo_state)
        self.release_time_entry.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        self._update_venue_controls()
        self._update_time_button_states()

    def _update_venue_controls(self, *_args) -> None:
        venue_key = self._venue_key_from_name(self.venue_var.get())
        is_huxiaoming = venue_key == "huxiaoming"
        if not is_huxiaoming and self.huxiaoming_scope_var.get() != "全部都要":
            self.huxiaoming_scope_var.set("全部都要")

        allowed_courts = rush_allowed_courts(
            venue_key,
            self.huxiaoming_scope_var.get(),
        )
        court_values = tuple(str(court) for court in allowed_courts)
        self.court_combo.configure(values=court_values)
        if self.court_var.get() not in court_values:
            self.court_var.set(court_values[0])

        scope_state = "readonly" if self.inputs_enabled and is_huxiaoming else tk.DISABLED
        self.huxiaoming_scope_combo.configure(state=scope_state)

    def _render_time_rows(self) -> None:
        for child in self.time_rows_frame.winfo_children():
            child.destroy()

        self.time_combos = []
        combo_state = "readonly" if self.inputs_enabled else tk.DISABLED
        for index, variable in enumerate(self.time_vars):
            ttk.Label(
                self.time_rows_frame,
                text=RUSH_TIME_LABELS[index],
            ).grid(row=index, column=0, padx=(0, 8), pady=3, sticky="w")
            combo = ttk.Combobox(
                self.time_rows_frame,
                textvariable=variable,
                values=self.time_options,
                state=combo_state,
                width=16,
            )
            combo.grid(row=index, column=1, pady=3, sticky="ew")
            self.time_combos.append(combo)
        self._update_time_button_states()

    def _add_time(self) -> None:
        if not self.inputs_enabled or len(self.time_vars) >= MAX_RUSH_TIME_SLOTS:
            return
        selected = {variable.get() for variable in self.time_vars}
        default_value = next(
            (option for option in self.time_options if option not in selected),
            self.time_options[0],
        )
        self.time_vars.append(tk.StringVar(value=default_value))
        self._render_time_rows()

    def _delete_time(self) -> None:
        if not self.inputs_enabled or len(self.time_vars) <= 1:
            return
        self.time_vars.pop()
        self._render_time_rows()

    def _update_time_button_states(self) -> None:
        if not hasattr(self, "add_time_button"):
            return
        add_enabled = self.inputs_enabled and len(self.time_vars) < MAX_RUSH_TIME_SLOTS
        delete_enabled = self.inputs_enabled and len(self.time_vars) > 1
        self.add_time_button.configure(state=tk.NORMAL if add_enabled else tk.DISABLED)
        self.delete_time_button.configure(state=tk.NORMAL if delete_enabled else tk.DISABLED)

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
