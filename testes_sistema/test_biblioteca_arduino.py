"""Testes estruturais com ZIP sintetico; nao executam uma rede TensorFlow real."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import importar_edge_impulse as ei


class BibliotecaArduinoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sketch = self.root / 'firmware/tcc_monitor'
        self.sketch.mkdir(parents=True)
        shutil.copy2(Path(__file__).resolve().parents[1] / 'firmware/tcc_monitor/modelo_inferencia.h', self.sketch)
        (self.root / 'config').mkdir()
        self.contract = self.root / 'config/modelo.json'
        self.contract.write_text(json.dumps({'modelo': 'anterior', 'sha256': 'anterior'}))
        self.data = b'\0\0\0\0TFL3fixture_not_a_real_model'
        self.profile = dict(nome='teste_export', sha256_modelo=hashlib.sha256(self.data).hexdigest(),
                            janelas=25, intervalo_janela_ms=200,
                            entrada='features_sem_normalizacao_externa', indices_features=ei.SELECTED)
        self.archive = self.root / 'export.zip'
        self.metadata = '\n'.join('#define %s %d' % (k,v) for k,v in {
            'EI_CLASSIFIER_LABEL_COUNT':5, 'EI_CLASSIFIER_OBJECT_DETECTION':0,
            'EI_CLASSIFIER_QUANTIZATION_ENABLED':0, 'EI_CLASSIFIER_RAW_SAMPLE_COUNT':350,
            'EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME':1, 'EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE':350,
            'EI_CLASSIFIER_PROJECT_ID':123, 'EI_CLASSIFIER_PROJECT_DEPLOY_VERSION':7,
            'EI_CLASSIFIER_INTERVAL_MS':1}.items()) + '\n'
        self.make_zip()

    def make_zip(self, public=True):
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('export/library.properties', 'name=Teste Edge Impulse\nversion=1.0.7\n')
            z.writestr('export/src/model-parameters/model_metadata.h', self.metadata)
            z.writestr('export/src/model-parameters/model_variables.h',
                       'const char *ei_classifier_inferencing_categories[] = {' +
                       ','.join(json.dumps(x) for x in ei.LABELS) + '};')
            z.writestr('export/src/tflite-model/model.cpp', 'const unsigned char model[] = {'+
                       ','.join('0x%02x' % b for b in self.data)+'};')
            z.writestr('export/src/edge-impulse-sdk/classifier/ei_run_classifier.h', '// synthetic fixture\n')
            if public:
                z.writestr('export/src/teste_inferencing.h', '#include "model-parameters/model_metadata.h"\n')

    def apply(self, external=True):
        return ei.apply_export(self.archive, self.root, self.profile, arduino_library=external)

    def test_external_config_and_backup_of_previous_local_sdk(self):
        sdk = self.sketch / 'src/edge_impulse'
        sdk.mkdir(parents=True)
        (sdk/'old.cpp').write_text('old sdk')
        before = self.archive.read_bytes()
        info = self.apply()
        self.assertFalse(sdk.exists())
        self.assertEqual((Path(info['backup'])/'edge_impulse/old.cpp').read_text(), 'old sdk')
        header = (self.sketch/'modelo_edge_impulse_config.h').read_text()
        self.assertIn('#include <teste_inferencing.h>', header)
        self.assertIn('EI_CLASSIFIER_PROJECT_DEPLOY_VERSION == 7', header)
        self.assertEqual(json.loads(self.contract.read_text())['sha256'], self.profile['sha256_modelo'])
        self.assertEqual(before, self.archive.read_bytes())

    def test_local_sdk_mode_still_available(self):
        self.apply(False)
        self.assertTrue((self.sketch/'src/edge_impulse/tflite-model/model.cpp').exists())
        self.assertNotIn('TCC_EI_BIBLIOTECA_ARDUINO', (self.sketch/'modelo_edge_impulse_config.h').read_text())

    def test_missing_public_header_leaves_configuration_unchanged(self):
        before = self.contract.read_bytes()
        self.make_zip(False)
        with self.assertRaisesRegex(ValueError, 'header publico'):
            self.apply()
        self.assertEqual(self.contract.read_bytes(), before)
        self.assertFalse((self.root/'backups_modelo').exists())

    def test_failed_write_restores_previous_sdk_and_configuration(self):
        sdk = self.sketch/'src/edge_impulse'
        sdk.mkdir(parents=True)
        (sdk/'old.cpp').write_text('old sdk')
        config = self.sketch/'modelo_edge_impulse_config.h'
        config.write_text('old config')
        before = self.contract.read_bytes()
        original = Path.write_text
        def fail(path, *a, **kw):
            if path == self.contract:
                raise OSError('simulated write failure')
            return original(path, *a, **kw)
        with patch.object(Path, 'write_text', fail):
            with self.assertRaisesRegex(OSError, 'simulated'):
                self.apply()
        self.assertEqual(self.contract.read_bytes(), before)
        self.assertEqual(config.read_text(), 'old config')
        self.assertEqual((sdk/'old.cpp').read_text(), 'old sdk')

    @unittest.skipUnless(shutil.which('g++'), 'g++ necessario para testar interface C++')
    def test_adapter_public_include_mapping_and_wrong_deploy_rejection(self):
        self.apply()
        include = self.root/'fake_library'
        include.mkdir()
        # Apenas contrato de API para testar o adaptador, sem simular acuracia.
        stub = self.metadata + '''
#include <cstddef>
namespace ei { struct signal_t { size_t total_length; int (*get_data)(size_t,size_t,float*); }; }
struct classification_t { const char *label; float value; };
struct ei_impulse_result_t { classification_t classification[5]; };
enum EI_IMPULSE_ERROR { EI_IMPULSE_OK=0, EI_IMPULSE_ERROR_TEST=1 };
static EI_IMPULSE_ERROR run_classifier(ei::signal_t *s, ei_impulse_result_t *r, bool) {
    float x[3];
    if(s->total_length!=350 || s->get_data(13,3,x)!=0 || x[0]!=65 || x[1]!=107 || x[2]!=108)
        return EI_IMPULSE_ERROR_TEST;
    if(s->get_data(349,2,x)==0) return EI_IMPULSE_ERROR_TEST;
    const char *labels[]={"microondas_desligando","microondas_ligando","liquidificador_desligando","liquidificador_ligando","sem_evento_alvo"};
    for(int i=0;i<5;++i) { r->classification[i].label=labels[i]; r->classification[i].value=i==3?1.f:0.f; }
    return EI_IMPULSE_OK;
}
'''
        public = include/'teste_inferencing.h'
        public.write_text(stub)
        source = self.root/'main.cpp'
        source.write_text('''#include "modelo_inferencia.h"
#include <cassert>
int main(){float x[2650],p[5]={};for(int i=0;i<2650;++i)x[i]=float(i);
assert(tcc_modelo::inferir(x,p));assert(p[1]==1.f && p[0]==0.f && p[4]==0.f);}
''')
        exe = self.root/'test_adapter'
        cmd=['g++','-std=c++17','-I'+str(self.sketch),'-I'+str(include),str(source),'-o',str(exe)]
        subprocess.run(cmd, check=True, capture_output=True)
        subprocess.run([str(exe)], check=True)
        public.write_text(stub.replace('EI_CLASSIFIER_PROJECT_DEPLOY_VERSION 7', 'EI_CLASSIFIER_PROJECT_DEPLOY_VERSION 8'))
        result = subprocess.run(cmd,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Versao diferente',result.stderr)


if __name__ == '__main__':
    unittest.main()
