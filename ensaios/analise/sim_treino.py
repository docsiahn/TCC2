"""Simula o modo continuo sobre as 600 capturas de treino (degrau deslizando pela janela de 5 s)
e compara a maquina de estados atual com a regra de posicao do degrau."""
import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parent))
from base import *
import numpy as np
N = 25
X, cond, ses, hdr = carregar()
ANTES = {1:(0,0),2:(1,1),3:(1,0),4:(0,1),5:(0,0),6:(1,0),7:(0,0),8:(0,1),9:(0,1),10:(1,1),11:(1,0),12:(1,1)}

def posicao(x):
    best, bp = -1.0, 0
    for p in range(1, N):
        s = abs(x[p:].mean() - x[:p].mean()) * np.sqrt(p * (N - p) / N)
        if s > best: best, bp = s, p
    return bp

class Estados:
    def __init__(s, a, b): s.v=[a,b]; s.cand=-1; s.conf=0; s.fundo=0; s.bloq=[False,False]; s.tem=[False,False]; s.ult=[0,0]; s.eventos=[]
    def observar(s, k, conf, mg, t, pos_ok=True):
        if k<0 or conf<.6 or mg<.1: s.cand=-1; s.conf=s.fundo=0; return 'baixa'
        if k==0:
            s.cand=-1; s.conf=0; s.fundo+=1
            if s.fundo>=2: s.bloq=[False,False]
            return 'sem'
        if not pos_ok: s.cand=-1; s.conf=s.fundo=0; return 'fora'
        s.fundo=0
        if s.cand!=k: s.cand=k; s.conf=1
        else: s.conf+=1
        if s.conf<2: return 'confirmando'
        d=0 if k<=2 else 1; alvo=1 if k in (1,3) else 0
        if s.v[d]==alvo: return 'repetido'
        if s.bloq[d] or (s.tem[d] and t-s.ult[d]<1500): return 'aguardando'
        s.v[d]=alvo; s.bloq[d]=True; s.tem[d]=True; s.ult[d]=t; s.eventos.append((t,k)); return 'C'

def fluxo(x, pad=N):
    return np.concatenate([np.repeat(x[:1], pad, 0), x, np.repeat(x[-1:], pad, 0)])

# pre-calcula probabilidades e posicoes de cada contexto deslizante
cache = []
for i in range(len(X)):
    f = fluxo(X[i]); ctx = np.stack([f[j:j+N] for j in range(len(f)-N+1)])
    P = rede(ctx); pos = np.array([posicao(c[:, 1]) for c in ctx])
    cache.append((P, pos))
np.save(TRAB / 'cache_P.npy', np.array([c[0] for c in cache])); np.save(TRAB / 'cache_pos.npy', np.array([c[1] for c in cache]))

def avaliar(lo, hi, desconhecido=False):
    ok = falso = perdido = 0; lat = []; falsos_det = []
    for i in range(len(X)):
        P, pos = cache[i]; alvo = COND_CLASSE[cond[i]]
        e = Estados(*((-1,-1) if desconhecido else ANTES[cond[i]]))
        for j in range(len(P)):
            p = P[j]; k = int(p.argmax()); conf = p[k]; mg = conf - np.sort(p)[-2]
            e.observar(k, conf, mg, j*200, pos_ok=(lo <= pos[j] <= hi) if lo is not None else True)
        certos = [ev for ev in e.eventos if ev[1] == alvo]
        errados = [ev for ev in e.eventos if ev[1] != alvo]
        if alvo:
            if certos: ok += 1; lat.append(certos[0][0]/200)
            else: perdido += 1
        falso += len(errados)
        if errados: falsos_det.append((int(cond[i]), [int(ev[1]) for ev in errados]))
    n_ev = int((np.vectorize(COND_CLASSE.get)(cond) > 0).sum())
    return ok, n_ev, perdido, falso, (np.median(lat) if lat else None), falsos_det

if __name__ == '__main__':
    for desc in (False, True):
        print('\n== estados iniciais', 'DESCONHECIDOS (-1,-1)' if desc else 'conhecidos (como no treino)')
        ok, n, perd, fal, lat, det = avaliar(None, None, desc)
        print(f'atual:          {ok}/{n} detectados, {perd} perdidos, {fal} eventos falsos, contexto mediano na confirmacao {lat}')
        for lo in (8, 9, 10, 11):
            for hi in (15, 16, 17, 18, 19):
                ok, n, perd, fal, lat, det = avaliar(lo, hi, desc)
                print(f'faixa [{lo:2d},{hi:2d}]:    {ok}/{n} detectados, {perd} perdidos, {fal} eventos falsos, contexto mediano na confirmacao {lat}')
