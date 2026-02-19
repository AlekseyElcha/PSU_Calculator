import sys
import os
from datetime import datetime
from PyQt6.QtCore import Qt, QPoint, QTimer, QUrl
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QLineEdit, QProgressDialog
)
from PyQt6.QtGui import QDesktopServices, QCursor
import json
import requests
from multiprocessing import Process

from input_menu import InputMenu
from config_card import ConfigCard
from calls import CalculationWorker
from result_details import ResultDetailsDialog
import storage_sql as storage

def resource_path(rel_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel_path)
    return os.path.join(os.path.abspath("."), rel_path)

def load_stylesheet(filename: str) -> str:
    path = resource_path(os.path.join("client", filename))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"Файл стилей не найден: {path}")
        return ""

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._loading_workers = []
        self._active_workers = []
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.default_width, self.default_height = 1000, 700
        self.resize(self.default_width, self.default_height)

        self.drag_pos = None
        self.resizing_edge = None
        self.resize_margin = 8

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        central_widget.setMouseTracking(True)

        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.title_bar_widget = self._create_title_bar()
        self.main_layout.addWidget(self.title_bar_widget)

        content = QWidget()
        content.setMouseTracking(True)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 30, 30, 30)
        content_layout.setSpacing(20)
        self.main_layout.addWidget(content)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск...")
        self.search_input.setFixedWidth(300)
        self.search_input.textChanged.connect(self.on_search_text_changed)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._perform_search)

        self.add_btn = QPushButton("+ Новая конфигурация")
        self.add_btn.setObjectName("PrimaryButton")
        self.add_btn.clicked.connect(self.start_calculation)

        action_bar = QHBoxLayout()
        action_bar.addWidget(self.search_input)
        action_bar.addStretch()
        action_bar.addWidget(self.add_btn)
        content_layout.addLayout(action_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        self.card_container = QWidget()
        self.card_container.setStyleSheet("background: transparent;")
        self.card_layout = QVBoxLayout(self.card_container)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.card_layout.setSpacing(15)

        scroll.setWidget(self.card_container)
        content_layout.addWidget(scroll)

        self.overlay = QWidget(self)
        self.overlay.setStyleSheet("background-color: rgba(0, 0, 0, 120);")
        self.overlay.hide()

        storage.setup()
        self.load_from_db()


    def _cleanup_worker(self, worker):
        if worker in self._active_workers:
            self._active_workers.remove(worker)
        if worker.isRunning():
            worker.wait(1000)
        worker.deleteLater()


    def closeEvent(self, event):
        for worker in self._active_workers[:]:
            if worker.isRunning():
                worker.stop()
                worker.quit()
                if not worker.wait(2000):
                    worker.terminate()
                    worker.wait(1000)
            worker.deleteLater()
        
        self._active_workers.clear()
        event.accept()


    def _create_title_bar(self) -> QFrame:
        title_bar = QFrame()
        title_bar.setFixedHeight(50)
        title_bar.setStyleSheet("background-color: #0f172a; border-bottom: 1px solid #1e293b;")
        title_bar.setMouseTracking(True)

        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(15, 0, 10, 0)

        title = QLabel("Client")
        title.setStyleSheet("font-weight: bold; font-size: 16px; color: white; border: none;")
        layout.addWidget(title)

        version_link = QLabel("Актуальная версия доступна здесь!")
        version_link.setStyleSheet("""
            QLabel {
                color: #3498db;
                font-size: 12px;
                text-decoration: underline;
                border: none;
                padding: 5px 10px;
            }
            QLabel:hover {
                color: #5dade2;
                background-color: rgba(52, 152, 219, 0.1);
                border-radius: 4px;
            }
        """)
        version_link.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        version_link.mousePressEvent = lambda event: self._open_releases_page()
        layout.addWidget(version_link)
        
        layout.addStretch()

        btn_min = QPushButton("—")
        btn_min.setFixedSize(30, 30)
        btn_min.setObjectName("MinButton")
        btn_min.clicked.connect(self.showMinimized)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(30, 30)
        btn_close.setObjectName("IconButton")
        btn_close.clicked.connect(self.close)

        layout.addWidget(btn_min)
        layout.addWidget(btn_close)
        return title_bar

    def _open_releases_page(self):
        QDesktopServices.openUrl(QUrl("https://github.com/AlekseyElcha/PSU_Calculator/releases"))


    def load_from_db(self):
        rows = storage.get_all_configs()
        for row in rows:
            self.add_card_from_db(row)

    def add_card_from_db(self, row: dict):
        d = datetime.fromisoformat(row["created_at"]) if row.get("created_at") else datetime.now()
        psus_data = None
        p = row.get("psus")
        if p is not None:
            try:
                psus_data = json.loads(p) if isinstance(p, str) else p
            except Exception:
                psus_data = None

        card = ConfigCard(
            self._card_base_height(),
            row.get("name", ""),
            row.get("cpu"),
            row.get("gpu"),
            row.get("ram"),
            row.get("mem"),
            d,
            row.get("watts", "---"),
            db_id=row.get("id"),
            psus=psus_data
        )
        card.request_delete.connect(self._remove_card)
        card.renamed.connect(self._on_card_renamed)
        self.card_layout.insertWidget(0, card)

    def _card_base_height(self) -> int:
        h = self.height()
        return h if h > 200 else getattr(self, "default_height", 700)

    def on_search_text_changed(self):
        self.search_timer.start(220)

    def _perform_search(self):
        query = self.search_input.text().strip()
        self._filter_cards(query)

    def _filter_cards(self, query: str):
        tokens = [t.lower() for t in (query or "").split() if t]
        for i in range(self.card_layout.count()):
            w = self.card_layout.itemAt(i).widget()
            if not w:
                continue
            searchable = " ".join([
                str(getattr(w, "_name", "")),
                str(getattr(w, "_cpu", "")),
                str(getattr(w, "_gpu", "")),
                str(getattr(w, "_ram", "")),
                str(getattr(w, "_mem", "")),
                str(getattr(w, "_watts", "")),
            ]).lower()
            w.setVisible(all(tok in searchable for tok in tokens) if tokens else True)

    def _remove_card(self, card: ConfigCard):
        db_id = getattr(card, "_db_id", None)
        if db_id:
            storage.delete_config(db_id)
        self.card_layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()

    def _on_card_renamed(self, card: ConfigCard, new_name: str):
        db_id = getattr(card, "_db_id", None)
        if db_id:
            storage.rename_config(db_id, new_name)

    def start_calculation(self):
        self.add_btn.setText("Загрузка компонентов...")
        self.add_btn.setEnabled(False)

        worker = CalculationWorker(task="fetch")
        worker.finished.connect(self.open_menu)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._active_workers.append(worker)
        worker.start()

    def open_menu(self, api_data=None):
        self.add_btn.setText("+ Новая конфигурация")
        self.add_btn.setEnabled(True)

        if not api_data:
            try:
                r = requests.get("http://127.0.0.1:8000/cpus/")
                api_data = r.json()
                print(f"Загружено данных: cpus={len(api_data.get('cpus', []))}")
            except Exception as e:
                print("Ошибка API:", e)
                api_data = {"cpus": [], "gpus": [], "psus": []}

        print(f"Данные для диалога:")
        print(f"  CPUs: {len(api_data.get('cpus', []))}")
        print(f"  GPUs: {len(api_data.get('gpus', []))}")
        print(f"  RAMs: {len(api_data.get('rams', []))}")
        print(f"  Storages: {len(api_data.get('storages', []))}")
        print(f"  Cooling: {len(api_data.get('cooling', []))}")
        print(f"  Drives: {len(api_data.get('drives', []))}")
        print(f"  Motherboards: {len(api_data.get('motherboards', []))}")

        self.overlay.resize(self.size())
        self.overlay.show()

        dialog = InputMenu(
            self, 
            cpus=api_data.get("cpus", []), 
            gpus=api_data.get("gpus", []),
            rams=api_data.get("rams", []),
            storages=api_data.get("storages", []),
            cooling=api_data.get("cooling", []),
            drives=api_data.get("drives", []),
            motherboards=api_data.get("motherboards", [])
        )

        if dialog.exec():
            data = dialog.get_data()
            cpu_name = data.get("CPU", "")
            gpu_name = data.get("GPU", "")
            ram_name = data.get("RAM", "")
            ram_modules = data.get("RAM_modules", 1)
            storage_names = data.get("Storages", [])
            cooling_name = data.get("Cooling", "")
            drive_name = data.get("Drive", "")
            motherboard_name = data.get("Motherboard", "")
            power_margin = data.get("power_margin", 20)

            progress = QProgressDialog("Вычисление...", None, 0, 0, self)
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setCancelButton(None)
            progress.show()

            calc_worker = CalculationWorker(
                task="calc", 
                cpu_name=cpu_name, 
                gpu_name=gpu_name,
                ram_name=ram_name,
                ram_modules=ram_modules,
                storage_names=storage_names,
                cooling_name=cooling_name,
                drive_name=drive_name,
                motherboard_name=motherboard_name,
                power_margin=power_margin
            )
            calc_worker.finished.connect(lambda res: self._on_calc_finished(res, progress, cpu_name, gpu_name, ram_name, ram_modules, storage_names, cooling_name, drive_name, motherboard_name, power_margin))
            calc_worker.finished.connect(lambda: self._cleanup_worker(calc_worker))
            self._active_workers.append(calc_worker)
            calc_worker.start()

        self.overlay.hide()

    def _on_calc_finished(self, result, progress, cpu_name="", gpu_name="", ram_name="",
                          ram_modules=1, storage_names=None, cooling_name="", drive_name="",
                          motherboard_name="", power_margin=20):
        if progress:
            progress.close()

        if not result or result.get("error"):
            print("Ошибка:", result.get("error"))
            return

        required = result.get("required", "---")
        psus = result.get("psus", [])
        ram_modules_count = result.get("ram_modules", 1)

        ram_display = f"{ram_name} x{ram_modules_count}" if ram_name and ram_modules_count > 1 else ram_name

        storage_names = storage_names or []
        storage_display = ", ".join([name for name in storage_names if name.strip()]) if storage_names else ""

        details_data = {
            "cpu_name": cpu_name,
            "gpu_name": gpu_name,
            "ram_name": ram_name,
            "storage_names": storage_names,
            "storage_display": storage_display,
            "cooling_name": cooling_name,
            "drive_name": drive_name,
            "motherboard_name": motherboard_name,
            "cpu_w": result.get("cpu_w", 0),
            "gpu_w": result.get("gpu_w", 0),
            "ram_w": result.get("ram_w", 0),
            "ram_w_single": result.get("ram_w_single", 0),
            "ram_modules": ram_modules_count,
            "storage_w": result.get("storage_w", 0),
            "storage_details": result.get("storage_details", []),
            "cooling_w": result.get("cooling_w", 0),
            "drive_w": result.get("drive_w", 0),
            "motherboard_w": result.get("motherboard_w", 0),
            "overhead": result.get("overhead", 200),
            "raw_total": result.get("raw_total", 0),
            "power_margin": power_margin,
            "required": required
        }
        
        details_dialog = ResultDetailsDialog(self, details_data)
        details_dialog.exec()

        data = {
            "name": "Результат",
            "cpu": cpu_name,
            "gpu": gpu_name,
            "ram": ram_display,
            "mem": storage_display,
            "watts": required,
            "psus": psus
        }

        new_id = storage.add_config_dict(data)

        card = ConfigCard(
            self._card_base_height(),
            "Результат",
            cpu_name,
            gpu_name,
            ram_display,
            storage_display,
            datetime.now(),
            required,
            psus=psus,
            db_id=new_id
        )
        card.request_delete.connect(self._remove_card)
        card.renamed.connect(self._on_card_renamed)
        self.card_layout.insertWidget(0, card)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    storage.setup()

    stylesheet = load_stylesheet("style.qss")
    if stylesheet:
        app.setStyleSheet(stylesheet)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
