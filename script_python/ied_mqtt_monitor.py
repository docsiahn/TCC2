r"""
Monitor MQTT para o IED com ESP32.

Funcionalidades:
1) Osciloscopio MQTT em tempo real
   - Recebe ied/osc/batch
   - Mostra VP e VN brutos
   - Mostra VP e VN centralizados pela media da janela

2) Features MQTT
   - Recebe ied/features/ciclo
   - Salva CSV
   - Gera um unico dashboard PNG

Uso:
    python .\script_python\ied_mqtt_monitor.py --host localhost

Modo osciloscopio:
    python .\script_python\ied_mqtt_monitor.py --host localhost --mode 1

Modo features:
    python .\script_python\ied_mqtt_monitor.py --host localhost --mode 2

Modo automatico:
    python .\script_python\ied_mqtt_monitor.py --host localhost --mode 3
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Deque, Dict, List, Optional

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import numpy as np
import pandas as pd
import paho.mqtt.client as mqtt


TOPIC_DEFAULT = "ied/#"


@dataclass
class Config:
    host: str
    port: int
    topic: str
    client_id: str
    mode: str
    output_dir: Path
    expected_rows: int
    janela_segundos: float
    fs: int
    plot: bool


class EstadoIED:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)

        self.lock = Lock()

        self.batch_id: Optional[str] = None
        self.features: List[Dict[str, Any]] = []

        self.n_pontos = max(20, int(cfg.fs * cfg.janela_segundos))

        self.buffer_t: Deque[float] = deque(maxlen=self.n_pontos)
        self.buffer_vp: Deque[float] = deque(maxlen=self.n_pontos)
        self.buffer_vn: Deque[float] = deque(maxlen=self.n_pontos)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.raw_path = self.cfg.output_dir / f"mqtt_raw_{stamp}.jsonl"
        self.raw_file = self.raw_path.open("a", encoding="utf-8")

        print(f"Log bruto MQTT: {self.raw_path}")

    def fechar(self) -> None:
        self.raw_file.close()

    def salvar_mensagem_bruta(self, topic: str, payload_text: str) -> None:
        registro = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "topic": topic,
            "payload": payload_text,
        }

        self.raw_file.write(json.dumps(registro, ensure_ascii=False) + "\n")
        self.raw_file.flush()

    # =================================================
    # OSCILOSCÓPIO
    # =================================================

    def adicionar_batch_osciloscopio(self, payload: Dict[str, Any]) -> None:
        vp = payload.get("vp", [])
        vn = payload.get("vn", [])

        t0_us = int(payload.get("t0_us", 0))
        dt_us = int(payload.get("dt_us", 1000000 // self.cfg.fs))

        n = min(len(vp), len(vn))

        with self.lock:
            for i in range(n):
                t_s = (t0_us + i * dt_us) / 1_000_000.0

                self.buffer_t.append(t_s)
                self.buffer_vp.append(float(vp[i]))
                self.buffer_vn.append(float(vn[i]))

    # =================================================
    # FEATURES
    # =================================================

    def iniciar_batch_features(self, payload: Dict[str, Any]) -> None:
        self.batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.features = []

        print()
        print("Inicio de pacote de features recebido.")
        print(f"Batch: {self.batch_id}")
        print(f"Payload: {payload}")

    def adicionar_feature(self, payload: Dict[str, Any]) -> None:
        if self.batch_id is None:
            self.batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.features.append(payload)

        n = len(self.features)

        if n == 1 or n % 25 == 0:
            print(f"Features recebidas: {n}/{self.cfg.expected_rows}")

    def mostrar_resumo_mqtt(self, payload: Dict[str, Any]) -> None:
        print()
        print("Resumo recebido via MQTT:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    def finalizar_batch_features(self, payload: Dict[str, Any]) -> None:
        print()
        print("Fim de pacote de features recebido.")
        print(f"Payload: {payload}")

        if not self.features:
            print("Nenhuma feature foi acumulada.")
            return

        self.salvar_csv_e_dashboard()

        self.batch_id = None
        self.features = []

    def salvar_csv_e_dashboard(self) -> None:
        df = pd.DataFrame(self.features)

        if df.empty:
            print("DataFrame vazio. Nada a salvar.")
            return

        if "ciclo" not in df.columns:
            df.insert(0, "ciclo", np.arange(len(df)))

        if "tempo_s" not in df.columns:
            df.insert(1, "tempo_s", df["ciclo"] / 60.0)

        batch = self.batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")

        csv_path = self.cfg.output_dir / f"features_mqtt_{batch}.csv"
        df.to_csv(csv_path, index=False)

        print(f"CSV salvo em: {csv_path}")

        print()
        print("Resumo estatistico:")

        cols = [c for c in df.columns if c not in ("ciclo", "tempo_s")]

        if cols:
            resumo = df[cols].describe().T[["mean", "std", "min", "max"]]
            print(resumo)

        if self.cfg.plot:
            gerar_dashboard_unico(df, self.cfg.output_dir, batch)


# =====================================================
# FUNÇÕES AUXILIARES
# =====================================================

def escolher_modo_interativo() -> str:
    print()
    print("Escolha o modo MQTT:")
    print("  1 - Osciloscopio MQTT em tempo real")
    print("  2 - Features MQTT com CSV e dashboard")
    print("  3 - Automatico / escutar tudo")

    while True:
        escolha = input("Opcao [1/2/3]: ").strip()

        if escolha == "1":
            return "osc"

        if escolha == "2":
            return "features"

        if escolha == "3":
            return "auto"

        print("Opcao invalida. Digite 1, 2 ou 3.")


def normalizar_modo(mode: str) -> str:
    mode = mode.lower().strip()

    if mode in ("1", "osc", "osciloscopio", "oscilloscope"):
        return "osc"

    if mode in ("2", "features", "feature"):
        return "features"

    if mode in ("3", "auto", "automatico"):
        return "auto"

    if mode == "menu":
        return "menu"

    return mode


def criar_cliente(client_id: str):
    try:
        return mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
    except Exception:
        return mqtt.Client(client_id=client_id)


def payload_para_json(payload_bytes: bytes) -> tuple[str, Optional[Dict[str, Any]]]:
    payload_text = payload_bytes.decode("utf-8", errors="replace")

    try:
        payload_json = json.loads(payload_text)
    except json.JSONDecodeError:
        payload_json = None

    return payload_text, payload_json


# =====================================================
# CALLBACKS MQTT
# =====================================================

def on_connect(client, userdata, flags, reason_code, properties=None):
    cfg: Config = userdata["cfg"]

    print()
    print("Conectado ao broker MQTT.")
    print(f"Assinando topico: {cfg.topic}")

    client.subscribe(cfg.topic)


def on_disconnect(client, userdata, *args):
    print()
    print("Desconectado do broker MQTT.")


def on_message(client, userdata, msg):
    estado: EstadoIED = userdata["estado"]
    cfg: Config = userdata["cfg"]

    payload_text, payload_json = payload_para_json(msg.payload)

    estado.salvar_mensagem_bruta(msg.topic, payload_text)

    if payload_json is None:
        print(f"{msg.topic}: {payload_text}")
        return

    # -------------------------
    # OSCILOSCÓPIO MQTT
    # -------------------------

    if msg.topic == "ied/osc/inicio":
        print(f"{msg.topic}: {json.dumps(payload_json, ensure_ascii=False)}")
        return

    if msg.topic == "ied/osc/batch":
        if cfg.mode in ("osc", "auto"):
            estado.adicionar_batch_osciloscopio(payload_json)
        return

    if msg.topic == "ied/osc/fim":
        print(f"{msg.topic}: {json.dumps(payload_json, ensure_ascii=False)}")
        return

    # -------------------------
    # FEATURES MQTT
    # -------------------------

    if msg.topic == "ied/features/inicio":
        if cfg.mode in ("features", "auto"):
            estado.iniciar_batch_features(payload_json)
        return

    if msg.topic == "ied/features/ciclo":
        if cfg.mode in ("features", "auto"):
            estado.adicionar_feature(payload_json)
        return

    if msg.topic == "ied/features/resumo":
        if cfg.mode in ("features", "auto"):
            estado.mostrar_resumo_mqtt(payload_json)
        return

    if msg.topic == "ied/features/fim":
        if cfg.mode in ("features", "auto"):
            estado.finalizar_batch_features(payload_json)
        return

    # -------------------------
    # STATUS E EVENTOS
    # -------------------------

    if msg.topic in ("ied/status", "ied/eventos"):
        print(f"{msg.topic}: {json.dumps(payload_json, ensure_ascii=False)}")
        return

    print(f"{msg.topic}: {json.dumps(payload_json, ensure_ascii=False)}")


# =====================================================
# OSCILOSCÓPIO MQTT
# =====================================================

def abrir_osciloscopio_mqtt(estado: EstadoIED) -> None:
    print()
    print("Osciloscopio MQTT em tempo real.")
    print("No ESP32, de duplo clique no botao para iniciar/parar o osciloscopio.")
    print("Canal superior: tensao VP.")
    print("Canal inferior: corrente VN.")
    print("Feche a janela do grafico para sair.")
    print()

    fig, (ax_v, ax_i) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)

    linha_v, = ax_v.plot([], [], label="Tensao - VP")
    linha_i, = ax_i.plot([], [], label="Corrente - VN")

    ax_v.set_title("Canal de tensao - VP")
    ax_v.set_ylabel("ADC")
    ax_v.set_ylim(0, 4095)
    ax_v.grid(True)
    ax_v.legend(loc="upper right")

    ax_i.set_title("Canal de corrente - VN")
    ax_i.set_xlabel("Tempo (s)")
    ax_i.set_ylabel("ADC")
    ax_i.set_ylim(0, 4095)
    ax_i.grid(True)
    ax_i.legend(loc="upper right")

    texto_info = ax_v.text(
        0.01,
        0.95,
        "",
        transform=ax_v.transAxes,
        verticalalignment="top",
    )

    def atualizar(frame):
        with estado.lock:
            if len(estado.buffer_t) < 10:
                return linha_v, linha_i, texto_info

            t = np.array(estado.buffer_t, dtype=float)
            vp = np.array(estado.buffer_vp, dtype=float)
            vn = np.array(estado.buffer_vn, dtype=float)

        t = t - t[0]

        linha_v.set_data(t, vp)
        linha_i.set_data(t, vn)

        ax_v.set_xlim(t[0], t[-1])
        ax_i.set_xlim(t[0], t[-1])

        info = (
            f"VP min={vp.min():.0f} max={vp.max():.0f} media={vp.mean():.1f}\n"
            f"VN min={vn.min():.0f} max={vn.max():.0f} media={vn.mean():.1f}"
        )

        texto_info.set_text(info)

        return linha_v, linha_i, texto_info

    ani = FuncAnimation(fig, atualizar, interval=30, blit=False)

    plt.tight_layout()
    plt.show()

    _ = ani


# =====================================================
# DASHBOARD DAS FEATURES
# =====================================================

def plotar_linhas(ax, df: pd.DataFrame, cols: List[str], titulo: str, ylabel: str) -> bool:
    cols = [c for c in cols if c in df.columns]

    if not cols:
        ax.axis("off")
        return False

    x = df["tempo_s"] if "tempo_s" in df.columns else df.index

    for col in cols:
        ax.plot(x, df[col], label=col)

    ax.set_title(titulo)
    ax.set_xlabel("Tempo (s)")
    ax.set_ylabel(ylabel)
    ax.grid(True)
    ax.legend(loc="best")

    return True


def gerar_dashboard_unico(df: pd.DataFrame, output_dir: Path, batch: str) -> None:
    print()
    print("Gerando dashboard unico com os graficos...")

    fig, axs = plt.subplots(4, 2, figsize=(16, 18))
    axs = axs.ravel()

    plotar_linhas(
        axs[0],
        df,
        ["vrms"],
        "Tensao eficaz",
        "V_RMS",
    )

    plotar_linhas(
        axs[1],
        df,
        ["irms"],
        "Corrente eficaz",
        "I_RMS",
    )

    plotar_linhas(
        axs[2],
        df,
        ["vrms", "irms"],
        "Valores eficazes",
        "RMS",
    )

    plotar_linhas(
        axs[3],
        df,
        ["vfund_rms", "ifund_rms"],
        "Componentes fundamentais pela FFT",
        "RMS da fundamental",
    )

    plotar_linhas(
        axs[4],
        df,
        ["thdv", "thdi"],
        "Distorcao harmonica total",
        "THD (%)",
    )

    plotar_linhas(
        axs[5],
        df,
        ["p_ativa", "s_aparente"],
        "Potencias calculadas",
        "Potencia",
    )

    plotar_linhas(
        axs[6],
        df,
        ["fp"],
        "Fator de potencia verdadeiro",
        "FP",
    )

    cols_numericas = [
        c for c in df.columns
        if c not in ("ciclo", "tempo_s")
        and pd.api.types.is_numeric_dtype(df[c])
    ]

    if cols_numericas:
        medias = df[cols_numericas].mean().sort_values(ascending=False)

        axs[7].bar(medias.index, medias.values)
        axs[7].set_title("Media das features na janela de 5 segundos")
        axs[7].set_xlabel("Feature")
        axs[7].set_ylabel("Media")
        axs[7].tick_params(axis="x", rotation=45)
        axs[7].grid(True, axis="y")
    else:
        axs[7].axis("off")

    fig.suptitle("Dashboard de features do IED via MQTT", fontsize=16)
    fig.tight_layout(rect=[0, 0, 1, 0.97])

    path = output_dir / f"dashboard_features_{batch}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")

    print(f"Dashboard salvo em: {path}")


# =====================================================
# MAIN
# =====================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Monitor MQTT para o projeto IED"
    )

    parser.add_argument(
        "--host",
        default="localhost",
        help="Endereco do broker MQTT. Exemplo: localhost ou 10.233.205.43",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=1883,
        help="Porta MQTT. Padrao: 1883",
    )

    parser.add_argument(
        "--topic",
        default=TOPIC_DEFAULT,
        help="Topico para assinar. Padrao: ied/#",
    )

    parser.add_argument(
        "--client-id",
        default="python-monitor-ied",
        help="Client ID MQTT",
    )

    parser.add_argument(
        "--mode",
        default="menu",
        help="Modo: 1/osc, 2/features, 3/auto ou menu",
    )

    parser.add_argument(
        "--output-dir",
        default="dados_ied_mqtt",
        help="Pasta para salvar CSV, JSONL e dashboard",
    )

    parser.add_argument(
        "--expected-rows",
        type=int,
        default=300,
        help="Quantidade esperada de ciclos/features",
    )

    parser.add_argument(
        "--janela",
        type=float,
        default=0.25,
        help="Janela do osciloscopio em segundos",
    )

    parser.add_argument(
        "--fs",
        type=int,
        default=1920,
        help="Frequencia de amostragem esperada",
    )

    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Nao gerar dashboard",
    )

    args = parser.parse_args()

    mode = normalizar_modo(args.mode)

    if mode == "menu":
        mode = escolher_modo_interativo()

    if mode not in ("osc", "features", "auto"):
        print("Modo invalido. Use --mode 1, --mode 2, --mode 3 ou --mode auto.")
        return 1

    cfg = Config(
        host=args.host,
        port=args.port,
        topic=args.topic,
        client_id=args.client_id,
        mode=mode,
        output_dir=Path(args.output_dir),
        expected_rows=args.expected_rows,
        janela_segundos=args.janela,
        fs=args.fs,
        plot=not args.no_plot,
    )

    estado = EstadoIED(cfg)

    client = criar_cliente(cfg.client_id)

    client.user_data_set(
        {
            "estado": estado,
            "cfg": cfg,
        }
    )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    try:
        print(f"Conectando em {cfg.host}:{cfg.port}...")
        client.connect(cfg.host, cfg.port, keepalive=60)

        if cfg.mode == "osc":
            client.loop_start()
            abrir_osciloscopio_mqtt(estado)
            client.loop_stop()
        else:
            client.loop_forever()

    except KeyboardInterrupt:
        print()
        print("Monitor MQTT encerrado pelo usuario.")

    except Exception as exc:
        print(f"Erro no monitor MQTT: {exc}")
        return 1

    finally:
        estado.fechar()

    return 0


if __name__ == "__main__":
    sys.exit(main())