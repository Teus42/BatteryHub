import time


def is_offline(device, offline_minutes, now=None):
    """Dispositivo que nunca reportou não é 'offline', apenas 'aguardando'."""
    if device["last_seen"] is None:
        return False
    now = time.time() if now is None else now
    return now - device["last_seen"] > offline_minutes * 60


def format_last_seen(last_seen, now=None):
    if last_seen is None:
        return "Nunca"
    now = time.time() if now is None else now
    delta = max(0, int(now - last_seen))
    if delta < 60:
        return "agora"
    if delta < 3600:
        return f"há {delta // 60} min"
    if delta < 86400:
        return f"há {delta // 3600} h {delta % 3600 // 60} min"
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(last_seen))
