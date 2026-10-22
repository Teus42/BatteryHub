"""Tema visual compartilhado: paleta, botões arredondados, diálogos e estilo ttk."""
import ctypes
import tkinter as tk
import winreg
from tkinter import font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

SS = 4  # supersampling para bordas suaves

LOW_COLOR = "#e5534b"
CHARGING_COLOR = "#3ddc84"

# Cada botão: (normal, hover, pressionado, texto)
PALETTES = {
    "dark": dict(
        mode="dark", bg="#2b2b2b", card="#383838", hover="#444444", text="#ffffff",
        muted="#a6a6a6", track="#5c5c5c", border="#454545", selection="#3d4b66",
        primary=("#3b82f6", "#5a96f8", "#2f6bd1", "#ffffff"),
        secondary=("#474747", "#545454", "#3b3b3b", "#ffffff"),
        danger=("#d9534f", "#e56b67", "#b8423e", "#ffffff"),
        warning=("#d98a1f", "#e69b35", "#b87315", "#ffffff"),
    ),
    "light": dict(
        mode="light", bg="#f3f3f3", card="#ffffff", hover="#e3e3e3", text="#1a1a1a",
        muted="#6b6b6b", track="#d2d2d2", border="#d0d0d0", selection="#cfe0ff",
        primary=("#2f6fe0", "#4a85ee", "#2358b8", "#ffffff"),
        secondary=("#e2e2e2", "#d3d3d3", "#c5c5c5", "#1a1a1a"),
        danger=("#d9534f", "#e56b67", "#b8423e", "#ffffff"),
        warning=("#d98a1f", "#e69b35", "#b87315", "#ffffff"),
    ),
}

BUTTON_RADIUS = 13
FONT = "Segoe UI"

_anchor = None  # janela sobre a qual todos os pop-ups são centralizados


def system_theme():
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return "light" if winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] else "dark"
    except OSError:
        return "dark"


def current_palette():
    return PALETTES[system_theme()]


def make_icon_image(size=64, alert=False):
    """Ícone do app (bateria), desenhado em escala para ficar nítido em qualquer tamanho.
    Com `alert`, ganha uma bolinha vermelha (busca de dispositivo em andamento)."""
    k = size * SS / 64
    img = Image.new("RGBA", (size * SS, size * SS), (235, 235, 235, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6 * k, 16 * k, 54 * k, 50 * k), radius=6 * k, outline=(235, 235, 235, 255), width=round(5 * k))
    d.rectangle((54 * k, 27 * k, 60 * k, 39 * k), fill=(235, 235, 235, 255))
    d.rounded_rectangle((12 * k, 22 * k, 40 * k, 44 * k), radius=3 * k, fill=(46, 204, 113, 255))
    if alert:
        d.ellipse((38 * k, 2 * k, 62 * k, 26 * k), fill=(229, 83, 75, 255), outline=(255, 255, 255, 255), width=round(2 * k))
    return img.resize((size, size), Image.LANCZOS)


# -- janela ---------------------------------------------------------------
def _hwnd(win):
    return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()


def style_titlebar(win, pal):
    """Barra de título escura/clara conforme o tema (Windows 10 2004+ / 11)."""
    try:
        value = ctypes.c_int(1 if pal["mode"] == "dark" else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            _hwnd(win), 20, ctypes.byref(value), ctypes.sizeof(value)
        )
    except OSError:
        pass


def _anchor_center(win):
    """Centro (área útil) do painel de gerenciamento, ou da tela se ele estiver oculto."""
    if _anchor is not None and _anchor.winfo_viewable():
        return (
            _anchor.winfo_rootx() + _anchor.winfo_width() / 2,
            _anchor.winfo_rooty() + _anchor.winfo_height() / 2,
        )
    return win.winfo_screenwidth() / 2, win.winfo_screenheight() / 2


def center_on_anchor(win):
    """Centraliza `win` no painel. Duas passadas: a segunda compensa a moldura da janela
    (barra de título e bordas), que `geometry` inclui mas winfo_root* não."""
    win.update_idletasks()
    width, height = win.winfo_reqwidth(), win.winfo_reqheight()
    cx, cy = _anchor_center(win)
    win.geometry(f"{width}x{height}+{max(int(cx - width / 2), 0)}+{max(int(cy - height / 2), 0)}")
    win.update_idletasks()
    dx = cx - (win.winfo_rootx() + win.winfo_width() / 2)
    dy = cy - (win.winfo_rooty() + win.winfo_height() / 2)
    win.geometry(f"+{max(int(win.winfo_x() + dx), 0)}+{max(int(win.winfo_y() + dy), 0)}")


# -- widgets ---------------------------------------------------------------
def _rounded_image(width, height, radius, color):
    big = Image.new("RGBA", (width * SS, height * SS), color + "00")  # mesma cor, alfa 0: sem halo escuro
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, width * SS - 1, height * SS - 1), radius=radius * SS, fill=color
    )
    return ImageTk.PhotoImage(big.resize((width, height), Image.LANCZOS))


class RoundedButton(tk.Canvas):
    """Botão com cantos arredondados e estados de hover/pressionado."""

    def __init__(self, parent, pal, text, command, kind="secondary", bg=None, height=38, widest_text=None):
        """`widest_text`: reserva a largura do maior texto que o botão vai ter (ver set_text)."""
        font = tkfont.Font(family=FONT, size=10, weight="bold")
        width = max(font.measure(text), font.measure(widest_text or "")) + 40
        super().__init__(
            parent, width=width, height=height, bd=0, highlightthickness=0,
            bg=bg or pal["bg"], cursor="hand2",
        )
        self._pal, self._width, self._height = pal, width, height
        self._command = command
        self._kind = None
        self._images = {}
        self._background = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(width / 2, height / 2, text=text, font=font)
        self.set_kind(kind)

        self.bind("<Enter>", lambda _e: self._set("hover"))
        self.bind("<Leave>", lambda _e: self._set("normal"))
        self.bind("<ButtonPress-1>", lambda _e: self._set("pressed"))
        self.bind("<ButtonRelease-1>", self._release)

    def set_kind(self, kind):
        """Troca a cor do botão (primary, secondary, danger)."""
        if kind == self._kind:
            return
        self._kind = kind
        normal, hover, pressed, fg = self._pal[kind]
        radius = min(BUTTON_RADIUS, self._height // 2)
        self._images = {
            name: _rounded_image(self._width, self._height, radius, color)
            for name, color in (("normal", normal), ("hover", hover), ("pressed", pressed))
        }
        self.itemconfigure(self._label, fill=fg)
        self._set("normal")

    def set_text(self, text):
        self.itemconfigure(self._label, text=text)

    def _set(self, state):
        self.itemconfigure(self._background, image=self._images[state])

    def _release(self, event):
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._set("hover" if inside else "normal")
        if inside:
            self._command()


def make_label(parent, pal, text="", muted=False, size=10, bold=False, bg=None, **kwargs):
    return tk.Label(
        parent, text=text, bg=bg or pal["bg"], fg=pal["muted"] if muted else pal["text"],
        font=(FONT, size, "bold" if bold else "normal"), **kwargs,
    )


def make_entry(parent, pal, variable, width=24, readonly=False):
    return tk.Entry(
        parent, textvariable=variable, width=width, relief="flat", bd=0,
        font=(FONT, 10), bg=pal["card"], fg=pal["text"], insertbackground=pal["text"],
        readonlybackground=pal["card"], state="readonly" if readonly else "normal",
        highlightthickness=1, highlightbackground=pal["border"], highlightcolor=pal["primary"][0],
        disabledforeground=pal["muted"],
    )


def apply_style(root, pal):
    """Estilo ttk (lista, barra de rolagem, caixa de seleção) e âncora dos pop-ups."""
    global _anchor
    _anchor = root
    root.configure(bg=pal["bg"])
    root.iconphoto(True, ImageTk.PhotoImage(make_icon_image()))  # ícone padrão de todas as janelas
    style = ttk.Style(root)
    style.theme_use("clam")

    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure(
        "Treeview", background=pal["card"], fieldbackground=pal["card"], foreground=pal["text"],
        borderwidth=0, rowheight=42, font=(FONT, 10),
    )
    style.map(
        "Treeview",
        background=[("selected", pal["selection"])],
        foreground=[("selected", pal["text"])],
    )
    style.configure(
        "Treeview.Heading", background=pal["card"], foreground=pal["muted"], relief="flat",
        borderwidth=0, padding=(8, 10), font=(FONT, 9, "bold"),
    )
    style.map("Treeview.Heading", background=[("active", pal["card"])])

    style.configure(
        "Vertical.TScrollbar", background=pal["hover"], troughcolor=pal["card"],
        bordercolor=pal["card"], lightcolor=pal["hover"], darkcolor=pal["hover"],
        arrowcolor=pal["muted"], relief="flat",
    )
    style.map("Vertical.TScrollbar", background=[("active", pal["border"])])

    style.configure(
        "Themed.TCheckbutton", background=pal["bg"], foreground=pal["text"], font=(FONT, 10),
        indicatorbackground=pal["card"], indicatorforeground=pal["text"],
        bordercolor=pal["border"], focuscolor=pal["bg"],
    )
    style.map(
        "Themed.TCheckbutton",
        background=[("active", pal["bg"])],
        indicatorbackground=[("selected", pal["primary"][0]), ("active", pal["hover"])],
    )


# -- diálogos ---------------------------------------------------------------
class Dialog(tk.Toplevel):
    """Pop-up temático, sempre centralizado no painel. `body` recebe o conteúdo."""

    def __init__(self, parent, pal, title):
        super().__init__(parent)
        self.withdraw()
        self.pal = pal
        self.title(title)
        self.configure(bg=pal["bg"])
        self.transient(parent)
        self.resizable(False, False)
        self.body = tk.Frame(self, bg=pal["bg"], padx=24, pady=22)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda _e: self.destroy())

    def present(self, modal=True):
        self.update_idletasks()
        style_titlebar(self, self.pal)
        self.attributes("-alpha", 0)  # evita piscar enquanto reposiciona
        self.deiconify()
        center_on_anchor(self)
        self.attributes("-alpha", 1)
        self.focus_force()
        if modal:
            self.grab_set()
            self.wait_window()

    def buttons(self, *specs):
        """Linha de botões alinhada à direita: specs = (texto, comando, tipo)."""
        row = tk.Frame(self.body, bg=self.pal["bg"])
        for text, command, kind in reversed(specs):
            RoundedButton(row, self.pal, text, command, kind).pack(side="right", padx=(8, 0))
        return row


def ask_string(parent, pal, title, prompt, initial=""):
    result = {"value": None}
    dialog = Dialog(parent, pal, title)
    make_label(dialog.body, pal, prompt).pack(anchor="w")
    var = tk.StringVar(value=initial)
    entry = make_entry(dialog.body, pal, var, width=34)
    entry.pack(fill="x", pady=(8, 18), ipady=7)

    def accept(_event=None):
        result["value"] = var.get()
        dialog.destroy()

    dialog.buttons(("Cancelar", dialog.destroy, "secondary"), ("OK", accept, "primary")).pack(fill="x")
    entry.bind("<Return>", accept)
    entry.focus_set()
    entry.select_range(0, "end")
    dialog.present()
    return result["value"]


def confirm(parent, pal, title, message, ok_text="Confirmar", danger=False):
    result = {"value": False}
    dialog = Dialog(parent, pal, title)
    make_label(dialog.body, pal, message, wraplength=380, justify="left").pack(anchor="w", pady=(0, 20))

    def accept():
        result["value"] = True
        dialog.destroy()

    dialog.buttons(
        ("Cancelar", dialog.destroy, "secondary"),
        (ok_text, accept, "danger" if danger else "primary"),
    ).pack(fill="x")
    dialog.present()
    return result["value"]


def notify(parent, pal, title, message):
    dialog = Dialog(parent, pal, title)
    make_label(dialog.body, pal, message, wraplength=380, justify="left").pack(anchor="w", pady=(0, 20))
    dialog.buttons(("OK", dialog.destroy, "primary")).pack(fill="x")
    dialog.present()


def _check_icon(size=64):
    """Círculo verde com um visto branco."""
    big = size * SS
    img = Image.new("RGBA", (big, big), CHARGING_COLOR + "00")
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, big - 1, big - 1), fill=CHARGING_COLOR)
    points = [(0.26, 0.52), (0.44, 0.69), (0.75, 0.34)]
    draw.line([(x * big, y * big) for x, y in points], fill="#ffffff", width=round(big * 0.09), joint="curve")
    for x, y in (points[0], points[-1]):  # pontas arredondadas
        r = big * 0.045
        draw.ellipse((x * big - r, y * big - r, x * big + r, y * big + r), fill="#ffffff")
    return ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))


def success(parent, pal, title, heading, message):
    dialog = Dialog(parent, pal, title)
    icon = _check_icon()
    tk.Label(dialog.body, image=icon, bg=pal["bg"]).pack()
    dialog.icon = icon  # evita que o PhotoImage seja descartado
    make_label(dialog.body, pal, heading, size=15, bold=True).pack(pady=(14, 4))
    make_label(dialog.body, pal, message, muted=True, wraplength=340, justify="center").pack()
    dialog.buttons(("OK", dialog.destroy, "primary")).pack(fill="x", pady=(22, 0))
    dialog.present()
