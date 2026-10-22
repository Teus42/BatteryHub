import ctypes
import queue
import threading
import tkinter as tk
from ctypes import wintypes

import pystray
from pystray._util import win32 as win32api

from . import system, theme
from .config import APP_NAME, DATA_DIR
from .db import Database
from .server import HubServer
from .status import is_offline
from .theme import make_icon_image
from .tray_popup import TrayPopup
from .ui import Panel

OFFLINE_CHECK_SECONDS = 60


class TrayIcon(pystray.Icon):
    """Ícone da bandeja sem o menu nativo: os cliques viram eventos para o popup próprio."""

    def __init__(self, *args, on_left_click, on_right_click, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_left_click = on_left_click
        self._on_right_click = on_right_click

    def _on_notify(self, wparam, lparam):
        if lparam == win32api.WM_LBUTTONUP:
            self._on_left_click()
        elif lparam == win32api.WM_RBUTTONUP:
            win32api.SetForegroundWindow(self._hwnd)  # sem isso o popup não recebe foco
            point = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            self._on_right_click(point.x, point.y)


class App:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.db = Database(DATA_DIR / "hub.db")
        self.server = HubServer(self.db, self._on_reading, self._on_paired, self._on_find_changed)
        self.actions = queue.Queue()  # ações pedidas por threads da bandeja/servidor
        self._low_alerted = set()
        self._offline_alerted = set()
        self._full_alerted = set()
        self._finding = frozenset()  # aparelhos em busca na última atualização da interface
        self._stop = threading.Event()

        self.root = tk.Tk()
        self.root.withdraw()
        self.panel = Panel(self.root, self.db, self.server.start, self.server)
        self.popup = TrayPopup(
            self.root, self.db, self.panel.show, self._quit, self.server, self._toggle_find
        )
        self.icon = TrayIcon(
            APP_NAME, make_icon_image(), APP_NAME,
            on_left_click=lambda: self.actions.put(("show",)),
            on_right_click=lambda x, y: self.actions.put(("popup", x, y)),
        )

    # -- localizar dispositivo ------------------------------------------
    def _toggle_find(self, device_id):
        """Clique no cartão do popup: liga a busca, ou desliga se já estiver ativa."""
        if self.server.find_state(device_id) != "off":
            self.server.stop_find(device_id)
        else:
            self.server.start_find(device_id)

    def _on_find_changed(self, device_id):
        """Chamado nas threads do servidor; a interface é atualizada na thread principal."""
        self.actions.put(("find_changed",))

    def _apply_find_changes(self):
        """Atualiza painel, popup e ícone da bandeja (bolinha vermelha enquanto houver busca)."""
        ids = frozenset(self.server.finding_ids())
        if ids != self._finding:
            names = [d["name"] for d in self.db.list_devices() if d["id"] in ids]
            self.icon.icon = make_icon_image(64, alert=bool(ids))
            self.icon.title = f"{APP_NAME} — procurando: {', '.join(names)}" if names else APP_NAME
            self._finding = ids
        self.panel.refresh()
        self.popup.refresh()

    def _tick_find(self):
        """Pedidos expiram sozinhos; confere de tempos em tempos para a interface acompanhar."""
        if frozenset(self.server.finding_ids()) != self._finding:
            self._apply_find_changes()
        self.root.after(5000, self._tick_find)

    # -- eventos --------------------------------------------------------
    def _on_reading(self, device, reading):
        """Chamado na thread do servidor a cada leitura recebida."""
        self._offline_alerted.discard(device["id"])
        low_percent = int(self.db.get_setting("low_battery_percent"))
        is_low = reading["level"] <= low_percent and not reading["charging"]
        if is_low and device["id"] not in self._low_alerted:
            self._low_alerted.add(device["id"])
            self.icon.notify(
                f"{device['name']}: bateria em {reading['level']}%", "Bateria baixa"
            )
        elif not is_low:
            self._low_alerted.discard(device["id"])

        if reading["level"] >= 100:
            if device["id"] not in self._full_alerted:
                self._full_alerted.add(device["id"])
                self.icon.notify(f"{device['name']} já carregou 100%.", "Bateria carregada")
        else:
            self._full_alerted.discard(device["id"])

    def _on_paired(self, device):
        """Chamado na thread do servidor quando um aparelho conclui o pareamento."""
        self.actions.put(("paired", device["id"], device["name"]))

    def _watch_offline(self):
        while not self._stop.wait(OFFLINE_CHECK_SECONDS):
            minutes = int(self.db.get_setting("offline_minutes"))
            for device in self.db.list_devices():
                if is_offline(device, minutes) and device["id"] not in self._offline_alerted:
                    self._offline_alerted.add(device["id"])
                    self.icon.notify(
                        f"{device['name']} não reporta há mais de {minutes} min.",
                        "Dispositivo offline",
                    )

    def _poll_actions(self):
        try:
            while True:
                action, *args = self.actions.get_nowait()
                if action == "show":
                    self.panel.show()
                elif action == "popup":
                    self.popup.show(*args)
                elif action == "find_changed":
                    self._apply_find_changes()
                elif action == "paired":
                    # after(0): o pop-up é modal e não pode bloquear este laço da fila
                    self.root.after(0, lambda a=args: self.panel.on_device_paired(*a))
        except queue.Empty:
            pass
        self.root.after(200, self._poll_actions)

    def _quit(self):
        self._stop.set()
        self.icon.stop()
        self.server.stop()
        self.root.destroy()

    # -- ciclo de vida --------------------------------------------------
    def run(self):
        port = int(self.db.get_setting("port"))
        try:
            self.server.start(port)
        except OSError as exc:
            self.panel.show()
            theme.notify(
                self.root, self.panel.pal, APP_NAME,
                f"Não foi possível abrir a porta {port}:\n{exc}\n\n"
                "Outro programa pode estar usando-a. Altere em Configurações.",
            )

        self.icon.run_detached()
        threading.Thread(target=self._watch_offline, daemon=True).start()
        self.root.after(200, self._poll_actions)
        self.root.after(5000, self._tick_find)
        self.root.mainloop()


def main():
    if not system.acquire_single_instance():
        return
    App().run()
