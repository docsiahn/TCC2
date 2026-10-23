"""Testes de API local, confirmação de modo e publicação MQTT real em loopback.

Executar na raiz: python -m unittest discover -s testes_sistema -v
Os testes MQTT usam somente portas efêmeras do próprio computador.
"""
from __future__ import annotations

import copy
import http.client
import importlib.util
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import servidor


def connected(state):
    state.accept_event({"type": "connection", "connected": True,
                        "port": "COM_TESTE", "message": "USB conectado"})


def packet(kind="heartbeat", **changes):
    message = dict(protocolo=servidor.PROTOCOL, modelo=servidor.MODEL,
                   sha256=servidor.MODEL_SHA, tipo=kind, boot=0x1234, epoch=1,
                   modo="teste", origem_modo="python", cenario_selecionado=0,
                   captura_ativa=False, captura_id="", estados=[0, 0])
    message.update(changes)
    return message


def result(**changes):
    return packet("resultado", classe_id=1, classe="liquidificador_ligando",
                  probabilidades=[.01, .96, .01, .01, .01], confianca=.96,
                  margem=.95, aceito=True, alterados=1, seq=41,
                  estados=[1, 0], **changes)


def serial(state, message):
    return state.accept_event({"type": "serial", "message": message})


class StateTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.state = servidor.DashboardState(clock=lambda: self.now)
        connected(self.state)

    def test_mode_needs_board_confirmation_after_usb_ack(self):
        serial(self.state, packet())
        queued = self.state.queue_command({"op": "mode", "value": "aquisicao"})
        self.assertEqual(self.state.snapshot()["mode_confirmed"], "teste")
        self.assertEqual(self.state.snapshot()["mode_pending"], "aquisicao")
        self.state.accept_event(dict(type="command_result", command_id=queued["id"], success=True))
        self.assertEqual(self.state.snapshot()["mode_confirmed"], "teste")
        serial(self.state, packet("modo", modo="aquisicao", origem_modo="botao"))
        self.assertEqual(self.state.snapshot()["mode_confirmed"], "aquisicao")
        self.assertEqual(self.state.snapshot()["mode_source"], "botao")
        self.assertIsNone(self.state.snapshot()["mode_pending"])

    def test_pending_mode_expires_even_after_transport_ack(self):
        serial(self.state, packet())
        queued = self.state.queue_command({"op": "mode", "value": "aquisicao"})
        self.state.accept_event(dict(type="command_result", command_id=queued["id"], success=True))
        self.now += 16
        connected(self.state)
        snapshot = self.state.snapshot()
        self.assertIsNone(snapshot["mode_pending"])
        self.assertEqual(snapshot["mode_confirmed"], "teste")
        self.assertIn("confirmou", snapshot["last_error"])

    def test_firmware_busy_rejects_pending_mode_without_confirmation(self):
        serial(self.state, packet())
        self.state.queue_command({"op": "mode", "value": "aquisicao"})
        serial(self.state, packet("aviso", motivo="ocupado"))
        snapshot = self.state.snapshot()
        self.assertEqual(snapshot["mode_confirmed"], "teste")
        self.assertIsNone(snapshot["mode_pending"])
        self.assertEqual(snapshot["pending_commands"], 0)
        self.assertIn("Aguarde", snapshot["last_error"])

    def test_commands_repeat_until_ack_and_are_bounded(self):
        first = self.state.queue_command({"op": "cancel"})
        before = self.state.pending_commands()
        self.assertEqual(before, self.state.pending_commands())
        self.state.accept_event(dict(type="command_result", command_id=first["id"], success=True))
        self.assertEqual(self.state.pending_commands(), {"commands": []})
        for _ in range(servidor.COMMAND_LIMIT):
            self.state.queue_command({"op": "cancel"})
        with self.assertRaises(OverflowError):
            self.state.queue_command({"op": "cancel"})
        self.now += 16
        connected(self.state)
        self.assertEqual(self.state.pending_commands(), {"commands": []})

    def test_command_whitelist_rejects_arbitrary_text(self):
        for invalid in ({"op": "exec", "value": "echo unsafe"},
                        {"op": "mode", "value": "continuo"},
                        {"op": "scenario", "value": True},
                        {"op": "states", "values": [-1, 0]},
                        {"op": "start", "path": "anywhere"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.state.queue_command(invalid)

    def test_start_requires_confirmed_acquisition_scenario(self):
        with self.assertRaises(ValueError):
            self.state.queue_command({"op": "start"})
        serial(self.state, packet(modo="aquisicao", cenario_selecionado=5))
        self.assertTrue(self.state.queue_command({"op": "start"})["ok"])
        serial(self.state, packet("captura_inicio", modo="aquisicao", cenario_selecionado=5,
                                  captura_ativa=True, captura_id="1234:1"))
        with self.assertRaises(ValueError):
            self.state.queue_command({"op": "scenario", "value": 6})

    def test_disconnect_and_stale_heartbeat_clear_estimated_states(self):
        serial(self.state, result())
        self.assertEqual(self.state.snapshot()["states"], [1, 0])
        self.now += servidor.CLIENT_TTL + .1
        snapshot = self.state.snapshot()
        self.assertFalse(snapshot["serial"]["connected"])
        self.assertEqual(snapshot["states"], [-1, -1])
        self.assertIsNone(snapshot["result"])
        self.assertIsNone(snapshot["mode_confirmed"])
        connected(self.state)
        self.assertEqual(self.state.snapshot()["states"], [-1, -1])
        serial(self.state, packet("estados", estados=[0, 1]))
        self.assertEqual(self.state.snapshot()["states"], [0, 1])
        self.state.accept_event(dict(type="connection", connected=False, port="COM_TESTE"))
        self.assertEqual(self.state.snapshot()["states"], [-1, -1])

    def test_reboot_clears_old_result_and_pending_commands(self):
        serial(self.state, result())
        self.state.queue_command({"op": "cancel"})
        serial(self.state, packet("pronto", boot=0x5678, estados=[-1, -1]))
        snapshot = self.state.snapshot()
        self.assertIsNone(snapshot["result"])
        self.assertEqual(snapshot["states"], [-1, -1])
        self.assertEqual(snapshot["pending_commands"], 0)

    def test_106_features_and_model_contract_are_required(self):
        message = packet("entrada", janela=0, valores=[1.] * 106, captura_id="1234:1")
        serial(self.state, message)
        for invalid in (dict(message, valores=[1.] * 14),
                        dict(message, valores=[float("nan")] * 106),
                        dict(message, protocolo="TCC_EVENTOS5_V1_P10"),
                        dict(message, sha256="f" * 64)):
            with self.subTest(invalid=invalid["protocolo"]), self.assertRaises(ValueError):
                serial(self.state, invalid)

    def test_invalid_neural_probabilities_rejected(self):
        message = result()
        for field, value in (("confianca", .8), ("margem", .2),
                             ("probabilidades", [.2] * 5), ("classe_id", True)):
            invalid = dict(message, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                serial(self.state, invalid)

    def test_saved_file_confirmation_must_match_current_capture(self):
        serial(self.state, packet("entrada_fim", captura_id="1234:1"))
        event = dict(type="capture_saved", capture_id="1234:1", path="dados/captura.csv", class_id=5)
        self.state.accept_event(event)
        self.assertEqual(self.state.snapshot()["capture"]["phase"], "salva")
        serial(self.state, packet())
        self.assertEqual(self.state.snapshot()["capture"]["phase"], "salva")
        with self.assertRaises(ValueError):
            self.state.accept_event(dict(event, capture_id="1234:2"))
        serial(self.state, packet("captura_inicio", captura_id="1234:2", captura_ativa=True))
        self.assertNotIn("saved_path", self.state.snapshot()["capture"])


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = servidor.DashboardState()
        cls.http = servidor.LocalHTTPServer(cls.state, port=0)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(2)

    def request(self, method, path, data=None, headers=None, raw=None):
        payload = json.dumps(data).encode() if data is not None else raw
        merged = {"Content-Type": "application/json"}
        merged.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=3)
        try:
            connection.request(method, path, body=payload, headers=merged)
            response = connection.getresponse()
            body = response.read()
            parsed = json.loads(body) if response.getheader("Content-Type", "").startswith("application/json") else body.decode()
            return response.status, parsed, dict(response.getheaders())
        finally:
            connection.close()

    def test_loopback_only_and_security_headers(self):
        self.assertEqual(self.http.server_address[0], "127.0.0.1")
        status, html, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML", html)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_foreign_host_and_origin_blocked(self):
        for headers in ({"Host": "malicious.invalid"},
                        {"Origin": "https://malicious.invalid"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("GET", "/api/estado", headers=headers)[0], 403)

    def test_json_limits_content_type_and_duplicate_keys(self):
        self.assertEqual(self.request("POST", "/api/comando", raw=b"{}",
                                     headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/comando", raw=b'{"op":"cancel","op":"reset"}')[0], 400)
        self.assertEqual(self.request("POST", "/api/comando", raw=b'{"op":"cancel","n":NaN}')[0], 400)
        self.assertEqual(self.request("POST", "/api/comando", raw=b" " * (servidor.BODY_LIMIT + 1))[0], 413)
        self.assertEqual(self.request("OPTIONS", "/api/comando")[0], 405)
        self.assertEqual(self.request("GET", "/missing")[0], 404)

    def test_http_complete_command_ack_and_button_mode(self):
        self.assertEqual(self.request("POST", "/api/evento", dict(type="connection", connected=True,
                                                                  port="COM_TESTE", message="USB conectado"))[0], 200)
        self.assertEqual(self.request("POST", "/api/evento", dict(type="serial", message=packet()))[0], 200)
        _, queued, _ = self.request("POST", "/api/comando", {"op": "mode", "value": "aquisicao"})
        _, pending, _ = self.request("GET", "/api/comandos")
        self.assertEqual(pending["commands"][0]["id"], queued["id"])
        self.request("POST", "/api/evento", dict(type="command_result", command_id=queued["id"], success=True))
        _, snapshot, _ = self.request("GET", "/api/estado")
        self.assertEqual(snapshot["mode_confirmed"], "teste")
        self.request("POST", "/api/evento", dict(type="serial", message=packet("modo", modo="aquisicao", origem_modo="botao")))
        _, snapshot, _ = self.request("GET", "/api/estado")
        self.assertEqual(snapshot["mode_confirmed"], "aquisicao")
        self.assertEqual(snapshot["mode_source"], "botao")
        _, config, _ = self.request("GET", "/api/config")
        self.assertEqual(config["contract"]["features_aquisicao"], 106)
        self.assertEqual(len(config["scenarios"]), 12)


def mqtt_available():
    try:
        return importlib.util.find_spec("amqtt") and importlib.util.find_spec("paho.mqtt.client")
    except ModuleNotFoundError:
        return False


def wait_until(predicate, timeout=8):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(.04)
    return False


@unittest.skipUnless(mqtt_available(), "Instale requirements.txt para os testes MQTT.")
class MQTTTests(unittest.TestCase):
    def test_broker_roundtrip_reconnect_retained_state_and_106_filter(self):
        import paho.mqtt.client as mqtt
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        state = servidor.DashboardState(demo=True)
        connected(state)
        serial(state, result())
        service = servidor.MQTTService(state, port=port, topic="tcc/teste", external=False)
        messages = []
        subscribed = threading.Event()
        reader = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="teste_servidor_leitor")
        reader.on_connect = lambda client, userdata, flags, reason, props: client.subscribe(service.topic + "/#", qos=1)
        reader.on_subscribe = lambda *args: subscribed.set()
        reader.on_message = lambda client, userdata, message: messages.append((message.topic, json.loads(message.payload)))
        replacement = None
        try:
            self.assertTrue(service.start())
            self.assertTrue(wait_until(lambda: state.snapshot()["mqtt"]["connected"]))
            self.assertTrue(service.topic.endswith("/demo"))
            reader.connect("127.0.0.1", port)
            reader.loop_start()
            self.assertTrue(subscribed.wait(3))
            message = result()
            service.event({"type": "serial", "message": message})
            self.assertTrue(wait_until(lambda: any(topic.endswith("/telemetria") for topic, _ in messages)))
            self.assertTrue(wait_until(lambda: state.snapshot()["mqtt"]["received"] >= 1))
            result_payload = next(payload for topic, payload in messages if topic.endswith("/telemetria"))
            self.assertEqual(result_payload["classe"], "liquidificador_ligando")
            self.assertTrue(result_payload["simulation"])
            before = state.snapshot()["mqtt"]["published"]
            service.event({"type": "serial", "message": packet("entrada", janela=0, valores=[1.] * 106)})
            self.assertEqual(state.snapshot()["mqtt"]["published"], before)
            service.broker.stop()
            self.assertTrue(wait_until(lambda: not state.snapshot()["mqtt"]["connected"]))
            state.accept_event(dict(type="connection", connected=False, port="COM_TESTE"))
            messages.clear()
            replacement = servidor.EmbeddedBroker(port)
            replacement.start()
            self.assertTrue(wait_until(lambda: state.snapshot()["mqtt"]["connected"], timeout=12))
            self.assertTrue(wait_until(lambda: any(topic.endswith("/estado") and payload["usb_conectado"] is False
                                                 for topic, payload in messages), timeout=12))
            restored = next(payload for topic, payload in reversed(messages) if topic.endswith("/estado"))
            self.assertEqual(restored["estados"], [-1, -1])
        finally:
            reader.disconnect()
            reader.loop_stop()
            service.stop()
            if replacement:
                replacement.stop()


if __name__ == "__main__":
    unittest.main()
