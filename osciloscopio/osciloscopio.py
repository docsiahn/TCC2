import serial
import argparse
import time
from collections import deque

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# ==========================
# CONFIGURAÇÕES
# ==========================
parser = argparse.ArgumentParser(description="Osciloscopio GPIO36/39, ADC bruto")
parser.add_argument("porta", nargs="?")
args = parser.parse_args()
PORTA = args.porta or input("Porta do ESP32 (exemplo COM6): ").strip()
if not PORTA:
    raise SystemExit("Nenhuma porta informada.")
BAUD = 921600
FS = 1920

JANELA_SEGUNDOS = 0.25
N_PONTOS = int(FS * JANELA_SEGUNDOS)

# ==========================
# ABERTURA DA SERIAL
# ==========================
ser = serial.Serial(PORTA, BAUD, timeout=0.01)
time.sleep(2)

firmware_confirmado = False
buffer_t = deque(maxlen=N_PONTOS)
buffer_vp = deque(maxlen=N_PONTOS)
buffer_vn = deque(maxlen=N_PONTOS)

# ==========================
# FIGURA
# ==========================
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)

linha_vp_raw, = ax1.plot([], [], label="GPIO36 bruto (TP no coletor)")
linha_vn_raw, = ax1.plot([], [], label="GPIO39 bruto (TC no coletor)")

linha_vp_cent, = ax2.plot([], [], label="GPIO36 centralizado")
linha_vn_cent, = ax2.plot([], [], label="GPIO39 centralizado")

ax1.set_title("Osciloscopio GPIO36/39 - contagens ADC, sem calibracao em V/A")
ax1.set_ylabel("ADC bruto")
ax1.set_ylim(0, 4095)
ax1.grid(True)
ax1.legend(loc="upper right")

ax2.set_title("Sinais centralizados pela média da janela")
ax2.set_xlabel("Tempo (s)")
ax2.set_ylabel("ADC - média")
ax2.set_ylim(-2200, 2200)
ax2.grid(True)
ax2.legend(loc="upper right")

texto_info = ax1.text(
    0.01,
    0.95,
    "",
    transform=ax1.transAxes,
    verticalalignment="top"
)

def ler_serial():
    global firmware_confirmado
    while ser.in_waiting:
        linha = ser.readline().decode(errors="ignore").strip()

        if not linha:
            continue

        if linha.startswith("#OSC_GPIO36_39_V1,pinos=36/39,fs=1920"):
            firmware_confirmado = True
            continue
        if not firmware_confirmado:
            continue
        if "tempo" in linha or linha.startswith("#"):
            continue

        partes = linha.split(",")

        if len(partes) != 3:
            continue

        try:
            t_us = int(partes[0])
            vp = int(partes[1])
            vn = int(partes[2])
        except ValueError:
            continue

        buffer_t.append(t_us / 1_000_000)
        buffer_vp.append(vp)
        buffer_vn.append(vn)

def atualizar(frame):
    ler_serial()

    if not firmware_confirmado:
        texto_info.set_text("Aguardando firmware GPIO36/39. O firmware antigo 34/35 nao e aceito.")
    if len(buffer_t) < 10:
        return linha_vp_raw, linha_vn_raw, linha_vp_cent, linha_vn_cent, texto_info

    t = np.array(buffer_t)
    vp = np.array(buffer_vp)
    vn = np.array(buffer_vn)

    t = t - t[0]

    vp_media = np.mean(vp)
    vn_media = np.mean(vn)

    vp_cent = vp - vp_media
    vn_cent = vn - vn_media

    linha_vp_raw.set_data(t, vp)
    linha_vn_raw.set_data(t, vn)

    linha_vp_cent.set_data(t, vp_cent)
    linha_vn_cent.set_data(t, vn_cent)

    ax1.set_xlim(t[0], t[-1])
    ax2.set_xlim(t[0], t[-1])

    info = (
        f"GPIO36 min={vp.min()} max={vp.max()} media={vp_media:.1f} RMS_AC={np.sqrt(np.mean(vp_cent**2)):.2f}\n"
        f"GPIO39 min={vn.min()} max={vn.max()} media={vn_media:.1f} RMS_AC={np.sqrt(np.mean(vn_cent**2)):.2f}"
    )
    texto_info.set_text(info)

    return linha_vp_raw, linha_vn_raw, linha_vp_cent, linha_vn_cent, texto_info

ani = FuncAnimation(fig, atualizar, interval=30, blit=False)

plt.tight_layout()
plt.show()

ser.close()