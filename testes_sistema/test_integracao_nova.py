import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch, MagicMock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import servidor
import aquisicao
import importar_edge_impulse as ei


class ProbeTests(unittest.TestCase):
    def fake(self, response):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        def worker():
            try:
                with listener.accept()[0] as client:
                    client.recv(4096)
                    # Resposta fragmentada, como pode acontecer em TCP real.
                    for piece in response:
                        client.sendall(bytes([piece]))
            finally:
                listener.close()
        thread = threading.Thread(target=worker)
        thread.start()
        return port, thread

    def test_accepted_broker(self):
        port, thread = self.fake(b'\x20\x02\x00\x00')
        try:
            self.assertTrue(servidor.probe_mqtt('127.0.0.1', port))
        finally:
            thread.join(3)

    def test_not_mqtt_and_auth_refused(self):
        for data in (b'HTTP', b'\x20\x02\x00\x05'):
            port, thread = self.fake(data)
            try:
                with self.assertRaises(RuntimeError):
                    servidor.probe_mqtt('127.0.0.1', port)
            finally:
                thread.join(3)

    def test_unused_port(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        self.assertFalse(servidor.probe_mqtt('127.0.0.1', port))

    def test_existing_broker_reused_and_not_stopped(self):
        paho = types.ModuleType('paho')
        mqtt = types.ModuleType('paho.mqtt')
        client_module = types.ModuleType('paho.mqtt.client')
        paho.mqtt = mqtt
        mqtt.client = client_module
        client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
        client_module.Client = MagicMock()
        client_module.Client.return_value.is_connected.return_value = False
        state = servidor.DashboardState()
        service = servidor.MQTTService(state)
        with patch.dict(sys.modules, {'paho': paho, 'paho.mqtt': mqtt, 'paho.mqtt.client': client_module}), patch.object(servidor, 'probe_mqtt', return_value=True), patch.object(servidor, 'EmbeddedBroker') as broker:
            self.assertTrue(service.start())
            broker.assert_not_called()
            self.assertTrue(service.reused)
            client_module.Client.return_value.connect_async.assert_called_once_with('127.0.0.1', 1883, keepalive=15)
            service.stop()
            broker.assert_not_called()


class ImportTests(unittest.TestCase):
    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'malicioso.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('../fora.h', 'no')
            with self.assertRaisesRegex(ValueError, 'inseguro'):
                ei.inspect_export(path)

    def test_includes_localized_without_modifying_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'model-parameters').mkdir()
            (root/'model-parameters/model_metadata.h').write_text('#define TEST 350\n')
            (root/'edge-impulse-sdk').mkdir()
            code = '#include "model-parameters/model_metadata.h"\n#include <Arduino.h>\nfloat w = 0.123f;\n'
            file = root/'edge-impulse-sdk/test.h'
            file.write_text(code)
            self.assertEqual(ei.localize_includes(root), 1)
            self.assertEqual(file.read_text(), code.replace('"model-parameters/', '"../model-parameters/'))

    def test_campaign_rejects_another_model_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            store = aquisicao.CaptureStore(temp)
            store.close()
            with patch.object(aquisicao, 'MODEL_SHA', '0'*64):
                with self.assertRaises(ValueError):
                    aquisicao.CaptureStore(temp)

    def test_path_boundary(self):
        root = Path(tempfile.gettempdir())/'tcc_valid'
        self.assertTrue(aquisicao.path_inside(root/'a.csv', root))
        self.assertFalse(aquisicao.path_inside(root.parent/'tcc_valid_other/a.csv', root))


if __name__ == '__main__':
    unittest.main()
