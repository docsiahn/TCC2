"""Ensaios continuos (modo de teste continuo): P10 (bloco B), so Wi-Fi (bloco C) e P3 (bloco D).

Verdade: acoes do operador, com o instante do degrau de corrente refinado no IRMS registrado.
Placa: mudancas do campo "estados" dos resultados (o resultado de confirmacao pode cair numa
lacuna do coletor; nesse caso vale o primeiro resultado ja com o estado novo).
Sem filtro: as mesmas saidas da rede (todas as janelas com classe de evento, registradas pelo
programa de aquisicao em diagnostico.log) reprocessadas pela maquina de estados sem o filtro.
"""
import json
import statistics

import numpy as np

from comum import FIG, SAIDA, fmt, ler_diagnostico, ler_registros, marcas, salvar_tabela
from estilo import BOM, CRITICO, GRADE, SERIE, TEXTO, TEXTO2, TEXTO3, plt, salvar

L_, M_ = "L", "M"
NOME_ACAO = {(L_, 1): "liga liquidificador", (L_, 0): "desliga liquidificador",
             (M_, 1): "liga micro-ondas", (M_, 0): "desliga micro-ondas"}


def segmentos(m):
    b2 = 1794092855.0
    return [
        dict(id="B1", bloco="B", desc="P10, rodada 1 (estados informados)", t0=m["B_inicio"], t1=b2 - 0.1, ini=[0, 0],
             diag="guiado_P10", acoes=[(111, L_, 1), (127, M_, 1), (140, L_, 0), (151, L_, 1), (167, M_, 0), (176, L_, 0), (192, M_, 1), (203, M_, 0)]),
        dict(id="B2", bloco="B", desc="P10, rodada 2 (estados informados)", t0=b2, t1=m["B_rodada3"] - 3, ini=[0, 0], diag="guiado_P10",
             acoes=[(73, L_, 1), (78, M_, 1), (91, L_, 0), (100, L_, 1), (112, M_, 0), (120, L_, 0), (132, M_, 1), (141, M_, 0)]),
        dict(id="B3", bloco="B", desc="P10, rodada 3 (estados desconhecidos)", t0=m["B_rodada3"], t1=m["B_rodada3"] + 160, ini=[-1, -1],
             diag="guiado_P10", acoes=[(74, L_, 1), (83, M_, 1), (97, L_, 0), (107, L_, 1), (120, M_, 0), (128, L_, 0), (137, M_, 1), (146, M_, 0)]),
        dict(id="C", bloco="C", desc="P10, só pelo Wi-Fi (USB fechada)", t0=m["C_inicio"], t1=m["C_inicio"] + 185, ini=[-1, -1], diag=None,
             acoes=[(84, L_, 1), (94, M_, 1), (108, L_, 0), (120, M_, 0), (150, M_, 1), (164, M_, 0)]),
        dict(id="D", bloco="D", desc="P3, rodada contínua (estados informados)", t0=m["D_continuo"], t1=m["D_continuo"] + 150, ini=[0, 0],
             diag="guiado_P3", acoes=[(80, L_, 1), (89, M_, 1), (99.5, L_, 0), (106, M_, 0), (133, M_, 1), (141, M_, 0)],
             nao_acoes=[(97, "ciclo do magnetron (P3): 503 → 140")]),
    ]


def resultados(L, t0, t1):
    out = []
    for x in L:
        if x["tipo"] != "resultado" or not (t0 <= x["t_pc"] <= t1):
            continue
        r = x["r"]
        if r.get("entrada_a_seguir"):
            continue
        out.append(dict(t=x["t_pc"], seq=r["seq"], boot=r["boot"], irms=r.get("irms_adc"), c=r.get("classe_id"),
                        conf=r.get("confianca"), mg=r.get("margem"), d=r.get("decisao"), est=r.get("estados"),
                        pos=r.get("posicao_degrau"), tr=r.get("transporte", "usb")))
    return out


def refinar(res, t0, ta):
    """Instante e seq do degrau: primeira amostra que cruza o meio entre o nivel antes e depois."""
    def nivel(a, b):
        v = [r["irms"] for r in res if t0 + ta + a <= r["t"] <= t0 + ta + b]
        return statistics.median(v) if v else None
    pre, pos = nivel(-2.0, -0.3), nivel(3.0, 6.0)
    if pre is None or pos is None:
        return None
    meio = (pre + pos) / 2
    sobe = pos > pre
    for r in res:
        if t0 + ta - 1.0 <= r["t"] <= t0 + ta + 2.5 and ((r["irms"] > meio) if sobe else (r["irms"] < meio)):
            return dict(t=r["t"], seq=r["seq"], pre=pre, pos=pos)
    return None


def mudancas(res, ini):
    """Mudancas de estado por aparelho, na ordem dos resultados."""
    out, ant = [], list(ini)
    prev_seq = None
    for r in res:
        e = r["est"]
        if not e:
            continue
        for d in (0, 1):
            if e[d] != ant[d] and e[d] in (0, 1):
                exato = r["d"] == "evento_confirmado"
                out.append(dict(dev=d, val=e[d], seq=r["seq"], t=r["t"], exato=exato,
                                seq_min=r["seq"] if exato else (prev_seq + 1 if prev_seq else r["seq"])))
        ant = list(e)
        prev_seq = r["seq"]
    return out


def casar(acoes_ref, ev, janela=25):
    """Associa cada acao ao primeiro evento do mesmo aparelho/sentido em ate 5 s (25 janelas)."""
    usados, linhas = set(), []
    for a in acoes_ref:
        cand = [i for i, e in enumerate(ev) if i not in usados and e["dev"] == a["dev"] and e["val"] == a["val"]
                and a["seq"] - 2 <= e["seq"] <= a["seq"] + janela]
        if cand:
            i = cand[0]
            usados.add(i)
            e = ev[i]
            linhas.append(dict(a, ok=True, lat_s=(e["seq"] - a["seq"]) * 0.2, lat_min_s=(e["seq_min"] - a["seq"]) * 0.2,
                               exato=e["exato"], lat_t=(e["t"] - a["t"]) if e["t"] is not None else None))
        else:
            linhas.append(dict(a, ok=False))
    falsos = [e for i, e in enumerate(ev) if i not in usados]
    return linhas, falsos


class Estados:
    """Copia da maquina de estados do firmware (estados.h, com o parametro posicaoValida)."""
    def __init__(s, ini):
        s.v = list(ini); s.cand = -1; s.n = 0; s.fundo = 0
        s.bloq = [False, False]; s.tem = [False, False]; s.ult = [0, 0]; s.ev = []

    def observar(s, k, conf, mg, seq, pos_ok=True):
        t = seq * 200
        if k < 0 or conf is None or conf < .6 or mg < .1:
            s.cand = -1; s.n = 0; s.fundo = 0; return
        if k == 0:
            s.cand = -1; s.n = 0; s.fundo += 1
            if s.fundo >= 2:
                s.bloq = [False, False]
            return
        if not pos_ok:
            s.cand = -1; s.n = 0; s.fundo = 0; return
        s.fundo = 0
        if s.cand != k:
            s.cand = k; s.n = 1
        else:
            s.n += 1
        if s.n < 2:
            return
        d = 0 if k <= 2 else 1
        alvo = 1 if k in (1, 3) else 0
        if s.v[d] == alvo:
            return
        if s.bloq[d] or (s.tem[d] and t - s.ult[d] < 1500):
            return
        s.v[d] = alvo; s.bloq[d] = True; s.tem[d] = True; s.ult[d] = t
        s.ev.append(dict(dev=d, val=alvo, seq=seq, seq_min=seq, t=None, exato=True))


def reprocessar(diag, boot, s0, s1, ini, com_filtro):
    por_seq = {x["seq"]: x for x in diag if x["boot"] == boot and s0 <= x["seq"] <= s1}
    e = Estados(ini)
    for sq in range(s0, s1 + 1):
        x = por_seq.get(sq)
        if x is None:
            e.observar(0, 1.0, 1.0, sq)
        else:
            e.observar(x["classe"], x["conf"], x["margem"], sq,
                       pos_ok=(x["decisao"] != "fora_da_posicao") if com_filtro else True)
    return e.ev, len(por_seq)


def fig_linha_tempo(seg, res, ref, ev, falsos, nome, titulo):
    t0 = seg["t0"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9.5, 4.6), sharex=True, gridspec_kw=dict(height_ratios=[2.2, 1.3]))
    tt = np.array([r["t"] - t0 for r in res]); ii = np.array([r["irms"] for r in res])
    a1.plot(tt, ii, color=SERIE[0], linewidth=1.4)
    a1.set_ylabel("IRMS (contagens)")
    for i, a in enumerate(ref):
        rot = ("L" if a["dev"] == 0 else "M") + ("↑" if a["val"] else "↓")
        a1.axvline(a["t"] - t0, color=TEXTO3, linewidth=0.8, linestyle=":")
        a1.text(a["t"] - t0, 600 if i % 2 == 0 else 565, rot, ha="center", va="bottom", fontsize=8, color=TEXTO2,
                bbox=dict(facecolor="white", edgecolor="none", pad=0.5))
    for t, txt in seg.get("nao_acoes", []):
        a1.annotate(txt, xy=(t, 300), xytext=(t + 6, 420), fontsize=8, color=CRITICO,
                    arrowprops=dict(arrowstyle="->", color=CRITICO, linewidth=1))
    a1.set_ylim(0, 660)
    a1.set_title(titulo, loc="left")
    # estados: verdade (faixa) x placa (linha)
    tv = np.arange(0, seg["t1"] - t0, 0.1)
    for d, (base, cor, nome_ap) in enumerate(((1.6, SERIE[1], "Liquidificador"), (0, SERIE[2], "Micro-ondas"))):
        verdade = np.full_like(tv, seg["ini"][d] if seg["ini"][d] >= 0 else 0, dtype=float)
        for a in ref:
            if a["dev"] == d:
                verdade[tv >= a["t"] - t0] = a["val"]
        a2.fill_between(tv, base, base + verdade, step="post", color=GRADE, linewidth=0, label="Real" if d == 0 else None)
        placa_t = [0.0] + [r["t"] - t0 for r in res]
        placa_v = [seg["ini"][d]] + [(r["est"][d] if r["est"] else -1) for r in res]
        pv = np.array([v if v >= 0 else np.nan for v in placa_v], dtype=float)
        a2.step(placa_t, base + pv, where="post", color=cor, linewidth=1.8, label=f"{nome_ap} (placa)")
    for f in falsos:
        a2.plot(f["t"] - t0, (1.6 if f["dev"] == 0 else 0) + f["val"] + 0.5, marker="x", color=CRITICO, markersize=9, mew=2)
    a2.set_yticks([0, 1, 1.6, 2.6], ["Micro desl.", "Micro lig.", "Liq. desl.", "Liq. lig."], fontsize=7)
    a2.set_xlabel("Tempo desde o início do bloco (s)")
    a2.grid(axis="y", visible=False)
    t_ini = min(a["t"] for a in ref) - t0 - 8
    a2.set_xlim(t_ini, seg["t1"] - t0)
    h, l = a2.get_legend_handles_labels()
    a2.legend(h, l, loc="upper center", ncol=3, fontsize=8, bbox_to_anchor=(0.5, -0.32))
    fig.tight_layout()
    salvar(fig, FIG / nome)


def main():
    L = ler_registros(); m = marcas(L)
    diags = {"guiado_P10": ler_diagnostico("guiado_P10"), "guiado_P3": ler_diagnostico("guiado_P3")}
    R, todas = {}, []
    for seg in segmentos(m):
        res = resultados(L, seg["t0"], seg["t1"])
        ref = []
        for ta, dev, val in seg["acoes"]:
            p = refinar(res, seg["t0"], ta)
            ref.append(dict(dev=0 if dev == L_ else 1, val=val, acao=NOME_ACAO[(dev, val)], ta=ta,
                            t=p["t"] if p else seg["t0"] + ta, seq=p["seq"] if p else None,
                            pre=p and round(p["pre"]), pos=p and round(p["pos"])))
        ev = mudancas(res, seg["ini"])
        linhas, falsos = casar(ref, ev)
        cobertura = len(res) / max(1, (res[-1]["seq"] - res[0]["seq"] + 1)) if res else 0
        info = dict(desc=seg["desc"], n_acoes=len(ref), corretos=sum(l["ok"] for l in linhas),
                    perdidos=[l["acao"] for l in linhas if not l["ok"]],
                    falsos=[dict(dev=f["dev"], val=f["val"], t_rel=round(f["t"] - seg["t0"], 1)) for f in falsos],
                    latencias=[round(l["lat_s"], 2) for l in linhas if l["ok"]],
                    latencias_t=[round(l["lat_t"], 2) for l in linhas if l["ok"]],
                    cobertura_coletor=round(cobertura, 3), transporte=sorted({r["tr"] for r in res}),
                    acoes=[dict(acao=l["acao"], t_rel=round(l["t"] - seg["t0"], 1), seq=l["seq"], ok=l["ok"],
                                lat_s=round(l.get("lat_s", float("nan")), 2), exato=l.get("exato"),
                                pre=l["pre"], pos=l["pos"]) for l in linhas])
        # reprocessamento com e sem filtro (todas as janelas de evento, do diagnostico da aquisicao)
        if seg["diag"]:
            boot = res[0]["boot"]
            s0, s1 = res[0]["seq"], res[-1]["seq"]
            for rot, cf in (("com_filtro", True), ("sem_filtro", False)):
                evr, n_ev_jan = reprocessar(diags[seg["diag"]], boot, s0, s1, seg["ini"], cf)
                lr, fr = casar(ref, evr)
                info[rot] = dict(corretos=sum(l["ok"] for l in lr), perdidos=[l["acao"] for l in lr if not l["ok"]],
                                 falsos=[dict(dev=f["dev"], val=f["val"], seq=f["seq"]) for f in fr],
                                 latencias=[round(l["lat_s"], 2) for l in lr if l["ok"]], janelas_evento=n_ev_jan)
        # Contrafactual por acao: o OUTRO aparelho com estado desconhecido (pior caso), com e sem filtro.
        if seg["diag"]:
            boot = res[0]["boot"]
            cf_out = []
            for l, a_ in zip(linhas, ref):
                if a_["seq"] is None:
                    continue
                ini = [-1, -1]
                ini[a_["dev"]] = 1 - a_["val"]
                r_ = {}
                for rot, cf in (("com_filtro", True), ("sem_filtro", False)):
                    evr, _ = reprocessar(diags[seg["diag"]], boot, a_["seq"] - 10, a_["seq"] + 30, ini, cf)
                    r_[rot] = dict(certo=any(e["dev"] == a_["dev"] and e["val"] == a_["val"] for e in evr),
                                   outro=[(e["dev"], e["val"]) for e in evr if e["dev"] != a_["dev"]])
                cf_out.append(dict(acao=a_["acao"], **r_))
            info["contrafactual_outro_desconhecido"] = cf_out
        R[seg["id"]] = info
        for l in linhas:
            todas.append(dict(seg=seg["id"], bloco=seg["bloco"], **{k: l[k] for k in ("acao", "dev", "val", "ok")},
                              lat_s=l.get("lat_s"), exato=l.get("exato")))
        if seg["id"] in ("B1", "D", "C"):
            titulos = {"B1": "Ensaio contínuo em P10, rodada 1: corrente medida e estados (real × placa)",
                       "D": "Ensaio contínuo em P3: o ciclo interno do magnetron gera um evento falso",
                       "C": "Operação só pelo Wi-Fi (telemetria a 1 Hz; eventos imediatos)"}
            fig_linha_tempo(seg, res, ref, ev, falsos, f"fig_linha_tempo_{seg['id']}", titulos[seg["id"]])
    json.dump(dict(segmentos=R, acoes=todas), open(SAIDA / "resumo_continuo.json", "w"), ensure_ascii=False, indent=1, default=float)
    for k, v in R.items():
        print(k, v["desc"], "corretos", v["corretos"], "/", v["n_acoes"], "perdidos", v["perdidos"], "falsos", v["falsos"],
              "lat", v["latencias"], "cob", v["cobertura_coletor"], v["transporte"])
        for rot in ("com_filtro", "sem_filtro"):
            if rot in v:
                print("   ", rot, v[rot])
        for a in v["acoes"]:
            print("     ", a)


if __name__ == "__main__":
    main()
