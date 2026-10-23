"""Prepara a biblioteca Arduino do Edge Impulse, sem converter pesos.

Uso: python python/importar_edge_impulse.py firmware/modelo_edge_impulse.zip
Com --biblioteca-arduino, usa o mesmo ZIP instalado na Arduino IDE.
Use --verificar para apenas inspecionar. Feche os programas antes de aplicar.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LABELS = ['sem_evento_alvo', 'liquidificador_ligando', 'liquidificador_desligando',
          'microondas_ligando', 'microondas_desligando']
SELECTED = [1, 2, 3, 5] + list(range(56, 66))
KNOWN = {
    '0ddae062f70ed64c90c31633043d2bcb65df80eb4aba308a8e5efdf9a225e98e': 'eventos5_20261012',
    'dd5a7cef19f07b1cdf6c4bd5b62aa321f81d18283c6094dd074c227178dc1cdb': 'eventos5_v1_p10_20261015',
}


def macro(text, name):
    found = re.search(r'^\s*#define\s+' + re.escape(name) + r'\s+(\d+)\b', text, re.M)
    if not found:
        raise ValueError('Metadado inteiro ausente: ' + name)
    return int(found.group(1))


def inspect_export(path, profile=None):
    """Validacao somente leitura, inclusive para ZIP nao confiavel."""
    with zipfile.ZipFile(path) as archive:
        names = set()
        total = 0
        for entry in archive.infolist():
            name = entry.filename
            pure = PurePosixPath(name)
            if (pure.is_absolute() or '..' in pure.parts or '\\' in name or ':' in name
                    or any(p.rstrip(' .') != p for p in pure.parts)
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError('Caminho inseguro no ZIP: ' + name)
            if name.casefold() in names:
                raise ValueError('Caminho duplicado no ZIP: ' + name)
            names.add(name.casefold())
            total += entry.file_size
        if total > 160 * 1024 * 1024 or len(names) > 12000:
            raise ValueError('Exportacao excede o limite de tamanho.')
        candidates = [n for n in archive.namelist() if n.endswith('/src/model-parameters/model_metadata.h')]
        if len(candidates) != 1:
            raise ValueError('Use Deployment > Arduino library; esperado um unico src/model-parameters/model_metadata.h.')
        metadata_name = candidates[0]
        prefix = metadata_name[:-len('model-parameters/model_metadata.h')]
        metadata = archive.read(metadata_name).decode('utf-8-sig')
        variables = archive.read(prefix + 'model-parameters/model_variables.h').decode('utf-8-sig')
        cat = re.findall(r'const char\s*\*\s*ei_classifier_inferencing_categories\w*\s*\[\s*\]\s*=\s*\{([^}]+)\}', variables)
        if len(cat) != 1:
            raise ValueError('Esperada uma unica lista de classes no modelo.')
        labels = re.findall(r'"([^"\\]+)"', cat[0])
        if len(labels) != 5 or set(labels) != set(LABELS) or macro(metadata, 'EI_CLASSIFIER_LABEL_COUNT') != 5:
            raise ValueError('Classes incompativeis: ' + ', '.join(labels))
        if macro(metadata, 'EI_CLASSIFIER_OBJECT_DETECTION') != 0:
            raise ValueError('O sistema aceita classificacao de eventos, nao deteccao de objetos.')
        if macro(metadata, 'EI_CLASSIFIER_QUANTIZATION_ENABLED') != 0:
            raise ValueError('Esta integracao exige exportacao float32; int8 precisa de validacao propria.')
        samples = macro(metadata, 'EI_CLASSIFIER_RAW_SAMPLE_COUNT')
        axes = macro(metadata, 'EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME')
        inputs = samples * axes
        if inputs not in (350, 2650):
            raise ValueError('Esperadas 350 ou 2650 entradas; exportacao tem %d.' % inputs)
        # Hash dos bytes do modelo, independente do nome do projeto ou versao do SDK.
        models = []
        for name in archive.namelist():
            if name.startswith(prefix + 'tflite-model/') and name.endswith(('.h', '.cpp')):
                content = archive.read(name).decode('utf-8-sig')
                for body in re.findall(r'(?:unsigned char|uint8_t)\s+\w+\s*\[[^\]]*\][^{;=]*=\s*\{([^}]+)\}', content, re.S):
                    values = re.findall(r'0x([0-9a-fA-F]{2})\b', body)
                    if values:
                        data = bytes(int(x, 16) for x in values)
                        if data[4:8] == b'TFL3':
                            models.append(data)
        if len(models) != 1:
            raise ValueError('Esperado um modelo TensorFlow Lite float32; exportacao EON nao suportada nesta revisao.')
        digest = hashlib.sha256(models[0]).hexdigest()
        if digest in KNOWN:
            if inputs != 350:
                raise ValueError('Modelo conhecido com quantidade de entradas divergente.')
            # O modelo conhecido espera 350 numeros sem DSP adicional.
            clean = re.sub(r'/\*.*?\*/|//[^\n]*', '', variables, flags=re.S)
            raw_configs = re.findall(r'ei_dsp_config_raw_t\s+\w+\s*=\s*\{([^}]+)\}', clean)
            raw_ok = len(raw_configs) == 1 and [v.strip() for v in raw_configs[0].split(',') if v.strip()][1:] == ['1', '1', '1.0f']
            if (not raw_ok or variables.count('&extract_raw_features') != 1 or
                    macro(metadata, 'EI_CLASSIFIER_NN_INPUT_FRAME_SIZE') != 350 or
                    macro(metadata, 'EI_CLASSIFIER_HAS_DATA_NORMALIZATION') != 0):
                raise ValueError('O DSP exportado mudou. O modelo conhecido exige Raw Data sem escala adicional.')
            channels = SELECTED
            name = KNOWN[digest]
        else:
            if not profile:
                raise ValueError('Modelo novo: informe --perfil com os indices, nomes e pre-processamento do treino. Nao e seguro inferir isso apenas pelo tamanho. SHA256 do modelo: ' + digest)
            if profile.get('sha256_modelo') != digest:
                raise ValueError('O SHA256 no perfil deve corresponder exatamente ao modelo exportado: ' + digest)
            channels = profile.get('indices_features')
            if (not isinstance(channels, list) or len(channels)*25 != inputs or
                    len(set(channels)) != len(channels) or any(type(c) is not int or not 0 <= c < 106 for c in channels)):
                raise ValueError('Perfil deve selecionar 14 ou 106 indices distintos das features, na ordem usada no treino.')
            if profile.get('janelas') != 25 or profile.get('intervalo_janela_ms') != 200:
                raise ValueError('Perfil deve corresponder a 25 janelas fisicas de 200 ms.')
            if profile.get('entrada') != 'features_sem_normalizacao_externa':
                raise ValueError('O SDK/modelo deve conter todo o pre-processamento. Normalizacao externa nao e aplicada.')
            name = profile.get('nome', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', name):
                raise ValueError('Nome do modelo invalido no perfil.')
        return dict(modelo=name, sha256=digest, labels=labels, indices=channels,
                    entradas=inputs, prefix=prefix, projeto=macro(metadata, 'EI_CLASSIFIER_PROJECT_ID'),
                    deploy=macro(metadata, 'EI_CLASSIFIER_PROJECT_DEPLOY_VERSION'),
                    intervalo_sdk_ms=macro(metadata, 'EI_CLASSIFIER_INTERVAL_MS'),
                    sha256_zip=hashlib.sha256(Path(path).read_bytes()).hexdigest())


def localize_includes(source):
    """Arduino compila src recursivamente; caminhos relativos dispensam flags -I extras."""
    changes = 0
    pattern = re.compile(r'(^\s*#\s*include\s*)[<"]([^>"\r\n]+)[>"]', re.M)
    for file in source.rglob('*'):
        if not file.is_file() or file.suffix.lower() not in ('.h', '.hpp', '.c', '.cpp', '.cc', '.s'):
            continue
        text = file.read_text(encoding='utf-8-sig')
        def replace(match):
            nonlocal changes
            include = match[2]
            if (file.parent / include).is_file():
                return match[0]
            for base in (source, source / 'edge-impulse-sdk'):
                target = base / include
                if target.is_file():
                    changes += 1
                    return match[1] + '"' + os.path.relpath(target, file.parent).replace('\\', '/') + '"'
            return match[0]
        updated = pattern.sub(replace, text)
        if updated != text:
            file.write_text(updated, encoding='utf-8')
    return changes


def inspect_arduino_library(path, info):
    """Descobre o include publico do ZIP validado, sem extrair ou alterar o SDK."""
    prefix = info['prefix']
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        headers = [n[len(prefix):] for n in names if n.startswith(prefix)
                   and '/' not in n[len(prefix):] and n.endswith('_inferencing.h')]
        if len(headers) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]+_inferencing\.h', headers[0]):
            raise ValueError('Esperado um unico header publico *_inferencing.h na biblioteca Arduino.')
        for name in (prefix[:-len('src/')] + 'library.properties',
                     prefix + 'edge-impulse-sdk/classifier/ei_run_classifier.h'):
            if name not in names:
                raise ValueError('Biblioteca Arduino incompleta: ' + name)
    return headers[0]


def apply_export(path, root=ROOT, profile=None, arduino_library=False):
    root = Path(root).resolve()
    info = inspect_export(path, profile)
    if arduino_library:
        info['header_arduino'] = inspect_arduino_library(path, info)
        info['instalacao'] = 'biblioteca_arduino'
    else:
        info['instalacao'] = 'sdk_no_sketch'
    sketch = root / 'firmware/tcc_monitor'
    if not (sketch / 'modelo_inferencia.h').is_file():
        raise ValueError('Use a pasta da nova integracao, que contem modelo_inferencia.h.')
    contract_path = root / 'config/modelo.json'
    contract = json.loads(contract_path.read_text(encoding='utf-8-sig'))
    current = contract['sha256']
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = root / 'backups_modelo' / stamp
    target = sketch / 'src/edge_impulse'
    config_path = sketch / 'modelo_edge_impulse_config.h'
    if os.name == 'nt' and not arduino_library:
        with zipfile.ZipFile(path) as archive:
            longest = max((len(str(target / n[len(info['prefix']):]))
                           for n in archive.namelist() if n.startswith(info['prefix'])), default=0)
        if longest >= 250:
            raise ValueError('Caminho muito longo para o SDK no Windows. Extraia o projeto em uma pasta curta, como C:\\TCC, e repita a importacao.')
    with tempfile.TemporaryDirectory(prefix='ei_import_', dir=str(root)) as temp:
        staged = Path(temp) / 'edge_impulse'
        staged.mkdir()
        if not arduino_library:
            with zipfile.ZipFile(path) as archive:
                for entry in archive.infolist():
                    if not entry.filename.startswith(info['prefix']) or entry.is_dir():
                        continue
                    relative = entry.filename[len(info['prefix']):]
                    if not relative:
                        continue
                    dest = staged / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(archive.read(entry))
            for required in ('edge-impulse-sdk/classifier/ei_run_classifier.h', 'model-parameters/model_variables.h'):
                if not (staged / required).is_file():
                    raise ValueError('SDK incompleto: ' + required)
            info['includes_localizados'] = localize_includes(staged)
        contract.update(modelo=info['modelo'], sha256=info['sha256'], motor='edge_impulse_sdk',
                        features_previsao=len(info['indices']), indices_previsao_base_zero=info['indices'],
                        labels=LABELS, edge_impulse=info)
        header = ('#pragma once\n#define TCC_EI_NOME "%s"\n#define TCC_EI_SHA256 "%s"\n'
                  '#define TCC_EI_FEATURES_POR_JANELA %d\nstatic constexpr unsigned TCC_EI_CANAIS[%d]={%s};\n') % (
                      info['modelo'], info['sha256'], len(info['indices']), len(info['indices']),
                      ','.join(map(str, info['indices'])))
        if arduino_library:
            header += ('#define TCC_EI_BIBLIOTECA_ARDUINO 1\n'
                       '#include <%s>\n'
                       'static_assert(EI_CLASSIFIER_PROJECT_ID == %d, "Biblioteca de outro projeto Edge Impulse");\n'
                       'static_assert(EI_CLASSIFIER_PROJECT_DEPLOY_VERSION == %d, "Versao diferente: reinstale o mesmo ZIP e prepare novamente");\n') % (
                           info['header_arduino'], info['projeto'], info['deploy'])
        backup.mkdir(parents=True)
        shutil.copy2(contract_path, backup / 'modelo.json')
        if config_path.exists():
            shutil.copy2(config_path, backup / config_path.name)
        target.parent.mkdir(exist_ok=True)
        moved = False
        installed = False
        try:
            if target.exists():
                target.rename(backup / 'edge_impulse')
                moved = True
            if not arduino_library:
                staged.rename(target)
                installed = True
            config_path.write_text(header, encoding='utf-8')
            contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        except Exception:
            # Conserva inclusive a tentativa falha; nao apaga fontes do usuario.
            if installed:
                target.rename(backup / 'tentativa_incompleta')
            if moved:
                (backup / 'edge_impulse').rename(target)
            shutil.copy2(backup / 'modelo.json', contract_path)
            if (backup / config_path.name).exists():
                shutil.copy2(backup / config_path.name, config_path)
            elif config_path.exists():
                config_path.rename(backup / 'config_tentativa_incompleta.h')
            raise
    info.update(backup=str(backup), modelo_anterior_sha256=current)
    (backup / 'importacao.json').write_text(json.dumps(info, indent=2)+'\n', encoding='utf-8')
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('arquivo', nargs='?', type=Path, default=ROOT / 'firmware/modelo_edge_impulse.zip')
    parser.add_argument('--perfil', type=Path, help='Contrato de entrada para um modelo novo, conferido com o treino.')
    parser.add_argument('--verificar', action='store_true', help='Somente inspeciona; nao altera firmware nem configuracao.')
    parser.add_argument('--biblioteca-arduino', action='store_true',
                        help='Usa o ZIP instalado por Sketch > Include Library > Add .ZIP Library na Arduino IDE.')
    args = parser.parse_args()
    try:
        profile = json.loads(args.perfil.read_text(encoding='utf-8-sig')) if args.perfil else None
        if args.verificar:
            result = inspect_export(args.arquivo, profile)
            if args.biblioteca_arduino:
                result['header_arduino'] = inspect_arduino_library(args.arquivo, result)
        else:
            result = apply_export(args.arquivo, profile=profile, arduino_library=args.biblioteca_arduino)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not args.verificar:
            if args.biblioteca_arduino:
                print('Configuracao preparada. Instale este MESMO ZIP na Arduino IDE por Adicionar biblioteca .ZIP. O script nao instala bibliotecas globais.')
            print('Modelo importado. Reinicie os dois programas, compile e grave tcc_monitor.ino. Use uma nova pasta de capturas se o modelo mudou.')
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        parser.exit(1, 'Importacao interrompida: %s\n' % exc)


if __name__ == '__main__':
    main()
