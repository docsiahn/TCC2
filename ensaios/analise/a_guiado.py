"""Ensaios guiados (modo de aquisicao): P10 (bloco A, 36 capturas) e P3 (bloco D, 12 capturas)."""
import json

import numpy as np

from comum import (ANTES, CLASSES_CURTO, COND_CLASSE, DEPOIS, FIG, NOME_COND, SAIDA, carregar, fmt,
                   ler_guiado, pct, rede, salvar_tabela, wilson)
from estilo import CMAP_AZUL, SERIE, TEXTO, TEXTO2, plt, salvar
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from sim_treino import posicao  # mesmo estimador do firmware (ponto de mudanca)

R = {}


def metricas(reg):
    y = np.array([r["classe_esperada"] for r in reg])
    p = np.array([r["resultado"]["classe_id"] for r in reg])
    M = np.zeros((5, 5), int)
    for a, b in zip(y, p):
        M[a, b] += 1
    por_classe = []
    for k in range(5):
        tp = M[k, k]
        prec = tp / M[:, k].sum() if M[:, k].sum() else float("nan")
        rev = tp / M[k, :].sum() if M[k, :].sum() else float("nan")
        f1 = 2 * prec * rev / (prec + rev) if prec + rev else 0.0
        por_classe.append(dict(classe=k, suporte=int(M[k, :].sum()), precisao=prec, revocacao=rev, f1=f1))
    acc = np.trace(M) / M.sum()
    M12 = np.zeros((12, 5), int)
    for r in reg:
        M12[r["cenario"] - 1, r["resultado"]["classe_id"]] += 1
    estados_ok = sum(tuple(r["resultado"]["estados"]) == DEPOIS[r["cenario"]] for r in reg)
    return M, M12, por_classe, acc, estados_ok


def equivalencia(reg):
    difs, iguais = [], 0
    for r in reg:
        p_pc = rede(r["X"][None])[0]
        p_pl = np.array(r["resultado"]["probabilidades"], dtype=np.float64)
        difs.append(float(np.abs(p_pc - p_pl).max()))
        iguais += int(np.argmax(p_pc) == r["resultado"]["classe_id"])
    return max(difs), iguais


def fig_matriz(M, titulo, nome, linhas=None):
    n_l = M.shape[0]
    fig, ax = plt.subplots(figsize=(5.6, 0.42 * n_l + 1.6))
    norm = M / np.maximum(M.sum(1, keepdims=True), 1)
    ax.imshow(norm, cmap=CMAP_AZUL, vmin=0, vmax=1, aspect="auto")
    for i in range(n_l):
        for j in range(5):
            if M[i, j]:
                ax.text(j, i, str(M[i, j]), ha="center", va="center", fontsize=9,
                        color="white" if norm[i, j] > 0.55 else TEXTO, fontweight="bold" if i == j or (linhas and j == COND_CLASSE[i + 1]) else "normal")
    ax.set_xticks(range(5), [c.replace(" ", "\n", 1) for c in CLASSES_CURTO], fontsize=8)
    ax.set_yticks(range(n_l), linhas or CLASSES_CURTO, fontsize=8)
    ax.set_xlabel("Classe prevista pela placa")
    ax.set_ylabel("Condição do ensaio" if linhas else "Classe verdadeira")
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks(np.arange(-.5, 5), minor=True)
    ax.set_yticks(np.arange(-.5, n_l), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", length=0)
    from matplotlib.patches import Rectangle
    from estilo import CRITICO
    for i in range(n_l):
        for j in range(5):
            certo = (j == COND_CLASSE[i + 1]) if linhas else (i == j)
            if M[i, j] and not certo:
                # erro: fundo vermelho claro + borda, para nao confundir com acerto
                ax.add_patch(Rectangle((j - .5, i - .5), 1, 1, facecolor="#f8dcdc", edgecolor=CRITICO, linewidth=1.6, zorder=2))
                ax.text(j, i, str(M[i, j]), ha="center", va="center", fontsize=9, color=TEXTO, fontweight="bold", zorder=3)
    ax.set_title(titulo, loc="left")
    salvar(fig, FIG / nome)


def tabela_por_classe(pc, M, acc, n, nome, legenda, rotulo):
    linhas = []
    for d in pc:
        linhas.append([CLASSES_CURTO[d["classe"]], d["suporte"],
                       "—" if np.isnan(d["precisao"]) else fmt(100 * d["precisao"], 1),
                       fmt(100 * d["revocacao"], 1), fmt(100 * d["f1"], 1)])
    macro = np.nanmean([d["f1"] for d in pc])
    lo, hi = wilson(int(np.trace(M)), n)
    linhas.append(["Total", n, "", "", ""])
    salvar_tabela(nome, ["Classe", "Capturas", "Precisão (%)", "Revocação (%)", "F1 (%)"], linhas, legenda, rotulo,
                  colunas_tex="lrrrr",
                  nota=f"Acurácia {fmt(100*acc,1)} % ({int(np.trace(M))}/{n}; intervalo de confiança de 95 % de Wilson: "
                       f"{fmt(100*lo,1)} % a {fmt(100*hi,1)} %). F1 macro: {fmt(100*macro,1)} %.")
    return macro, (lo, hi)


def perfis(reg_p10, reg_p3):
    """IRMS mediano por janela: ensaio P10 de hoje x base de treino (P3)."""
    Xtr, cond, ses, hdr = carregar()
    fig, axs = plt.subplots(3, 4, figsize=(10, 6.4), sharex=True)
    for c in range(1, 13):
        ax = axs[(c - 1) // 4, (c - 1) % 4]
        tr = np.median(Xtr[cond == c][:, :, 1], axis=0)
        q1, q3 = np.percentile(Xtr[cond == c][:, :, 1], [25, 75], axis=0)
        ax.fill_between(range(25), q1, q3, color=SERIE[1], alpha=0.18, linewidth=0)
        ax.plot(range(25), tr, color=SERIE[1], label="Treino (P3, n=50)")
        hoje = [r["X"][:, 1] for r in reg_p10 if r["cenario"] == c]
        for i, h in enumerate(hoje):
            ax.plot(range(25), h, color=SERIE[0], linewidth=1.2, alpha=0.9, label="Ensaio P10 (07/11)" if i == 0 else None)
        p3 = [r["X"][:, 1] for r in reg_p3 if r["cenario"] == c]
        for h in p3:
            ax.plot(range(25), h, color=SERIE[2], linewidth=1.2, linestyle="--", label="Ensaio P3 (07/11)")
        ax.axvline(10, color=TEXTO2, linewidth=0.8, linestyle=":")
        ax.set_title(f"{c}: {NOME_COND[c]}", fontsize=8, loc="left")
        ax.set_ylim(0, 620)
        if (c - 1) % 4 == 0:
            ax.set_ylabel("IRMS (contagens)")
        if c > 8:
            ax.set_xlabel("Janela (200 ms)")
    h, l = axs[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    salvar(fig, FIG / "fig_perfis_irms_condicoes")


def fase_p3(reg_p3):
    """Descreve o que a corrente fez em cada captura P3 (para separar erros pela fase do magnetron)."""
    out = []
    for r in reg_p3:
        x = r["X"][:, 1]
        dif = np.diff(x)
        grandes = [(int(i + 1), round(float(d))) for i, d in enumerate(dif) if abs(d) > 60]
        out.append(dict(cond=r["cenario"], esperado=COND_CLASSE[r["cenario"]], previsto=r["resultado"]["classe_id"],
                        conf=round(r["resultado"]["confianca"], 3), irms_ini=round(float(np.median(x[:5]))),
                        irms_fim=round(float(np.median(x[-5:]))), degraus=grandes, posicao=posicao(x)))
    return out


def main():
    _, p10 = ler_guiado("guiado_P10")
    _, p3 = ler_guiado("guiado_P3")
    for nome, reg, titulo in (("P10", p10, "Ensaio guiado em P10 (36 capturas)"), ("P3", p3, "Ensaio guiado em P3 (12 capturas)")):
        M, M12, pc, acc, est = metricas(reg)
        dif, iguais = equivalencia(reg)
        inf = np.array([r["resultado"]["inferencia_ms"] for r in reg])
        atr = np.array([r["resultado"]["atraso_ms"] for r in reg])
        conf = np.array([r["resultado"]["confianca"] for r in reg])
        sat = sum(bool((r.get("saturacao_adc") or {}).get("janelas_i")) for r in reg)
        fig_matriz(M, titulo, f"fig_matriz_guiado_{nome}")
        fig_matriz(M12, titulo + " — por condição", f"fig_matriz_condicoes_{nome}",
                   linhas=[f"{c:2d} {NOME_COND[c]}" for c in range(1, 13)])
        macro, ic = tabela_por_classe(pc, M, acc, len(reg), f"tab_guiado_{nome}_por_classe",
                                      f"Desempenho por classe no ensaio guiado em {nome} (07/11/2026, firmware posicao\\_degrau\\_v6)",
                                      f"tab:guiado_{nome.lower()}_classe")
        erros = [dict(cond=r["cenario"], rep=r["repeticao"], esperado=r["classe_esperada"], previsto=r["resultado"]["classe_id"],
                      conf=round(r["resultado"]["confianca"], 3), estados=r["resultado"]["estados"])
                 for r in reg if r["classe_esperada"] != r["resultado"]["classe_id"]]
        R[nome] = dict(n=len(reg), acertos=int(np.trace(M)), acuracia=acc, ic95=ic, macro_f1=macro,
                       estados_corretos=int(est), matriz=M.tolist(), matriz_cond=M12.tolist(),
                       por_classe=pc, erros=erros, max_dif_prob_pc_placa=dif, classe_pc_igual=iguais,
                       inferencia_ms=dict(mediana=float(np.median(inf)), min=float(inf.min()), max=float(inf.max())),
                       atraso_ms=dict(mediana=float(np.median(atr)), p95=float(np.percentile(atr, 95)), max=float(atr.max())),
                       confianca=dict(mediana=float(np.median(conf)), min=float(conf.min()),
                                      acertos_min=float(min(c for c, r in zip(conf, reg) if r["classe_esperada"] == r["resultado"]["classe_id"]))),
                       capturas_com_saturacao=int(sat),
                       posicao_degrau=[int(posicao(r["X"][:, 1])) for r in reg if COND_CLASSE[r["cenario"]] > 0])
    R["P3_fases"] = fase_p3(p3)
    perfis(p10, p3)
    # Tabela-resumo dos ensaios guiados (hoje e anteriores, da monografia)
    linhas = [
        ["Ensaio físico final P10 (15/10/2026)", 24, "24", "100,0 %", "24/24", "monografia"],
        ["Ensaio P3 (12/10/2026)", 80, "67", "83,8 %", "73/80", "monografia"],
        ["Guiado P10 (07/11/2026)", R["P10"]["n"], R["P10"]["acertos"], pct(R["P10"]["acertos"], R["P10"]["n"]),
         f'{R["P10"]["estados_corretos"]}/{R["P10"]["n"]}', "este ensaio"],
        ["Guiado P3 (07/11/2026)", R["P3"]["n"], R["P3"]["acertos"], pct(R["P3"]["acertos"], R["P3"]["n"]),
         f'{R["P3"]["estados_corretos"]}/{R["P3"]["n"]}', "este ensaio"],
    ]
    salvar_tabela("tab_guiado_resumo", ["Ensaio", "Capturas", "Acertos", "Acurácia", "Estados corretos", "Fonte"], linhas,
                  "Ensaios guiados: resultados de 07/11/2026 ao lado dos ensaios anteriores", "tab:guiado_resumo",
                  colunas_tex="lrrrrl")
    json.dump(R, open(SAIDA / "resumo_guiado.json", "w"), ensure_ascii=False, indent=1, default=float)
    print(json.dumps({k: (v if k == "P3_fases" else {kk: vv for kk, vv in v.items() if kk not in ("matriz_cond", "por_classe")})
                      for k, v in R.items()}, ensure_ascii=False, indent=1, default=float)[:6000])


if __name__ == "__main__":
    main()
