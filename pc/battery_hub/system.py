"""Integrações com o Windows: instância única, início automático e firewall."""
import ctypes
import os
import subprocess
import sys
import winreg
from pathlib import Path

from .config import APP_NAME

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_FIREWALL_RULE = "Battery Hub"
_ERROR_ALREADY_EXISTS = 183

_mutex = None  # mantém o handle vivo durante todo o processo


def acquire_single_instance():
    global _mutex
    # BATTERYHUB_INSTANCE permite rodar uma segunda cópia isolada (testes); normalmente vazio.
    name = "Local\\BatteryHubSingleInstance" + os.environ.get("BATTERYHUB_INSTANCE", "")
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    return ctypes.windll.kernel32.GetLastError() != _ERROR_ALREADY_EXISTS


def _launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    runner = Path(__file__).resolve().parents[1] / "run.py"
    return f'"{pythonw}" "{runner}"'


def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False


def set_autostart(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def open_firewall_port(port):
    """Cria regra de entrada (rede privada) pedindo elevação ao usuário (UAC)."""
    args = (
        f'advfirewall firewall delete rule name="{_FIREWALL_RULE}" & '
        f'netsh advfirewall firewall add rule name="{_FIREWALL_RULE}" dir=in action=allow '
        f"protocol=TCP localport={port} profile=private"
    )
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "cmd.exe", f"/c netsh {args}", None, 0
    )
    return result > 32  # <= 32 significa erro ou UAC recusado
