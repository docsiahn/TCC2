"""Painel HTTP e broker MQTT locais do projeto TCC.

Execute ``python python/servidor.py`` a partir da raiz do repositório.
Este programa nunca abre a porta serial. ``aquisicao.py`` é o único dono da
USB e encaminha os eventos confirmados pela placa para esta API local.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import ipaddress
import json
import logging
import math
import re
import socket
import threading
import time
import uuid
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


def lan_addresses() -> list[str]:
    """Endereços IPv4 locais anunciados pelo computador, sem serviço externo."""
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        return []
    return sorted(address for address in addresses
                  if ipaddress.ip_address(address).is_private
                  and not ipaddress.ip_address(address).is_loopback
                  and not ipaddress.ip_address(address).is_link_local)


def listen_host(value: str) -> str:
    if value in ("localhost", "127.0.0.1", "0.0.0.0"):
        return "127.0.0.1" if value == "localhost" else value
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError):
        raise ValueError("Use 0.0.0.0, 127.0.0.1 ou um IPv4 da rede local.") from None
    if not address.is_private or address.is_multicast or address.is_unspecified:
        raise ValueError("O endereço de escuta deve pertencer à rede local.")
    return str(address)


PROTOCOL = "TCC_SISTEMA_V2"
MODEL = "eventos5_v1_p10_20261015"
MODEL_SHA = "dd5a7cef19f07b1cdf6c4bd5b62aa321f81d18283c6094dd074c227178dc1cdb"
LABELS = (
    "sem_evento_alvo", "liquidificador_ligando", "liquidificador_desligando",
    "microondas_ligando", "microondas_desligando",
)
TITLES = (
    "Ambos parados", "Ambos funcionando", "Somente liquidificador",
    "Somente micro-ondas", "Liquidificador ligando; micro parado",
    "Liquidificador desligando; micro parado",
    "Micro-ondas ligando; liquidificador parado",
    "Micro-ondas desligando; liquidificador parado",
    "Liquidificador ligando; micro funcionando",
    "Liquidificador desligando; micro funcionando",
    "Micro-ondas ligando; liquidificador funcionando",
    "Micro-ondas desligando; liquidificador funcionando",
)
KINDS = frozenset((
    "pronto", "heartbeat", "modo", "cenario", "captura_armada",
    "captura_inicio", "acao", "resultado", "entrada", "entrada_fim",
    "captura_cancelada", "aviso", "falha", "aquecendo", "estados",
))
COMMAND_TTL = 15.0
CLIENT_TTL = 8.0
COMMAND_LIMIT = 32
BODY_LIMIT = 65536
LOG = logging.getLogger("tcc.servidor")


def _integer(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _text(value: Any, limit: int = 300) -> bool:
    return isinstance(value, str) and len(value) <= limit


def _json_finite(value: Any, depth: int = 0) -> None:
    """Recusa NaN, estruturas excessivas e tipos fora do contrato JSON."""
    if depth > 8:
        raise ValueError("Mensagem muito aninhada.")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Valor numérico inválido.")
        return
    if isinstance(value, str):
        if len(value) > 4096:
            raise ValueError("Texto excessivo.")
        return
    if isinstance(value, list) and len(value) <= 200:
        for item in value:
            _json_finite(item, depth + 1)
        return
    if isinstance(value, dict) and len(value) <= 100:
        for key, item in value.items():
            if not _text(key, 100):
                raise ValueError("Chave inválida.")
            _json_finite(item, depth + 1)
        return
    raise ValueError("Estrutura JSON inválida.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chave JSON duplicada.")
        result[key] = value
    return result


def load_contract(root: Path = ROOT) -> dict[str, Any]:
    """Lê apenas o contrato do próprio repositório, sem caminhos do cliente."""
    path = root / "config" / "modelo.json"
    contract = {
        "protocolo": PROTOCOL, "modelo": MODEL, "sha256": MODEL_SHA,
        "labels": list(LABELS), "features_aquisicao": 106,
        "features_previsao": 14, "context_ms": 5000,
        "sampling_interval_ms": 200, "serial_baud": 115200,
    }
    if path.is_file():
        supplied = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(supplied, dict):
            raise ValueError("config/modelo.json deve conter um objeto JSON.")
        contract.update(supplied)
    contract.setdefault("cenarios", [
        {"id": index, "title": title}
        for index, title in enumerate(TITLES, 1)
    ])
    return contract


def load_settings(root: Path = ROOT) -> dict[str, Any]:
    """Configuração relativa ao repositório; a linha de comando tem prioridade."""
    settings = {"http": {"host": "0.0.0.0", "port": 8765},
                "mqtt": {"host": "0.0.0.0", "port": 1883,
                         "topic": "tcc/eventos5/esp32_01", "embedded": True}}
    path = root / "config" / "config_exemplo.json"
    if path.is_file():
        supplied = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(supplied, dict):
            raise ValueError("A configuração deve conter um objeto JSON.")
        for section in ("http", "mqtt"):
            if section in supplied:
                if not isinstance(supplied[section], dict):
                    raise ValueError(f"Configuração {section} inválida.")
                settings[section].update(supplied[section])
            settings[section]["host"] = listen_host(settings[section]["host"])
            if not _integer(settings[section]["port"], 1, 65535):
                raise ValueError(f"Porta {section} inválida.")
        if type(settings["mqtt"]["embedded"]) is not bool:
            raise ValueError("mqtt.embedded deve ser true ou false.")
    return settings


class DashboardState:
    """Estado de visualização; nenhuma regra neural é executada no servidor."""

    def __init__(self, contract: dict[str, Any] | None = None, *, demo: bool = False,
                 clock=time.monotonic):
        self.lock = threading.RLock()
        self.server_id = uuid.uuid4().hex
        self.contract = contract or load_contract()
        self.demo = demo
        self.clock = clock
        self.updated_at: float | None = None
        self.client_seen: float | None = None
        self.commands: deque[dict[str, Any]] = deque()
        self.next_command = 1
        self.revision = 0
        self.mode_confirmed: str | None = None
        self.mode_pending: str | None = None
        self.mode_pending_since: float | None = None
        self.mode_source = ""
        self.scenario = 0
        self.states = [-1, -1]
        self.result: dict[str, Any] | None = None
        self.capture: dict[str, Any] = {"active": False, "phase": "aguardando", "id": ""}
        self.serial = {"connected": False, "port": "", "message": "Abra o programa de aquisição."}
        self.mqtt = {"enabled": False, "connected": False, "broker_running": False,
                     "changing": False,
                     "message": "MQTT desligado", "published": 0, "received": 0,
                     "dropped": 0, "host": "127.0.0.1", "port": 1883, "topic": ""}
        self.last_error = ""
        self.firmware: dict[str, Any] | None = None
        self.boot: int | None = None
        self.last_state_loss: dict[str, Any] | None = None

    def _remember_state_loss(self, reason: str, message=None) -> None:
        if not any(value >= 0 for value in self.states):
            return
        self.last_state_loss = {
            "time": time.time(), "reason": reason, "previous_states": list(self.states),
            "previous_firmware": copy.deepcopy(self.firmware),
            "message": {k: message.get(k) for k in ("tipo", "motivo", "boot", "epoch", "uptime_ms", "estados", "diagnostico_entrada", "revisao_firmware")}
                       if message else None,
        }

    def _changed(self) -> None:
        self.revision += 1
        self.updated_at = time.time()

    def _invalidate(self, message: str) -> None:
        self._remember_state_loss(message)
        self.serial.update(connected=False, message=message)
        self.states = [-1, -1]
        self.result = None
        self.mode_confirmed = None
        self.mode_pending = None
        self.mode_pending_since = None
        self.capture.update(active=False, phase="interrompida", id="")
        self.commands.clear()
        self._changed()

    def expire(self) -> bool:
        """Invalida dados sem heartbeat; nunca mantém um estado conectado fictício."""
        with self.lock:
            changed = False
            now = self.clock()
            expired = [item for item in self.commands if item["expires"] <= now]
            if expired:
                self.commands = deque(item for item in self.commands if item["expires"] > now)
                if any(item["command"]["op"] == "mode" for item in expired):
                    self.mode_pending = None
                self.last_error = "Comando expirou sem confirmação. Tente novamente."
                self._changed()
                changed = True
            if (self.mode_pending is not None and self.mode_pending_since is not None
                    and now - self.mode_pending_since >= COMMAND_TTL):
                self.mode_pending = None
                self.mode_pending_since = None
                self.last_error = "A placa não confirmou a mudança de modo. Tente novamente."
                self._changed()
                changed = True
            if (self.serial["connected"] and self.client_seen is not None
                    and now - self.client_seen > CLIENT_TTL):
                self._invalidate("Programa de aquisição sem resposta.")
                changed = True
            return changed

    def snapshot(self) -> dict[str, Any]:
        self.expire()
        with self.lock:
            return copy.deepcopy({
                "api_version": 1, "server_id": self.server_id, "revision": self.revision,
                "simulation": self.demo, "updated_at": self.updated_at,
                "mode_confirmed": self.mode_confirmed, "mode_pending": self.mode_pending,
                "mode_source": self.mode_source, "scenario": self.scenario,
                "capture": self.capture, "states": self.states, "result": self.result,
                "serial": self.serial, "mqtt": self.mqtt, "last_error": self.last_error,
                "firmware": self.firmware, "pending_commands": len(self.commands),
                "last_state_loss": self.last_state_loss,
            })

    def set_mqtt(self, **fields: Any) -> None:
        with self.lock:
            self.mqtt.update(fields)
            self._changed()

    def validate_serial(self, message: Any) -> dict[str, Any]:
        if not isinstance(message, dict):
            raise ValueError("Mensagem serial deve ser um objeto.")
        _json_finite(message)
        for field in ("protocolo", "modelo", "sha256"):
            if message.get(field) != self.contract.get(field):
                raise ValueError("Firmware incompatível com o contrato deste repositório.")
        if message.get("tipo") not in KINDS:
            raise ValueError("Tipo de mensagem serial desconhecido.")
        if message.get("modo") not in ("teste", "aquisicao"):
            raise ValueError("Modo da placa inválido.")
        states = message.get("estados")
        if (not isinstance(states, list) or len(states) != 2
                or any(type(item) is not int or item not in (-1, 0, 1) for item in states)):
            raise ValueError("Estados da placa inválidos.")
        if not _integer(message.get("boot"), 0, 0xffffffff):
            raise ValueError("Identificação de inicialização inválida.")
        if not _integer(message.get("cenario_selecionado"), 0, 12):
            raise ValueError("Cenário selecionado inválido.")
        if type(message.get("captura_ativa")) is not bool:
            raise ValueError("Indicador de captura inválido.")
        capture_id = message.get("captura_id")
        if not _text(capture_id, 40) or (capture_id and not re.fullmatch(r"[0-9a-fA-F]{1,8}:[0-9]{1,10}", capture_id)):
            raise ValueError("Identificador de captura inválido.")
        if message["tipo"] == "entrada":
            values = message.get("valores")
            if (not _integer(message.get("janela"), 0, 24)
                    or not isinstance(values, list) or len(values) != 106
                    or any(type(item) not in (int, float) or not math.isfinite(item) for item in values)):
                raise ValueError("Entrada incompleta: são necessárias 106 features.")
        if message["tipo"] == "resultado":
            probabilities = message.get("probabilidades")
            cid = message.get("classe_id")
            if (not _integer(cid, 0, 4) or message.get("classe") != LABELS[cid]
                    or not isinstance(probabilities, list) or len(probabilities) != 5
                    or any(type(item) not in (int, float) or not 0 <= item <= 1 for item in probabilities)
                    or abs(sum(probabilities) - 1) > .0001):
                raise ValueError("Resultado neural inválido.")
            confidence = message.get("confianca")
            margin = message.get("margem")
            if (type(confidence) not in (int, float) or type(margin) not in (int, float)
                    or abs(probabilities[cid] - confidence) > .0001
                    or abs(max(probabilities) - confidence) > .0001
                    or abs(margin - (sorted(probabilities)[-1] - sorted(probabilities)[-2])) > .0001):
                raise ValueError("Confiança ou margem neural inválida.")
        return message

    def accept_event(self, event: Any) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise ValueError("Evento deve ser um objeto JSON.")
        _json_finite(event)
        kind = event.get("type")
        with self.lock:
            if kind == "connection":
                if (type(event.get("connected")) is not bool
                        or not _text(event.get("port", ""), 100)
                        or not _text(event.get("message", ""))):
                    raise ValueError("Evento de conexão inválido.")
                self.client_seen = self.clock()
                was_connected = self.serial["connected"]
                self.serial.update(connected=event["connected"], port=event.get("port", ""),
                                   message=event.get("message", ""))
                if not event["connected"]:
                    self._invalidate(event.get("message") or "USB desconectado.")
                elif not was_connected:
                    self.states = [-1, -1]
                    self.result = None
                    self.mode_confirmed = None
                    self.last_error = ""
            elif kind == "serial":
                message = self.validate_serial(event.get("message"))
                if not self.serial["connected"]:
                    raise ValueError("Confirme a conexão USB antes de enviar dados da placa.")
                self.client_seen = self.clock()
                incoming_states = [-1, -1] if message["tipo"] == "falha" else message["estados"]
                if any(old >= 0 and new < 0 for old, new in zip(self.states, incoming_states)):
                    if self.boot is not None and self.boot != message["boot"]:
                        reason = "O ESP32 reiniciou."
                    elif message["tipo"] == "falha":
                        reason = "A placa informou falha: " + str(message.get("motivo", "nao_especificada"))[:200]
                        detail = message.get("diagnostico_entrada")
                        if isinstance(detail, dict):
                            reason += " (medido: %s; limite/esperado: %s; janela: %s)" % (
                                detail.get("medido"), detail.get("limite_ou_esperado"),
                                detail.get("janela_base_zero"))
                    elif self.firmware and self.firmware.get("epoch") != message.get("epoch"):
                        reason = "A placa reiniciou o contexto de aquisição (epoch mudou)."
                    elif message.get("motivo") == "reiniciado":
                        reason = "Os estados foram reiniciados por comando."
                    else:
                        reason = "A placa enviou estado desconhecido; consulte diagnostico.log."
                    self._remember_state_loss(reason, message)
                if self.boot is not None and self.boot != message["boot"]:
                    self.result = None
                    self.states = [-1, -1]
                    self.capture = {"active": False, "phase": "reiniciada", "id": ""}
                    self.commands.clear()
                    self.mode_pending = None
                    self.mode_pending_since = None
                self.boot = message["boot"]
                self.mode_confirmed = message["modo"]
                self.mode_source = message.get("origem_modo", "")
                if self.mode_confirmed == self.mode_pending:
                    self.mode_pending = None
                    self.mode_pending_since = None
                self.scenario = message["cenario_selecionado"]
                self.states = list(message["estados"])
                if message["captura_id"] and message["captura_id"] != self.capture["id"]:
                    self.capture = {"active": False, "phase": "aguardando", "id": message["captura_id"]}
                self.capture["active"] = message["captura_ativa"]
                self.firmware = {key: message.get(key) for key in (
                    "protocolo", "modelo", "sha256", "boot", "epoch", "modo",
                    "origem_modo", "cenario_selecionado", "captura_ativa", "captura_id",
                    "revisao_firmware",
                )}
                subtype = message["tipo"]
                phases = {"captura_armada": "preparando", "captura_inicio": "capturando",
                          "acao": "agora", "resultado": "recebendo", "entrada": "recebendo",
                          "entrada_fim": "recebida", "captura_cancelada": "cancelada"}
                if subtype in phases and (subtype != "resultado" or message.get("entrada_a_seguir") is True):
                    self.capture["phase"] = phases[subtype]
                elif message["modo"] == "teste" and not message["captura_ativa"] and subtype in ("resultado", "modo", "pronto"):
                    self.capture["phase"] = "monitorando"
                if subtype == "resultado":
                    self.result = copy.deepcopy(message)
                elif subtype in ("falha", "captura_cancelada"):
                    self.last_error = str(message.get("motivo", "Captura interrompida."))[:300]
                    if subtype == "falha":
                        self.result = None
                        self.states = [-1, -1]
                elif subtype == "aviso":
                    reasons = {"ocupado": "Aguarde a captura terminar antes de mudar o modo.",
                               "modo_incompativel": "Mude para aquisição antes de capturar.",
                               "cenario_nao_selecionado": "Selecione uma condição antes de capturar.",
                               "comando_invalido": "A placa recusou o comando.",
                               "fila_comandos_cheia": "A placa está ocupada. Tente novamente."}
                    reason = message.get("motivo", "")
                    if reason in reasons:
                        self.last_error = reasons[reason]
                        self.mode_pending = None
                        self.mode_pending_since = None
                        self.commands = deque(item for item in self.commands
                                              if item["command"]["op"] != "mode")
                elif subtype not in ("heartbeat", "entrada"):
                    self.last_error = ""
            elif kind == "capture_saved":
                if (not _text(event.get("capture_id"), 40)
                        or not _text(event.get("path"), 1024)
                        or not _integer(event.get("class_id"), 0, 12)):
                    raise ValueError("Confirmação de arquivo inválida.")
                if event["capture_id"] != self.capture["id"]:
                    raise ValueError("A confirmação pertence a outra captura.")
                self.capture.update(active=False, phase="salva", saved_path=event["path"],
                                    class_id=event["class_id"])
            elif kind == "command_result":
                cid = event.get("command_id")
                if (not _integer(cid, 1, 2**53 - 1) or type(event.get("success")) is not bool
                        or not _text(event.get("error", ""))):
                    raise ValueError("Confirmação de comando inválida.")
                matching = [item for item in self.commands if item["id"] == cid]
                self.commands = deque(item for item in self.commands if item["id"] != cid)
                if not event["success"]:
                    self.last_error = event.get("error") or "O comando não foi enviado à placa."
                    if any(item["command"]["op"] == "mode" for item in matching):
                        self.mode_pending = None
                        self.mode_pending_since = None
                # ACK significa escrita na USB; apenas mensagem da placa confirma o modo.
            else:
                raise ValueError("Tipo de evento desconhecido.")
            self._changed()
            return {"ok": True, "revision": self.revision}

    def queue_command(self, command: Any) -> dict[str, Any]:
        if not isinstance(command, dict):
            raise ValueError("Comando deve ser um objeto JSON.")
        op = command.get("op")
        allowed = {"mode": {"op", "value"}, "scenario": {"op", "value"},
                   "start": {"op"}, "cancel": {"op"}, "states": {"op", "values"},
                   "reset": {"op"}}
        if op not in allowed or set(command) != allowed[op]:
            raise ValueError("Comando não permitido.")
        if op == "mode" and command["value"] not in ("teste", "aquisicao"):
            raise ValueError("Modo inválido.")
        if op == "scenario" and not _integer(command["value"], 1, 12):
            raise ValueError("Cenário inválido.")
        if op == "states":
            values = command["values"]
            if (not isinstance(values, list) or len(values) != 2
                    or any(type(item) is not int or item not in (0, 1) for item in values)):
                raise ValueError("Informe parado ou funcionando para os dois aparelhos.")
        self.expire()
        with self.lock:
            if not self.serial["connected"]:
                raise ValueError("Conecte a placa pelo programa de aquisição.")
            if len(self.commands) >= COMMAND_LIMIT:
                raise OverflowError("Fila de comandos cheia. Aguarde a comunicação USB.")
            if op == "start" and (self.mode_confirmed != "aquisicao" or not self.scenario):
                raise ValueError("Confirme o modo aquisição e selecione uma condição primeiro.")
            if self.capture["active"] and op in ("start", "mode", "scenario", "states", "reset"):
                raise ValueError("Aguarde o término da captura ou cancele-a.")
            item = {"id": self.next_command, "command": dict(command),
                    "expires": self.clock() + COMMAND_TTL}
            self.next_command += 1
            self.commands.append(item)
            if op == "mode":
                self.mode_pending = command["value"]
                self.mode_pending_since = self.clock()
            self.last_error = ""
            self._changed()
            return {"ok": True, "id": item["id"], "confirmed": False}

    def pending_commands(self) -> dict[str, Any]:
        self.expire()
        with self.lock:
            return {"commands": [
                {"id": item["id"], "command": dict(item["command"])}
                for item in self.commands
            ]}


class EmbeddedBroker:
    """aMQTT em uma thread asyncio, com escuta local ou na LAN."""

    def __init__(self, port: int, host: str = "127.0.0.1"):
        self.port = port
        self.host = listen_host(host)
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.error: Exception | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop_async: asyncio.Event | None = None

    def start(self) -> None:
        from amqtt.broker import Broker

        async def run() -> None:
            self.loop = asyncio.get_running_loop()
            self.stop_async = asyncio.Event()
            broker = Broker({
                "listeners": {"default": {"type": "tcp", "bind": f"{self.host}:{self.port}",
                                           "max_connections": 20}},
                "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin":
                            {"allow_anonymous": True}},
            })
            started = False
            try:
                await broker.start()
                started = True
                self.ready.set()
                await self.stop_async.wait()
            finally:
                if started:
                    await broker.shutdown()

        def worker() -> None:
            try:
                asyncio.run(run())
            except Exception as exc:
                self.error = exc
                self.ready.set()

        self.thread = threading.Thread(target=worker, name="broker-mqtt", daemon=True)
        self.thread.start()
        if not self.ready.wait(6):
            self.stop()
            raise RuntimeError("O broker MQTT não iniciou no prazo esperado.")
        if self.error:
            raise RuntimeError(f"Não foi possível abrir MQTT na porta {self.port}. "
                               "Se já existe outro broker, use --mqtt-externo; confira também as dependências.") from self.error

    def stop(self) -> None:
        if self.loop and self.stop_async and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.stop_async.set)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)


def probe_mqtt(host: str, port: int, timeout: float = 1.5) -> bool:
    """Detecta um broker MQTT 3.1.1; nao confunde uma porta TCP com MQTT."""
    try:
        connection = socket.create_connection((host, port), timeout)
    except (ConnectionRefusedError, socket.timeout):
        return False
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10061:
            return False
        raise RuntimeError(f"Nao foi possivel verificar MQTT em {host}:{port}: {exc}") from exc
    with connection:
        connection.settimeout(timeout)
        client_id = ("tcc_probe_" + uuid.uuid4().hex[:12]).encode("ascii")
        body = b"\x00\x04MQTT\x04\x02\x00\x05" + len(client_id).to_bytes(2, "big") + client_id
        connection.sendall(b"\x10" + bytes([len(body)]) + body)
        response = b""
        try:
            while len(response) < 4:
                part = connection.recv(4 - len(response))
                if not part:
                    break
                response += part
        except OSError as exc:
            raise RuntimeError(f"A porta {host}:{port} esta ocupada, mas nao respondeu como MQTT.") from exc
        if len(response) != 4 or response[:2] != b"\x20\x02":
            raise RuntimeError(f"A porta {host}:{port} esta ocupada por um servico que nao confirmou MQTT.")
        if response[3] != 0:
            raise RuntimeError(f"O broker MQTT recusou a conexao (CONNACK {response[3]}). Confira autenticacao/permissoes.")
        connection.sendall(b"\xe0\x00")
        return True


class MQTTService:
    """Publicação não bloqueante; reconexão republica o estado atual confirmado."""

    def __init__(self, state: DashboardState, *, port: int = 1883,
                 topic: str = "tcc/eventos5/esp32_01", external: bool = False,
                 bind_host: str = "127.0.0.1", connect_host: str = "127.0.0.1",
                 auto_reuse: bool = True):
        if not _integer(port, 1, 65535):
            raise ValueError("Porta MQTT inválida.")
        if not re.fullmatch(r"[A-Za-z0-9_/-]{1,120}", topic) or topic.startswith("/"):
            raise ValueError("Tópico MQTT inválido.")
        self.state, self.port = state, port
        self.topic = topic.rstrip("/") + ("/demo" if state.demo else "")
        self.external = external
        self.bind_host = listen_host(bind_host)
        self.connect_host = listen_host(connect_host)
        if self.connect_host == "0.0.0.0":
            raise ValueError("O destino MQTT nao pode ser 0.0.0.0; use o IP do broker.")
        self.auto_reuse = auto_reuse
        self.reused = False
        self.client = None
        self.broker: EmbeddedBroker | None = None
        self.stopping = False
        self.state_fingerprint: str | None = None
        state.set_mqtt(port=port, topic=self.topic, host=self.connect_host)

    def start(self) -> bool:
        if self.client is not None:
            return True
        self.stopping = False
        self.state_fingerprint = None
        try:
            import paho.mqtt.client as mqtt
            self.reused = False
            if not self.external:
                self.reused = self.auto_reuse and probe_mqtt(self.connect_host, self.port)
                if not self.reused:
                    self.broker = EmbeddedBroker(self.port, self.bind_host)
                    self.broker.start()
            self.state.set_mqtt(enabled=True, broker_running=self.broker is not None,
                                broker_source="existente" if self.external or self.reused else "interno",
                                message=f"Conectando ao MQTT em {self.connect_host}:{self.port}...")
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id="tcc_servidor_" + uuid.uuid4().hex[:12])
            client.max_queued_messages_set(64)
            client.reconnect_delay_set(1, 8)
            client.will_set(self.topic + "/status", json.dumps({"online": False,
                            "simulation": self.state.demo}), qos=1, retain=True)

            def connected(c, userdata, flags, reason, properties):
                good = not reason.is_failure
                self.state.set_mqtt(connected=good, message=(f"MQTT conectado em {self.connect_host}:{self.port} — "
                                            + ("broker existente" if self.external or self.reused else "broker interno"))
                                            if good else f"Conexao MQTT recusada: {reason}")
                if good:
                    c.subscribe(self.topic + "/telemetria", qos=0)
                    c.publish(self.topic + "/status", json.dumps({"online": True,
                              "simulation": self.state.demo}), qos=1, retain=True)
                    self.publish_snapshot()

            def disconnected(c, userdata, flags, reason, properties):
                self.state.set_mqtt(connected=False,
                                    message="MQTT desligado" if self.stopping else "MQTT reconectando...")

            def received(c, userdata, message):
                try:
                    payload = json.loads(message.payload)
                    if payload.get("protocolo") != self.state.contract["protocolo"]:
                        return
                    with self.state.lock:
                        self.state.mqtt["received"] += 1
                        self.state._changed()
                except (ValueError, UnicodeError, AttributeError):
                    pass

            client.on_connect = connected
            client.on_disconnect = disconnected
            client.on_message = received
            self.client = client
            client.connect_async(self.connect_host,
                                 self.port, keepalive=15)
            client.loop_start()
            return True
        except ImportError:
            self.state.set_mqtt(enabled=False, connected=False, broker_running=False,
                                message="Instale requirements.txt para habilitar o MQTT.")
        except Exception as exc:
            self.state.set_mqtt(enabled=False, connected=False, broker_running=False,
                                message=str(exc)[:300])
            LOG.warning("MQTT indisponível: %s", exc)
        if self.broker:
            self.broker.stop()
            self.broker = None
        if self.client:
            self.client.disconnect()
            self.client.loop_stop()
            self.client = None
        return False

    def _publish(self, suffix: str, data: dict[str, Any], *, qos=0, retain=False) -> bool:
        client = self.client
        if not client or not client.is_connected():
            return False
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        response = client.publish(self.topic + suffix, payload, qos=qos, retain=retain)
        with self.state.lock:
            field = "published" if response.rc == 0 else "dropped"
            self.state.mqtt[field] += 1
        return response.rc == 0

    def _snapshot_payload(self) -> dict[str, Any]:
        snapshot = self.state.snapshot()
        return {
            "protocolo": self.state.contract["protocolo"],
            "modelo": self.state.contract["modelo"], "sha256": self.state.contract["sha256"],
            "modo": snapshot["mode_confirmed"], "estados": snapshot["states"],
            "usb_conectado": snapshot["serial"]["connected"],
            "captura": snapshot["capture"], "simulation": self.state.demo,
            "transporte": "usb_notebook",
        }

    def publish_snapshot(self) -> None:
        payload = self._snapshot_payload()
        fingerprint = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        payload["recebido_em"] = time.time()
        if self._publish("/estado", payload, qos=1, retain=True):
            self.state_fingerprint = fingerprint

    def sync_snapshot(self) -> None:
        # Uma leitura HTTP também pode detectar timeout antes do watchdog.
        # Compare o estado, em vez de depender de quem chamou expire() primeiro.
        fingerprint = json.dumps(self._snapshot_payload(), sort_keys=True, ensure_ascii=False)
        if fingerprint != self.state_fingerprint:
            self.publish_snapshot()

    def event(self, event: dict[str, Any]) -> None:
        if event["type"] == "serial":
            message = event["message"]
            # As 106 features pertencem ao CSV; não são despejadas no broker.
            if message["tipo"] == "entrada":
                return
            if message["tipo"] == "resultado":
                payload = dict(message, simulation=self.state.demo,
                               recebido_em=time.time(), transporte="usb_notebook")
                self._publish("/telemetria", payload)
                if message.get("aceito") and message.get("alterados"):
                    payload["evento_id"] = ":".join(str(message.get(key, ""))
                                                     for key in ("boot", "epoch", "seq"))
                    self._publish("/eventos", payload, qos=1)
            elif message["tipo"] == "heartbeat":
                return
        self.publish_snapshot()

    def stop(self) -> None:
        self.stopping = True
        client, self.client = self.client, None
        if client:
            try:
                if client.is_connected():
                    client.publish(self.topic + "/status", json.dumps({"online": False,
                                   "simulation": self.state.demo}), qos=1,
                                   retain=True).wait_for_publish(timeout=1)
                client.disconnect()
                client.loop_stop()
            except (OSError, RuntimeError):
                pass
        if self.broker:
            self.broker.stop()
            self.broker = None
        self.state.set_mqtt(enabled=False, connected=False, broker_running=False,
                            message="MQTT desligado")


HTML = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TCC · Monitor de equipamentos</title><style>
:root{font-family:Segoe UI,Arial,sans-serif;color:#122a32;background:#edf3f3;color-scheme:light}
*{box-sizing:border-box}body{margin:0}main{max-width:1120px;margin:auto;padding:36px 26px}
header{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:28px}
h1{font-size:clamp(26px,4vw,38px);letter-spacing:-1.1px;margin:5px 0}p{margin:7px 0;color:#546971;line-height:1.5}
.eyebrow{text-transform:uppercase;letter-spacing:2px;font-size:12px;color:#526c70;font-weight:700}
.status{display:flex;gap:8px;flex-wrap:wrap}.chip{padding:9px 13px;border-radius:22px;background:#dce5e7;font-size:13px;font-weight:600}
.chip.on{background:#daf0e3;color:#1c6543}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.card{background:white;border:1px solid #dce6e7;border-radius:20px;padding:25px;box-shadow:0 5px 20px #173b4005}
.full{grid-column:1/-1}.label{font-size:12px;text-transform:uppercase;letter-spacing:1.6px;color:#577079;font-weight:700}
.state{font-size:clamp(28px,4vw,43px);font-weight:750;letter-spacing:-1px;margin-top:14px}
.state.on{color:#147a58}.state.off{color:#596d76}.network{font-size:clamp(25px,3vw,36px);font-weight:700;margin-top:16px}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.spread{justify-content:space-between}.mt{margin-top:18px}
button,select{font:inherit;border-radius:10px;border:1px solid #cbdadb;padding:12px 17px;background:white;color:#173e45}
button{cursor:pointer;font-weight:650;transition:background .15s}button:hover{background:#e5f3f0}button:disabled{cursor:default;opacity:.45}
button.primary,button.selected{background:#126a5b;color:white;border-color:#126a5b}button.danger{color:#9d3b37}
button.large{font-size:19px;padding:15px 23px}select{max-width:100%;width:100%;margin-top:12px}
small,.small{font-size:13px;color:#5c747a;line-height:1.5}.alert{padding:14px 18px;border-radius:12px;background:#fff0da;color:#795000;margin-top:18px;display:none}
.demo{padding:13px;text-align:center;background:#f5d968;color:#3b3508;font-weight:700;display:none}.muted{color:#6d838a}
.manual{display:flex;gap:12px;align-items:end;margin-top:18px}.manual label{flex:1;font-size:13px}.manual select{margin-top:6px}
footer{font-size:13px;color:#5c747a;text-align:center;margin-top:24px;line-height:1.8}#phase{font-size:22px;font-weight:700;color:#126a5b}
@media(max-width:700px){main{padding:24px 16px}.grid{grid-template-columns:1fr}header{align-items:start;flex-direction:column}.card{padding:21px}.manual{flex-wrap:wrap}.manual label{min-width:120px}}
</style></head><body><div id="demo" class="demo">DEMONSTRAÇÃO · nenhuma medição física</div>
<main><header><div><div class="eyebrow">Projeto TCC · Monitor de equipamentos</div><h1>Acompanhe o sistema</h1><p>Estados e eventos confirmados pela placa.</p></div><div class="status"><span id="usb" class="chip">USB desconectado</span><span id="mqtt" class="chip">MQTT desligado</span></div></header>
<div class="grid">
<section class="card full"><div class="row spread"><div><div class="label">Conexão MQTT</div><p id="mqttinfo">MQTT desligado</p></div><button id="mqttbutton" class="primary" onclick="toggleMqtt()">Conectar MQTT</button></div><p id="share" class="small"></p></section>
<section class="card full"><div class="row spread"><div><div class="label">Modo de operação</div><p id="modeinfo">Aguardando a placa</p></div><div class="row"><button id="testmode" onclick="send({op:'mode',value:'teste'})">Modo teste</button><button id="acqmode" onclick="send({op:'mode',value:'aquisicao'})">Aquisição</button></div></div><small>Botão da placa: 1 toque captura · 2 toques modo teste · 3 toques aquisição.</small></section>
<section class="card"><div class="label">Liquidificador</div><div id="blender" class="state off">DESCONHECIDO</div></section>
<section class="card"><div class="label">Micro-ondas</div><div id="microwave" class="state off">DESCONHECIDO</div></section>
<section class="card full"><div class="label">Resposta da rede</div><div id="network" class="network muted">Aguardando resultado</div><p id="confidence">—</p><p id="stateloss" class="small"></p></section>
<section class="card full"><div class="row spread"><div><div class="label">Captura de 5 segundos</div><p id="phase">Selecione uma condição</p></div><div class="row"><button id="start" class="primary large" onclick="send({op:'start'})">Iniciar captura</button><button id="cancel" class="danger" onclick="send({op:'cancel'})">Cancelar</button></div></div>
<select id="scenario" aria-label="Condição da captura" onchange="send({op:'scenario',value:Number(this.value)})"><option value="0">Selecione uma condição</option></select><p class="small" id="saved">Os arquivos são salvos pelo programa de aquisição.</p></section>
<section class="card full"><div class="label">Estados iniciais</div><p class="small">Informe o estado real antes do teste contínuo.</p><div class="manual"><label>Liquidificador<select id="manualbl"><option value="0">Parado</option><option value="1">Funcionando</option></select></label><label>Micro-ondas<select id="manualmw"><option value="0">Parado</option><option value="1">Funcionando</option></select></label><button id="states" onclick="send({op:'states',values:[Number(byId('manualbl').value),Number(byId('manualmw').value)]})">Confirmar estados</button></div></section>
</div><div id="error" class="alert" role="status"></div><footer>Abra aquisicao.py para conectar a USB e salvar as aquisições.<br>O modo só muda na tela após a confirmação da placa.</footer></main>
<script>
const byId=id=>document.getElementById(id);let busy=false;let localError='';let configLoaded=false;let mqttEnabled=false;
async function toggleMqtt(){byId('mqttbutton').disabled=true;try{const r=await fetch('/api/mqtt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:mqttEnabled?'disconnect':'connect'})});const d=await r.json();if(!r.ok)throw Error(d.error||'Falha na conexão MQTT');localError='';}catch(e){localError=e.message;showError(localError);}finally{await refresh();}}
const classes=['SEM EVENTO ALVO','LIQUIDIFICADOR LIGANDO','LIQUIDIFICADOR DESLIGANDO','MICRO-ONDAS LIGANDO','MICRO-ONDAS DESLIGANDO'];
async function send(command){try{const r=await fetch('/api/comando',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(command)});const d=await r.json();if(!r.ok)throw Error(d.error||'Comando não enviado');localError='';await refresh();}catch(e){localError=e.message;showError(localError);}}
function showError(text){byId('error').textContent=text;byId('error').style.display=text?'block':'none';}
function render(s){
byId('demo').style.display=s.simulation?'block':'none';const online=s.serial.connected;
byId('usb').textContent=online?'USB conectado':'USB desconectado';byId('usb').className='chip'+(online?' on':'');
byId('mqtt').textContent=s.mqtt.connected?'MQTT conectado':'MQTT desconectado';byId('mqtt').className='chip'+(s.mqtt.connected?' on':'');byId('mqtt').title=s.mqtt.message;
mqttEnabled=s.mqtt.enabled;byId('mqttbutton').textContent=s.mqtt.changing?'Aguarde…':mqttEnabled?'Desconectar MQTT':'Conectar MQTT';byId('mqttbutton').disabled=!!s.mqtt.changing;byId('mqttinfo').textContent=s.mqtt.message;
byId('testmode').className=s.mode_confirmed==='teste'?'selected':'';byId('acqmode').className=s.mode_confirmed==='aquisicao'?'selected':'';
byId('modeinfo').textContent=s.mode_pending?'Aguardando confirmação da placa…':s.mode_confirmed==='teste'?'Teste contínuo':s.mode_confirmed==='aquisicao'?'Aquisição de dados':'Aguardando a placa';
['blender','microwave'].forEach((id,i)=>{const v=s.states[i];byId(id).textContent=v===1?'FUNCIONANDO':v===0?'PARADO':'DESCONHECIDO';byId(id).className='state '+(v===1?'on':'off');});
const r=s.result;byId('network').textContent=r?classes[r.classe_id]:'Aguardando resultado';byId('network').className='network'+(r?'':' muted');
byId('confidence').textContent=r?'Confiança: '+(100*r.confianca).toFixed(1)+'% · '+(r.classe_id===0?'Sem mudança de estado':r.aceito?'Evento aceito':'Estado preservado'):'—';
const loss=s.last_state_loss;byId('stateloss').textContent=loss?'Última perda de estado ('+new Date(loss.time*1000).toLocaleTimeString()+'): '+loss.reason:'';
const phases={aguardando:'Selecione uma condição',monitorando:'Monitoramento contínuo · sem gravação de captura',preparando:'Prepare os equipamentos',capturando:'CAPTURANDO',agora:'AGORA · faça a transição',recebendo:'Recebendo dados…',recebida:'Recebida',salva:'CAPTURA SALVA',cancelada:'Captura cancelada',interrompida:'Captura interrompida',reiniciada:'Placa reiniciada'};
byId('phase').textContent=phases[s.capture.phase]||s.capture.phase;byId('saved').textContent=s.capture.saved_path||'Os arquivos são salvos pelo programa de aquisição.';
if(document.activeElement!==byId('scenario'))byId('scenario').value=String(s.scenario||0);
byId('start').disabled=!online||s.mode_confirmed!=='aquisicao'||!s.scenario||s.capture.active||s.pending_commands>0;
byId('cancel').disabled=!online||!s.capture.active;byId('scenario').disabled=!online||s.capture.active;
['testmode','acqmode'].forEach(id=>byId(id).disabled=!online||!!s.mode_pending||s.capture.active);
byId('states').disabled=!online||s.capture.active;showError(localError||s.last_error||(!online?s.serial.message:''));
}
async function refresh(){if(busy)return;busy=true;try{if(!configLoaded){const c=await(await fetch('/api/config')).json();for(const x of c.scenarios){const o=document.createElement('option');o.value=x.id;o.textContent=x.id+' · '+(x.title||x.titulo||x.slug);byId('scenario').appendChild(o);}byId('share').textContent=c.share_urls.length?'No PC ou celular da mesma rede, abra: '+c.share_urls.join(' ou '):'Acesso pela rede local desativado.';configLoaded=true;}const r=await fetch('/api/estado');if(!r.ok)throw Error('Servidor indisponível');render(await r.json());}catch(e){showError('Servidor sem resposta. Mantenha servidor.py aberto.');byId('usb').className='chip';byId('usb').textContent='Sem comunicação';['start','cancel','testmode','acqmode','states','scenario','mqttbutton'].forEach(id=>byId(id).disabled=true);}finally{busy=false;}}
refresh();setInterval(refresh,500);
</script></body></html>"""


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, state: DashboardState, port: int = 8765,
                 mqtt: MQTTService | None = None, host: str = "127.0.0.1"):
        self.state = state
        self.mqtt = mqtt
        self.mqtt_lock = threading.Lock()
        self.mqtt_thread: threading.Thread | None = None
        host = listen_host(host)
        self.allowed_hosts = {"127.0.0.1", "localhost"}
        addresses = lan_addresses() if host != "127.0.0.1" else []
        if host not in ("0.0.0.0", "127.0.0.1"):
            addresses = [host]
        self.allowed_hosts.update(addresses)
        super().__init__((host, port), RequestHandler)
        self.share_urls = [f"http://{ip}:{self.server_port}" for ip in addresses]

    def control_mqtt(self, command: Any) -> dict[str, Any]:
        if not isinstance(command, dict) or set(command) != {"action"} or command["action"] not in ("connect", "disconnect"):
            raise ValueError("Selecione conectar ou desconectar MQTT.")
        if self.mqtt is None:
            raise ValueError("O serviço MQTT não foi configurado.")
        with self.mqtt_lock:
            if self.mqtt_thread and self.mqtt_thread.is_alive():
                raise ValueError("Aguarde a operação MQTT terminar.")
            self.state.set_mqtt(changing=True)

            def operate() -> None:
                try:
                    if command["action"] == "connect":
                        self.mqtt.start()
                    else:
                        self.mqtt.stop()
                except Exception as exc:
                    LOG.exception("Falha no controle MQTT")
                    self.state.set_mqtt(message=str(exc)[:300])
                finally:
                    self.state.set_mqtt(changing=False)

            self.mqtt_thread = threading.Thread(target=operate, name="conexao-mqtt", daemon=True)
            self.mqtt_thread.start()
        return {"ok": True, "action": command["action"]}

    def stop_mqtt(self) -> None:
        if self.mqtt_thread:
            self.mqtt_thread.join(timeout=10)
        if self.mqtt:
            self.mqtt.stop()


class RequestHandler(BaseHTTPRequestHandler):
    server: LocalHTTPServer
    server_version = "TCCLocal/1"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(4)

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.debug("HTTP %s", fmt % args)

    def _reply(self, status: int, data: Any, *, html: bool = False) -> None:
        body = data.encode("utf-8") if html else json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _local_request(self) -> bool:
        port = self.server.server_port
        allowed_hosts = {f"{host}:{port}" for host in self.server.allowed_hosts}
        origins = {f"http://{self.headers.get('Host')}"}
        valid = (len(self.headers.get_all("Host", [])) == 1
                 and self.headers.get("Host") in allowed_hosts
                 and (not self.headers.get("Origin") or self.headers.get("Origin") in origins)
                 and self.headers.get("Sec-Fetch-Site") != "cross-site")
        if not valid:
            self._reply(403, {"error": "Abra o painel pelo endereço deste computador na rede local."})
        return valid

    def do_GET(self) -> None:
        if not self._local_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/comandos" and not ipaddress.ip_address(self.client_address[0]).is_loopback:
            self._reply(403, {"error": "Este caminho é reservado ao coletor no notebook."})
            return
        if path == "/":
            self._reply(200, HTML, html=True)
        elif path == "/api/estado":
            self._reply(200, self.server.state.snapshot())
        elif path == "/api/comandos":
            self._reply(200, self.server.state.pending_commands())
        elif path == "/api/config":
            self._reply(200, {"api_version": 1, "contract": self.server.state.contract,
                              "scenarios": self.server.state.contract["cenarios"],
                              "simulation": self.server.state.demo,
                              "share_urls": self.server.share_urls,
                              "command_ttl_s": COMMAND_TTL, "client_timeout_s": CLIENT_TTL})
        else:
            self._reply(404, {"error": "Caminho não encontrado."})

    def do_POST(self) -> None:
        if not self._local_request():
            return
        path = urlsplit(self.path).path
        if path not in ("/api/evento", "/api/comando", "/api/mqtt"):
            self._reply(404, {"error": "Caminho não encontrado."})
            return
        if path == "/api/evento" and not ipaddress.ip_address(self.client_address[0]).is_loopback:
            self._reply(403, {"error": "Os dados da placa são recebidos pelo coletor no notebook."})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (self.headers.get("Transfer-Encoding") or len(lengths) != 1
                or not lengths[0].isdigit()):
            self._reply(411, {"error": "Informe o tamanho do corpo JSON."})
            return
        length = int(lengths[0])
        if length > BODY_LIMIT:
            self._reply(413, {"error": "Mensagem excessiva."})
            return
        if self.headers.get_content_type() != "application/json":
            self._reply(415, {"error": "Envie application/json."})
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("Corpo JSON incompleto.")
            data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
            _json_finite(data)
            if path == "/api/evento":
                response = self.server.state.accept_event(data)
                if self.server.mqtt:
                    self.server.mqtt.event(data)
            elif path == "/api/mqtt":
                response = self.server.control_mqtt(data)
            else:
                response = self.server.state.queue_command(data)
            self._reply(200, response)
        except OverflowError as exc:
            self._reply(429, {"error": str(exc)})
        except (ValueError, UnicodeError, TypeError, KeyError) as exc:
            self._reply(400, {"error": str(exc)[:300]})
        except (socket.timeout, TimeoutError):
            self._reply(408, {"error": "Recebimento da mensagem expirou."})

    def do_OPTIONS(self) -> None:
        self._reply(405, {"error": "Método não permitido."})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Painel HTTP e broker MQTT locais do TCC.")
    try:
        settings = load_settings()
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    parser.add_argument("--porta-http", type=int, default=settings["http"]["port"], help="Porta do painel (8765).")
    parser.add_argument("--porta-mqtt", type=int, default=settings["mqtt"]["port"], help="Porta do broker local (1883).")
    parser.add_argument("--host-mqtt", default=settings["mqtt"].get("connect_host", "127.0.0.1"),
                        help="IP de destino do broker; padrao 127.0.0.1.")
    parser.add_argument("--topico-mqtt", default=settings["mqtt"]["topic"])
    parser.add_argument("--mqtt-desligado", action="store_true", help="Inicia com MQTT desligado; o botão permite conectar.")
    parser.add_argument("--somente-local", action="store_true", help="Restringe painel e broker ao próprio notebook.")
    parser.add_argument("--mqtt-externo", action="store_true", default=not settings["mqtt"]["embedded"],
                        help="Usa um broker já aberto em 127.0.0.1.")
    parser.add_argument("--sem-navegador", action="store_true", help="Não abre uma aba automaticamente.")
    parser.add_argument("--demonstracao", action="store_true", help="Identifica painel e tópicos como demonstração.")
    args = parser.parse_args(argv)
    for port in (args.porta_http, args.porta_mqtt):
        if not 1 <= port <= 65535:
            parser.error("As portas devem estar entre 1 e 65535.")
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("amqtt").setLevel(logging.ERROR)
    state = DashboardState(demo=args.demonstracao)
    http_host = "127.0.0.1" if args.somente_local else settings["http"]["host"]
    mqtt_host = "127.0.0.1" if args.somente_local else settings["mqtt"]["host"]
    try:
        server = LocalHTTPServer(state, args.porta_http, host=http_host)
    except OSError:
        print(f"A porta HTTP {args.porta_http} já está em uso. Feche o outro servidor ou use --porta-http.")
        return 1
    try:
        mqtt = MQTTService(state, port=args.porta_mqtt, topic=args.topico_mqtt,
                           external=args.mqtt_externo, bind_host=mqtt_host, connect_host=args.host_mqtt,
                           auto_reuse=settings["mqtt"].get("auto_reuse", True))
    except ValueError as exc:
        server.server_close()
        parser.error(str(exc))
    server.mqtt = mqtt
    if not args.mqtt_desligado:
        server.control_mqtt({"action": "connect"})
    stop = threading.Event()

    def watchdog() -> None:
        while not stop.wait(1):
            state.expire()
            if mqtt:
                mqtt.sync_snapshot()

    watcher = threading.Thread(target=watchdog, name="estado-usb", daemon=True)
    watcher.start()
    url = f"http://127.0.0.1:{args.porta_http}"
    print(f"Painel: {url}\nAbra aquisicao.py para conectar a placa.\nCtrl+C para encerrar.")
    for address in server.share_urls:
        print(f"PC/celular na mesma rede: {address}")
    if server.share_urls:
        print(f"MQTT: destino {args.host_mqtt}:{args.porta_mqtt}. Um broker existente controla sua propria escuta na rede.")
    if not args.sem_navegador:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        server.stop_mqtt()
        watcher.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
