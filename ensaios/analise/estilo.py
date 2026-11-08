"""Estilo das figuras (paleta de referencia da skill de visualizacao, modo claro, para impressao)."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

SERIE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
TEXTO, TEXTO2, TEXTO3 = "#0b0b0b", "#52514e", "#8a8984"
GRADE, SUPERFICIE = "#e4e3df", "#ffffff"
BOM, CRITICO, ATENCAO = "#0ca30c", "#d03b3b", "#fab219"
AZUL_SEQ = ["#ffffff", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
CMAP_AZUL = LinearSegmentedColormap.from_list("azul", AZUL_SEQ)

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
    "axes.edgecolor": TEXTO3, "axes.labelcolor": TEXTO2, "axes.titlecolor": TEXTO,
    "xtick.color": TEXTO2, "ytick.color": TEXTO2, "axes.grid": True, "grid.color": GRADE,
    "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "axes.axisbelow": True, "legend.frameon": False, "figure.dpi": 100, "savefig.dpi": 300,
    "savefig.bbox": "tight", "lines.linewidth": 2.0, "axes.facecolor": SUPERFICIE,
    "figure.facecolor": SUPERFICIE, "pdf.fonttype": 42,
})


def salvar(fig, caminho_sem_ext):
    fig.savefig(str(caminho_sem_ext) + ".png")
    fig.savefig(str(caminho_sem_ext) + ".pdf")
    plt.close(fig)


def virgula(eixo, casas=1):
    """Separador decimal com virgula (portugues)."""
    from matplotlib.ticker import FuncFormatter
    eixo.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.{casas}f}".replace(".", ",")))
