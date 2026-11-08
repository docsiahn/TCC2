"""Tabelas dos ensaios continuos, do filtro de posicao, do desempenho embarcado e da base de 600."""
import json
import re
import statistics

import numpy as np

from comum import (CLASSES_CURTO, COND_CLASSE, DADOS, FIG, SAIDA, carregar, fmt, ler_registros, marcas, rede,
                   salvar_tabela)
from estilo import CMAP_AZUL, SERIE, TEXTO, TEXTO2, plt, salvar, virgula

TIPOS = ["liga liquidificador", "desliga liquidificador", "liga micro-ondas", "desliga micro-ondas"]


def med(v):
    return statistics.median(v) if v else float("nan")


def continuo():
    R = json.load(open(SAIDA / "resumo_continuo.json"))
    S = R["segmentos"]
    linhas = []
    for k in ("B1", "B2", "B3", "C", "D"):
        v = S[k]
        lat = v["latencias"]
        linhas.append([k, v["desc"], v["n_acoes"], v["corretos"], len(v["perdidos"]), len(v["falsos"]),
                       f"{fmt(med(lat))} ({fmt(min(lat))}–{fmt(max(lat))})" if lat else "—"])
    b = [S[k] for k in ("B1", "B2", "B3")]
    latb = sum((v["latencias"] for v in b), [])
    linhas.append(["B total", "P10, 3 rodadas", sum(v["n_acoes"] for v in b), sum(v["corretos"] for v in b),
                   sum(len(v["perdidos"]) for v in b), sum(len(v["falsos"]) for v in b),
                   f"{fmt(med(latb))} ({fmt(min(latb))}–{fmt(max(latb))})"])
    salvar_tabela("tab_continuo_resumo", ["Bloco", "Situação", "Ações", "Corretos", "Perdidos", "Falsos", "Resposta (s)"],
                  linhas, "Ensaios contínuos de 07/11/2026: ações do operador, eventos confirmados pela placa e tempo de resposta "
                  "(do degrau de corrente até a confirmação; mediana, mínimo–máximo)", "tab:continuo_resumo", colunas_tex="llrrrrr",
                  nota="Resposta medida pelo número de janelas de 200 ms entre o degrau e a confirmação. No bloco C (Wi-Fi), a "
                       "telemetria chega a 1 Hz e o tempo é um limite superior.")
    # por tipo de acao, so P10 com USB (B)
    lin2 = []
    for tipo in TIPOS:
        acs = [a for a in R["acoes"] if a["bloco"] == "B" and a["acao"] == tipo]
        lat = [a["lat_s"] for a in acs if a["ok"]]
        lin2.append([tipo, len(acs), sum(a["ok"] for a in acs), fmt(med(lat)), fmt(max(lat))])
    salvar_tabela("tab_continuo_por_acao", ["Ação (P10)", "Ocorrências", "Detectadas", "Resposta mediana (s)", "Máxima (s)"],
                  lin2, "Ensaio contínuo em P10 (bloco B): detecção e tempo de resposta por tipo de ação", "tab:continuo_acao",
                  colunas_tex="lrrrr")
    # filtro de posicao
    sim = open(__import__("base").TRAB / "resumo_cpp.txt").read()
    nums = [(int(a), int(b)) for a, b in re.findall(r"detectados (\d+)/400, perdidos \d+, eventos falsos (\d+)", sim)]
    # ordem no arquivo: atual-conhecidos, faixa-conhecidos, atual-desconhecidos, faixa-desconhecidos
    segs = [S[k] for k in ("B1", "B2", "B3", "D")]
    cs = sum(v["com_filtro"]["corretos"] for v in segs); ss = sum(v["sem_filtro"]["corretos"] for v in segs)
    fc = sum(len(v["com_filtro"]["falsos"]) for v in segs); fs = sum(len(v["sem_filtro"]["falsos"]) for v in segs)
    lc = sum((v["com_filtro"]["latencias"] for v in segs), []); ls = sum((v["sem_filtro"]["latencias"] for v in segs), [])
    n_ac = sum(v["n_acoes"] for v in segs)
    cf = [c for v in segs for c in v.get("contrafactual_outro_desconhecido", [])]
    cf_s = sum(bool(c["sem_filtro"]["outro"]) for c in cf); cf_c = sum(bool(c["com_filtro"]["outro"]) for c in cf)
    lin3 = [
        ["600 capturas, degrau deslizando (estados conhecidos)", "400/400", "400/400", nums[0][1], nums[1][1], "—", "—"],
        ["600 capturas, degrau deslizando (estados desconhecidos)", "400/400", "400/400", nums[2][1], nums[3][1], "—", "—"],
        [f"Ensaios contínuos B e D reprocessados ({n_ac} ações)", f"{ss}/{n_ac}", f"{cs}/{n_ac}", fs, fc, fmt(med(ls)), fmt(med(lc))],
        [f"Mesmas ações, outro aparelho desconhecido ({len(cf)} ações)", "—", "—", cf_s, cf_c, "—", "—"],
    ]
    salvar_tabela("tab_filtro_posicao", ["Avaliação", "Detectados sem filtro", "Com filtro", "Falsos sem filtro", "Com filtro",
                                         "Resposta sem (s)", "Com (s)"], lin3,
                  "Efeito do filtro de posição do degrau (janelas 11 a 17): simulação nas capturas de treino e reprocessamento "
                  "das saídas da rede registradas nos ensaios contínuos", "tab:filtro_posicao", colunas_tex="lrrrrrr",
                  nota="Na última linha, cada ação é reprocessada com o estado do outro aparelho desconhecido (pior caso); conta-se "
                       "a ação que gera evento do aparelho errado.")
    return dict(filtro=dict(sim=nums, cont=dict(acoes=n_ac, corretos_com=cs, corretos_sem=ss, falsos_com=fc, falsos_sem=fs,
                                                 lat_med_com=med(lc), lat_med_sem=med(ls)),
                            contrafactual=dict(acoes=len(cf), sem=cf_s, com=cf_c)),
                lat_B=dict(mediana=med(latb), min=min(latb), max=max(latb)))


def fig_latencia():
    R = json.load(open(SAIDA / "resumo_continuo.json"))
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    for i, tipo in enumerate(TIPOS):
        b = [a["lat_s"] for a in R["acoes"] if a["bloco"] == "B" and a["acao"] == tipo and a["ok"]]
        c = [a["lat_s"] for a in R["acoes"] if a["bloco"] == "C" and a["acao"] == tipo and a["ok"]]
        rng = np.random.default_rng(i)
        ax.scatter(b, i + rng.uniform(-0.12, 0.12, len(b)), s=36, color=SERIE[0], edgecolor="white", linewidth=1,
                   label="USB (bloco B)" if i == 0 else None, zorder=3)
        if c:
            ax.scatter(c, [i + 0.3] * len(c), s=36, marker="D", color=SERIE[1], edgecolor="white", linewidth=1,
                       label="Wi-Fi (bloco C, limite sup.)" if i == 0 or not ax.get_legend_handles_labels()[1][1:] else None, zorder=3)
    ax.axvline(1.6, color=TEXTO2, linestyle=":", linewidth=0.9)
    ax.text(1.63, -0.45, "confirmação com o degrau na janela 16", fontsize=7, color=TEXTO2, va="center")
    ax.set_yticks(range(4), TIPOS)

    ax.set_xlim(0, 3); ax.set_ylim(3.55, -0.7); virgula(ax.xaxis)
    ax.set_xlabel("Tempo do degrau de corrente até a confirmação (s)")
    ax.legend(loc="upper right", fontsize=8, bbox_to_anchor=(1.0, 0.93))
    ax.grid(axis="y", visible=False)
    ax.set_title("Tempo de resposta no modo contínuo (P10)", loc="left")
    salvar(fig, FIG / "fig_tempo_resposta")


def sistema():
    L = ler_registros()
    usb = [x["r"] for x in L if x["tipo"] == "resultado" and x["r"].get("transporte", "usb") == "usb"
           and not x["r"].get("entrada_a_seguir")]
    wifi = [x for x in L if x["tipo"] == "resultado" and x["r"].get("transporte") == "wifi_esp32"]
    inf = np.array([r["inferencia_ms"] for r in usb]); atr = np.array([r["atraso_ms"] for r in usb])
    heap = np.array([r["heap_livre"] for r in usb])
    boots = sorted({r["boot"] for r in usb})
    perd = {b: (min(r["inferencias_descartadas"] for r in usb if r["boot"] == b),
                max(r["inferencias_descartadas"] for r in usb if r["boot"] == b),
                min(r["seq"] for r in usb if r["boot"] == b), max(r["seq"] for r in usb if r["boot"] == b),
                max(r["frames_perdidos"] for r in usb if r["boot"] == b)) for b in boots}
    sat = sum(1 for r in usb if (r.get("saturacao") or {}).get("janelas_i"))
    # Wi-Fi: intervalo entre telemetrias no bloco C
    m = marcas(L)
    tw = [x["t_pc"] for x in wifi if m["C_inicio"] <= x["t_pc"] <= m["D_inicio"]]
    dw = np.diff(tw) if len(tw) > 1 else np.array([np.nan])
    fw = open(DADOS / "gravar_firmware.log", encoding="utf-8", errors="replace").read()
    flash = re.search(r"Sketch uses (\d+) bytes \((\d+)%\)", fw); ram = re.search(r"Global variables use (\d+) bytes \((\d+)%\)", fw)
    diag = open(DADOS / "diag_serial.log", encoding="utf-8", errors="replace").read()
    mq = json.loads(re.search(r'ultimo heartbeat:\s*(\{.*?\n\})', diag, re.S).group(1))
    out = dict(inferencia_ms=dict(mediana=float(np.median(inf)), p99=float(np.percentile(inf, 99)), max=float(inf.max()), n=len(inf)),
               atraso_ms=dict(mediana=float(np.median(atr)), p95=float(np.percentile(atr, 95)), max=float(atr.max())),
               heap=dict(min=int(heap.min()), max=int(heap.max())), boots=perd, saturacao_resultados=sat,
               wifi=dict(telemetrias=len(tw), intervalo_mediano_s=float(np.nanmedian(dw)), intervalo_max_s=float(np.nanmax(dw))),
               flash=(int(flash.group(1)), int(flash.group(2))), ram=(int(ram.group(1)), int(ram.group(2))),
               mqtt_heartbeat=mq["mqtt_direto"], uptime_s=mq["uptime_ms"] / 1000)
    b0 = perd[boots[0]]
    jan = b0[3] - b0[2]
    lin = [
        ["Inferência da rede (por janela)", f"mediana {fmt(out['inferencia_ms']['mediana'], 3)} ms; máx. {fmt(out['inferencia_ms']['max'], 3)} ms",
         f"{len(inf)} resultados"],
        ["Fim da janela → resultado na USB", f"mediana {fmt(out['atraso_ms']['mediana'])} ms; p95 {fmt(out['atraso_ms']['p95'])} ms",
         "inclui cálculo das 106 características"],
        ["Programa (flash)", f"{out['flash'][0]:,} bytes ({out['flash'][1]} %)".replace(",", "."), "de 1.310.720"],
        ["Variáveis globais (RAM)", f"{out['ram'][0]:,} bytes ({out['ram'][1]} %)".replace(",", "."), "de 327.680"],
        ["Memória livre em operação", f"{out['heap']['min']:,}–{out['heap']['max']:,} bytes".replace(",", "."), "com Wi-Fi e MQTT ativos"],
        ["Janelas perdidas (aquisição)", str(max(v[4] for v in perd.values())), f"{jan:,} janelas no período".replace(",", ".")],
        ["Inferências descartadas", f"{b0[1] - b0[0]} no período", "tarefa ocupada; janela seguinte é usada"],
        ["Saturação do ADC", f"{sat} resultados", "plugue P2 preso"],
        ["Telemetria pelo Wi-Fi (bloco C)", f"1 a cada {fmt(out['wifi']['intervalo_mediano_s'], 2)} s (máx. {fmt(out['wifi']['intervalo_max_s'], 1)} s)",
         f"{out['wifi']['telemetrias']} mensagens"],
        ["MQTT direto da placa (heartbeat 20:17)", f"{mq['mqtt_direto']['conexoes']} conexões; {mq['mqtt_direto']['publicadas']} publicadas; "
         f"{mq['mqtt_direto']['descartadas']} descartadas", "desconectada após o reinício do servidor"],
    ]
    salvar_tabela("tab_desempenho_embarcado", ["Grandeza", "Valor", "Observação"], lin,
                  "Desempenho embarcado medido nos ensaios de 07/11/2026 (firmware posicao\\_degrau\\_v6, ESP32 a 240 MHz)",
                  "tab:desempenho_embarcado", colunas_tex="lll")
    return out


def base600():
    X, cond, ses, hdr = carregar()
    P = rede(X)
    y = np.array([COND_CLASSE[c] for c in cond]); p = P.argmax(1)
    out = {}
    for nome, mask in (("todas", np.ones(len(y), bool)), ("sessao5", ses == 5)):
        M = np.zeros((5, 5), int)
        for a, b in zip(y[mask], p[mask]):
            M[a, b] += 1
        out[nome] = dict(n=int(mask.sum()), acertos=int(np.trace(M)), matriz=M.tolist())
    # por sessao
    out["por_sessao"] = {int(s): float((y[ses == s] == p[ses == s]).mean()) for s in sorted(set(ses))}
    M = np.array(out["sessao5"]["matriz"])
    fig, ax = plt.subplots(figsize=(6.6, 3.9))
    norm = M / np.maximum(M.sum(1, keepdims=True), 1)
    ax.imshow(norm, cmap=CMAP_AZUL, vmin=0, vmax=1)
    for i in range(5):
        for j in range(5):
            if M[i, j]:
                ax.text(j, i, M[i, j], ha="center", va="center", color="white" if norm[i, j] > .55 else TEXTO, fontsize=9)
    ax.set_xticks(range(5), [c.replace(" ", "\n", 1) for c in CLASSES_CURTO], fontsize=8)
    ax.set_yticks(range(5), CLASSES_CURTO, fontsize=8)
    ax.grid(False)
    ax.set_xlabel("Classe prevista (modelo final)"); ax.set_ylabel("Classe verdadeira")
    ax.set_title(f"Base de 600, sessão 5: {np.trace(M)}/{M.sum()} com o modelo final", loc="left")
    for s in ax.spines.values():
        s.set_visible(False)
    salvar(fig, FIG / "fig_matriz_base600_sessao5")
    return out


def main():
    out = dict(continuo=continuo(), sistema=sistema(), base600=base600())
    fig_latencia()
    json.dump(out, open(SAIDA / "resumo_sistema.json", "w"), ensure_ascii=False, indent=1, default=float)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=float)[:4000])


if __name__ == "__main__":
    main()
