import json, re, csv, os, numpy as np
from pathlib import Path
# Base de 600 capturas (pasta com campanha.json). Defina TCC_BASE600 ou rode na pasta que a contem.
RAIZ = Path(os.environ.get('TCC_BASE600', 'capturas_liquidificador_microondas'))
# Pesos e padronizacao da V1, lidos do proprio firmware do repositorio.
# Pasta de arquivos intermediarios (caches da simulacao e resumo do harness C++).
TRAB = Path(os.environ.get('TCC_ANALISE_TRAB', 'analise_trabalho'))
TRAB.mkdir(parents=True, exist_ok=True)
H = (Path(__file__).resolve().parents[2] / 'firmware/tcc_monitor/rede_eventos5.h').read_text()
def arr(name):
    m = re.search(r'static constexpr float %s\[\d+\]=\{(.*?)\};' % name, H, re.S)
    return np.array([float(x.strip().rstrip('f')) for x in m.group(1).replace('\n', '').split(',') if x.strip()], dtype=np.float32)
CANAIS = [1,2,3,5,56,57,58,59,60,61,62,63,64,65]
NOMES14 = ['IRMS','P','Q','ITHD50']+['IFFT_H%d'%i for i in range(1,11)]
MEDIA, ESCALA = arr('MEDIA'), arr('ESCALA')
W0, B0, W1, B1, W2, B2 = arr('W0').reshape(350,16), arr('B0'), arr('W1').reshape(16,8), arr('B1'), arr('W2').reshape(8,5), arr('B2')
ROT = ["sem_evento","liq_lig","liq_deslig","micro_lig","micro_deslig"]
COND_CLASSE = {1:0,2:0,3:0,4:0,5:1,9:1,6:2,10:2,7:3,11:3,8:4,12:4}
def entrada(x25x106):
    x = np.asarray(x25x106, dtype=np.float32)[..., CANAIS]       # (...,25,14)
    return (x.reshape(*x.shape[:-2], 350) - MEDIA) / ESCALA
def rede(x25x106):
    z = entrada(x25x106)
    h0 = np.maximum(z @ W0 + B0, 0); h1 = np.maximum(h0 @ W1 + B1, 0); lg = h1 @ W2 + B2
    e = np.exp(lg - lg.max(-1, keepdims=True)); return e / e.sum(-1, keepdims=True)
def carregar():
    c = json.loads((RAIZ/'campanha.json').read_text())
    X, cond, ses = [], [], []
    for cap in c['capturas']:
        rows = list(csv.reader(open(RAIZ/cap['csv'])))
        X.append([[float(v) for v in r[1:]] for r in rows[1:]])
        cond.append(cap['etapa']['classe']); ses.append(cap['etapa']['sessao'])
    hdr = rows[0][1:]
    return np.array(X, dtype=np.float32), np.array(cond), np.array(ses), hdr
