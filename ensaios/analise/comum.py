"""Leitura dos dados dos ensaios finais (07/11/2026) e utilitarios comuns."""
import csv
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base import rede, carregar, COND_CLASSE  # noqa: E402  (rede numpy = rede da placa)

# Pasta dos ensaios (guiado_P10/, guiado_P3/, registros/, prints/) e pasta de saida.
DADOS = Path(os.environ.get("TCC_ENSAIOS_DADOS", "."))
SAIDA = Path(os.environ.get("TCC_ENSAIOS_SAIDA", str(DADOS / "resultados")))
FIG, TAB, PRT = SAIDA / "figuras", SAIDA / "tabelas", SAIDA / "prints"

CLASSES = ["sem_evento", "liq_ligando", "liq_desligando", "micro_ligando", "micro_desligando"]
CLASSES_PT = ["Sem evento", "Liquidificador ligando", "Liquidificador desligando",
              "Micro-ondas ligando", "Micro-ondas desligando"]
CLASSES_CURTO = ["C0 sem evento", "C1 liq. liga", "C2 liq. desliga", "C3 micro liga", "C4 micro desliga"]
ROTULO_ID = {"sem_evento_alvo": 0, "liquidificador_ligando": 1, "liquidificador_desligando": 2,
             "microondas_ligando": 3, "microondas_desligando": 4}
# Condicoes 1..12: estados antes/depois [liquidificador, micro-ondas] (firmware estados.h)
ANTES = {1: (0, 0), 2: (1, 1), 3: (1, 0), 4: (0, 1), 5: (0, 0), 6: (1, 0), 7: (0, 0), 8: (0, 1),
         9: (0, 1), 10: (1, 1), 11: (1, 0), 12: (1, 1)}
DEPOIS = {1: (0, 0), 2: (1, 1), 3: (1, 0), 4: (0, 1), 5: (1, 0), 6: (0, 0), 7: (0, 1), 8: (0, 0),
          9: (1, 1), 10: (0, 1), 11: (1, 1), 12: (1, 0)}
NOME_COND = {1: "Ambos parados", 2: "Ambos funcionando", 3: "Só liquidificador", 4: "Só micro-ondas",
             5: "Liq. ligando (micro parado)", 6: "Liq. desligando (micro parado)",
             7: "Micro ligando (liq. parado)", 8: "Micro desligando (liq. parado)",
             9: "Liq. ligando (micro funcionando)", 10: "Liq. desligando (micro funcionando)",
             11: "Micro ligando (liq. funcionando)", 12: "Micro desligando (liq. funcionando)"}


def ler_guiado(pasta):
    """Registros de uma campanha guiada, com a entrada 25x106 lida do CSV."""
    pasta = DADOS / pasta
    c = json.load(open(pasta / "campanha.json", encoding="utf-8"))
    out = []
    for r in c["registros"]:
        linhas = list(csv.reader(open(pasta / r["csv"], encoding="utf-8")))
        X = np.array([[float(v) for v in row[1:]] for row in linhas[1:]], dtype=np.float32)
        out.append(dict(r, X=X))
    return c, out


def ler_registros():
    """Todas as linhas do coletor, em ordem de tempo."""
    L = []
    for f in sorted(glob.glob(str(DADOS / "registros" / "registro_*.jsonl"))):
        for linha in open(f, encoding="utf-8"):
            try:
                L.append(json.loads(linha))
            except ValueError:
                pass
    L.sort(key=lambda x: x["t_pc"])
    return L


def marcas(L):
    return {x["nome"]: x["t_pc"] for x in L if x["tipo"] == "marca"}


def ler_diagnostico(pasta):
    """Resultados com classe de evento registrados pelo programa de aquisicao (todas as janelas)."""
    out = []
    for linha in open(DADOS / pasta / "diagnostico.log", encoding="utf-8", errors="replace"):
        if "DIAGNOSTICO_PLACA" not in linha:
            continue
        try:
            j = json.loads(linha.split("DIAGNOSTICO_PLACA", 1)[1])
        except ValueError:
            continue
        a = j.get("atual", {})
        if a.get("tipo") != "resultado" or a.get("seq") is None:
            continue
        out.append(dict(boot=a.get("boot"), seq=a["seq"], classe=ROTULO_ID.get(a.get("classe"), -1),
                        conf=a.get("confianca"), margem=a.get("margem"), decisao=a.get("decisao"),
                        estados=a.get("estados"), irms=a.get("irms_adc")))
    return out


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - m, c + m)


def salvar_tabela(nome, cabecalho, linhas, legenda, rotulo, colunas_tex=None, nota=None):
    """Grava a tabela em CSV e em LaTeX (booktabs)."""
    with open(TAB / f"{nome}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cabecalho)
        w.writerows(linhas)
    def esc(s):
        s = str(s)
        for a, b in (("\\", "\\textbackslash{}"), ("%", "\\%"), ("&", "\\&"), ("_", "\\_"), ("#", "\\#")):
            s = s.replace(a, b)
        return s
    cols = colunas_tex or ("l" + "r" * (len(cabecalho) - 1))
    tex = ["\\begin{table}[htbp]", "\\centering", f"\\caption{{{legenda}}}", f"\\label{{{rotulo}}}",
           "\\small", f"\\begin{{tabular}}{{{cols}}}", "\\toprule",
           " & ".join(esc(c) for c in cabecalho) + " \\\\", "\\midrule"]
    tex += [" & ".join(esc(c) for c in l) + " \\\\" for l in linhas]
    tex += ["\\bottomrule", "\\end{tabular}"]
    if nota:
        tex += [f"\\par\\smallskip\\footnotesize {esc(nota)}"]
    tex += ["\\end{table}"]
    (TAB / f"{nome}.tex").write_text("\n".join(tex) + "\n", encoding="utf-8")


def fmt(x, n=1):
    return f"{x:.{n}f}".replace(".", ",")


def pct(k, n, d=1):
    return fmt(100 * k / n, d) + " %"
