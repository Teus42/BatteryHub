"""Instalador do Battery Hub, com o mesmo tema do app. Também atua como desinstalador (Uninstall.exe).

Uso:
    BatteryHub-Setup.exe                         assistente gráfico
    BatteryHub-Setup.exe --silent [--dir PASTA]  instalação sem tela
    Uninstall.exe                                assistente de remoção
    Uninstall.exe --silent [--remove-data]       remoção sem tela
"""
import argparse
import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
import winreg
import zipfile
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from PIL import ImageTk  # noqa: E402

from battery_hub import theme  # noqa: E402
from battery_hub.theme import RoundedButton, make_entry, make_label  # noqa: E402

APP_NAME = "Battery Hub"
APP_EXE = "BatteryHub.exe"
VERSION = "1.1.1"
UNINSTALLER = "Uninstall.exe"
MANIFEST = "installed_files.txt"
DEFAULT_PORT = 8765
FIREWALL_RULE = "Battery Hub"

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\BatteryHub"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "BatteryHub"

NO_WINDOW = 0x08000000
DETACHED = 0x00000008
FOLDER_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
FOLDER_PROGRAMS = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"  # Menu Iniciar\Programas do usuário


class InstallError(Exception):
    """Falha com mensagem pronta para mostrar ao usuário."""


@dataclass
class Options:
    directory: Path
    desktop_shortcut: bool = True
    start_menu_shortcut: bool = True
    autostart: bool = True
    firewall: bool = True


# -- sistema ---------------------------------------------------------------
class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


def known_folder(folder_id):
    """Pasta do usuário respeitando redirecionamentos (ex.: Área de Trabalho em outro disco)."""
    guid = _GUID.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    raw = ctypes.c_void_p()
    ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(raw))
    try:
        return Path(ctypes.wstring_at(raw.value))
    finally:
        ctypes.windll.ole32.CoTaskMemFree(raw)


def shortcut_paths():
    return [known_folder(FOLDER_DESKTOP) / f"{APP_NAME}.lnk", known_folder(FOLDER_PROGRAMS) / f"{APP_NAME}.lnk"]


def create_shortcut(link, target, workdir):
    def quote(text):
        return str(text).replace("'", "''")

    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{quote(link)}');"
        f"$s.TargetPath='{quote(target)}';$s.WorkingDirectory='{quote(workdir)}';"
        f"$s.IconLocation='{quote(target)},0';$s.Description='{APP_NAME}';$s.Save()"
    )
    Path(link).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )


def stop_app():
    subprocess.run(["taskkill", "/IM", APP_EXE, "/F"], capture_output=True, creationflags=NO_WINDOW)


def launch_app(target):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")  # o filho não pode reaproveitar a pasta temporária do instalador
    subprocess.Popen([str(target / APP_EXE)], cwd=target, env=env, close_fds=True,
                     creationflags=DETACHED | 0x00000200)


def _run_elevated(command):
    """Executa via UAC. Retorna se o usuário aceitou (não espera a conclusão)."""
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f"/c {command}", None, 0) > 32


def firewall_rule_exists():
    result = subprocess.run(
        ["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE}"],
        capture_output=True, creationflags=NO_WINDOW,
    )
    return result.returncode == 0


def add_firewall_rule():
    return _run_elevated(
        f'netsh advfirewall firewall delete rule name="{FIREWALL_RULE}" & '
        f'netsh advfirewall firewall add rule name="{FIREWALL_RULE}" dir=in action=allow '
        f"protocol=TCP localport={DEFAULT_PORT} profile=private"
    )


def remove_firewall_rule():
    return _run_elevated(f'netsh advfirewall firewall delete rule name="{FIREWALL_RULE}"')


# -- instalação ---------------------------------------------------------------
def payload_path():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "payload.zip"


def payload_size():
    with zipfile.ZipFile(payload_path()) as archive:
        return sum(m.file_size for m in archive.infolist())


def default_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP_NAME


def resolve_target(text):
    """Valida o caminho; se for a raiz do disco ou uma pasta com outros arquivos, usa uma subpasta."""
    directory = Path(os.path.expandvars(text.strip().strip('"'))).expanduser()
    if not text.strip() or not directory.is_absolute():
        raise InstallError("Informe um caminho completo, como C:\\Programas\\Battery Hub.")
    if directory.parent == directory:
        return directory / APP_NAME
    if directory.exists():
        if not directory.is_dir():
            raise InstallError("O caminho informado é um arquivo, não uma pasta.")
        has_files = any(directory.iterdir())
        ours = (directory / APP_EXE).exists() or (directory / MANIFEST).exists()
        if has_files and not ours:
            return directory / APP_NAME
    return directory


def install(options, progress=lambda fraction, text: None):
    target = options.directory
    stop_app()
    try:
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(payload_path()) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            total = sum(m.file_size for m in members) or 1
            done, installed = 0, []
            root = target.resolve()
            for member in members:
                destination = (target / member.filename).resolve()
                if root not in destination.parents:
                    raise InstallError("Pacote de instalação inválido.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(destination, "wb") as out:
                    shutil.copyfileobj(source, out, 1 << 20)
                installed.append(member.filename.replace("/", "\\"))
                done += member.file_size
                progress(0.9 * done / total, member.filename)

        progress(0.92, "Criando o desinstalador…")
        if getattr(sys, "frozen", False):
            shutil.copy2(sys.executable, target / UNINSTALLER)
            installed.append(UNINSTALLER)
        installed.append(MANIFEST)
        (target / MANIFEST).write_text("\n".join(installed), encoding="utf-8")

        progress(0.95, "Registrando no Windows…")
        register_uninstall(target, payload_size() // 1024)
        exe = target / APP_EXE
        if options.desktop_shortcut:
            create_shortcut(shortcut_paths()[0], exe, target)
        if options.start_menu_shortcut:
            create_shortcut(shortcut_paths()[1], exe, target)
        if options.autostart:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe}"')
        if options.firewall:
            progress(0.98, "Liberando a porta no Firewall…")
            add_firewall_rule()
        progress(1.0, "Concluído")
    except PermissionError:
        raise InstallError(
            f"Sem permissão para gravar em:\n{target}\n\nEscolha outra pasta ou feche o Battery Hub e tente de novo."
        ) from None
    except OSError as exc:
        raise InstallError(f"Não foi possível instalar:\n{exc}") from None
    except subprocess.CalledProcessError as exc:
        raise InstallError(f"Falha ao criar atalhos:\n{exc.stderr.decode(errors='replace')[:300]}") from None


def register_uninstall(target, size_kb):
    uninstaller = f'"{target / UNINSTALLER}"'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
        for name, value in (
            ("DisplayName", APP_NAME), ("DisplayVersion", VERSION), ("Publisher", APP_NAME),
            ("InstallLocation", str(target)), ("DisplayIcon", str(target / APP_EXE)),
            ("UninstallString", uninstaller), ("QuietUninstallString", f"{uninstaller} --silent"),
        ):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("EstimatedSize", size_kb), ("NoModify", 1), ("NoRepair", 1)):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def installed_directory():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return Path(winreg.QueryValueEx(key, "InstallLocation")[0])
    except OSError:
        return None


def uninstall(target, remove_data=False, remove_firewall=False, progress=lambda fraction, text: None):
    progress(0.05, "Encerrando o Battery Hub…")
    stop_app()
    manifest = target / MANIFEST
    relative = manifest.read_text(encoding="utf-8").splitlines() if manifest.exists() else []
    files = [target / name for name in relative if name]

    progress(0.2, "Removendo arquivos…")
    for path in files:
        if path.name.lower() in (UNINSTALLER.lower(), MANIFEST.lower()) and path.parent == target:
            continue  # o desinstalador em execução e o manifesto saem por último
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise InstallError(f"Não foi possível remover:\n{path}\n\n{exc}") from None
    folders = {parent for path in files for parent in path.parents if target in parent.parents}
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()  # só remove se estiver vazia: nada do usuário é apagado
        except OSError:
            pass

    progress(0.7, "Removendo atalhos e registro…")
    for link in shortcut_paths():
        link.unlink(missing_ok=True)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except OSError:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass

    if remove_data:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    if remove_firewall and firewall_rule_exists():
        remove_firewall_rule()

    # O Uninstall.exe em execução não consegue se apagar: um cmd separado faz isso depois que ele sair.
    leftovers = " & ".join(f'del /f /q "{target / name}"' for name in (UNINSTALLER, MANIFEST))
    # String pronta (não lista): o Python escaparia as aspas internas de um jeito que o cmd não entende.
    subprocess.Popen(
        f'cmd /c "ping 127.0.0.1 -n 4 >nul & {leftovers} & rmdir "{target}""',
        creationflags=DETACHED | NO_WINDOW, close_fds=True,
    )
    progress(1.0, "Concluído")


# -- interface ---------------------------------------------------------------
class ProgressBar(tk.Canvas):
    def __init__(self, parent, pal, width=440, height=10):
        super().__init__(parent, width=width, height=height, bg=pal["bg"], bd=0, highlightthickness=0)
        self._width, self._height, self._pal = width, height, pal
        self._track = theme._rounded_image(width, height, height // 2, pal["track"])
        self.create_image(0, 0, anchor="nw", image=self._track)
        self._fill_item = self.create_image(0, 0, anchor="nw")
        self._fill = None
        self._filled = -1

    def set(self, fraction):
        filled = int(self._width * max(0.0, min(1.0, fraction)))
        if filled == self._filled:
            return
        self._filled = filled
        if filled < self._height:
            self.itemconfigure(self._fill_item, image="")
            return
        self._fill = theme._rounded_image(filled, self._height, self._height // 2, self._pal["primary"][0])
        self.itemconfigure(self._fill_item, image=self._fill)


class Wizard:
    """Janela única com páginas, no mesmo visual do aplicativo."""

    def __init__(self, uninstalling, directory=None):
        self.uninstalling = uninstalling
        self.pal = theme.current_palette()
        self.work = queue.Queue()
        self.target = directory
        self.root = root = tk.Tk()
        root.title(f"{APP_NAME} — {'Desinstalação' if uninstalling else 'Instalação'}")
        root.geometry("720x480")
        root.resizable(False, False)
        theme.apply_style(root, self.pal)

        sidebar = tk.Frame(root, bg=self.pal["card"], width=210)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._logo = ImageTk.PhotoImage(theme.make_icon_image(96))
        tk.Label(sidebar, image=self._logo, bg=self.pal["card"]).pack(pady=(70, 12))
        make_label(sidebar, self.pal, APP_NAME, size=18, bold=True, bg=self.pal["card"]).pack()
        make_label(sidebar, self.pal, f"Versão {VERSION}", muted=True, bg=self.pal["card"]).pack(pady=(2, 0))

        main = tk.Frame(root, bg=self.pal["bg"])
        main.pack(side="left", fill="both", expand=True)
        self.bar = tk.Frame(main, bg=self.pal["bg"])
        self.bar.pack(side="bottom", fill="x", padx=28, pady=(0, 22))
        self.page = tk.Frame(main, bg=self.pal["bg"])
        self.page.pack(side="top", fill="both", expand=True, padx=32, pady=(36, 8))

        root.update_idletasks()
        theme.style_titlebar(root, self.pal)
        x = (root.winfo_screenwidth() - 720) // 2
        y = (root.winfo_screenheight() - 480) // 2
        root.geometry(f"+{x}+{y}")
        root.protocol("WM_DELETE_WINDOW", self.cancel)

    # -- helpers ----------------------------------------------------------
    def _reset(self, title, subtitle=None):
        for frame in (self.page, self.bar):
            for child in frame.winfo_children():
                child.destroy()
        make_label(self.page, self.pal, title, size=20, bold=True).pack(anchor="w")
        if subtitle:
            make_label(self.page, self.pal, subtitle, muted=True, wraplength=420, justify="left").pack(
                anchor="w", pady=(8, 0)
            )

    def _buttons(self, left=(), right=()):
        for text, command, kind in left:
            RoundedButton(self.bar, self.pal, text, command, kind).pack(side="left")
        for text, command, kind in reversed(right):
            RoundedButton(self.bar, self.pal, text, command, kind).pack(side="right", padx=(8, 0))

    def _check(self, text, value):
        var = tk.BooleanVar(value=value)
        ttk.Checkbutton(self.page, text=text, variable=var, style="Themed.TCheckbutton").pack(anchor="w", pady=4)
        return var

    def cancel(self):
        if theme.confirm(self.root, self.pal, "Sair", "Deseja sair do assistente?", ok_text="Sair", danger=True):
            self.root.destroy()

    def error(self, message):
        self._reset("Algo deu errado")
        make_label(self.page, self.pal, message, wraplength=440, justify="left").pack(anchor="w", pady=(14, 0))
        self._buttons(right=[("Fechar", self.root.destroy, "primary")])

    # -- instalação ---------------------------------------------------------
    def show_welcome(self):
        self._reset(
            "Bem-vindo ao Battery Hub",
            "Acompanhe a bateria dos seus celulares e tablets Android direto na bandeja do Windows, "
            "pela rede local. Sem nuvem e sem cadastro.\n\nO assistente vai instalar o aplicativo no seu computador.",
        )
        self._buttons(left=[("Cancelar", self.cancel, "secondary")], right=[("Avançar", self.show_location, "primary")])

    def show_location(self):
        self._reset("Onde instalar?", "O Battery Hub será instalado na pasta abaixo.")
        self.path_var = tk.StringVar(value=str(self.target or default_directory()))
        row = tk.Frame(self.page, bg=self.pal["bg"])
        row.pack(fill="x", pady=(14, 4))
        make_entry(row, self.pal, self.path_var, width=40).pack(side="left", fill="x", expand=True, ipady=7, ipadx=6)
        RoundedButton(row, self.pal, "Procurar…", self._browse, height=34).pack(side="left", padx=(10, 0))
        self.space_label = make_label(self.page, self.pal, muted=True, size=9)
        self.space_label.pack(anchor="w", pady=(0, 14))
        self.path_var.trace_add("write", lambda *_: self._update_space())
        self._update_space()

        self.opt_desktop = self._check("Criar atalho na Área de Trabalho", True)
        self.opt_menu = self._check("Criar atalho no Menu Iniciar", True)
        self.opt_autostart = self._check("Iniciar com o Windows", True)
        self.opt_firewall = self._check(f"Liberar a porta {DEFAULT_PORT} no Firewall (pede permissão de administrador)", True)
        self._buttons(
            left=[("Cancelar", self.cancel, "secondary")],
            right=[("Voltar", self.show_welcome, "secondary"), ("Instalar", self.start_install, "primary")],
        )

    def _browse(self):
        chosen = filedialog.askdirectory(parent=self.root, title="Escolha a pasta de instalação", mustexist=False)
        if chosen:
            path = Path(chosen)
            self.path_var.set(str(path if path.name.lower() == APP_NAME.lower() else path / APP_NAME))

    def _update_space(self):
        probe = Path(self.path_var.get() or ".")
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        try:
            free = shutil.disk_usage(probe).free / 1024 ** 3
            self.space_label.configure(
                text=f"Espaço necessário: {payload_size() / 1024 ** 2:.0f} MB    ·    Livre no disco: {free:.1f} GB"
            )
        except OSError:
            self.space_label.configure(text="")

    def start_install(self):
        try:
            target = resolve_target(self.path_var.get())
        except InstallError as exc:
            return theme.notify(self.root, self.pal, APP_NAME, str(exc))
        self.target = target
        options = Options(
            target, self.opt_desktop.get(), self.opt_menu.get(), self.opt_autostart.get(), self.opt_firewall.get()
        )
        self._show_progress("Instalando…", f"Copiando arquivos para\n{target}")
        self._run(lambda cb: install(options, cb), self.show_done)

    def show_done(self):
        self._reset("Tudo pronto!")
        self._icon = theme._check_icon(72)
        tk.Label(self.page, image=self._icon, bg=self.pal["bg"]).pack(anchor="w", pady=(16, 8))
        make_label(self.page, self.pal, f"O Battery Hub foi instalado em:\n{self.target}", muted=True,
                   wraplength=440, justify="left").pack(anchor="w", pady=(0, 14))
        self.opt_launch = self._check("Abrir o Battery Hub agora", True)
        self._buttons(right=[("Concluir", self.finish_install, "primary")])

    def finish_install(self):
        if self.opt_launch.get():
            launch_app(self.target)
        self.root.destroy()

    # -- desinstalação ------------------------------------------------------
    def show_uninstall(self):
        self._reset("Remover o Battery Hub?", f"O aplicativo será removido de\n{self.target}")
        self.opt_data = self._check("Apagar também meus dados (dispositivos e histórico)", False)
        self.opt_rule = None
        if firewall_rule_exists():
            self.opt_rule = self._check("Remover a regra do Firewall (pede permissão de administrador)", True)
        self._buttons(
            left=[("Cancelar", self.root.destroy, "secondary")],
            right=[("Desinstalar", self.start_uninstall, "danger")],
        )

    def start_uninstall(self):
        remove_data = self.opt_data.get()
        remove_rule = bool(self.opt_rule and self.opt_rule.get())
        self._show_progress("Removendo…", "Aguarde um instante.")
        self._run(lambda cb: uninstall(self.target, remove_data, remove_rule, cb), self.show_uninstalled)

    def show_uninstalled(self):
        self._reset("Battery Hub removido")
        self._icon = theme._check_icon(72)
        tk.Label(self.page, image=self._icon, bg=self.pal["bg"]).pack(anchor="w", pady=(16, 8))
        make_label(self.page, self.pal, "O aplicativo foi desinstalado deste computador.", muted=True).pack(anchor="w")
        self._buttons(right=[("Fechar", self.root.destroy, "primary")])

    # -- execução em segundo plano -----------------------------------------------
    def _show_progress(self, title, subtitle):
        self._reset(title, subtitle)
        self.progress = ProgressBar(self.page, self.pal)
        self.progress.pack(anchor="w", pady=(28, 8))
        self.progress_label = make_label(self.page, self.pal, muted=True, size=9)
        self.progress_label.pack(anchor="w")

    def _run(self, job, on_done):
        def worker():
            try:
                job(lambda fraction, text: self.work.put(("progress", fraction, text)))
                self.work.put(("done",))
            except InstallError as exc:
                self.work.put(("error", str(exc)))
            except Exception as exc:  # noqa: BLE001 - qualquer falha vira mensagem na tela
                self.work.put(("error", f"Erro inesperado:\n{exc}"))

        def poll():
            try:
                while True:
                    kind, *data = self.work.get_nowait()
                    if kind == "progress":
                        self.progress.set(data[0])
                        self.progress_label.configure(text=str(data[1])[:70])
                    elif kind == "done":
                        return on_done()
                    else:
                        return self.error(data[0])
            except queue.Empty:
                pass
            self.root.after(40, poll)

        threading.Thread(target=worker, daemon=True).start()
        poll()


# -- linha de comando ---------------------------------------------------------
def main():
    is_uninstaller = Path(sys.executable).name.lower().startswith("uninstall") and getattr(sys, "frozen", False)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dir")
    parser.add_argument("--remove-data", action="store_true")
    parser.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--no-startmenu", action="store_true")
    parser.add_argument("--no-autostart", action="store_true")
    parser.add_argument("--firewall", action="store_true")
    args, _ = parser.parse_known_args()

    if is_uninstaller or args.uninstall:
        target = Path(sys.executable).parent if is_uninstaller else (Path(args.dir) if args.dir else installed_directory())
        if target is None or not target.exists():
            print("Battery Hub não está instalado.")
            return 1
        if args.silent:
            uninstall(target, args.remove_data, False)
            return 0
        wizard = Wizard(uninstalling=True, directory=target)
        wizard.show_uninstall()
        wizard.root.mainloop()
        return 0

    if args.silent:
        try:
            target = resolve_target(args.dir or str(default_directory()))
            # Firewall é opt-in na instalação silenciosa: pede UAC, o que não faz sentido sem tela.
            install(Options(target, not args.no_desktop, not args.no_startmenu, not args.no_autostart,
                            args.firewall))
        except InstallError as exc:
            print(exc)
            return 1
        print(f"Instalado em {target}")
        return 0

    wizard = Wizard(uninstalling=False, directory=installed_directory())
    wizard.show_welcome()
    wizard.root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
