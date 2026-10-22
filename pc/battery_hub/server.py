import json
import queue
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY_BYTES = 4096

# Anti força-bruta do código de pareamento (1.000.000 de combinações, 10 min de validade).
PAIR_FAILS_PER_IP = (5, 60)        # 5 erros por minuto por IP
PAIR_FAILS_GLOBAL = (30, 600)      # 30 erros em 10 min, somando todos os IPs

# "Localizar dispositivo": um pedido vale por este tempo e o aparelho para sozinho ao fim dele.
FIND_MAX_SECONDS = 600
# Comentário enviado ao aparelho de tempos em tempos para o PC notar que ele sumiu (e o aparelho,
# que o PC sumiu). Espaçado de propósito: cada envio acorda o rádio Wi-Fi do aparelho.
KEEPALIVE_SECONDS = 240


class FailureLimiter:
    def __init__(self, max_failures, window_seconds):
        self._max = max_failures
        self._window = window_seconds
        self._failures = {}  # chave -> [timestamps]
        self._lock = threading.Lock()

    def blocked(self, key):
        with self._lock:
            recent = self._recent(key)
            return len(recent) >= self._max

    def record(self, key):
        with self._lock:
            self._recent(key).append(time.time())

    def _recent(self, key):
        cutoff = time.time() - self._window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        self._failures[key] = recent
        return recent


def local_ips():
    """IPv4 da máquina na rede local, o mais provável primeiro."""
    ips = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # não envia nada, só escolhe a interface
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith(("127.", "169.254.")):
                ips.append(ip)
    except OSError:
        pass
    return ips


def _parse_reading(data):
    level = data.get("level")
    if isinstance(level, bool) or not isinstance(level, (int, float)) or not 0 <= level <= 100:
        raise ValueError("level inválido")

    def optional_number(key):
        value = data.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} inválido")
        return float(value)

    return {
        "level": int(round(level)),
        "charging": bool(data.get("charging", False)),
        "temperature": optional_number("temperature"),
        "voltage": optional_number("voltage"),
    }


class HubServer:
    def __init__(self, db, on_reading=None, on_paired=None, on_find_changed=None):
        self._db = db
        self._on_reading = on_reading
        self._on_paired = on_paired
        self._on_find_changed = on_find_changed  # callback(device_id), chamado em threads do servidor
        self._ip_limiter = FailureLimiter(*PAIR_FAILS_PER_IP)
        self._global_limiter = FailureLimiter(*PAIR_FAILS_GLOBAL)
        self._httpd = None
        self._thread = None
        self._lock = threading.Lock()
        self._channels = {}  # id do dispositivo -> fila de mensagens do canal aberto por ele
        self._find = {}      # id do dispositivo -> {"state": "pending" | "ringing", "until": epoch}

    def start(self, port):
        self.stop()
        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), self._make_handler())
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        with self._lock:
            for channel in self._channels.values():
                channel.put("close")  # encerra as conexões abertas dos aparelhos

    # -- localizar dispositivo ----------------------------------------------
    def find_state(self, device_id):
        """'off', 'pending' (pedido enviado, aparelho ainda não confirmou) ou 'ringing'."""
        with self._lock:
            entry = self._find.get(device_id)
            if entry and entry["until"] < time.time():
                del self._find[device_id]
                entry = None
            return entry["state"] if entry else "off"

    def finding_ids(self):
        with self._lock:
            ids = list(self._find)
        return [i for i in ids if self.find_state(i) != "off"]

    def is_listening(self, device_id):
        """O aparelho está com o canal aberto (recebe o comando na hora)?"""
        with self._lock:
            return device_id in self._channels

    def start_find(self, device_id):
        """Pede ao aparelho que toque. Retorna se ele está conectado agora; se não, o pedido
        fica valendo e é entregue quando ele reconectar (até FIND_MAX_SECONDS)."""
        with self._lock:
            self._find[device_id] = {"state": "pending", "until": time.time() + FIND_MAX_SECONDS}
            channel = self._channels.get(device_id)
        if channel:
            channel.put("find-start")
        self._changed(device_id)
        return channel is not None

    def stop_find(self, device_id):
        with self._lock:
            self._find.pop(device_id, None)
            channel = self._channels.get(device_id)
        if channel:
            channel.put("find-stop")
        self._changed(device_id)

    def forget_device(self, device_id):
        """Dispositivo removido: cancela a busca e derruba o canal dele."""
        self.stop_find(device_id)
        with self._lock:
            channel = self._channels.pop(device_id, None)
        if channel:
            channel.put("close")

    def _device_ack(self, device_id, ringing):
        with self._lock:
            if ringing:
                if device_id in self._find:
                    self._find[device_id]["state"] = "ringing"
            else:
                self._find.pop(device_id, None)  # parou (pelo PC, pelo aparelho ou por tempo)
        self._changed(device_id)

    def _changed(self, device_id):
        if self._on_find_changed:
            self._on_find_changed(device_id)

    def _open_channel(self, device_id):
        channel = queue.Queue()
        with self._lock:
            previous = self._channels.get(device_id)
            self._channels[device_id] = channel
        if previous:
            previous.put("close")  # o mesmo aparelho reconectou: vale a conexão nova
        return channel

    def _close_channel(self, device_id, channel):
        with self._lock:
            if self._channels.get(device_id) is channel:
                del self._channels[device_id]

    # -- HTTP ----------------------------------------------------------------
    def _make_handler(self):
        db, on_reading, on_paired, hub = self._db, self._on_reading, self._on_paired, self
        ip_limiter, global_limiter = self._ip_limiter, self._global_limiter

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authenticate(self):
                auth = self.headers.get("Authorization", "")
                token = auth[7:].strip() if auth.startswith("Bearer ") else ""
                return db.get_device_by_token(token) if token else None

            def do_GET(self):
                if self.path == "/health":
                    self._reply(200, {"ok": True})
                elif self.path == "/events":
                    self._get_events()
                else:
                    self._reply(404, {"ok": False, "error": "not found"})

            def _read_json(self):
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= MAX_BODY_BYTES:
                    raise ValueError("tamanho inválido")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON deve ser um objeto")
                return data

            def do_POST(self):
                if self.path == "/battery":
                    self._post_battery()
                elif self.path == "/pair":
                    self._post_pair()
                elif self.path == "/find/ack":
                    self._post_find_ack()
                else:
                    self._reply(404, {"ok": False, "error": "not found"})

            def _get_events(self):
                """Canal aberto pelo aparelho: o PC escreve nele os comandos ('data: find-start')."""
                device = self._authenticate()
                if not device:
                    return self._reply(401, {"ok": False, "error": "token inválido"})
                device_id = device["id"]
                channel = hub._open_channel(device_id)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(b": conectado\n\n")
                    if hub.find_state(device_id) != "off":  # pedido feito enquanto ele estava fora
                        self.wfile.write(b"data: find-start\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            message = channel.get(timeout=KEEPALIVE_SECONDS)
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        if message == "close":
                            break
                        self.wfile.write(f"data: {message}\n\n".encode())
                        self.wfile.flush()
                except OSError:
                    pass  # aparelho desconectou
                finally:
                    hub._close_channel(device_id, channel)

            def _post_find_ack(self):
                device = self._authenticate()
                if not device:
                    return self._reply(401, {"ok": False, "error": "token inválido"})
                try:
                    ringing = bool(self._read_json().get("ringing"))
                except (ValueError, json.JSONDecodeError) as exc:
                    return self._reply(400, {"ok": False, "error": str(exc)})
                hub._device_ack(device["id"], ringing)
                self._reply(200, {"ok": True})

            def _post_pair(self):
                ip = self.client_address[0]
                if ip_limiter.blocked(ip) or global_limiter.blocked("*"):
                    return self._reply(429, {"ok": False, "error": "muitas tentativas, aguarde"})
                try:
                    code = re.sub(r"\D", "", str(self._read_json().get("code", "")))
                    if len(code) != 6:
                        raise ValueError("código deve ter 6 dígitos")
                except (ValueError, json.JSONDecodeError) as exc:
                    return self._reply(400, {"ok": False, "error": str(exc)})

                device = db.complete_pairing(code)
                if not device:
                    ip_limiter.record(ip)
                    global_limiter.record("*")
                    return self._reply(404, {"ok": False, "error": "código inválido ou expirado"})
                if on_paired:
                    on_paired(device)
                self._reply(200, {"ok": True, "token": device["token"], "name": device["name"]})

            def _post_battery(self):
                device = self._authenticate()
                if not device:
                    return self._reply(401, {"ok": False, "error": "token inválido"})

                try:
                    reading = _parse_reading(self._read_json())
                except (ValueError, json.JSONDecodeError) as exc:
                    return self._reply(400, {"ok": False, "error": str(exc)})

                db.record_reading(device["id"], **reading)
                if on_reading:
                    on_reading(device, reading)
                self._reply(200, {"ok": True, "server_time": int(time.time())})

        return Handler
