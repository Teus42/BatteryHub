"""Popup do clique direito na bandeja: cartões de bateria + ações, no estilo do ATK Hub."""
import ctypes
import tkinter as tk
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont, ImageTk

from . import system
from .status import format_last_seen, is_offline
from .theme import CHARGING_COLOR, LOW_COLOR, PALETTES, system_theme

SS = 4            # supersampling para suavizar as bordas
ICON_PX = 44
RING_WIDTH = 3.5
POPUP_WIDTH = 340
NAME_MAX_CHARS = 24

_VK_LBUTTON, _VK_RBUTTON = 0x01, 0x02


def _font(size):
    for name in ("segoeuib.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_battery_with_bolt(draw, n, outline, bolt):
    u = n / ICON_PX
    body = (11 * u, 16.5 * u, 30.5 * u, 27.5 * u)
    draw.rounded_rectangle(body, radius=2.2 * u, outline=outline, width=round(1.6 * u))
    draw.rounded_rectangle((30.5 * u, 20.5 * u, 33.2 * u, 23.5 * u), radius=0.8 * u, fill=outline)
    points = [(22.6, 18.2), (17.6, 22.6), (20.9, 22.6), (19.2, 26.4), (24.2, 21.4), (21.1, 21.4), (22.6, 18.2)]
    draw.polygon([(x * u, y * u) for x, y in points], fill=bolt)


def render_ring(level, charging, accent, track, text_color):
    """Anel de progresso com a % no centro ou, carregando, uma bateria com raio."""
    n = ICON_PX * SS
    width = round(RING_WIDTH * SS)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    box = (width / 2, width / 2, n - width / 2, n - width / 2)
    draw.ellipse(box, outline=track, width=width)
    if level is not None and level > 0:
        if level >= 100:
            draw.ellipse(box, outline=accent, width=width)
        else:
            draw.arc(box, -90, -90 + 360 * level / 100, fill=accent, width=width)

    if charging:
        _draw_battery_with_bolt(draw, n, text_color, CHARGING_COLOR)
    else:
        label = "—" if level is None else f"{level}%"
        size = (13 if len(label) <= 3 else 11) * SS
        draw.text((n / 2, n / 2), label, font=_font(size), fill=text_color, anchor="mm")
    return img.resize((ICON_PX, ICON_PX), Image.LANCZOS)


def render_device_icon(color, waves=0):
    """Aparelho; com `waves` (1 ou 2) desenha ondas de som ao redor, para a busca em andamento."""
    n = ICON_PX * SS
    u = n / ICON_PX
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((13 * u, 6 * u, 31 * u, 38 * u), radius=3.5 * u, outline=color, width=round(2.2 * u))
    draw.rounded_rectangle((19.5 * u, 32.5 * u, 24.5 * u, 33.8 * u), radius=0.6 * u, fill=color)
    for radius in (13, 17.5)[:waves]:
        box = ((22 - radius) * u, (22 - radius) * u, (22 + radius) * u, (22 + radius) * u)
        draw.arc(box, -38, 38, fill=color, width=round(2 * u))
        draw.arc(box, 142, 218, fill=color, width=round(2 * u))
    return img.resize((ICON_PX, ICON_PX), Image.LANCZOS)


class TrayPopup:
    def __init__(self, root, db, on_open_panel, on_quit, hub, toggle_find):
        self.root = root
        self.db = db
        self.on_open_panel = on_open_panel
        self.on_quit = on_quit
        self.hub = hub                  # HubServer: consulta o estado da busca
        self.toggle_find = toggle_find  # callable(device_id): inicia/para a busca
        self.win = None
        self._images = []  # PhotoImage precisa de referência viva
        self._pulses = []  # (rótulo do ícone, quadros, cartão) dos aparelhos em busca
        self._last_pos = (0, 0)

    # -- ciclo de vida --------------------------------------------------
    def refresh(self):
        """Reconstrói o popup no mesmo lugar (ex.: o estado da busca mudou)."""
        if self.win is not None:
            self.show(*self._last_pos)

    def show(self, cursor_x, cursor_y):
        self.close()
        self._last_pos = (cursor_x, cursor_y)
        self._pulses = []
        pal = PALETTES[system_theme()]
        win = self.win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=pal["border"])

        body = tk.Frame(win, bg=pal["bg"])
        body.pack(padx=1, pady=1)
        self._build(body, pal)

        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        screen_w, screen_h = win.winfo_screenwidth(), win.winfo_screenheight()
        x = min(max(cursor_x - width, 8), screen_w - width - 8)
        y = cursor_y - height - 12
        if y < 8:  # barra de tarefas no topo
            y = min(cursor_y + 12, screen_h - height - 8)
        win.geometry(f"{width}x{height}+{x}+{y}")
        self._round_corners(win)
        win.deiconify()
        win.focus_force()
        win.bind("<Escape>", lambda _e: self.close())
        win.after(200, lambda: win.bind("<FocusOut>", self._on_focus_out))
        win.after(300, self._watch_outside_click)
        if self._pulses:
            self._pulse(win, 0)

    def _pulse(self, win, step):
        """Anima o aparelho em busca: ondas de som que crescem e a borda do cartão piscando."""
        if win is not self.win:
            return
        accent, quiet = self._pulse_colors
        for label, frames, card in self._pulses:
            label.configure(image=frames[step % len(frames)])
            card.configure(highlightbackground=accent if step % 2 == 0 else quiet)
        win.after(550, lambda: self._pulse(win, step + 1))

    def close(self):
        if self.win is not None:
            win, self.win = self.win, None
            win.destroy()
            self._images.clear()

    def _on_focus_out(self, event):
        if event.widget is self.win:
            self.close()

    def _watch_outside_click(self):
        """Plano B ao FocusOut: fecha ao clicar fora, mesmo se o foco não foi concedido."""
        win = self.win
        if win is None:
            return
        user32 = ctypes.windll.user32
        pressed = (user32.GetAsyncKeyState(_VK_LBUTTON) | user32.GetAsyncKeyState(_VK_RBUTTON)) & 0x8000
        if pressed:
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            inside = (win.winfo_rootx() <= point.x < win.winfo_rootx() + win.winfo_width()
                      and win.winfo_rooty() <= point.y < win.winfo_rooty() + win.winfo_height())
            if not inside:
                return self.close()
        win.after(30, self._watch_outside_click)

    @staticmethod
    def _round_corners(win):
        try:  # Windows 11: DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int)
            )
        except OSError:
            pass

    # -- conteúdo -------------------------------------------------------
    def _build(self, body, pal):
        tk.Frame(body, bg=pal["bg"], width=POPUP_WIDTH, height=1).pack()  # largura mínima fixa
        offline_minutes = int(self.db.get_setting("offline_minutes"))
        low_percent = int(self.db.get_setting("low_battery_percent"))
        devices = [d for d in self.db.list_devices() if d["show_in_tray"]]

        for device in devices:
            self._device_card(body, pal, device, offline_minutes, low_percent)
        if devices:
            tk.Frame(body, bg=pal["border"], height=1).pack(fill="x", padx=8, pady=(2, 4))
        else:
            body.configure(pady=4)

        self._menu_row(body, pal, "Abrir painel", self.on_open_panel)
        autostart = system.is_autostart_enabled()
        self._menu_row(
            body, pal, "Iniciar com o Windows",
            lambda: system.set_autostart(not autostart), checked=autostart,
        )
        self._menu_row(body, pal, "Sair", self.on_quit)
        tk.Frame(body, bg=pal["bg"], height=4).pack()

    def _device_card(self, body, pal, device, offline_minutes, low_percent):
        level = device["level"]
        offline = is_offline(device, offline_minutes)
        charging = bool(device["charging"]) and not offline
        if offline or level is None:
            accent = pal["muted"]
        elif charging:
            accent = CHARGING_COLOR
        elif level <= low_percent:
            accent = LOW_COLOR
        else:
            accent = pal["text"]

        if device["last_seen"] is None:
            subtitle = "Aguardando primeira leitura"
        elif offline:
            subtitle = f"Offline · {format_last_seen(device['last_seen'])}"
        else:
            subtitle = ("Carregando · " if charging else "") + format_last_seen(device["last_seen"])

        finding = self.hub.find_state(device["id"])
        highlight = pal["primary"][0]
        subtitle_color = pal["muted"]
        if finding != "off":
            subtitle = ("Procurando… aguardando o aparelho" if finding == "pending" else "Tocando no aparelho")
            subtitle += " · clique para parar"
            subtitle_color = highlight

        muted_or_text = pal["muted"] if offline else pal["text"]
        ring = ImageTk.PhotoImage(render_ring(level, charging, accent, pal["track"], muted_or_text))
        if finding != "off":
            frames = [ImageTk.PhotoImage(render_device_icon(highlight, waves)) for waves in (1, 2)]
            icon = frames[0]
            self._images += frames
        else:
            icon = ImageTk.PhotoImage(render_device_icon(muted_or_text))
        self._images += [ring, icon]

        outer = tk.Frame(body, bg=pal["bg"])
        outer.pack(fill="x", padx=8, pady=(8, 0))
        # A borda existe sempre (na cor do cartão) para o tamanho não mudar quando a busca liga.
        border = highlight if finding != "off" else pal["card"]
        card = tk.Frame(outer, bg=pal["card"], padx=10, pady=8, highlightthickness=2,
                        highlightbackground=border, highlightcolor=border)
        card.pack(fill="x")
        icon_label = tk.Label(card, image=icon, bg=pal["card"])
        icon_label.pack(side="left")
        text = tk.Frame(card, bg=pal["card"])
        text.pack(side="left", fill="x", expand=True, padx=(10, 6))
        name = device["name"]
        if len(name) > NAME_MAX_CHARS:
            name = name[:NAME_MAX_CHARS - 1] + "…"
        tk.Label(text, text=name, bg=pal["card"], fg=pal["text"], font=("Segoe UI", 11, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(text, text=subtitle, bg=pal["card"], fg=subtitle_color, font=("Segoe UI", 9),
                 anchor="w").pack(fill="x")
        tk.Label(card, image=ring, bg=pal["card"]).pack(side="right")

        if finding != "off":
            self._pulses.append((icon_label, frames, card))
            self._pulse_colors = (highlight, pal["border"])

        # Clicar no cartão liga/desliga a busca por esse aparelho (o popup é refeito pelo evento).
        for widget in (outer, card, *card.winfo_children(), *text.winfo_children()):
            widget.bind("<Button-1>", lambda _e, i=device["id"]: self.root.after(10, lambda: self.toggle_find(i)))

    def _menu_row(self, body, pal, text, command, checked=False):
        # Sem width/height: configure() num Frame com tamanho fixo reaplica esse tamanho
        # e colapsa o conteúdo (o hover muda o bg).
        row = tk.Frame(body, bg=pal["bg"])
        row.pack(fill="x", padx=4)
        labels = [tk.Label(row, text=text, bg=pal["bg"], fg=pal["text"], font=("Segoe UI", 10),
                           anchor="w", padx=18, pady=8)]
        labels[0].pack(side="left")
        if checked:
            labels.append(tk.Label(row, text="✓", bg=pal["bg"], fg=pal["text"],
                                   font=("Segoe UI", 10), padx=18))
            labels[1].pack(side="right")

        def paint(color):
            for widget in (row, *labels):
                widget.configure(bg=color)

        for widget in (row, *labels):
            widget.bind("<Enter>", lambda _e: paint(pal["hover"]))
            widget.bind("<Leave>", lambda _e: paint(pal["bg"]))
            widget.bind("<Button-1>", lambda _e: self._run(command))

    def _run(self, command):
        self.close()
        self.root.after(10, command)
