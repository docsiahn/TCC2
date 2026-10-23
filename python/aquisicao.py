"""Aquisição USB e operação do ESP32. Executar: python aquisicao.py.

Apenas este programa abre a porta serial. servidor.py é opcional e recebe
os eventos por HTTP local; indisponibilidade da rede não impede salvar CSV.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

PROTOCOL = "TCC_SISTEMA_V2"
MODEL = "eventos5_v1_p10_20261015"
MODEL_SHA = "dd5a7cef19f07b1cdf6c4bd5b62aa321f81d18283c6094dd074c227178dc1cdb"
_MODEL_CONFIG = Path(__file__).resolve().parents[1] / "config" / "modelo.json"
if _MODEL_CONFIG.is_file():
    _contract = json.loads(_MODEL_CONFIG.read_text(encoding="utf-8-sig"))
    PROTOCOL = _contract["protocolo"]
    MODEL = _contract["modelo"]
    MODEL_SHA = _contract["sha256"]

FEATURES = ("VRMS", "IRMS", "P", "Q", "VTHD50", "ITHD50") + tuple(
    f"{channel}FFT_H{n}" for channel in ("V", "I") for n in range(1, 51)
)
ORDER = (1, 5, 3, 11, 2, 10, 4, 8, 7, 9, 12, 6)
LABELS = ("sem_evento_alvo", "liquidificador_ligando", "liquidificador_desligando",
          "microondas_ligando", "microondas_desligando")
NETWORK_TITLES = ("SEM EVENTO", "LIQUIDIFICADOR LIGANDO", "LIQUIDIFICADOR DESLIGANDO",
                  "MICRO-ONDAS LIGANDO", "MICRO-ONDAS DESLIGANDO")
_TITLES = ("Ambos parados", "Ambos funcionando", "Somente liquidificador", "Somente micro-ondas",
           "Liquidificador ligando; micro-ondas parado", "Liquidificador desligando; micro-ondas parado",
           "Micro-ondas ligando; liquidificador parado", "Micro-ondas desligando; liquidificador parado",
           "Liquidificador ligando; micro-ondas funcionando", "Liquidificador desligando; micro-ondas funcionando",
           "Micro-ondas ligando; liquidificador funcionando", "Micro-ondas desligando; liquidificador funcionando")
_BEFORE = ((0, 0), (1, 1), (1, 0), (0, 1), (0, 0), (1, 0), (0, 0), (0, 1), (0, 1), (1, 1), (1, 0), (1, 1))
_AFTER = ((0, 0), (1, 1), (1, 0), (0, 1), (1, 0), (0, 0), (0, 1), (0, 0), (1, 1), (0, 1), (1, 1), (1, 0))
_GROUP = (0, 0, 0, 0, 1, 2, 3, 4, 1, 2, 3, 4)
_SLUGS = ("ambos_parados", "ambos_funcionando", "somente_liquidificador", "somente_microondas",
          "liquidificador_ligando_microondas_parado", "liquidificador_desligando_microondas_parado",
          "microondas_ligando_liquidificador_parado", "microondas_desligando_liquidificador_parado",
          "liquidificador_ligando_microondas_funcionando", "liquidificador_desligando_microondas_funcionando",
          "microondas_ligando_liquidificador_funcionando", "microondas_desligando_liquidificador_funcionando")
SCENARIOS = [dict(id=n + 1, slug=_SLUGS[n], title=_TITLES[n], before=list(_BEFORE[n]),
                  after=list(_AFTER[n]), neural_class=_GROUP[n],
                  action=("MANTENHA OS APARELHOS COMO ESTÃO" if n < 4 else NETWORK_TITLES[_GROUP[n]]))
             for n in range(12)]


def integer(x, lo=0, hi=0xFFFFFFFF):
    return type(x) is int and lo <= x <= hi


def finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def path_inside(path, root):
    # relative_to tambem esta disponivel no Python 3.8 usado no diagnostico.
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_message(m):
    """Valida o contrato; a classe prevista nunca é usada para aceitar o rótulo."""
    if not isinstance(m, dict) or m.get("protocolo") != PROTOCOL:
        raise ValueError("Firmware incompatível. Grave o firmware desta versão pela Arduino IDE.")
    if m.get("modelo") != MODEL or m.get("sha256") != MODEL_SHA:
        raise ValueError("O modelo da placa não corresponde a esta versão.")
    if not integer(m.get("boot")) or not integer(m.get("epoch"), 1):
        raise ValueError("Identificação inválida da placa.")
    if m.get("modo") not in ("teste", "aquisicao"):
        raise ValueError("Modo inválido.")
    states = m.get("estados")
    if not isinstance(states, list) or len(states) != 2 or any(type(x) is not int or x not in (-1, 0, 1) for x in states):
        raise ValueError("Estados inválidos.")
    kind = m.get("tipo")
    if kind not in {"pronto", "heartbeat", "modo", "cenario", "estados", "aviso", "falha", "aquecendo",
                    "captura_armada", "captura_inicio", "acao", "resultado", "entrada", "entrada_fim", "captura_cancelada"}:
        raise ValueError("Mensagem desconhecida.")
    if kind == "entrada":
        values = m.get("valores")
        if not isinstance(values, list) or len(values) != 106 or not all(map(finite, values)) or not integer(m.get("janela"), 0, 24):
            raise ValueError("Dados incompletos da captura.")
    if kind in {"captura_armada", "captura_inicio", "acao", "entrada", "entrada_fim", "captura_cancelada"}:
        if not isinstance(m.get("captura_id"), str) or not m["captura_id"] or len(m["captura_id"]) > 80:
            raise ValueError("Identificador da captura inválido.")
    if kind == "resultado":
        c, p = m.get("classe_id"), m.get("probabilidades")
        if not integer(c, 0, 4) or m.get("classe") != LABELS[c]:
            raise ValueError("Classe inválida.")
        if not isinstance(p, list) or len(p) != 5 or any(not finite(x) or not 0 <= x <= 1 for x in p):
            raise ValueError("Probabilidades inválidas.")
        conf, margin = m.get("confianca"), m.get("margem")
        if not finite(conf) or abs(conf - max(p)) > .0001 or abs(conf - p[c]) > .0001 or abs(sum(p) - 1) > .0001:
            raise ValueError("Resultado da rede inconsistente.")
        if not finite(margin) or abs(margin - (sorted(p)[-1] - sorted(p)[-2])) > .0001:
            raise ValueError("Margem inválida.")
    return m


def atomic_json(path, value):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


class CaptureStore:
    """Manifesto é o ponto de confirmação: dados parciais não contam."""
    def __init__(self, root, repetitions=50, conditions=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "campanha.json"
        if not integer(repetitions, 1, 10000):
            raise ValueError("Quantidade por classe inválida.")
        self.lock = threading.RLock()
        self._file_lock = (self.root / ".campanha.lock").open("a+b")
        if self._file_lock.seek(0, 2) == 0:
            self._file_lock.write(b"0")
            self._file_lock.flush()
        self._file_lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._file_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file_lock.close()
            raise ValueError("Esta campanha já está aberta em outro programa de aquisição.") from None
        try:
            if self.path.exists():
                self.manifest = json.loads(self.path.read_text(encoding="utf-8"))
                if self.manifest.get("schema") != 1 or self.manifest.get("modelo") != MODEL or self.manifest.get("sha256_modelo") != MODEL_SHA or self.manifest.get("features") != list(FEATURES):
                    raise ValueError("Esta pasta pertence a uma campanha incompatível.")
                for r in self.manifest["registros"]:
                    p = self.root / r["csv"]
                    if not path_inside(p, self.root) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != r["sha256_csv"]:
                        raise ValueError("Uma captura já registrada está ausente ou foi alterada. Revise a pasta.")
            else:
                self.manifest = dict(schema=1, campanha_id=uuid.uuid4().hex, criado_em=datetime.now(timezone.utc).isoformat(),
                                     modelo=MODEL, sha256_modelo=MODEL_SHA, protocolo=PROTOCOL,
                                     features=list(FEATURES), repeticoes=repetitions,
                                     condicoes=conditions or dict(potencia_microondas="P10", velocidade_liquidificador="1", cargas_fundo="não informado"),
                                     registros=[])
                atomic_json(self.path, self.manifest)
        except Exception:
            self.close()
            raise

    def close(self):
        file_lock = getattr(self, "_file_lock", None)
        if file_lock is not None and not file_lock.closed:
            try:
                file_lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(file_lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file_lock.fileno(), fcntl.LOCK_UN)
            finally:
                file_lock.close()

    def __del__(self):
        try:
            self.close()
        except (OSError, ImportError, ValueError):
            pass

    @property
    def records(self):
        return list(self.manifest["registros"])

    @property
    def counts(self):
        counts = {n: 0 for n in range(1, 13)}
        for r in self.manifest["registros"]:
            counts[r["cenario"]] += 1
        return counts

    def save(self, capture):
        cid = capture["captura_id"]
        rows, scenario = capture["rows"], capture["cenario"]
        if not integer(scenario, 1, 12) or len(rows) != 25 or any(len(r) != 106 or not all(map(finite, r)) for r in rows):
            raise ValueError("A captura não foi salva: faltam dados.")
        if not isinstance(capture.get("resultado"), dict):
            raise ValueError("A captura não terminou.")
        with self.lock:
            if any(r["captura_id"] == cid and r["boot"] == capture["boot"] for r in self.records):
                return None
            scenario_info = SCENARIOS[scenario - 1]
            unique = uuid.uuid4().hex
            folder = self.root / "csv" / scenario_info["slug"]
            folder.mkdir(parents=True, exist_ok=True)
            csv_path = folder / (unique + ".csv")
            with csv_path.open("x", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(("timestamp",) + FEATURES)
                for index, values in enumerate(rows):
                    writer.writerow([index * 200] + [format(x, ".9g") for x in values])
                f.flush()
                os.fsync(f.fileno())
            relative = csv_path.relative_to(self.root).as_posix()
            record = dict(id=unique, captura_id=cid, boot=capture["boot"], epoch=capture["epoch"],
                          cenario=scenario, rotulo=scenario_info["slug"], classe_esperada=scenario_info["neural_class"],
                          repeticao=self.counts[scenario] + 1, salvo_em=datetime.now(timezone.utc).isoformat(),
                          csv=relative, sha256_csv=hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                          condicoes=self.manifest["condicoes"], resultado=capture["resultado"],
                          origem_captura=capture.get("origem_captura", "não informada"))
            detail = self.root / "detalhes" / (unique + ".json")
            detail.parent.mkdir(exist_ok=True)
            atomic_json(detail, record)
            updated = dict(self.manifest, registros=self.records + [record])
            atomic_json(self.path, updated)
            self.manifest = updated
            return record


class CaptureReceiver:
    def __init__(self, store, on_update=None):
        self.store, self.on_update = store, on_update
        self.active = None
        self.last_mode = None
        self.last_error = ""
        self.context = None

    def abort(self, reason):
        if self.active:
            logging.warning("Captura %s incompleta: %s", self.active["captura_id"], reason)
        self.active = None
        self.last_error = reason

    def accept(self, message):
        try:
            m = validate_message(message)
        except ValueError:
            if self.active:
                self.abort("Dados inválidos durante a captura. Repita a aquisição.")
            raise
        context = (m["boot"], m["epoch"])
        if self.context is not None and context != self.context:
            self.abort("A placa reiniciou ou houve interrupção na aquisição.")
        self.context = context
        self.last_mode = m["modo"]
        kind, cid = m["tipo"], m.get("captura_id")
        if kind == "captura_armada":
            scenario = m.get("cenario")
            if not integer(scenario, 1, 12):
                raise ValueError("Cenário da captura inválido.")
            if self.active and self.active["captura_id"] == cid:
                return None
            if self.active:
                self.abort("Nova captura antes do encerramento da anterior.")
            self.active = dict(captura_id=cid, boot=m["boot"], epoch=m["epoch"], cenario=scenario,
                               rows=[], resultado=None, inicio=time.monotonic(), origem_captura=m.get("origem_captura"))
            self.last_error = ""
        elif kind in ("captura_cancelada", "falha"):
            if kind == "falha" or not self.active or self.active["captura_id"] == cid:
                self.abort("Captura cancelada: " + str(m.get("motivo", "interrupção")))
        elif self.active and cid == self.active["captura_id"]:
            if m.get("cenario") != self.active["cenario"]:
                self.abort("O cenário mudou durante a captura.")
                return None
            if kind == "resultado":
                if self.active["resultado"] is not None and self.active["resultado"] != m:
                    self.abort("Resultados conflitantes na captura.")
                elif m.get("cenario") != self.active["cenario"]:
                    self.abort("O cenário mudou durante a captura.")
                else:
                    self.active["resultado"] = dict(m)
            elif kind == "entrada":
                index = m["janela"]
                rows = self.active["rows"]
                if index < len(rows) and rows[index] == m["valores"]:
                    return None
                if index != len(rows):
                    self.abort("Dados fora de ordem. Repita a captura.")
                else:
                    rows.append(list(m["valores"]))
            elif kind == "entrada_fim":
                capture, self.active = self.active, None
                try:
                    if m.get("janelas") != 25:
                        raise ValueError("Encerramento incompleto da captura.")
                    record = self.store.save(capture)
                    self.last_error = ""
                    if self.on_update:
                        self.on_update(record)
                    return record
                except (ValueError, OSError):
                    self.last_error = "A captura não foi salva. Verifique a pasta e repita."
                    raise
        return None


class LocalBridge(threading.Thread):
    """HTTP em tarefa separada, com fila limitada e ACK de comandos."""
    def __init__(self, url, command, snapshot, firmware_snapshot=None):
        super().__init__(daemon=True)
        self.url = url.rstrip("/")
        self.command, self.snapshot = command, snapshot
        self.events = queue.Queue(maxsize=128)
        self.stop_event = threading.Event()
        self.online = False
        self.handled = {}
        self.server_id = None
        self.firmware_snapshot = firmware_snapshot
        self.server_state = {}

    def send(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            logging.warning("Supervisão HTTP atrasada; aquisição local preservada.")

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=.7) as response:
            return json.load(response)

    def run(self):
        heartbeat = 0.
        while not self.stop_event.wait(.25):
            try:
                state = self.request("/api/estado")
                self.server_state = state
                new_id = state.get("server_id")
                if not self.online or new_id != self.server_id:
                    self.handled.clear()
                    self.server_id = new_id
                    # Ao voltar, publicar o estado atual, sem reproduzir uma fila antiga.
                    while True:
                        try:
                            self.events.get_nowait()
                        except queue.Empty:
                            break
                    self.request("/api/evento", dict(type="connection", **self.snapshot()))
                    if self.firmware_snapshot and self.snapshot()["connected"]:
                        message = self.firmware_snapshot()
                        if message and message.get("tipo") not in ("entrada", "entrada_fim"):
                            self.request("/api/evento", dict(type="serial", message=message))
                    heartbeat = time.monotonic()
                self.online = True
                if time.monotonic() - heartbeat >= 2:
                    self.request("/api/evento", dict(type="connection", **self.snapshot()))
                    heartbeat = time.monotonic()
                for _ in range(12):
                    try:
                        event = self.events.get_nowait()
                    except queue.Empty:
                        break
                    if event.get("type") == "mqtt_control":
                        self.request("/api/mqtt", dict(action=event["action"]))
                    else:
                        self.request("/api/evento", event)
                commands = self.request("/api/comandos").get("commands", [])
                for item in commands:
                    ident = item["id"]
                    if ident not in self.handled:
                        try:
                            self.command(item["command"])
                            result = dict(type="command_result", command_id=ident, success=True, error="")
                        except (ValueError, OSError) as exc:
                            result = dict(type="command_result", command_id=ident, success=False, error=str(exc))
                        self.handled[ident] = result
                    self.request("/api/evento", self.handled[ident])
                if len(self.handled) > 128:
                    self.handled = dict(list(self.handled.items())[-64:])
            except (OSError, ValueError, KeyError, urllib.error.URLError):
                self.online = False


def registrar_diagnostico_placa(message, previous=None):
    """Registra transicoes e falhas antes de qualquer envio HTTP; sem dados brutos."""
    previous = previous or {}
    reasons = []
    if previous:
        if message.get("boot") != previous.get("boot"):
            reasons.append("reinicio_esp32")
        if message.get("epoch") != previous.get("epoch"):
            reasons.append("mudanca_epoch_aquisicao")
        if message.get("estados") != previous.get("estados"):
            reasons.append("mudanca_estados")
    else:
        reasons.append("primeira_mensagem")
    if message.get("tipo") in ("falha", "captura_cancelada"):
        if (message.get("tipo"), message.get("motivo")) != (previous.get("tipo"), previous.get("motivo")):
            reasons.append("falha_informada")
    if message.get("tipo") == "resultado" and message.get("classe_id") in (1, 2, 3, 4):
        reasons.append("previsao_transicao")
    if reasons:
        keys = ("tipo", "motivo", "boot", "epoch", "uptime_ms", "estados", "classe",
                "confianca", "margem", "decisao", "aceito", "alterados", "seq",
                "frames_perdidos", "inferencias_descartadas", "vrms_adc", "irms_adc",
                "diagnostico_entrada", "revisao_firmware")
        record = dict(gatilhos=reasons, anterior={k: previous.get(k) for k in
                      ("tipo", "boot", "epoch", "estados")},
                      atual={k: message[k] for k in keys if k in message})
        logging.info("DIAGNOSTICO_PLACA %s", json.dumps(record, ensure_ascii=False))


class DeviceClient:
    def __init__(self, store, server_url=None):
        self.store = store
        self.receiver = CaptureReceiver(store)
        self.events = queue.Queue()
        self.serial = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.connected = False
        self.port = ""
        self.last_packet = 0.
        self.latest = None
        self.bridge = LocalBridge(server_url, self.remote_command, self.connection_snapshot, lambda: self.latest) if server_url else None
        if self.bridge:
            self.bridge.start()

    def connection_snapshot(self):
        return dict(connected=self.connected, port=self.port, message="USB conectado" if self.connected else "USB desconectado")

    def connect(self, port):
        if not port or len(port) > 200:
            raise ValueError("Selecione a porta do ESP32.")
        self.disconnect()
        self.port = port
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()

    def disconnect(self):
        self.stop_event.set()
        with self.lock:
            serial_port, self.serial = self.serial, None
        if serial_port:
            try:
                serial_port.close()
            except OSError:
                pass
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        self.connected = False
        self.receiver.abort("USB desconectado.")
        if self.bridge:
            self.bridge.send(dict(type="connection", **self.connection_snapshot()))

    def write(self, text):
        if not self.connected:
            raise ValueError("Conecte a placa primeiro.")
        with self.lock:
            if self.serial is None:
                raise ValueError("USB desconectado.")
            self.serial.write((text + "\n").encode("ascii"))

    def remote_command(self, command):
        op = command.get("op")
        if op == "mode" and command.get("value") in ("teste", "aquisicao"):
            text = "MODE " + command["value"]
        elif op == "scenario" and integer(command.get("value"), 1, 12):
            text = "SCENARIO " + str(command["value"])
        elif op in ("start", "cancel", "reset"):
            text = {"start": "CAPTURE", "cancel": "CANCEL", "reset": "RESET"}[op]
        elif op == "states" and isinstance(command.get("values"), list) and len(command["values"]) == 2 and all(integer(x, 0, 1) for x in command["values"]):
            text = "STATE %d %d" % tuple(command["values"])
        else:
            raise ValueError("Comando inválido.")
        self.write(text)

    def worker(self):
        try:
            import serial
        except ImportError:
            self.events.put(("error", "Instale as dependências com: python -m pip install -r requirements.txt"))
            return
        while not self.stop_event.is_set():
            port = None
            try:
                port = serial.Serial()
                port.port, port.baudrate, port.timeout, port.write_timeout = self.port, 115200, .2, 1
                port.dtr = port.rts = False
                port.open()
                with self.lock:
                    self.serial = port
                port.write(b"PING\n")
                start, last_ping, buffer = time.monotonic(), 0., b""
                self.last_packet = 0.
                while not self.stop_event.is_set():
                    data = port.read(min(max(port.in_waiting, 1), 8192))
                    buffer += data
                    if len(buffer) > 65536:
                        buffer = b""
                        self.receiver.abort("Comunicação interrompida. Repita a captura.")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        try:
                            m = validate_message(json.loads(line))
                        except (ValueError, UnicodeError, TypeError):
                            logging.warning("Linha serial inválida: %.180r", line)
                            if self.receiver.active:
                                self.receiver.abort("Comunicação interrompida. Repita a captura.")
                                self.events.put(("error", self.receiver.last_error))
                            continue
                        first_packet = not self.connected
                        self.connected = True
                        if first_packet and self.bridge:
                            self.bridge.send(dict(type="connection", **self.connection_snapshot()))
                        self.last_packet = time.monotonic()
                        registrar_diagnostico_placa(m, self.latest)
                        self.latest = m
                        try:
                            saved = self.receiver.accept(m)
                        except (ValueError, OSError) as exc:
                            self.events.put(("error", str(exc)))
                            saved = None
                        if self.bridge and m["tipo"] != "entrada":
                            self.bridge.send(dict(type="serial", message=m))
                        self.events.put(("message", m))
                        if saved:
                            self.events.put(("saved", saved))
                            if self.bridge:
                                self.bridge.send(dict(type="capture_saved", capture_id=saved["captura_id"],
                                                      path=saved["csv"], class_id=saved["cenario"], counts=self.store.counts))
                    now = time.monotonic()
                    if not self.connected and now - last_ping > 1:
                        port.write(b"PING\n")
                        last_ping = now
                    if not self.connected and now - start > 8:
                        raise OSError("A porta abriu, mas o firmware não respondeu. Confira a versão gravada.")
                    if self.connected and now - self.last_packet > 8:
                        raise OSError("Sem resposta da placa.")
                    if self.receiver.active and now - self.receiver.active["inicio"] > 20:
                        self.receiver.abort("A captura não terminou. Repita quando a placa estiver pronta.")
                        self.events.put(("error", self.receiver.last_error))
            except (OSError, serial.SerialException) as exc:
                logging.warning("DIAGNOSTICO_USB porta=%s motivo=%s", self.port, exc)
                self.connected = False
                self.receiver.abort("Falha na comunicação USB.")
                self.events.put(("error", "USB: " + str(exc)))
                if self.bridge:
                    self.bridge.send(dict(type="connection", **self.connection_snapshot()))
                self.stop_event.wait(1.5)
            finally:
                with self.lock:
                    if self.serial is port:
                        self.serial = None
                if port:
                    try:
                        port.close()
                    except OSError:
                        pass

    def close(self):
        self.disconnect()
        if self.bridge:
            self.bridge.stop_event.set()
            self.bridge.join(timeout=2)
        self.store.close()


class Interface:
    def __init__(self, root, client):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root, self.client = root, client
        root.title("TCC — Aquisição e teste")
        root.geometry("1080x740")
        root.minsize(850, 650)
        # A rolagem mantém todos os controles acessíveis em telas de notebook.
        window = root
        outer = ttk.Frame(window)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        content = ttk.Frame(canvas)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_id, width=event.width))
        window.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"))
        root = content
        self.style = ttk.Style(root)
        self.style.configure("TButton", font=("Segoe UI", 13), padding=8)
        self.style.configure("TLabel", font=("Segoe UI", 12))
        self.port = tk.StringVar()
        self.scenario = tk.StringVar(value="Escolha a classe antes da captura")
        self.auto_next = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Conecte a placa por USB")
        self.mode = tk.StringVar(value="AGUARDANDO PLACA")
        self.action = tk.StringVar(value="Prepare os aparelhos")
        self.initial = tk.StringVar(value="")
        self.progress = tk.StringVar()
        self.result = tk.StringVar(value="")
        self.states = tk.StringVar(value="Liquidificador: desconhecido     Micro-ondas: desconhecido")
        top = ttk.Frame(root, padding=16); top.pack(fill="x")
        ttk.Label(top, text="USB").pack(side="left")
        self.ports = ttk.Combobox(top, textvariable=self.port, width=18, state="readonly")
        self.ports.pack(side="left", padx=8)
        ttk.Button(top, text="Atualizar", command=self.refresh_ports).pack(side="left")
        ttk.Button(top, text="Conectar", command=lambda: self.call(lambda: client.connect(self.port.get()))).pack(side="left", padx=8)
        ttk.Button(top, text="Desconectar", command=client.disconnect).pack(side="left")
        network = ttk.Frame(root); network.pack(fill="x", padx=20, pady=5)
        self.mqtt_button = ttk.Button(network, text="Conectar MQTT", command=self.toggle_mqtt)
        self.mqtt_button.pack(side="left")
        self.mqtt_status = tk.StringVar(value="Abra servidor.py para habilitar MQTT e painel")
        ttk.Label(network, textvariable=self.mqtt_status, wraplength=700).pack(side="left", padx=12)
        ttk.Label(root, textvariable=self.mode, font=("Segoe UI", 23, "bold")).pack(pady=8)
        modes = ttk.Frame(root); modes.pack()
        ttk.Button(modes, text="Teste contínuo", command=lambda: self.send(dict(op="mode", value="teste"))).pack(side="left", padx=8)
        ttk.Button(modes, text="Aquisição", command=lambda: self.send(dict(op="mode", value="aquisicao"))).pack(side="left", padx=8)
        ttk.Label(root, text="BOTÃO: 1 clique captura  •  2 cliques teste  •  3 cliques aquisição").pack(pady=12)
        self.choices = ttk.Combobox(root, textvariable=self.scenario, values=[f'{s["id"]:02d} — {s["title"]}' for s in SCENARIOS], state="readonly", width=84, font=("Segoe UI", 13))
        self.choices.pack(fill="x", padx=20, pady=8)
        self.choices.bind("<<ComboboxSelected>>", lambda _: self.select())
        ttk.Label(root, textvariable=self.initial, wraplength=980).pack(pady=5)
        ttk.Label(root, textvariable=self.action, font=("Segoe UI", 25, "bold"), wraplength=1000, justify="center").pack(pady=16)
        buttons = ttk.Frame(root); buttons.pack(pady=7)
        ttk.Button(buttons, text="Iniciar captura (Enter)", command=lambda: self.send(dict(op="start"))).pack(side="left", padx=8)
        ttk.Button(buttons, text="Cancelar", command=lambda: self.send(dict(op="cancel"))).pack(side="left", padx=8)
        ttk.Checkbutton(root, text="Sugerir próxima classe após salvar", variable=self.auto_next).pack()
        ttk.Label(root, textvariable=self.progress).pack(pady=7)
        ttk.Label(root, textvariable=self.result, font=("Segoe UI", 15)).pack(pady=6)
        ttk.Label(root, textvariable=self.states).pack(pady=5)
        state_row = ttk.Frame(root);state_row.pack(pady=5)
        ttk.Button(state_row, text="Informar estados iniciais", command=self.state_dialog).pack(side="left", padx=6)
        ttk.Button(state_row, text="Abrir pasta dos dados", command=self.open_folder).pack(side="left", padx=6)
        ttk.Label(root, textvariable=self.status, wraplength=1000).pack(pady=10)
        window.bind("<Return>", lambda _: self.send(dict(op="start")))
        window.bind("<F11>", lambda _: window.attributes("-fullscreen", not window.attributes("-fullscreen")))
        window.bind("<Escape>", lambda _: window.attributes("-fullscreen", False))
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_ports(); self.update_progress(); root.after(100, self.tick)

    def call(self, fn):
        try:
            return fn()
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))

    def send(self, command):
        self.call(lambda: self.client.remote_command(command))

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
            self.ports["values"] = ports
            if ports and self.port.get() not in ports:
                self.port.set(ports[0])
        except ImportError:
            self.status.set("Instale as dependências do requirements.txt")

    def select(self):
        index = self.choices.current()
        if index >= 0:
            self.send(dict(op="scenario", value=index + 1))

    def toggle_mqtt(self):
        bridge = self.client.bridge
        if not bridge or not bridge.online:
            self.status.set("Abra servidor.py no notebook para conectar o MQTT.")
            return
        mqtt = bridge.server_state.get("mqtt", {})
        bridge.send(dict(type="mqtt_control", action="disconnect" if mqtt.get("enabled") else "connect"))
        self.mqtt_button.configure(state="disabled")

    def show_scenario(self, scenario):
        if integer(scenario, 1, 12):
            self.choices.current(scenario - 1)
            s = SCENARIOS[scenario - 1]
            names = ["PARADO", "FUNCIONANDO"]
            self.initial.set(f'Prepare: liquidificador {names[s["before"][0]]}; micro-ondas {names[s["before"][1]]}.')
            self.action.set(s["action"] if s["neural_class"] == 0 else "AGUARDE O AVISO PARA MUDAR")
            self.update_progress()

    def update_progress(self):
        counts = self.client.store.counts
        target = self.client.store.manifest["repeticoes"]
        self.progress.set(f"Capturas salvas: {sum(counts.values())} / {target * 12}")
        selected = self.choices.current() + 1
        if selected in counts:
            self.progress.set(self.progress.get() + f"     Nesta condição: {counts[selected]} / {target}")

    def tick(self):
        for _ in range(180):
            try:
                kind, value = self.client.events.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                self.status.set(value)
            elif kind == "saved":
                self.status.set("CAPTURA SALVA")
                self.action.set("SALVA — PREPARE A PRÓXIMA")
                self.update_progress()
                if self.auto_next.get():
                    counts, target = self.client.store.counts, self.client.store.manifest["repeticoes"]
                    old = ORDER.index(value["cenario"])
                    next_s = next((ORDER[(old + i) % 12] for i in range(1, 13) if counts[ORDER[(old + i) % 12]] < target), None)
                    if next_s:
                        self.root.after(250, lambda n=next_s: self.send(dict(op="scenario", value=n)))
            elif kind == "message":
                m = value
                self.mode.set("TESTE CONTÍNUO" if m["modo"] == "teste" else "AQUISIÇÃO")
                states = ["desconhecido" if x < 0 else "funcionando" if x else "parado" for x in m["estados"]]
                self.states.set(f"Liquidificador: {states[0]}     Micro-ondas: {states[1]}")
                k = m["tipo"]
                confirmed = m.get("cenario_selecionado")
                if integer(confirmed, 1, 12):
                    self.choices.current(confirmed - 1)
                if k == "cenario":
                    self.show_scenario(m.get("cenario_selecionado", m.get("cenario")))
                elif k == "pronto":
                    self.status.set("USB conectado. Selecione a classe ou informe os estados para o teste.")
                elif k == "captura_inicio":
                    self.action.set("CAPTURANDO — MANTENHA O ESTADO INICIAL")
                elif k == "acao":
                    scenario = m.get("cenario")
                    if integer(scenario, 1, 12):
                        self.action.set("AGORA: " + SCENARIOS[scenario - 1]["action"])
                        self.root.bell()
                elif k == "resultado":
                    self.result.set("Resposta da rede: " + NETWORK_TITLES[m["classe_id"]])
                    if m["modo"] == "aquisicao":
                        self.action.set("SALVANDO")
                elif k in ("aviso", "falha", "captura_cancelada"):
                    messages = {"cenario_nao_selecionado": "Selecione a classe antes de capturar.", "ocupado": "Aguarde a captura terminar ou cancele.", "captura_em_andamento": "Aguarde a captura terminar ou cancele.", "modo_incompativel": "Mude para aquisição antes de capturar.", "modo_teste": "Mude para aquisição antes de capturar."}
                    self.status.set(messages.get(m.get("motivo"), str(m.get("motivo", "Verifique a placa.")).replace("_", " ")))
        if not self.client.connected:
            self.mode.set("USB DESCONECTADO")
            self.states.set("Liquidificador: desconhecido     Micro-ondas: desconhecido")
        bridge = self.client.bridge
        if bridge and bridge.online:
            mqtt = bridge.server_state.get("mqtt", {})
            changing = mqtt.get("changing", False)
            self.mqtt_button.configure(text="Aguarde…" if changing else "Desconectar MQTT" if mqtt.get("enabled") else "Conectar MQTT",
                                       state="disabled" if changing else "normal")
            self.mqtt_status.set(mqtt.get("message", "Aguardando servidor"))
        else:
            self.mqtt_button.configure(state="normal", text="Conectar MQTT")
            self.mqtt_status.set("Servidor indisponível; abra servidor.py")
        self.root.after(100, self.tick)

    def state_dialog(self):
        from tkinter import simpledialog
        a = simpledialog.askinteger("Estado inicial", "Liquidificador: 0 parado; 1 funcionando", minvalue=0, maxvalue=1, parent=self.root)
        if a is None:
            return
        b = simpledialog.askinteger("Estado inicial", "Micro-ondas: 0 parado; 1 funcionando", minvalue=0, maxvalue=1, parent=self.root)
        if b is not None:
            self.send(dict(op="states", values=[a, b]))

    def open_folder(self):
        if os.name == "nt":
            os.startfile(self.client.store.root)
        else:
            self.status.set(str(self.client.store.root))

    def close(self):
        self.client.close()
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path.home() / "Documents" / "TCC" / "capturas"
    parser.add_argument("--dados", type=Path, default=default_root)
    parser.add_argument("--repeticoes", type=int, default=50)
    parser.add_argument("--porta")
    parser.add_argument("--servidor", default="http://127.0.0.1:8765")
    parser.add_argument("--sem-servidor", action="store_true")
    parser.add_argument("--sem-interface", action="store_true")
    parser.add_argument("--potencia", default="P10")
    parser.add_argument("--velocidade", default="1")
    parser.add_argument("--fundo", default="não informado")
    args = parser.parse_args(argv)
    try:
        store = CaptureStore(args.dados, args.repeticoes,
                             dict(potencia_microondas=args.potencia, velocidade_liquidificador=args.velocidade, cargas_fundo=args.fundo))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    handler = RotatingFileHandler(store.root / "diagnostico.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    client = DeviceClient(store, None if args.sem_servidor else args.servidor)
    if args.sem_interface:
        if not args.porta:
            parser.error("No modo sem interface, informe --porta COMx.")
        client.connect(args.porta)
        print("USB em operação. Ctrl+C encerra. Botão: 1 captura; 2 teste; 3 aquisição.")
        try:
            while True:
                kind, value = client.events.get()
                if kind in ("error", "saved"):
                    print(value if kind == "error" else "Captura salva: " + value["csv"], flush=True)
        except KeyboardInterrupt:
            client.close()
        return
    try:
        import tkinter as tk
        root = tk.Tk()
        Interface(root, client)
        if args.porta:
            client.connect(args.porta)
        root.mainloop()
    except ImportError:
        client.close()
        raise SystemExit("Instale Python com Tcl/Tk para abrir a tela, ou utilize --sem-interface.")


if __name__ == "__main__":
    main()
