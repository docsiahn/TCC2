import serial
import time
from collections import deque

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# ==========================
# CONFIGURAÇÕES
# ==========================
PORTA = "COM8"       # Troque para a porta do seu ESP32
BAUD = 921600
FS = 1920

JANELA_SEGUNDOS = 0.25
N_PONTOS = int(FS * JANELA_SEGUNDOS)

# ==========================
# ABERTURA DA SERIAL
# ==========================
ser = serial.Serial(PORTA, BAUD, timeout=0.01)
time.sleep(2)

buffer_t = deque(maxlen=N_PONTOS)
buffer_vp = deque(maxlen=N_PONTOS)
buffer_vn = deque(maxlen=N_PONTOS)

# ==========================
# FIGURA
# ==========================
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)

linha_vp_raw, = ax1.plot([], [], label="VP bruto")
linha_vn_raw, = ax1.plot([], [], label="VN bruto")

linha_vp_cent, = ax2.plot([], [], label="VP centralizado")
linha_vn_cent, = ax2.plot([], [], label="VN centralizado")

ax1.set_title("Osciloscópio serial - leituras brutas do ADC")
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
    while ser.in_waiting:
        linha = ser.readline().decode(errors="ignore").strip()

        if not linha:
            continue

        if "tempo" in linha or "VP" in linha:
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
        f"VP min={vp.min()} max={vp.max()} média={vp_media:.1f}\n"
        f"VN min={vn.min()} max={vn.max()} média={vn_media:.1f}"
    )
    texto_info.set_text(info)

    return linha_vp_raw, linha_vn_raw, linha_vp_cent, linha_vn_cent, texto_info

ani = FuncAnimation(fig, atualizar, interval=30, blit=False)

plt.tight_layout()
plt.show()

ser.close()