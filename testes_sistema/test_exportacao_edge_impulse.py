"""Executar: python -m unittest discover -s testes_sistema -v (sem USB e sem rede)."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import aquisicao as aq


def capture(scenario=5, ident="captura:1"):
    return dict(captura_id=ident, boot=123, epoch=1, cenario=scenario,
                rows=[[i + j / 1000 for j in range(106)] for i in range(25)],
                resultado={"classe_id": 0}, origem_captura="teste_software")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = aq.CaptureStore(self.root)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_all_12_scenarios_map_to_exactly_five_labels(self):
        for scenario in range(1, 13):
            record = self.store.save(capture(scenario, str(scenario)))
            expected = aq.LABELS[aq.SCENARIOS[scenario - 1]["neural_class"]]
            path = self.root / record["csv_edge_impulse"]
            self.assertEqual(path.parent.name, expected)
            self.assertEqual(path.name.split(".")[0], expected)
            self.assertEqual(record["rotulo_edge_impulse"], expected)
            self.assertEqual(path.read_bytes(), (self.root / record["csv"]).read_bytes())
        counts = {p.name: len(list(p.glob("*.csv"))) for p in self.store.edge_impulse_root.iterdir()}
        self.assertEqual(counts, dict(zip(aq.LABELS, (4, 2, 2, 2, 2))))
        self.assertEqual(sum(self.store.counts.values()), 12)

    def test_all_106_features_and_25_windows_preserved(self):
        record = self.store.save(capture())
        with (self.root / record["csv_edge_impulse"]).open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["timestamp", *aq.FEATURES])
        self.assertEqual(len(rows), 26)
        self.assertTrue(all(len(row) == 107 for row in rows))
        self.assertEqual(rows[1][0], "0")
        self.assertEqual(rows[-1][0], "4800")
        self.assertEqual(rows[-1][-1], "24.105")

    def test_prediction_does_not_define_training_label(self):
        record = self.store.save(capture(scenario=12))
        self.assertEqual(record["resultado"]["classe_id"], 0)
        self.assertEqual(record["rotulo_edge_impulse"], "microondas_desligando")

    def test_duplicates_do_not_create_extra_training_samples(self):
        self.store.save(capture())
        self.assertIsNone(self.store.save(capture()))
        self.store.export_edge_impulse()
        self.assertEqual(len(list(self.store.edge_impulse_root.rglob("*.csv"))), 1)
        self.assertEqual(len(self.store.records), 1)

    def test_incomplete_capture_does_not_export(self):
        incomplete = capture()
        incomplete["rows"].pop()
        with self.assertRaises(ValueError):
            self.store.save(incomplete)
        self.assertFalse(list(self.store.edge_impulse_root.rglob("*.csv")))

    def test_manifest_write_failure_does_not_export_uncommitted_data(self):
        original = aq.atomic_json
        def fail_manifest(path, value):
            if path.name == "campanha.json":
                raise OSError("falha simulada")
            return original(path, value)
        with patch.object(aq, "atomic_json", side_effect=fail_manifest), self.assertRaises(OSError):
            self.store.save(capture())
        self.assertFalse(self.store.records)
        self.assertFalse(list(self.store.edge_impulse_root.rglob("*.csv")))
        self.store.close()
        self.store = aq.CaptureStore(self.root)
        self.assertFalse(list(self.store.edge_impulse_root.rglob("*.csv")))

    def test_export_failure_preserves_original_and_recovers_next_capture(self):
        replace = aq.os.replace
        def fail_export(source, target):
            if "edge_impulse" in Path(target).parts:
                raise OSError("falha de exportacao simulada")
            return replace(source, target)
        with patch.object(aq.os, "replace", side_effect=fail_export):
            record = self.store.save(capture())
        self.assertTrue((self.root / record["csv"]).is_file())
        self.assertEqual(len(self.store.records), 1)
        self.assertTrue(self.store.export_error)
        self.assertFalse(list(self.store.edge_impulse_root.rglob("*.tmp")))
        self.store.save(capture(scenario=8, ident="segunda"))
        self.assertFalse(self.store.export_error)
        self.assertEqual(len(list(self.store.edge_impulse_root.rglob("*.csv"))), 2)

    def test_resuming_legacy_campaign_exports_without_rewriting_originals(self):
        record = self.store.save(capture())
        exported = self.root / record["csv_edge_impulse"]
        original = self.root / record["csv"]
        exported.unlink()
        self.store.close()
        # Simula o manifesto anterior à funcionalidade, sem os campos novos.
        manifest = json.loads((self.root / "campanha.json").read_text(encoding="utf-8"))
        for r in manifest["registros"]:
            r.pop("csv_edge_impulse")
            r.pop("rotulo_edge_impulse")
        aq.atomic_json(self.root / "campanha.json", manifest)
        previous = original.stat().st_mtime_ns
        self.store = aq.CaptureStore(self.root)
        self.assertTrue(exported.is_file())
        self.assertEqual(exported.read_bytes(), original.read_bytes())
        self.assertEqual(original.stat().st_mtime_ns, previous)
        self.assertEqual(len(self.store.records), 1)

    def test_derived_copy_repaired_and_idempotent(self):
        record = self.store.save(capture())
        target = self.root / record["csv_edge_impulse"]
        target.write_text("copia alterada", encoding="utf-8")
        self.assertTrue(self.store.export_edge_impulse())
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), record["sha256_csv"])
        stamp = target.stat().st_mtime_ns
        self.assertTrue(self.store.export_edge_impulse())
        self.assertEqual(target.stat().st_mtime_ns, stamp)

    def test_original_corruption_blocks_export(self):
        record = self.store.save(capture())
        target = self.root / record["csv_edge_impulse"]
        target.unlink()
        (self.root / record["csv"]).write_text("original alterado", encoding="utf-8")
        self.assertFalse(self.store.export_edge_impulse())
        self.assertFalse(target.exists())

    def test_cli_exports_without_usb_server_or_window(self):
        self.store.save(capture())
        self.store.close()
        result = subprocess.run([sys.executable, str(Path(aq.__file__)), "--dados", str(self.root),
                                 "--somente-exportar"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("5 classes", result.stdout)


if __name__ == "__main__":
    unittest.main()
