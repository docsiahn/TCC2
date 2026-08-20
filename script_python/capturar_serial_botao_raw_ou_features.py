#!/usr/bin/env python3
"""Captura raw data ou features selecionadas pelo botao do ESP32.

Uso no Windows:
    py -m pip install pyserial
    py capturar_serial_botao_raw_ou_features.py COM5
    py capturar_serial_botao_raw_ou_features.py COM5 --prefixo coleta_teste

O arquivo gerado depende do comando no botao:
    1 clique: <prefixo>_raw.csv
    2 cliques: <prefixo>_features.csv

O Monitor Serial da Arduino IDE deve permanecer fechado.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
import sys
import time


MARCADOR_INICIO = b"BEGIN_COLETA_BOTAO_V4"
MARCADOR_FEATURES = b"FEATURES_BEGIN"
CABECALHO_FEATURES = (
    b"tipo,janela,canal,harmonico,frequencia_nominal_hz,valor,unidade"
)
MARCADOR_RAW = b"RAW_BEGIN"
CABECALHO_RAW = b"indice,tensao_raw_gpio36,corrente_raw_gpio39"
MARCADOR_FIM = b"END_COLETA_BOTAO_V4"

BAUD_PADRAO = 460_800
TAMANHO_BLOCO = 65_536
AMOSTRAS_ESPERADAS = 51_200
JANELAS_ESPERADAS = 25
HARMONICA_MAXIMA = 50
CANAIS_ESPERADOS = {"TENSAO", "CORRENTE"}
FEATURES_ESPERADAS = 2_704

FNV_OFFSET_BASIS = 2_166_136_261
FNV_PRIME = 16_777_619

CABECALHO_CSV_RAW = (
    "tipo",
    "indice",
    "tempo_evento_us",
    "tempo_tensao_estimado_us",
    "tempo_corrente_estimado_us",
    "tensao_raw_gpio36",
    "corrente_raw_gpio39",
    "metrica",
    "valor",
    "unidade",
    "observacao",
)

CABECALHO_CSV_FEATURES = (
    "tipo",
    "janela",
    "inicio_janela_estimado_us",
    "canal",
    "harmonico",
    "frequencia_nominal_hz",
    "valor",
    "unidade",
    "metrica",
    "observacao",
)


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recebe do ESP32 raw data (1 clique) ou VRMS/IRMS, "
            "VFFT/IFFT e VTHD50/ITHD50 (2 cliques), valida a secao "
            "selecionada e gera o CSV correspondente."
        )
    )
    parser.add_argument(
        "porta",
        help="Porta do ESP32, por exemplo COM5 ou /dev/ttyUSB0.",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=BAUD_PADRAO,
        help=f"Velocidade Serial; padrao: {BAUD_PADRAO}.",
    )
    parser.add_argument(
        "--prefixo",
        type=Path,
        help=(
            "Prefixo dos dois CSVs. Se omitido, usa a data e a hora atuais."
        ),
    )
    parser.add_argument(
        "--timeout-inatividade",
        type=float,
        default=15.0,
        help="Segundos sem bytes antes de interromper; padrao: 15.",
    )
    return parser.parse_args()


def fnv1a_bytes(checksum: int, dados: bytes) -> int:
    for byte in dados:
        checksum ^= byte
        checksum = (checksum * FNV_PRIME) & 0xFFFF_FFFF
    return checksum


def fnv1a_amostra(checksum: int, indice: int, tensao: int, corrente: int) -> int:
    dados = (
        indice.to_bytes(4, "little")
        + tensao.to_bytes(2, "little")
        + corrente.to_bytes(2, "little")
    )
    return fnv1a_bytes(checksum, dados)


@dataclass(frozen=True)
class Feature:
    tipo: str
    janela: int | None
    canal: str
    harmonico: int | None
    frequencia_nominal_hz: int | None
    valor_texto: str
    valor: float
    unidade: str


class Decodificador:
    def __init__(self) -> None:
        self.iniciou = False
        self.secao = "antes"
        self.cabecalho_features_recebido = False
        self.features_end_recebido = False
        self.cabecalho_raw_recebido = False
        self.raw_end_recebido = False
        self.terminou = False

        self.metadados: dict[str, tuple[str, str, str]] = {}
        self.ordem_metadados: list[str] = []
        self.features: list[Feature] = []
        self.raw: list[tuple[int, int, int]] = []
        self.proximo_indice_esperado = 0

        self.checksum_metadados_calculado = FNV_OFFSET_BASIS
        self.checksum_metadados_informado: int | None = None
        self.meta_end_recebido = False

        self.checksum_features_calculado = FNV_OFFSET_BASIS
        self.checksum_features_informado: int | None = None
        self.quantidade_features_informada: int | None = None
        self.tempo_calculo_features_us: int | None = None

        self.checksum_raw_calculado = FNV_OFFSET_BASIS
        self.checksum_raw_informado: int | None = None
        self.quantidade_raw_informada: int | None = None

        self.erros_comuns: list[str] = []
        self.erros_features: list[str] = []
        self.erros_raw: list[str] = []
        self.total_erros = {"comum": 0, "features": 0, "raw": 0}
        self.bruto = bytearray()

    def registrar_erro(self, mensagem: str, escopo: str | None = None) -> None:
        if escopo is None:
            if self.secao == "features":
                escopo = "features"
            elif self.secao in {"raw", "apos_raw"}:
                escopo = "raw"
            else:
                escopo = "comum"

        listas = {
            "comum": self.erros_comuns,
            "features": self.erros_features,
            "raw": self.erros_raw,
        }
        destino = listas[escopo]
        self.total_erros[escopo] += 1
        if len(destino) < 30:
            destino.append(mensagem)

    def processar_linha(self, linha: bytes) -> bool:
        linha = linha.rstrip(b"\r")

        if not self.iniciou:
            posicao = linha.find(MARCADOR_INICIO)
            if posicao < 0:
                return False
            linha = linha[posicao:]
            self.iniciou = True
            self.secao = "metadados"
            self.bruto.extend(linha + b"\n")
            print("Coleta recebida; validando features e raw data...")
            return False

        self.bruto.extend(linha + b"\n")

        if linha == MARCADOR_INICIO:
            self.registrar_erro("marcador de inicio duplicado")
            return False

        if linha.startswith(b"META,"):
            self._processar_meta(linha)
            return False

        if linha.startswith(b"META_END,"):
            self._processar_meta_end(linha)
            return False

        if linha == MARCADOR_FEATURES:
            if self.secao != "metadados" or not self.meta_end_recebido:
                self.registrar_erro(
                    "FEATURES_BEGIN fora de ordem", "features"
                )
            self.secao = "features"
            return False

        if linha == CABECALHO_FEATURES:
            if self.secao != "features" or self.cabecalho_features_recebido:
                self.registrar_erro("cabecalho de features fora de ordem")
            self.cabecalho_features_recebido = True
            return False

        if linha.startswith(b"FEATURES_END,"):
            self._processar_features_end(linha)
            return False

        if linha == MARCADOR_RAW:
            if self.secao != "apos_features" or not self.features_end_recebido:
                self.registrar_erro("RAW_BEGIN fora de ordem", "raw")
            self.secao = "raw"
            return False

        if linha == CABECALHO_RAW:
            if self.secao != "raw" or self.cabecalho_raw_recebido:
                self.registrar_erro("cabecalho de raw data fora de ordem")
            self.cabecalho_raw_recebido = True
            return False

        if linha.startswith(b"RAW_END,"):
            self._processar_raw_end(linha)
            return False

        if linha == MARCADOR_FIM:
            if self.secao != "apos_raw" or not self.raw_end_recebido:
                self.registrar_erro("marcador final fora de ordem")
            self.terminou = True
            return True

        if self.secao == "features" and self.cabecalho_features_recebido:
            self._processar_feature(linha)
            return False

        if self.secao == "raw" and self.cabecalho_raw_recebido:
            self._processar_raw(linha)
            return False

        if linha:
            self.registrar_erro("linha inesperada no protocolo V4")
        return False

    def _processar_meta(self, linha: bytes) -> None:
        if self.secao != "metadados" or self.meta_end_recebido:
            self.registrar_erro("metadado fora da secao correta")
            return

        self.checksum_metadados_calculado = fnv1a_bytes(
            self.checksum_metadados_calculado, linha
        )
        partes = linha.split(b",", 4)
        if len(partes) != 5:
            self.registrar_erro("linha META incompleta")
            return
        try:
            chave, valor, unidade, observacao = (
                campo.decode("ascii") for campo in partes[1:]
            )
        except UnicodeDecodeError:
            self.registrar_erro("linha META contem bytes invalidos")
            return
        if chave in self.metadados:
            self.registrar_erro(f"metadado duplicado: {chave}")
            return
        self.metadados[chave] = (valor, unidade, observacao)
        self.ordem_metadados.append(chave)

    def _processar_meta_end(self, linha: bytes) -> None:
        if self.secao != "metadados" or self.meta_end_recebido:
            self.registrar_erro("META_END fora de ordem")
        self.meta_end_recebido = True
        partes = linha.split(b",")
        if len(partes) != 2:
            self.registrar_erro("linha META_END incompleta")
            return
        try:
            self.checksum_metadados_informado = int(partes[1], 16)
        except ValueError:
            self.registrar_erro("checksum de META_END invalido")

    def _processar_feature(self, linha: bytes) -> None:
        self.checksum_features_calculado = fnv1a_bytes(
            self.checksum_features_calculado, linha
        )
        partes = linha.split(b",")
        if len(partes) != 7:
            self.registrar_erro(
                f"linha de feature com {len(partes)} campos"
            )
            return
        try:
            tipo, janela_texto, canal, harmonico_texto, frequencia_texto, valor_texto, unidade = (
                campo.decode("ascii") for campo in partes
            )
            janela = int(janela_texto) if janela_texto else None
            harmonico = int(harmonico_texto) if harmonico_texto else None
            frequencia = int(frequencia_texto) if frequencia_texto else None
            valor = float(valor_texto)
        except (UnicodeDecodeError, ValueError):
            self.registrar_erro("linha de feature contem valor invalido")
            return

        self.features.append(
            Feature(
                tipo=tipo,
                janela=janela,
                canal=canal,
                harmonico=harmonico,
                frequencia_nominal_hz=frequencia,
                valor_texto=valor_texto,
                valor=valor,
                unidade=unidade,
            )
        )

    def _processar_features_end(self, linha: bytes) -> None:
        if self.secao != "features" or self.features_end_recebido:
            self.registrar_erro("FEATURES_END fora de ordem")
        self.features_end_recebido = True
        self.secao = "apos_features"
        partes = linha.split(b",")
        if len(partes) != 4:
            self.registrar_erro("linha FEATURES_END incompleta")
            return
        try:
            self.quantidade_features_informada = int(partes[1])
            self.checksum_features_informado = int(partes[2], 16)
            self.tempo_calculo_features_us = int(partes[3])
        except ValueError:
            self.registrar_erro("dados de FEATURES_END invalidos")

    def _processar_raw(self, linha: bytes) -> None:
        partes = linha.split(b",")
        if len(partes) != 3:
            self.registrar_erro(f"linha raw com {len(partes)} campos")
            return
        try:
            indice, tensao, corrente = (int(campo) for campo in partes)
        except ValueError:
            self.registrar_erro("linha raw contem valor nao numerico")
            return

        if indice != self.proximo_indice_esperado:
            self.registrar_erro(
                f"indice raw recebido {indice}; "
                f"esperado {self.proximo_indice_esperado}"
            )
        self.proximo_indice_esperado = indice + 1

        if not (0 <= tensao <= 4095 and 0 <= corrente <= 4095):
            self.registrar_erro(f"amostra {indice} fora de 12 bits")

        self.raw.append((indice, tensao, corrente))
        if (
            0 <= indice <= 0xFFFF_FFFF
            and 0 <= tensao <= 0xFFFF
            and 0 <= corrente <= 0xFFFF
        ):
            self.checksum_raw_calculado = fnv1a_amostra(
                self.checksum_raw_calculado, indice, tensao, corrente
            )

        if len(self.raw) % 5_000 == 0:
            print(
                f"{len(self.raw)}/{AMOSTRAS_ESPERADAS} "
                "pares raw recebidos"
            )

    def _processar_raw_end(self, linha: bytes) -> None:
        if self.secao != "raw" or self.raw_end_recebido:
            self.registrar_erro("RAW_END fora de ordem")
        self.raw_end_recebido = True
        self.secao = "apos_raw"
        partes = linha.split(b",")
        if len(partes) != 3:
            self.registrar_erro("linha RAW_END incompleta")
            return
        try:
            self.quantidade_raw_informada = int(partes[1])
            self.checksum_raw_informado = int(partes[2], 16)
        except ValueError:
            self.registrar_erro("dados de RAW_END invalidos")

    def meta(self, chave: str) -> tuple[str, str, str] | None:
        return self.metadados.get(chave)

    def modo_envio(self) -> str | None:
        meta = self.meta("modo_envio")
        return None if meta is None else meta[0]

    def _erros_do_escopo(self, escopo: str) -> list[str]:
        listas = {
            "comum": self.erros_comuns,
            "features": self.erros_features,
            "raw": self.erros_raw,
        }
        problemas = list(listas[escopo])
        omitidos = self.total_erros[escopo] - len(problemas)
        if omitidos > 0:
            problemas.append(f"mais {omitidos} erros omitidos")
        return problemas

    def validar_metadados(self) -> list[str]:
        problemas = self._erros_do_escopo("comum")
        if not self.iniciou:
            problemas.append("marcador inicial nao recebido")
        if not self.meta_end_recebido:
            problemas.append("META_END nao recebido")

        status = self.meta("status_coleta")
        if status is None or status[0] != "OK":
            problemas.append(
                "o ESP32 nao informou status_coleta=OK"
                if status is None
                else f"status da coleta no ESP32: {status[0]}"
            )

        modo = self.modo_envio()
        if modo not in {"APENAS_FEATURES", "APENAS_RAW"}:
            problemas.append(f"modo_envio invalido ou ausente: {modo}")

        cliques = self.meta("cliques_botao")
        cliques_esperados = {
            "APENAS_RAW": "1",
            "APENAS_FEATURES": "2",
        }.get(modo)
        if cliques is None or cliques[0] != cliques_esperados:
            problemas.append(
                "metadado cliques_botao nao corresponde ao modo selecionado"
            )

        if (
            self.checksum_metadados_informado
            != self.checksum_metadados_calculado
        ):
            problemas.append(
                "checksum de metadados divergiu: "
                f"recebido {formatar_checksum(self.checksum_metadados_informado)}, "
                f"calculado {self.checksum_metadados_calculado:08X}"
            )

        for chave in (
            "tempo_inicio_monotonico",
            "tempo_final_monotonico",
            "tempo_primeira_tensao_estimado",
            "intervalo_conversao_reconstrucao",
            "fft_pontos",
            "fft_janelas",
            "harmonica_maxima",
            "features_linhas_esperadas",
        ):
            if self.meta(chave) is None:
                problemas.append(f"metadado obrigatorio ausente: {chave}")

        linhas_features = self.meta("features_linhas_esperadas")
        if (
            linhas_features is not None
            and linhas_features[0] != str(FEATURES_ESPERADAS)
        ):
            problemas.append(
                "features_linhas_esperadas difere do protocolo V4"
            )

        return problemas

    def validar_features(self) -> list[str]:
        problemas = self._erros_do_escopo("features")
        if not self.cabecalho_features_recebido:
            problemas.append("cabecalho de features nao recebido")
        if not self.features_end_recebido:
            problemas.append("FEATURES_END nao recebido")
        if not self.terminou:
            problemas.append("marcador final nao recebido")

        if self.modo_envio() == "APENAS_FEATURES":
            problemas.extend(self._validar_features())
        else:
            if self.features or self.quantidade_features_informada != 0:
                problemas.append("features recebidas no modo APENAS_RAW")
            if self.checksum_features_informado != FNV_OFFSET_BASIS:
                problemas.append("checksum vazio de features invalido")
        return limitar_problemas(problemas)

    def validar_raw(self) -> list[str]:
        problemas = self._erros_do_escopo("raw")
        if not self.cabecalho_raw_recebido:
            problemas.append("cabecalho de raw data nao recebido")
        if not self.raw_end_recebido:
            problemas.append("RAW_END nao recebido")
        if not self.terminou:
            problemas.append("marcador final nao recebido")

        if self.modo_envio() == "APENAS_RAW":
            problemas.extend(self._validar_raw())
        else:
            if self.raw or self.quantidade_raw_informada != 0:
                problemas.append("raw data recebida no modo APENAS_FEATURES")
            if self.checksum_raw_informado != FNV_OFFSET_BASIS:
                problemas.append("checksum vazio de raw data invalido")
        return limitar_problemas(problemas)

    def validar(self) -> list[str]:
        return limitar_problemas(
            self.validar_metadados()
            + self.validar_features()
            + self.validar_raw()
        )

    def _validar_features(self) -> list[str]:
        problemas: list[str] = []

        if len(self.features) != FEATURES_ESPERADAS:
            problemas.append(
                f"recebidas {len(self.features)} de {FEATURES_ESPERADAS} "
                "linhas de features"
            )
        if self.quantidade_features_informada != len(self.features):
            problemas.append(
                "contagem de FEATURES_END difere das features recebidas"
            )
        if self.checksum_features_informado != self.checksum_features_calculado:
            problemas.append(
                "checksum de features divergiu: "
                f"recebido {formatar_checksum(self.checksum_features_informado)}, "
                f"calculado {self.checksum_features_calculado:08X}"
            )

        tipos_por_canal = {
            "TENSAO": {
                "fft": "VFFT",
                "thd": "VTHD50",
                "rms": "VRMS",
                "fft_5s": "VFFT_RMS_5S",
                "thd_5s": "VTHD50_5S",
                "rms_5s": "VRMS_5S",
            },
            "CORRENTE": {
                "fft": "IFFT",
                "thd": "ITHD50",
                "rms": "IRMS",
                "fft_5s": "IFFT_RMS_5S",
                "thd_5s": "ITHD50_5S",
                "rms_5s": "IRMS_5S",
            },
        }

        fft_janela: set[tuple[int, str, int]] = set()
        thd_janela: set[tuple[int, str]] = set()
        rms_janela: set[tuple[int, str]] = set()
        fft_5s: set[tuple[str, int]] = set()
        thd_5s: set[str] = set()
        rms_5s: set[str] = set()

        for feature in self.features:
            if feature.canal not in CANAIS_ESPERADOS:
                problemas.append(f"canal de feature invalido: {feature.canal}")
                continue

            if feature.valor < 0 and not math.isnan(feature.valor):
                problemas.append("feature com valor negativo")

            tipos = tipos_por_canal[feature.canal]
            if feature.tipo == tipos["fft"]:
                if not self._harmonica_valida(feature, exige_janela=True):
                    problemas.append(
                        f"linha {feature.tipo} com campos invalidos"
                    )
                    continue
                chave = (feature.janela, feature.canal, feature.harmonico)
                if chave in fft_janela:
                    problemas.append(f"FFT duplicada: {chave}")
                fft_janela.add(chave)
            elif feature.tipo == tipos["thd"]:
                if not self._escalar_valido(
                    feature, exige_janela=True, unidade="percent"
                ):
                    problemas.append(
                        f"linha {feature.tipo} com campos invalidos"
                    )
                    continue
                chave_thd = (feature.janela, feature.canal)
                if chave_thd in thd_janela:
                    problemas.append(f"THD duplicado: {chave_thd}")
                thd_janela.add(chave_thd)
            elif feature.tipo == tipos["rms"]:
                if not self._escalar_valido(
                    feature,
                    exige_janela=True,
                    unidade="contagem_adc_rms",
                ):
                    problemas.append(
                        f"linha {feature.tipo} com campos invalidos"
                    )
                    continue
                chave_rms = (feature.janela, feature.canal)
                if chave_rms in rms_janela:
                    problemas.append(f"RMS duplicado: {chave_rms}")
                rms_janela.add(chave_rms)
            elif feature.tipo == tipos["fft_5s"]:
                if not self._harmonica_valida(feature, exige_janela=False):
                    problemas.append(
                        f"linha {feature.tipo} com campos invalidos"
                    )
                    continue
                chave_5s = (feature.canal, feature.harmonico)
                if chave_5s in fft_5s:
                    problemas.append(f"FFT de 5 s duplicada: {chave_5s}")
                fft_5s.add(chave_5s)
            elif feature.tipo == tipos["thd_5s"]:
                if not self._escalar_valido(
                    feature, exige_janela=False, unidade="percent"
                ):
                    problemas.append(
                        f"linha {feature.tipo} com campos invalidos"
                    )
                    continue
                if feature.canal in thd_5s:
                    problemas.append(f"THD de 5 s duplicado: {feature.canal}")
                thd_5s.add(feature.canal)
            elif feature.tipo == tipos["rms_5s"]:
                if not self._escalar_valido(
                    feature,
                    exige_janela=False,
                    unidade="contagem_adc_rms",
                ):
                    problemas.append(
                        f"linha {feature.tipo} com campos invalidos"
                    )
                    continue
                if feature.canal in rms_5s:
                    problemas.append(f"RMS de 5 s duplicado: {feature.canal}")
                rms_5s.add(feature.canal)
            else:
                problemas.append(f"tipo de feature desconhecido: {feature.tipo}")

        if len(fft_janela) != 2500:
            problemas.append("conjunto de VFFT/IFFT por janela esta incompleto")
        if len(thd_janela) != 50:
            problemas.append("conjunto de VTHD50/ITHD50 por janela incompleto")
        if len(rms_janela) != 50:
            problemas.append("conjunto de VRMS/IRMS por janela esta incompleto")
        if len(fft_5s) != 100:
            problemas.append("resumo VFFT/IFFT de 5 s esta incompleto")
        if thd_5s != CANAIS_ESPERADOS:
            problemas.append("VTHD50/ITHD50 global de 5 s esta incompleto")
        if rms_5s != CANAIS_ESPERADOS:
            problemas.append("VRMS/IRMS global de 5 s esta incompleto")

        return limitar_problemas(problemas)

    @staticmethod
    def _harmonica_valida(feature: Feature, exige_janela: bool) -> bool:
        janela_valida = (
            feature.janela is not None
            and 0 <= feature.janela < JANELAS_ESPERADAS
            if exige_janela
            else feature.janela is None
        )
        return bool(
            janela_valida
            and feature.harmonico is not None
            and 1 <= feature.harmonico <= HARMONICA_MAXIMA
            and feature.frequencia_nominal_hz
            == 60 * feature.harmonico
            and feature.unidade == "contagem_adc_rms"
        )

    @staticmethod
    def _escalar_valido(
        feature: Feature, exige_janela: bool, unidade: str
    ) -> bool:
        janela_valida = (
            feature.janela is not None
            and 0 <= feature.janela < JANELAS_ESPERADAS
            if exige_janela
            else feature.janela is None
        )
        return bool(
            janela_valida
            and feature.harmonico is None
            and feature.frequencia_nominal_hz is None
            and feature.unidade == unidade
        )

    def _validar_raw(self) -> list[str]:
        problemas: list[str] = []
        if len(self.raw) != AMOSTRAS_ESPERADAS:
            problemas.append(
                f"recebidos {len(self.raw)} de {AMOSTRAS_ESPERADAS} pares raw"
            )
        if self.quantidade_raw_informada != len(self.raw):
            problemas.append("contagem de RAW_END difere dos pares recebidos")
        if self.checksum_raw_informado != self.checksum_raw_calculado:
            problemas.append(
                "checksum de raw data divergiu: "
                f"recebido {formatar_checksum(self.checksum_raw_informado)}, "
                f"calculado {self.checksum_raw_calculado:08X}"
            )
        return problemas


def formatar_checksum(valor: int | None) -> str:
    return "ausente" if valor is None else f"{valor:08X}"


def limitar_problemas(problemas: list[str], limite: int = 30) -> list[str]:
    if len(problemas) <= limite:
        return problemas
    return problemas[:limite] + [f"mais {len(problemas) - limite} erros omitidos"]


def caminho_com_sufixo(prefixo: Path, sufixo: str) -> Path:
    if prefixo.suffix.lower() == ".csv":
        prefixo = prefixo.with_suffix("")
    return prefixo.parent / f"{prefixo.name}_{sufixo}.csv"


def escrever_csv_raw(saida: Path, decodificador: Decodificador) -> None:
    inicio = decodificador.metadados["tempo_inicio_monotonico"]
    final = decodificador.metadados["tempo_final_monotonico"]
    primeiro = float(
        decodificador.metadados["tempo_primeira_tensao_estimado"][0]
    )
    intervalo_conversao = float(
        decodificador.metadados["intervalo_conversao_reconstrucao"][0]
    )

    temporario = saida.with_suffix(saida.suffix + ".tmp")
    with temporario.open("w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.writer(arquivo, lineterminator="\n")
        escritor.writerow(CABECALHO_CSV_RAW)
        escritor.writerow(
            (
                "INICIO", "", inicio[0], "", "", "", "",
                "tempo_inicio_monotonico", inicio[0], inicio[1], inicio[2],
            )
        )

        for indice, tensao, corrente in decodificador.raw:
            tempo_tensao = primeiro + 2.0 * indice * intervalo_conversao
            tempo_corrente = tempo_tensao + intervalo_conversao
            escritor.writerow(
                (
                    "AMOSTRA", indice, "",
                    f"{tempo_tensao:.3f}", f"{tempo_corrente:.3f}",
                    tensao, corrente, "", "", "",
                    "timestamps_estimados_no_computador",
                )
            )

        escritor.writerow(
            (
                "FIM", "", final[0], "", "", "", "",
                "tempo_final_monotonico", final[0], final[1], final[2],
            )
        )

        for chave in decodificador.ordem_metadados:
            if chave in {"tempo_inicio_monotonico", "tempo_final_monotonico"}:
                continue
            valor, unidade, observacao = decodificador.metadados[chave]
            escritor.writerow(
                (
                    "METADADO", "", "", "", "", "", "",
                    chave, valor, unidade, observacao,
                )
            )

        escritor.writerow(
            (
                "VALIDACAO", "", "", "", "", "", "",
                "checksum_fnv1a_raw",
                f"{decodificador.checksum_raw_calculado:08X}",
                "hex", "validado_no_computador",
            )
        )

    temporario.replace(saida)


def escrever_csv_features(saida: Path, decodificador: Decodificador) -> None:
    primeiro = float(
        decodificador.metadados["tempo_primeira_tensao_estimado"][0]
    )
    intervalo_por_canal = 2.0 * float(
        decodificador.metadados["intervalo_conversao_reconstrucao"][0]
    )
    duracao_janela_us = 2048.0 * intervalo_por_canal

    temporario = saida.with_suffix(saida.suffix + ".tmp")
    with temporario.open("w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.writer(arquivo, lineterminator="\n")
        escritor.writerow(CABECALHO_CSV_FEATURES)

        for feature in decodificador.features:
            inicio_janela = (
                ""
                if feature.janela is None
                else f"{primeiro + feature.janela * duracao_janela_us:.3f}"
            )
            escritor.writerow(
                (
                    feature.tipo,
                    "" if feature.janela is None else feature.janela,
                    inicio_janela,
                    feature.canal,
                    "" if feature.harmonico is None else feature.harmonico,
                    ""
                    if feature.frequencia_nominal_hz is None
                    else feature.frequencia_nominal_hz,
                    feature.valor_texto,
                    feature.unidade,
                    "",
                    descricao_metodo_feature(feature.tipo),
                )
            )

        for chave in decodificador.ordem_metadados:
            valor, unidade, observacao = decodificador.metadados[chave]
            escritor.writerow(
                (
                    "METADADO", "", "", "", "", "",
                    valor, unidade, chave, observacao,
                )
            )

        escritor.writerow(
            (
                "VALIDACAO", "", "", "", "", "",
                f"{decodificador.checksum_features_calculado:08X}",
                "hex", "checksum_fnv1a_features",
                "validado_no_computador",
            )
        )
        escritor.writerow(
            (
                "DIAGNOSTICO", "", "", "", "", "",
                decodificador.tempo_calculo_features_us,
                "us", "tempo_calculo_fft_thd_no_esp32",
                "nao_inclui_tempo_de_transmissao_serial",
            )
        )

    temporario.replace(saida)


def validar_destinos(destinos: list[Path]) -> bool:
    existentes = [caminho for caminho in destinos if caminho.exists()]
    if not existentes:
        return True
    print("Erro: os seguintes arquivos ja existem:")
    for caminho in existentes:
        print(f"  - {caminho.resolve()}")
    print("Escolha outro --prefixo para evitar sobrescreve-los.")
    return False


def descricao_metodo_feature(tipo: str) -> str:
    if "FFT" in tipo:
        return "subgrupo_bin_central_mais_dois_adjacentes"
    if "THD" in tipo:
        return "THD_da_2a_ate_a_50a_harmonica"
    if "RMS" in tipo:
        return "RMS_no_tempo_apos_remocao_do_offset_DC"
    return ""


def main() -> int:
    args = argumentos()
    prefixo = args.prefixo or Path(
        datetime.now().strftime("coleta_esp32_%Y%m%d_%H%M%S")
    )
    saida_features = caminho_com_sufixo(prefixo, "features")
    saida_raw = caminho_com_sufixo(prefixo, "raw")

    try:
        import serial
    except ImportError:
        print("Erro: instale o pyserial com: py -m pip install pyserial")
        return 1

    try:
        porta = serial.Serial(args.porta, args.baud, timeout=0.2)
    except serial.SerialException as erro:
        print(f"Nao foi possivel abrir {args.porta}: {erro}")
        print("Feche o Monitor Serial da Arduino IDE e tente novamente.")
        return 1

    decodificador = Decodificador()
    pendente = b""
    instante_ultimo_byte = time.monotonic()
    inicio_recepcao: float | None = None

    print(f"Porta {args.porta} aberta a {args.baud} baud.")
    print("Aguardando o botao do ESP32. Pressione Ctrl+C para cancelar.")

    try:
        with porta:
            porta.reset_input_buffer()
            while not decodificador.terminou:
                bloco = porta.read(TAMANHO_BLOCO)
                agora = time.monotonic()
                if not bloco:
                    if (
                        decodificador.iniciou
                        and agora - instante_ultimo_byte
                        > args.timeout_inatividade
                    ):
                        decodificador.registrar_erro(
                            "timeout por inatividade durante a transmissao"
                        )
                        break
                    continue

                instante_ultimo_byte = agora
                partes = (pendente + bloco).split(b"\n")
                pendente = partes.pop()
                for linha in partes:
                    iniciou_antes = decodificador.iniciou
                    terminou = decodificador.processar_linha(linha)
                    if not iniciou_antes and decodificador.iniciou:
                        inicio_recepcao = time.monotonic()
                    if terminou:
                        break

    except KeyboardInterrupt:
        print("\nCaptura cancelada pelo usuario.")
        decodificador.registrar_erro("captura cancelada pelo usuario")
    except (OSError, serial.SerialException) as erro:
        print(f"\nErro durante a captura: {erro}")
        decodificador.registrar_erro(f"erro de porta serial: {erro}")

    if pendente and decodificador.iniciou and not decodificador.terminou:
        decodificador.bruto.extend(pendente)
        decodificador.registrar_erro("fluxo terminou com linha parcial")

    problemas_metadados = decodificador.validar_metadados()
    modo = decodificador.modo_envio()
    espera_features = modo == "APENAS_FEATURES"
    espera_raw = modo == "APENAS_RAW"
    problemas_features = (
        problemas_metadados + decodificador.validar_features()
        if espera_features
        else []
    )
    problemas_raw = (
        problemas_metadados + decodificador.validar_raw()
        if espera_raw
        else []
    )
    features_validas = espera_features and not problemas_features
    raw_valida = espera_raw and not problemas_raw
    duracao = (
        0.0
        if inicio_recepcao is None
        else time.monotonic() - inicio_recepcao
    )

    destinos_a_criar = []
    if features_validas:
        destinos_a_criar.append(saida_features)
    if raw_valida:
        destinos_a_criar.append(saida_raw)
    if not validar_destinos(destinos_a_criar):
        return 1

    try:
        if features_validas:
            escrever_csv_features(saida_features, decodificador)
        if raw_valida:
            escrever_csv_raw(saida_raw, decodificador)
    except (OSError, ValueError, KeyError) as erro:
        print(f"Erro ao criar os CSVs: {erro}")
        return 1

    if features_validas:
        print(f"Features salvas: {saida_features.resolve()}")
    if raw_valida:
        print(f"Raw data salva: {saida_raw.resolve()}")

    if problemas_features or problemas_raw or problemas_metadados:
        if decodificador.iniciou:
            parcial = prefixo.parent / f"{prefixo.name}_parcial.txt"
            if parcial.exists():
                parcial = prefixo.parent / (
                    f"{prefixo.name}_parcial_"
                    f"{datetime.now().strftime('%H%M%S')}.txt"
                )
            parcial.write_bytes(decodificador.bruto)
            print(f"Fluxo recebido salvo para diagnostico: {parcial.resolve()}")

        if problemas_features:
            print("Secao de features rejeitada:")
            for problema in limitar_problemas(problemas_features):
                print(f"  - {problema}")
        if problemas_raw:
            print("Secao de raw data rejeitada:")
            for problema in limitar_problemas(problemas_raw):
                print(f"  - {problema}")
        if not espera_features and not espera_raw:
            print("Captura rejeitada por metadados ou modo de envio invalidos:")
            for problema in limitar_problemas(problemas_metadados):
                print(f"  - {problema}")
        return 2

    if features_validas:
        print(
            "Captura completa no modo FEATURES: "
            f"{len(decodificador.features)} valores recebidos."
        )
        print(
            "Checksum confirmado: "
            f"features={decodificador.checksum_features_calculado:08X}."
        )
    if raw_valida:
        print(
            "Captura completa no modo RAW: "
            f"{len(decodificador.raw)} pares recebidos."
        )
        print(
            "Checksum confirmado: "
            f"raw={decodificador.checksum_raw_calculado:08X}."
        )
    print(f"Tempo total de transmissao: {duracao:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
