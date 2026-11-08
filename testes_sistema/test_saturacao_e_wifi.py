"""Testes da tolerância à saturação do ADC e da placa publicando direto pelo Wi-Fi (MQTT).

Executar na raiz: python -m unittest testes_sistema.test_saturacao_e_wifi -v
"""
from __future__ import annotations

import json
import logging
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import servidor  # noqa: E402
from test_servidor import connected, mqtt_available, packet, result, serial, wait_until  # noqa: E402


def wifi(payload):
    return dict(payload, transporte="wifi_esp32", simulation=False)


class SaturacaoServidorTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.state = servidor.DashboardState(clock=lambda: self.now)
        connected(self.state)
        serial(self.state, result())   # liquidificador ligado: estados [1, 0]

    def test_aviso_de_saturacao_persistente_mantem_estados(self):
        serial(self.state, packet("aviso", motivo="saturacao_adc_corrente_persistente", estados=[1, 0]))
        snap = self.state.snapshot()
        self.assertEqual(snap["states"], [1, 0])
        self.assertIn("manteve os estados", snap["last_error"])
        self.assertIsNone(snap["last_state_loss"])

    def test_falha_do_firmware_anterior_ainda_zera_e_explica(self):
        serial(self.state, packet("falha", motivo="saturacao_adc_corrente", estados=[-1, -1]))
        snap = self.state.snapshot()
        self.assertEqual(snap["states"], [-1, -1])
        self.assertIn("firmware anterior", snap["last_error"])

    def test_resultado_com_janelas_saturadas_e_informado(self):
        serial(self.state, result(saturacao={"janelas_v": 0, "janelas_i": 2, "max_amostras_v": 0,
                                             "max_amostras_i": 180, "primeira_janela_i": 11,
                                             "limite_amostras_janela": 10, "limite_janelas": 3}))
        snap = self.state.snapshot()
        self.assertEqual(snap["result"]["saturacao"]["janelas_i"], 2)
        self.assertIn("2 janela(s) de corrente", snap["last_error"])
        self.assertEqual(servidor.saturation_note(result()), "")


class AquisicaoSaturacaoTests(unittest.TestCase):
    def test_diagnostico_registra_saturacao_em_resultado(self):
        import aquisicao
        mensagem = dict(result(), saturacao={"janelas_i": 1, "janelas_v": 0})
        with self.assertLogs(level=logging.INFO) as log:
            aquisicao.registrar_diagnostico_placa(mensagem, dict(mensagem))
        self.assertTrue(any("saturacao_adc" in linha for linha in log.output))


class WifiEstadoTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.state = servidor.DashboardState(clock=lambda: self.now)

    def test_sem_usb_estados_e_resultado_vem_do_wifi(self):
        self.assertTrue(self.state.accept_wifi("/placa/status", {"online": True, "transporte": "wifi_esp32"}))
        self.assertTrue(self.state.accept_wifi("/telemetria", wifi(result())))
        snap = self.state.snapshot()
        self.assertEqual(snap["states"], [1, 0])
        self.assertEqual(snap["result"]["classe"], "liquidificador_ligando")
        self.assertTrue(snap["wifi"]["source"])
        self.assertIn("Wi-Fi", snap["serial"]["message"])
        self.assertTrue(self.state.board_direct())

    def test_com_usb_a_usb_continua_sendo_a_fonte(self):
        connected(self.state)
        serial(self.state, result())
        self.state.accept_wifi("/estado", wifi(packet("estado", estados=[0, 1])))
        snap = self.state.snapshot()
        self.assertEqual(snap["states"], [1, 0])
        self.assertFalse(snap["wifi"]["source"])
        self.assertTrue(self.state.board_direct())   # mesmo assim o servidor não republica

    def test_dados_do_wifi_expiram_e_placa_offline_zera(self):
        self.state.accept_wifi("/telemetria", wifi(result()))
        self.now += servidor.WIFI_TTL + 1
        snap = self.state.snapshot()
        self.assertEqual(snap["states"], [-1, -1])
        self.assertFalse(self.state.board_direct())
        self.state.accept_wifi("/telemetria", wifi(result()))
        self.state.accept_wifi("/placa/status", {"online": False, "transporte": "wifi_esp32"})
        self.assertEqual(self.state.snapshot()["states"], [-1, -1])

    def test_ignora_publicacoes_do_proprio_servidor_e_contrato_diferente(self):
        self.assertFalse(self.state.accept_wifi("/telemetria", dict(result(), transporte="usb_notebook")))
        with self.assertRaises(ValueError):
            self.state.accept_wifi("/telemetria", wifi(result(sha256="0" * 64)))
        with self.assertRaises(ValueError):
            self.state.accept_wifi("/estado", wifi(packet("estado", estados=[2, 0])))


@unittest.skipUnless(mqtt_available(), "Instale requirements.txt para os testes MQTT.")
class WifiMQTTTests(unittest.TestCase):
    def test_placa_publicando_direto_sem_duplicar(self):
        import paho.mqtt.client as mqtt
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        state = servidor.DashboardState()
        service = servidor.MQTTService(state, port=port, topic="tcc/teste_wifi", external=False)
        mensagens, assinou = [], threading.Event()
        placa = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="placa_simulada")
        placa.will_set(service.topic + "/placa/status", json.dumps({"online": False, "transporte": "wifi_esp32"}),
                       qos=1, retain=True)
        placa.on_connect = lambda c, u, f, r, p: c.subscribe(service.topic + "/#", qos=1)
        placa.on_subscribe = lambda *a: assinou.set()
        placa.on_message = lambda c, u, m: mensagens.append((m.topic, json.loads(m.payload)))
        try:
            self.assertTrue(service.start())
            self.assertTrue(wait_until(lambda: state.snapshot()["mqtt"]["connected"]))
            placa.connect("127.0.0.1", port)
            placa.loop_start()
            self.assertTrue(assinou.wait(3))
            placa.publish(service.topic + "/placa/status",
                          json.dumps({"online": True, "transporte": "wifi_esp32"}), qos=1, retain=True)
            placa.publish(service.topic + "/telemetria", json.dumps(wifi(result())), qos=0)
            self.assertTrue(wait_until(lambda: state.snapshot()["states"] == [1, 0]))
            self.assertTrue(state.board_direct())
            # Com a USB ligada e a placa publicando, o servidor não republica o mesmo resultado.
            connected(state)
            antes = state.snapshot()["mqtt"]["published"]
            service.event({"type": "serial", "message": result()})
            service.publish_snapshot()
            self.assertEqual(state.snapshot()["mqtt"]["published"], antes)
            self.assertFalse(any(t.endswith("/telemetria") and p.get("transporte") == "usb_notebook"
                                 for t, p in mensagens))
        finally:
            placa.disconnect()
            placa.loop_stop()
            service.stop()


if __name__ == "__main__":
    unittest.main()
