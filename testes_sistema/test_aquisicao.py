"""Recepção transacional, retomada e integração HTTP sem uma placa conectada."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import aquisicao as aq
import servidor


def packet(kind, **changes):
    p = dict(protocolo=aq.PROTOCOL, modelo=aq.MODEL, sha256=aq.MODEL_SHA,
             tipo=kind, boot=123, epoch=1, modo="aquisicao", estados=[0, 0],
             captura_id="0000007b:1", cenario=5, cenario_selecionado=5,
             captura_ativa=True, origem_captura="botao")
    p.update(changes)
    return p


def prediction(**changes):
    return packet("resultado", classe_id=0, classe=aq.LABELS[0],
                  probabilidades=[.96, .01, .01, .01, .01], confianca=.96,
                  margem=.95, **changes)


def complete(receiver, count=25, **changes):
    receiver.accept(packet("captura_armada", **changes))
    receiver.accept(prediction(**changes))
    for i in range(count):
        receiver.accept(packet("entrada", janela=i, valores=[i + .125] * 106, **changes))
    return receiver.accept(packet("entrada_fim", janelas=25, captura_ativa=False, **changes))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = aq.CaptureStore(self.root)
        self.receiver = aq.CaptureReceiver(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_complete_capture_and_wrong_prediction_still_saved_with_true_label(self):
        record = complete(self.receiver)
        self.assertEqual(record["classe_esperada"], 1)
        self.assertEqual(record["resultado"]["classe_id"], 0)
        self.assertEqual(self.store.counts[5], 1)
        with (self.root / record["csv"]).open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["timestamp", *aq.FEATURES])
        self.assertEqual(len(rows), 26)
        self.assertTrue(all(len(row) == 107 for row in rows))
        self.assertEqual(rows[-1][0], "4800")

    def test_duplicate_capture_does_not_count_twice(self):
        complete(self.receiver)
        self.assertIsNone(complete(self.receiver))
        self.assertEqual(len(self.store.records), 1)

    def test_incomplete_capture_not_saved(self):
        with self.assertRaises(ValueError):
            complete(self.receiver, count=24)
        self.assertEqual(len(self.store.records), 0)

    def test_no_end_or_result_does_not_count(self):
        self.receiver.accept(packet("captura_armada"))
        for i in range(25):
            self.receiver.accept(packet("entrada", janela=i, valores=[1.] * 106))
        self.assertFalse(self.store.records)
        with self.assertRaises(ValueError):
            self.receiver.accept(packet("entrada_fim", janelas=25))
        self.assertFalse(self.store.records)

    def test_invalid_26th_row_aborts_all_data(self):
        self.receiver.accept(packet("captura_armada"))
        self.receiver.accept(prediction())
        for i in range(25):
            self.receiver.accept(packet("entrada", janela=i, valores=[1.] * 106))
        with self.assertRaises(ValueError):
            self.receiver.accept(packet("entrada", janela=25, valores=[1.] * 106))
        self.receiver.accept(packet("entrada_fim", janelas=25))
        self.assertFalse(self.store.records)

    def test_order_or_conflicting_duplicate_aborts(self):
        for index in (1, 0):
            with self.subTest(index=index):
                self.receiver.accept(packet("captura_armada"))
                self.receiver.accept(packet("entrada", janela=0, valores=[1.] * 106))
                self.receiver.accept(packet("entrada", janela=index, valores=[2.] * 106))
                if index == 1:
                    self.receiver.accept(packet("entrada", janela=3, valores=[2.] * 106))
                self.assertIsNone(self.receiver.active)

    def test_identical_row_duplicate_is_idempotent(self):
        self.receiver.accept(packet("captura_armada"))
        row = packet("entrada", janela=0, valores=[1.] * 106)
        self.receiver.accept(row)
        self.receiver.accept(row)
        self.assertEqual(len(self.receiver.active["rows"]), 1)

    def test_boot_epoch_cancel_and_scenario_change_abort(self):
        for message in (packet("heartbeat", boot=456), packet("heartbeat", epoch=2),
                        packet("captura_cancelada"), prediction(cenario=6)):
            with self.subTest(message=message["tipo"]):
                self.receiver.accept(packet("captura_armada"))
                self.receiver.accept(message)
                self.assertIsNone(self.receiver.active)
                self.assertFalse(self.store.records)

    def test_nan_wrong_model_or_14_features_rejected(self):
        for values, sha in (([float("nan")] * 106, aq.MODEL_SHA),
                            ([1.] * 14, aq.MODEL_SHA), ([1.] * 106, "wrong")):
            self.receiver.accept(packet("captura_armada"))
            with self.assertRaises(ValueError):
                self.receiver.accept(packet("entrada", janela=0, valores=values, sha256=sha))
            self.assertIsNone(self.receiver.active)

    def test_file_failure_never_commits_count(self):
        original = aq.atomic_json
        def fail_manifest(path, value):
            if path.name == "campanha.json":
                raise OSError("disco indisponível")
            return original(path, value)
        with patch.object(aq, "atomic_json", side_effect=fail_manifest):
            with self.assertRaises(OSError):
                complete(self.receiver)
        self.assertFalse(self.store.records)
        self.store.close()
        self.store = aq.CaptureStore(self.root)
        self.assertFalse(self.store.records)  # arquivos órfãos não viram capturas confirmadas

    def test_resume_and_hash_detect_changed_csv(self):
        record = complete(self.receiver)
        self.store.close()
        self.store = aq.CaptureStore(self.root)
        self.assertEqual(self.store.counts[5], 1)
        self.store.close()
        (self.root / record["csv"]).write_text("alterado", encoding="utf-8")
        with self.assertRaises(ValueError):
            aq.CaptureStore(self.root)

    def test_second_program_cannot_open_same_campaign(self):
        with self.assertRaises(ValueError):
            aq.CaptureStore(self.root)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.state = servidor.DashboardState()
        self.http = servidor.LocalHTTPServer(self.state, port=0)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.commands = []
        self.latest = packet("heartbeat", captura_id="", captura_ativa=False)
        self.bridge = aq.LocalBridge(f"http://127.0.0.1:{self.http.server_port}",
                                     self.commands.append,
                                     lambda: dict(connected=True, port="COM_TESTE", message="teste"),
                                     lambda: self.latest)
        self.bridge.start()

    def tearDown(self):
        self.bridge.stop_event.set()
        self.bridge.join(timeout=4)
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)

    def wait_for(self, predicate):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.02)
        self.fail("Integração HTTP não respondeu a tempo.")

    def test_command_ack_button_mode_and_server_restart(self):
        self.wait_for(lambda: self.state.snapshot()["mode_confirmed"] == "aquisicao")
        first = self.state.queue_command(dict(op="mode", value="teste"))
        self.wait_for(lambda: len(self.commands) == 1)
        self.assertEqual(self.commands[0], dict(op="mode", value="teste"))
        self.wait_for(lambda: self.state.pending_commands() == {"commands": []})
        self.assertEqual(self.state.snapshot()["mode_confirmed"], "aquisicao")
        self.bridge.send(dict(type="serial", message=packet("modo", modo="teste", origem_modo="botao")))
        self.wait_for(lambda: self.state.snapshot()["mode_confirmed"] == "teste")
        self.assertEqual(self.state.snapshot()["mode_source"], "botao")
        # Um servidor reiniciado pode reutilizar o mesmo número de comando.
        self.state = servidor.DashboardState()
        self.http.state = self.state
        self.wait_for(lambda: self.state.snapshot()["mode_confirmed"] == "aquisicao")
        second = self.state.queue_command(dict(op="mode", value="teste"))
        self.assertEqual(first["id"], second["id"])
        self.wait_for(lambda: len(self.commands) == 2)

    def test_serial_command_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            store = aq.CaptureStore(directory)
            client = aq.DeviceClient(store)
            lines = []
            client.write = lines.append
            try:
                for c in (dict(op="mode", value="aquisicao"), dict(op="scenario", value=9),
                          dict(op="states", values=[1, 0]), dict(op="start"), dict(op="cancel")):
                    client.remote_command(c)
                self.assertEqual(lines, ["MODE aquisicao", "SCENARIO 9", "STATE 1 0", "CAPTURE", "CANCEL"])
                with self.assertRaises(ValueError):
                    client.remote_command(dict(op="exec", value="anything"))
            finally:
                client.close()


class SerialWorkerTests(unittest.TestCase):
    def test_fragmented_serial_stream_is_saved_without_http(self):
        messages = [packet("captura_armada"), prediction()]
        messages += [packet("entrada", janela=i, valores=[i + .125] * 106) for i in range(25)]
        messages.append(packet("entrada_fim", janelas=25, captura_ativa=False))
        stream = bytearray(b"\r\n" + b"\n".join(json.dumps(m).encode() for m in messages) + b"\n")
        writes = []

        class Port:
            def __init__(self):
                self.closed = False
            @property
            def in_waiting(self):
                return len(stream)
            def open(self):
                pass
            def close(self):
                self.closed = True
            def write(self, data):
                writes.append(data)
            def read(self, size):
                if not stream:
                    time.sleep(.01)
                    return b""
                size = min(size, 379)  # fragmentos que cortam as linhas JSON
                result = bytes(stream[:size])
                del stream[:size]
                return result

        fake = types.SimpleNamespace(Serial=Port, SerialException=OSError)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, serial=fake):
            store = aq.CaptureStore(directory)
            client = aq.DeviceClient(store)
            try:
                client.connect("COM_TESTE")
                deadline = time.monotonic() + 5
                while not store.records and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(len(store.records), 1)
                self.assertIn(b"PING\n", writes)
                self.assertTrue(client.connected)
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
