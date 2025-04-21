import tkinter as tk
from tkinter import ttk, messagebox, Menu, scrolledtext, simpledialog
import subprocess
import threading
import time
import os
import json
from datetime import datetime
import sys
import logging

# ─── Константы ──────────────────────────────────────────────────────────────────
TILE_COLUMNS = 8                  # Количество столбцов плиток
LOG_FILE = 'error_log.txt'        # Файл для логирования ошибок
SETTINGS_FILE = 'settings.json'   # Файл с настройками приложения
AUTO_SAVE_INTERVAL = 86400        # Интервал автосохранения журнала (сек)

# ─── Настройка логирования ───────────────────────────────────────────────────────
logging.basicConfig(
    filename=LOG_FILE,
    filemode='a',
    encoding='utf-8',
    level=logging.ERROR,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def log_error(msg):
    # ─── Записывает сообщение об ошибке в лог-файл.
    logging.error(msg)

# ─── Класс для корректного ввода кириллицы в StringVar ──────────────────────────
original_StringVar = tk.StringVar
class FormatStringVar(original_StringVar):
    def __init__(self, master=None, value=None, name=None):
        # Поддержка вызова с первым аргументом = значение
        if isinstance(master, str) and value is None:
            value, master = master, None
        super().__init__(master=master, value=value, name=name)
        self.trace_add('write', self._on_write)
        # Карта байт CP1251 -> символ Unicode
        chars = (
            'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'
            'абвгдежзийклмнопрстуфхцчшщъыьэюя'
            'ЁёІіЇїЄє'
        )
        self._map = {i+192: c for i, c in enumerate(chars)}

    def _on_write(self, *args):
        val = self.get()
        corrected = ''.join(self._map.get(ord(c), c) for c in val)
        if corrected != val:
            self.set(corrected)

tk.StringVar = FormatStringVar

# ─── Основное приложение ─────────────────────────────────────────────────────────
class NetMonitorApp:
    def __init__(self):
        # Инициализация переменных
        self.monitors = []         # Список объектов DeviceMonitor
        self.row_frames = []       # Список фреймов-строк плиток
        self.full_log = []         # Список записей журнала (Text + tag)
        self.log_lock = threading.Lock()
        self.auto_save_timer = None
        self.start_time = None     # Время начала мониторинга
        self.timer_running = False
        self.last_save_time = None
        
        # Загрузка настроек и локализации
        self.settings = self._load_json(SETTINGS_FILE, default={'language':'ru','devices':{}})
        self.lang = self._load_json(f"lang_{self.settings.get('language','ru')}.json", default={})

        # Создание главного окна
        self.root = tk.Tk()
        self._setup_ui()
        self._start_timer_loop()

        # Перехват закрытия
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.root.mainloop()

    def _load_json(self, path, default):
        # Безопасная загрузка JSON-файла
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            log_error(f"Ошибка загрузки {path}: {e}")
        return default

    def _save_json(self, path, data):
        # Безопасная запись JSON-файла
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            log_error(f"Ошибка сохранения {path}: {e}")

    def _setup_ui(self):
        # Настройка интерфейса главного окна
        self.root.title('NET Monitor')
        self.root.state('zoomed')

        # Панель кнопок
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill=tk.X)
        buttons = [
            ('start', self._start_monitoring),
            ('stop', self._stop_monitoring),
            ('reset', self._reset_all),
            ('add', self._add_device),
            ('delete', self._delete_devices),
            ('show_log', self._show_log_window),
            ('summary', self._show_summary),
            ('settings', self._open_settings)
        ]
        for key, cmd in buttons:
            ttk.Button(
                toolbar,
                text=self.lang.get(key, key.capitalize()),
                command=cmd
            ).pack(side=tk.LEFT, padx=2)

        # Метка таймера
        self.timer_label = ttk.Label(toolbar, text='0:00:00:00', foreground='green', background='black')
        self.timer_label.pack(side=tk.RIGHT, padx=10)

        # Создание строк плиток устройств
        self._init_device_tiles()

    def _init_device_tiles(self):
        # Инициализация плиток устройств из настроек
        devices = self.settings.get('devices', {})
        for idx, (ip, name) in enumerate(devices.items()):
            if idx % TILE_COLUMNS == 0:
                frame = ttk.Frame(self.root)
                frame.pack(fill=tk.X)
                self.row_frames.append(frame)
            monitor = DeviceMonitor(frame, ip, name, self)
            self.monitors.append(monitor)

    def _start_timer_loop(self):
        # Запускает обновление таймера каждую секунду
        self._update_timer()
        self.root.after(1000, self._start_timer_loop)

    def _update_timer(self):
        # Обновляет отображение времени работы
        if self.timer_running and self.start_time:
            delta = datetime.now() - self.start_time
            d, rem = delta.days, delta.seconds
            h, rem = divmod(rem, 3600)
            m, s = divmod(rem, 60)
            self.timer_label.config(text=f"{d}:{h:02}:{m:02}:{s:02}")

    def _on_close(self):
        # Завершение авто-сохранения и закрытие приложения
        if self.auto_save_timer:
            self.auto_save_timer.cancel()
        self.root.destroy()

    # ─── Методы мониторинга ───────────────────────────────────────────────────────
    def _start_monitoring(self):
        # Запускает мониторинг всех устройств
        if not self.timer_running:
            self.timer_running = True
            self.start_time = datetime.now()
            self.last_save_time = self.start_time
            self._schedule_auto_save()
        for m in self.monitors:
            m.start_monitoring()

    def _stop_monitoring(self):
        # Останавливает мониторинг
        if self.timer_running:
            self.timer_running = False
            if self.auto_save_timer:
                self.auto_save_timer.cancel()
        for m in self.monitors:
            m.stop_monitoring()

    def _reset_all(self):
        # Сбрасывает данные мониторинга и таймер
        for m in self.monitors:
            m.reset()
        self.start_time = None
        self.timer_running = False
        self.timer_label.config(text='0:00:00:00')

    # ─── Методы работы с устройствами ─────────────────────────────────────────────
    def _add_device(self):
        # Добавление нового устройства
        ip = simpledialog.askstring(self.lang.get('add','Добавить'), self.lang.get('enter_ip','Введите IP:'), parent=self.root)
        if not ip or ip in self.settings['devices']:
            return
        name = simpledialog.askstring(self.lang.get('add','Добавить'), self.lang.get('enter_name','Введите имя:'), parent=self.root)
        if not name:
            return
        # Создание новой плитки
        if not self.row_frames or len(self.row_frames[-1].winfo_children()) >= TILE_COLUMNS:
            frame = ttk.Frame(self.root)
            frame.pack(fill=tk.X)
            self.row_frames.append(frame)
        else:
            frame = self.row_frames[-1]
        monitor = DeviceMonitor(frame, ip, name, self)
        self.monitors.append(monitor)
        self.settings['devices'][ip] = name
        self._save_json(SETTINGS_FILE, self.settings)
        if self.timer_running:
            monitor.start_monitoring()

    def _delete_devices(self):
        # Удаление отмеченных устройств
        to_del = [m for m in self.monitors if m.selected.get()]
        if not to_del:
            messagebox.showinfo(self.lang.get('delete','Удалить'), self.lang.get('no_selection','Не выбрано устройств'), parent=self.root)
            return
        names = ', '.join(m.name for m in to_del)
        if not messagebox.askyesno(self.lang.get('delete','Удалить'), f"Удалить: {names}?"): return
        for m in to_del:
            m.stop_monitoring()
            self.monitors.remove(m)
            self.settings['devices'].pop(m.ip, None)
            m.frame.destroy()
        self._save_json(SETTINGS_FILE, self.settings)

    # ─── Методы журнала ────────────────────────────────────────────────────────────
    def _show_log_window(self):
        # Отображает окно журнала
        if hasattr(self, 'log_window') and self.log_window.winfo_exists():
            return
        self.log_window = tk.Toplevel(self.root)
        self.log_window.title(self.lang.get('log_window_title','Журнал'))
        self.log_text = scrolledtext.ScrolledText(self.log_window, wrap=tk.WORD, width=80, height=20, state='disabled')
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_config('error', foreground='red')
        self.log_text.bind('<Button-3>', lambda e: Menu(self.log_text, tearoff=0).tk_popup(e.x_root, e.y_root))

        frame = ttk.Frame(self.log_window)
        frame.pack(fill=tk.X, pady=5)
        ttk.Button(frame, text=self.lang.get('clear_log','Очистить журнал'), command=self._clear_log).pack(side=tk.LEFT, padx=5)
        ttk.Button(frame, text=self.lang.get('save_log','Сохранить журнал'), command=lambda: self._save_log(True)).pack(side=tk.RIGHT, padx=5)
        self.log_window.protocol('WM_DELETE_WINDOW', self.log_window.destroy)

        # Вывод существующих записей
        with self.log_lock:
            self.log_text.config(state='normal')
            for txt, tag in self.full_log:
                self.log_text.insert(tk.END, txt, tag)
            self.log_text.config(state='disabled')

    def _clear_log(self):
        # Очищает журнал в окне и в памяти
        with self.log_lock:
            self.full_log.clear()
        if hasattr(self, 'log_text'):
            self.log_text.config(state='normal')
            self.log_text.delete(1.0, tk.END)
            self.log_text.config(state='disabled')

    def _save_log(self, notify=False):
        # Сохраняет журнал в файл log_YYYYMMDD_HHMMSS.txt
        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            fn = f'log_{ts}.txt'
            with self.log_lock:
                data = ''.join(txt for txt, _ in self.full_log)
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(data)
            self.last_save_time = datetime.now()
            if notify:
                messagebox.showinfo(self.lang.get('log_saved','Сохранение'), f'Журнал сохранён в {fn}')
            return True
        except Exception as e:
            log_error(f"Ошибка сохранения журнала: {e}")
            return False

    def _schedule_auto_save(self):
        # Планирует автосохранение каждые AUTO_SAVE_INTERVAL секунд
        def job():
            if self.timer_running and self.last_save_time and (datetime.now()-self.last_save_time).total_seconds()>=AUTO_SAVE_INTERVAL:
                self._save_log()
            self.auto_save_timer = threading.Timer(AUTO_SAVE_INTERVAL, job)
            self.auto_save_timer.daemon = True
            self.auto_save_timer.start()
        job()

    # ─── Методы сводки ─────────────────────────────────────────────────────────────
    def _show_summary(self):
        # Отображает окно сводки по простою устройств
        win = tk.Toplevel(self.root)
        win.title(self.lang.get('summary_title','Сводка'))
        ta = scrolledtext.ScrolledText(win, wrap=tk.WORD, width=80, height=20)
        ta.pack(fill=tk.BOTH, expand=True)
        def upd():
            if not win.winfo_exists(): return
            lines = sum((m.get_downtime_summary() for m in self.monitors), [])
            ta.config(state='normal')
            ta.delete(1.0, tk.END)
            ta.insert(tk.END, '\n'.join(lines) or self.lang.get('no_issues','Нет неполадок'))
            ta.config(state='disabled')
            win.after(5000, upd)
        upd()

    # ─── Методы настроек ───────────────────────────────────────────────────────────
    def _open_settings(self):
        # Открывает окно настроек языка
        def save_and_close():
            self.settings['language'] = var.get()
            self._save_json(SETTINGS_FILE, self.settings)
            settings_win.destroy()
            messagebox.showinfo('Info','Перезапустите приложение для применения изменений')
        settings_win = tk.Toplevel(self.root)
        settings_win.title(self.lang.get('settings','Настройки'))
        ttk.Label(settings_win, text=self.lang.get('language','Язык')).pack(pady=5)
        var = original_StringVar(value=self.settings.get('language','ru'))
        ttk.Radiobutton(settings_win, text='English', variable=var, value='en').pack()
        ttk.Radiobutton(settings_win, text='Русский', variable=var, value='ru').pack()
        ttk.Button(settings_win, text=self.lang.get('save','Сохранить'), command=save_and_close).pack(pady=10)
        settings_win.transient(self.root)
        settings_win.grab_set()
        self.root.wait_window(settings_win)

# ─── Класс мониторинга одного устройства ─────────────────────────────────────────
class DeviceMonitor:
    def __init__(self, parent, ip, name, app_ref):
        # Сохраняем ссылку на приложение и параметры
        self.app = app_ref
        self.ip = ip
        self.name = name
        self.availability = []      # История статуса (1 или 0)
        self.is_down = False        # Флаг простоя
        self.current_downtime_start = None

        # Создание фрейма плитки
        self.frame = ttk.Frame(parent, relief=tk.RIDGE, padding=5)
        self.frame.config(width=200, height=150)
        self.frame.pack_propagate(False)
        self.frame.pack(side=tk.LEFT, padx=5, pady=5)

        # Чекбокс для выбора
        self.selected = tk.BooleanVar(master=self.frame)
        ttk.Checkbutton(self.frame, variable=self.selected).place(relx=1, rely=0, anchor='ne', x=-4, y=4)

        # Метка имени и IP
        self.label = ttk.Label(self.frame, text=f"{self.name}: {self.ip}")
        self.label.pack(anchor='w')

        # Canvas для графического отображения доступности
        self.canvas = tk.Canvas(self.frame, width=190, height=80, bg='white')
        self.canvas.pack(pady=4)

        # Метка потерь пакетов
        self.packet_loss_label = ttk.Label(self.frame, text=f"Потеря пакетов: 0%")
        self.packet_loss_label.pack(anchor='w')

        # Двойной клик для переименования
        self.canvas.bind('<Double-1>', self._rename_device)

    def start_monitoring(self):
        # Запуск фонового потока мониторинга
        self.is_monitoring = True
        threading.Thread(target=self._monitor, daemon=True).start()

    def stop_monitoring(self):
        # Остановка мониторинга
        self.is_monitoring = False

    def reset(self):
        # Сброс данных графика
        self.availability.clear()
        self.packet_loss_label.config(text="Потеря пакетов: 0%")
        self._update_ui()

    def _monitor(self):
        # Основной цикл проверки доступности
        while self.is_monitoring:
            try:
                res = subprocess.run(["ping", "-n", "1", self.ip], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out = res.stdout.decode('cp866', errors='ignore')
                lost = "100% loss" in out or "100% потерь" in out
                ts = datetime.now()
                status = 0 if lost else 1

                # Обработка начала/окончания простоя
                if lost and not self.is_down:
                    self.is_down = True
                    self.current_downtime_start = ts
                elif not lost and self.is_down:
                    self.is_down = False
                    self.app.monitors[self.app.monitors.index(self)].downtime_summary.append((self.current_downtime_start, ts))
                    self.current_downtime_start = None

                # Запись в общий журнал
                entry = [(f"[{ts.strftime('%H:%M:%S')}] ", None),
                         (f"Обмен с {self.name}[{self.ip}]\n", None),
                         ("Ответ не получен\n" if lost else "Ответ получен\n", 'error' if lost else None)]
                with self.app.log_lock:
                    self.app.full_log.extend(entry)
                if hasattr(self.app, 'log_text') and self.app.log_text.winfo_exists():
                    self.app.root.after(0, self.app.log_text.insert, tk.END, ''.join(txt for txt, _ in entry))

                # Обновление данных и UI
                self.availability.append(status)
                if len(self.availability) > 720:
                    self.availability.pop(0)
                loss_pct = (1 - sum(self.availability)/len(self.availability)) * 100
                self.app.root.after(0, lambda: self.packet_loss_label.config(text=f"Потеря пакетов: {loss_pct:.2f}%"))
                self.app.root.after(0, self._update_ui)
                time.sleep(5)
            except Exception as e:
                log_error(f"Ошибка мониторинга {self.ip}: {e}")

    def _update_ui(self):
        # Отрисовка графика доступности
        self.canvas.delete('all')
        for i, v in enumerate(self.availability):
            x, y = i*3, 80 - v*70
            color = 'green' if v else 'red'
            self.canvas.create_rectangle(x, y, x+2, 80, fill=color, outline=color)

    def _rename_device(self, _):
        # Переименование устройства через диалог
        new_name = simpledialog.askstring(self.app.lang.get('rename','Переименование'), self.app.lang.get('rename_prompt','Введите новое имя:'), parent=self.frame)
        if new_name:
            self.name = new_name
            self.label.config(text=f"{self.name}: {self.ip}")
            self.app.settings['devices'][self.ip] = new_name
            self.app._save_json(SETTINGS_FILE, self.app.settings)

    def get_downtime_summary(self):
        # Возвращает список строк с периодами простоя
        lines = []
        for start, end in getattr(self, 'downtime_summary', []):
            lines.append(f"С {start.strftime('%H:%M:%S')} по {end.strftime('%H:%M:%S')} {start.strftime('%d.%m')}: {self.name}")
        if self.is_down and self.current_downtime_start:
            d = self.current_downtime_start
            lines.append(f"С {d.strftime('%H:%M:%S')} по наст. время {d.strftime('%d.%m')}: {self.name}")
        return lines

if __name__ == '__main__':
    NetMonitorApp()
