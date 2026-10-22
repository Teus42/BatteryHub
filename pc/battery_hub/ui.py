import time
import tkinter as tk
from tkinter import ttk

from . import system, theme
from .config import APP_NAME
from .server import local_ips
from .status import format_last_seen, is_offline
from .theme import Dialog, RoundedButton, make_entry, make_label

REFRESH_MS = 5000


class Panel:
    """Janela de gerenciamento. Fechar apenas esconde; o app segue na bandeja."""

    def __init__(self, root, db, apply_port, hub):
        self.root = root
        self.db = db
        self.apply_port = apply_port  # callable(port) que reinicia o servidor
        self.hub = hub                # HubServer: estado e controle da busca de dispositivos
        self.pal = theme.current_palette()
        self._after_id = None
        self._pairing_dialogs = {}  # id do dispositivo -> diálogo de código aberto

        theme.apply_style(root, self.pal)
        root.title(APP_NAME)
        root.geometry("880x480")
        root.minsize(740, 340)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        self._build()
        root.update_idletasks()
        theme.style_titlebar(root, self.pal)
        self.refresh()

    # -- janela ---------------------------------------------------------
    def show(self):
        self.refresh()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()

    def _build(self):
        pal = self.pal
        bar = tk.Frame(self.root, bg=pal["bg"])
        bar.pack(fill="x", padx=20, pady=(18, 0))
        for text, command, kind in (
            ("Adicionar", self.add_device, "primary"),
            ("Renomear", self.rename_device, "secondary"),
            ("Parear / Código", self.show_connection, "secondary"),
        ):
            RoundedButton(bar, pal, text, command, kind).pack(side="left", padx=(0, 8))
        # Alterna entre "Localizar" e "Parar busca" conforme o aparelho selecionado.
        self.find_button = RoundedButton(
            bar, pal, "Localizar", self.toggle_find, "secondary", widest_text="Parar busca"
        )
        self.find_button.pack(side="left", padx=(0, 8))
        RoundedButton(bar, pal, "Remover", self.remove_device, "danger").pack(side="left")
        RoundedButton(bar, pal, "Configurações", self.open_settings).pack(side="right")

        holder = tk.Frame(
            self.root, bg=pal["card"], highlightthickness=1, highlightbackground=pal["border"]
        )
        holder.pack(fill="both", expand=True, padx=20, pady=(16, 20))

        columns = ("name", "level", "charging", "last_seen", "status", "tray")
        self.tree = ttk.Treeview(holder, columns=columns, show="headings", selectmode="browse")
        for key, title, width in (
            ("name", "Dispositivo", 230),
            ("level", "Bateria", 80),
            ("charging", "Carregando", 100),
            ("last_seen", "Última leitura", 150),
            ("status", "Status", 170),
            ("tray", "Na bandeja", 100),
        ):
            self.tree.heading(key, text=title, anchor="w" if key == "name" else "center")
            self.tree.column(key, width=width, anchor="w" if key == "name" else "center")
        self.tree.tag_configure("offline", foreground=pal["muted"])
        self.tree.tag_configure("low", foreground=theme.LOW_COLOR)
        self.tree.tag_configure("finding", foreground=pal["primary"][0])

        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 6))
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_find_button())

    def refresh(self):
        offline_minutes = int(self.db.get_setting("offline_minutes"))
        low_percent = int(self.db.get_setting("low_battery_percent"))
        selected = self.tree.selection()

        self.tree.delete(*self.tree.get_children())
        for device in self.db.list_devices():
            code_active = bool(device["pairing_code"]) and device["pairing_expires"] > time.time()
            if code_active:
                status, tag = "Aguardando pareamento", ""
            elif device["last_seen"] is None:
                status, tag = "Sem leituras", ""
            elif is_offline(device, offline_minutes):
                status, tag = "Offline", "offline"
            else:
                status, tag = "Online", ""
            level = device["level"]
            if tag == "" and level is not None and level <= low_percent and not device["charging"]:
                tag = "low"
            finding = self.hub.find_state(device["id"])
            if finding != "off":  # a busca tem prioridade sobre os outros estados
                status = "Procurando…" if finding == "pending" else "Tocando no aparelho"
                tag = "finding"
            self.tree.insert(
                "", "end", iid=str(device["id"]), tags=(tag,),
                values=(
                    device["name"],
                    "—" if level is None else f"{level}%",
                    "—" if level is None else ("Sim" if device["charging"] else "Não"),
                    format_last_seen(device["last_seen"]),
                    status,
                    "☑" if device["show_in_tray"] else "☐",
                ),
            )
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self._sync_find_button()
        # refresh também é chamado manualmente; cancela o agendamento anterior
        if self._after_id:
            self.root.after_cancel(self._after_id)
        self._after_id = self.root.after(REFRESH_MS, self.refresh)

    # -- localizar dispositivo -------------------------------------------
    def _sync_find_button(self):
        """O botão mostra 'Parar busca' (âmbar) se o aparelho selecionado está sendo procurado."""
        selection = self.tree.selection()
        active = bool(selection) and self.hub.find_state(int(selection[0])) != "off"
        self.find_button.set_text("Parar busca" if active else "Localizar")
        self.find_button.set_kind("warning" if active else "secondary")

    def toggle_find(self):
        device = self._selected_device()
        if not device:
            return
        if self.hub.find_state(device["id"]) != "off":
            self.hub.stop_find(device["id"])
        elif not self.hub.start_find(device["id"]):
            theme.notify(
                self.root, self.pal, "Localizar dispositivo",
                f"“{device['name']}” não está conectado ao PC neste momento.\n\n"
                "A busca fica ativa por 10 minutos e o aparelho vai tocar assim que reconectar "
                "(o app Android precisa estar aberto em segundo plano e na mesma rede).",
            )
        self.refresh()

    # -- ações ----------------------------------------------------------
    def _clicked_tray_cell(self, event):
        return (
            self.tree.identify_region(event.x, event.y) == "cell"
            and self.tree.identify_column(event.x) == f"#{len(self.tree['columns'])}"
        )

    def _on_click(self, event):
        """Clicar na coluna 'Na bandeja' mostra/oculta o dispositivo no popup da bandeja."""
        if not self._clicked_tray_cell(event):
            return
        row = self.tree.identify_row(event.y)
        if row:
            device = self.db.get_device(int(row))
            self.db.set_show_in_tray(device["id"], not device["show_in_tray"])
            self.refresh()
            self.tree.selection_set(row)
        return "break"

    def _on_double_click(self, event):
        if not self._clicked_tray_cell(event):
            self.show_connection()
        return "break"

    def _selected_device(self):
        selection = self.tree.selection()
        if not selection:
            theme.notify(self.root, self.pal, APP_NAME, "Selecione um dispositivo na lista.")
            return None
        return self.db.get_device(int(selection[0]))

    def add_device(self):
        name = theme.ask_string(self.root, self.pal, "Novo dispositivo", "Nome do dispositivo:")
        if not name or not name.strip():
            return
        device = self.db.add_device(name.strip())
        self.refresh()
        self.tree.selection_set(str(device["id"]))
        self._connection_dialog(device)

    def rename_device(self):
        device = self._selected_device()
        if not device:
            return
        name = theme.ask_string(
            self.root, self.pal, "Renomear dispositivo", "Novo nome:", initial=device["name"]
        )
        if name and name.strip():
            self.db.rename_device(device["id"], name.strip())
            self.refresh()

    def remove_device(self):
        device = self._selected_device()
        if not device:
            return
        if theme.confirm(
            self.root, self.pal, "Remover dispositivo",
            f"Remover '{device['name']}' e todo o histórico?\n"
            "O app desse aparelho deixará de ser aceito.",
            ok_text="Remover", danger=True,
        ):
            self.hub.forget_device(device["id"])
            self.db.delete_device(device["id"])
            self.refresh()

    def show_connection(self):
        device = self._selected_device()
        if device:
            self._connection_dialog(device)

    def on_device_paired(self, device_id, name):
        """Aparelho concluiu o pareamento: fecha o diálogo do código e avisa o sucesso."""
        dialog = self._pairing_dialogs.pop(device_id, None)
        if dialog is not None:
            dialog.destroy()
        self.show()  # o aviso é centralizado no painel, que precisa estar visível
        theme.success(
            self.root, self.pal, "Dispositivo conectado", "Conectado com sucesso!",
            f"“{name}” foi pareado e já pode enviar a bateria.",
        )

    def _connection_dialog(self, device):
        pal = self.pal
        win = Dialog(self.root, pal, f"Parear — {device['name']}")
        body = win.body
        self._pairing_dialogs[device["id"]] = win  # fechado por on_device_paired
        win.bind(
            "<Destroy>",
            lambda e: e.widget is win and self._pairing_dialogs.pop(device["id"], None),
        )

        port = self.db.get_setting("port")
        ips = local_ips() or ["(IP não detectado)"]
        code_var = tk.StringVar()
        expiry_var = tk.StringVar()

        make_label(
            body, pal, "No app Android, informe o IP do PC, a porta e este código de ativação:"
        ).pack(anchor="w")

        card = tk.Frame(body, bg=pal["card"], padx=30, pady=14)
        card.pack(fill="x", pady=(14, 16))
        make_label(card, pal, size=36, bold=True, bg=pal["card"], textvariable=code_var).pack()
        make_label(card, pal, muted=True, bg=pal["card"], textvariable=expiry_var).pack()

        def show_code(fresh_device):
            code = fresh_device["pairing_code"]
            if code and fresh_device["pairing_expires"] > time.time():
                code_var.set(f"{code[:3]}-{code[3:]}")
                until = time.strftime("%H:%M", time.localtime(fresh_device["pairing_expires"]))
                expiry_var.set(f"Válido até {until} (uso único)")
            else:
                code_var.set("———-———")
                expiry_var.set("Sem código ativo. Gere um novo para (re)parear o aparelho.")

        def new_code():
            if device["last_seen"] is not None and not theme.confirm(
                self.root, pal, "Gerar novo código",
                "Este dispositivo já está pareado. Ao concluir um novo pareamento o token "
                "atual será substituído e o app antigo deixará de funcionar.\n\nGerar novo código?",
            ):
                return
            self.db.issue_pairing_code(device["id"])
            show_code(self.db.get_device(device["id"]))
            self.refresh()

        fields = tk.Frame(body, bg=pal["bg"])
        fields.pack(fill="x")
        fields.columnconfigure(1, weight=1)
        for index, (label, value) in enumerate((("IP do PC", ips[0]), ("Porta", port))):
            make_label(fields, pal, label).grid(row=index, column=0, sticky="w", pady=5)
            var = tk.StringVar(value=value)
            make_entry(fields, pal, var, width=26, readonly=True).grid(
                row=index, column=1, sticky="ew", padx=12, ipady=6
            )
            RoundedButton(
                fields, pal, "Copiar", lambda v=var: self._copy(win, v.get()), height=32
            ).grid(row=index, column=2)
        if len(ips) > 1:
            make_label(body, pal, "Outros IPs: " + ", ".join(ips[1:]), muted=True, size=9).pack(
                anchor="w", pady=(8, 0)
            )

        row = tk.Frame(body, bg=pal["bg"])
        row.pack(fill="x", pady=(20, 0))
        RoundedButton(row, pal, "Gerar novo código", new_code).pack(side="left")
        RoundedButton(row, pal, "Fechar", win.destroy, "primary").pack(side="right")
        show_code(self.db.get_device(device["id"]))
        win.present(modal=False)

    def _copy(self, widget, text):
        widget.clipboard_clear()
        widget.clipboard_append(text)

    def open_settings(self):
        pal = self.pal
        win = Dialog(self.root, pal, "Configurações")
        body = win.body

        port = tk.StringVar(value=self.db.get_setting("port"))
        offline = tk.StringVar(value=self.db.get_setting("offline_minutes"))
        low = tk.StringVar(value=self.db.get_setting("low_battery_percent"))
        autostart = tk.BooleanVar(value=system.is_autostart_enabled())

        fields = tk.Frame(body, bg=pal["bg"])
        fields.pack(fill="x")
        for i, (label, var) in enumerate((
            ("Porta do servidor", port),
            ("Considerar offline após (min)", offline),
            ("Alerta de bateria baixa (%)", low),
        )):
            make_label(fields, pal, label).grid(row=i, column=0, sticky="w", pady=6)
            make_entry(fields, pal, var, width=8).grid(row=i, column=1, padx=(18, 0), ipady=5)
        ttk.Checkbutton(
            body, text="Iniciar com o Windows", variable=autostart, style="Themed.TCheckbutton"
        ).pack(anchor="w", pady=(10, 0))

        def open_firewall():
            try:
                number = self._parse_int(port.get(), 1024, 65535, "Porta")
            except ValueError as exc:
                return theme.notify(self.root, pal, APP_NAME, str(exc))
            if not system.open_firewall_port(number):
                theme.notify(self.root, pal, APP_NAME, "A permissão de administrador foi negada.")

        RoundedButton(body, pal, "Liberar porta no Firewall", open_firewall).pack(anchor="w", pady=(16, 0))

        def save():
            try:
                new_port = self._parse_int(port.get(), 1024, 65535, "Porta")
                new_offline = self._parse_int(offline.get(), 20, 1440, "Tempo offline")
                new_low = self._parse_int(low.get(), 1, 99, "Bateria baixa")
            except ValueError as exc:
                return theme.notify(self.root, pal, APP_NAME, str(exc))

            if new_port != int(self.db.get_setting("port")):
                try:
                    self.apply_port(new_port)
                except OSError as exc:
                    return theme.notify(
                        self.root, pal, APP_NAME, f"Não foi possível usar a porta {new_port}:\n{exc}"
                    )
                self.db.set_setting("port", new_port)
            self.db.set_setting("offline_minutes", new_offline)
            self.db.set_setting("low_battery_percent", new_low)
            system.set_autostart(autostart.get())
            self.refresh()
            win.destroy()

        win.buttons(("Cancelar", win.destroy, "secondary"), ("Salvar", save, "primary")).pack(
            fill="x", pady=(22, 0)
        )
        win.present(modal=False)

    @staticmethod
    def _parse_int(text, low, high, label):
        try:
            value = int(text)
        except ValueError:
            raise ValueError(f"{label}: informe um número inteiro.") from None
        if not low <= value <= high:
            raise ValueError(f"{label}: use um valor entre {low} e {high}.")
        return value
