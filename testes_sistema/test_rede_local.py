"""Controle MQTT pelo painel e escuta pelo IPv4 local, sem ESP32."""
import http.client
import json
from pathlib import Path
import socket
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import servidor
from test_servidor import mqtt_available, wait_until


class LanTests(unittest.TestCase):
    def setUp(self):
        addresses = servidor.lan_addresses()
        if not addresses:
            self.skipTest("Sem IPv4 privado na interface de rede.")
        self.ip = addresses[0]
        self.state = servidor.DashboardState()
        self.server = servidor.LocalHTTPServer(self.state, port=0, host="0.0.0.0")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.stop_mqtt()
            self.server.server_close()
            self.thread.join(2)

    def request(self, method, path, data=None, origin=None):
        conn = http.client.HTTPConnection(self.ip, self.server.server_port, timeout=3)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        try:
            conn.request(method, path, body=json.dumps(data) if data is not None else None, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_lan_dashboard_origin_and_collector_routes(self):
        origin = f"http://{self.ip}:{self.server.server_port}"
        status, config = self.request("GET", "/api/config", origin=origin)
        self.assertEqual(status, 200)
        self.assertIn(origin, config["share_urls"])
        self.assertEqual(self.request("GET", "/api/estado")[0], 200)
        self.assertEqual(self.request("GET", "/api/estado", origin="https://foreign.invalid")[0], 403)
        self.assertEqual(self.request("GET", "/api/comandos")[0], 403)
        self.assertEqual(self.request("POST", "/api/evento", dict(type="connection", connected=True))[0], 403)

    @unittest.skipUnless(mqtt_available(), "Instale requirements.txt para os testes MQTT.")
    def test_mqtt_connect_disconnect_reconnect_from_lan(self):
        import paho.mqtt.client as mqtt
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.server.mqtt = servidor.MQTTService(self.state, port=port, bind_host="0.0.0.0")
        self.assertEqual(self.request("POST", "/api/mqtt", {"action": "invalid"})[0], 400)
        self.assertEqual(self.request("POST", "/api/mqtt", {"action": "connect"})[0], 200)
        self.assertTrue(wait_until(lambda: self.state.snapshot()["mqtt"]["connected"]))
        self.assertTrue(wait_until(lambda: not self.state.snapshot()["mqtt"]["changing"]))
        received = threading.Event()
        reader = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        reader.on_connect = lambda c, *args: c.subscribe(self.server.mqtt.topic + "/estado", qos=1)
        reader.on_message = lambda *args: received.set()
        try:
            reader.connect(self.ip, port)
            reader.loop_start()
            self.assertTrue(received.wait(5), "Broker não recebeu o cliente pelo IPv4 da LAN.")
        finally:
            reader.disconnect()
            reader.loop_stop()
        self.assertEqual(self.request("POST", "/api/mqtt", {"action": "disconnect"})[0], 200)
        self.assertTrue(wait_until(lambda: not self.state.snapshot()["mqtt"]["enabled"]
                                   and not self.state.snapshot()["mqtt"]["changing"]))
        self.assertEqual(self.request("POST", "/api/mqtt", {"action": "connect"})[0], 200)
        self.assertTrue(wait_until(lambda: self.state.snapshot()["mqtt"]["connected"]))


if __name__ == "__main__":
    unittest.main()
