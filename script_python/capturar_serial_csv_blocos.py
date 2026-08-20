#!/usr/bin/env python3
"""Captura em blocos a exportacao compacta do ESP32 e gera um CSV completo.

Uso:
    py capturar_serial_csv_blocos.py COM5
    py capturar_serial_csv_blocos.py COM5 --saida minha_coleta.csv

O Monitor Serial da Arduino IDE deve permanecer fechado. Depois de aparecer
"Aguardando o botao", pressione o botao ligado ao GPIO4 do ESP32.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
import sys
import time

try:
    import serial
except ImportError:
    print("Erro: instale o pyserial com: py -m pip install pyserial")
    raise SystemExit(1)


MARCADOR_INICIO = b"BEGIN_COLETA_COMPACTA_V2"
MARCADOR_DADOS = b"DATA_BEGIN"
CABECALHO_DADOS = b"indice,tensao_raw_gpio36,corrente_raw_gpio39"
MARCADOR_FIM = b"END_COLETA_COMPACTA_V2"

BAUD_PADRAO = 460_800
TAMANHO_BLOCO = 65_536
AMOSTRAS_ESPERADAS = 51_200
FNV_OFFSET_BASIS = 2_166_136_261
FNV_PRIME = 16_777_619

CABECALHO_CSV = (
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


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recebe a coleta compacta do ESP32 em blocos, valida a transmissao "
            "e reconstrói os timestamps no CSV final."
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
        "--saida",
        type=Path,
        help="Nome do CSV. Se omitido, usa a data e a hora atuais.",
    )
    parser.add_argument(
        "--timeout-inatividade",
        type=float,
        default=10.0,
        help="Segundos sem receber bytes antes de interromper; padrao: 10.",
    )
    return parser.parse_args()


def fnv1a_amostra(checksum: int, indice: int, tensao: int, corrente: int) -> int:
    dados = (
        indice.to_bytes(4, "little")
        + tensao.to_bytes(2, "little")
        + corrente.to_bytes(2, "little")
    )
    return fnv1a_bytes(checksum, dados)


def fnv1a_bytes(checksum: int, dados: bytes) -> int:
    for byte in dados:
        checksum ^= byte
        checksum = (checksum * FNV_PRIME) & 0xFFFF_FFFF
    return checksum


class Decodificador:
    def __init__(self) -> None:
        self.iniciou = False
        self.em_dados = False
        self.cabecalho_dados_recebido = False
        self.data_end_recebido = False
        self.terminou = False

        self.metadados: dict[str, tuple[str, str, str]] = {}
        self.ordem_metadados: list[str] = []
        self.amostras: list[tuple[int, int, int]] = []
        self.proximo_indice_esperado = 0

        self.checksum_calculado = FNV_OFFSET_BASIS
        self.checksum_informado: int | None = None
        self.quantidade_informada: int | None = None
        self.checksum_metadados_calculado = FNV_OFFSET_BASIS
        self.checksum_metadados_informado: int | None = None
        self.meta_end_recebido = False

        self.erros: list[str] = []
        self.total_erros = 0
        self.bruto = bytearray()

    def registrar_erro(self, mensagem: str) -> None:
        self.total_erros += 1
        if len(self.erros) < 20:
            self.erros.append(mensagem)

    def processar_linha(self, linha: bytes) -> bool:
        linha = linha.rstrip(b"\r")

        if not self.iniciou:
            posicao = linha.find(MARCADOR_INICIO)
            if posicao < 0:
                return False
            linha = linha[posicao:]
            self.iniciou = True
            self.bruto.extend(linha + b"\n")
            print("Coleta recebida; transferindo dados compactos...")
            return False

        self.bruto.extend(linha + b"\n")

        if linha == MARCADOR_INICIO:
            self.registrar_erro("marcador de inicio duplicado")
            return False

        if linha.startswith(b"META,"):
            if self.em_dados or self.data_end_recebido:
                self.registrar_erro("metadado recebido fora da secao correta")
                return False
            self.checksum_metadados_calculado = fnv1a_bytes(
                self.checksum_metadados_calculado, linha
            )
            partes = linha.split(b",", 4)
            if len(partes) != 5:
                self.registrar_erro("linha META incompleta")
                return False
            try:
                chave, valor, unidade, observacao = (
                    campo.decode("ascii") for campo in partes[1:]
                )
            except UnicodeDecodeError:
                self.registrar_erro("linha META contem bytes invalidos")
                return False
            if chave in self.metadados:
                self.registrar_erro(f"metadado duplicado: {chave}")
                return False
            self.metadados[chave] = (valor, unidade, observacao)
            self.ordem_metadados.append(chave)
            return False

        if linha.startswith(b"META_END,"):
            self.meta_end_recebido = True
            partes = linha.split(b",")
            if len(partes) != 2:
                self.registrar_erro("linha META_END incompleta")
                return False
            try:
                self.checksum_metadados_informado = int(partes[1], 16)
            except ValueError:
                self.registrar_erro("checksum de META_END invalido")
            return False

        if linha == MARCADOR_DADOS:
            if self.em_dados or self.data_end_recebido:
                self.registrar_erro("marcador DATA_BEGIN fora de ordem")
            self.em_dados = True
            return False

        if linha == CABECALHO_DADOS:
            if not self.em_dados or self.cabecalho_dados_recebido:
                self.registrar_erro("cabecalho de dados fora de ordem")
            self.cabecalho_dados_recebido = True
            return False

        if linha.startswith(b"DATA_END,"):
            self.em_dados = False
            self.data_end_recebido = True
            partes = linha.split(b",")
            if len(partes) != 3:
                self.registrar_erro("linha DATA_END incompleta")
                return False
            try:
                self.quantidade_informada = int(partes[1])
                self.checksum_informado = int(partes[2], 16)
            except ValueError:
                self.registrar_erro("contagem ou checksum de DATA_END invalido")
            return False

        if linha == MARCADOR_FIM:
            self.terminou = True
            return True

        if self.em_dados and self.cabecalho_dados_recebido:
            partes = linha.split(b",")
            if len(partes) != 3:
                self.registrar_erro(
                    f"linha de amostra com {len(partes)} campos"
                )
                return False
            try:
                indice, tensao, corrente = (int(campo) for campo in partes)
            except ValueError:
                self.registrar_erro("linha de amostra contem valor nao numerico")
                return False

            if indice != self.proximo_indice_esperado:
                self.registrar_erro(
                    f"indice recebido {indice}; "
                    f"esperado {self.proximo_indice_esperado}"
                )
            self.proximo_indice_esperado = indice + 1

            valores_adc_validos = (
                0 <= tensao <= 4095 and 0 <= corrente <= 4095
            )
            if not valores_adc_validos:
                self.registrar_erro(
                    f"amostra {indice} fora da faixa de 12 bits"
                )

            self.amostras.append((indice, tensao, corrente))
            if (
                0 <= indice <= 0xFFFF_FFFF
                and 0 <= tensao <= 0xFFFF
                and 0 <= corrente <= 0xFFFF
            ):
                self.checksum_calculado = fnv1a_amostra(
                    self.checksum_calculado, indice, tensao, corrente
                )

            if len(self.amostras) % 5_000 == 0:
                print(
                    f"{len(self.amostras)}/{AMOSTRAS_ESPERADAS} "
                    "pares recebidos"
                )
            return False

        if linha:
            self.registrar_erro("linha inesperada no fluxo compacto")
        return False

    def meta(self, chave: str) -> tuple[str, str, str] | None:
        return self.metadados.get(chave)

    def validar(self) -> list[str]:
        problemas = list(self.erros)
        if self.total_erros > len(self.erros):
            problemas.append(
                f"mais {self.total_erros - len(self.erros)} erros omitidos"
            )
        if not self.iniciou:
            problemas.append("marcador inicial nao recebido")
        if not self.data_end_recebido:
            problemas.append("marcador DATA_END nao recebido")
        if not self.meta_end_recebido:
            problemas.append("marcador META_END nao recebido")
        if not self.terminou:
            problemas.append("marcador final nao recebido")

        status = self.meta("status_coleta")
        if status is None or status[0] != "OK":
            problemas.append(
                "o ESP32 nao informou status_coleta=OK"
                if status is None
                else f"status da coleta no ESP32: {status[0]}"
            )

        esperado_meta = self.meta("amostras_esperadas_por_canal")
        if esperado_meta is None:
            problemas.append("metadado de quantidade esperada ausente")
        else:
            try:
                if int(esperado_meta[0]) != AMOSTRAS_ESPERADAS:
                    problemas.append("quantidade esperada no sketch foi alterada")
            except ValueError:
                problemas.append("quantidade esperada no metadado e invalida")

        if len(self.amostras) != AMOSTRAS_ESPERADAS:
            problemas.append(
                f"recebidas {len(self.amostras)} de {AMOSTRAS_ESPERADAS} amostras"
            )
        if self.quantidade_informada != len(self.amostras):
            problemas.append(
                "contagem de DATA_END difere da quantidade recebida"
            )
        if self.checksum_informado != self.checksum_calculado:
            informado = (
                "ausente"
                if self.checksum_informado is None
                else f"{self.checksum_informado:08X}"
            )
            problemas.append(
                "checksum divergiu: "
                f"recebido {informado}, calculado {self.checksum_calculado:08X}"
            )
        if (
            self.checksum_metadados_informado
            != self.checksum_metadados_calculado
        ):
            informado_meta = (
                "ausente"
                if self.checksum_metadados_informado is None
                else f"{self.checksum_metadados_informado:08X}"
            )
            problemas.append(
                "checksum dos metadados divergiu: "
                f"recebido {informado_meta}, "
                f"calculado {self.checksum_metadados_calculado:08X}"
            )

        for chave in (
            "tempo_inicio_monotonico",
            "tempo_final_monotonico",
            "tempo_primeira_tensao_estimado",
            "intervalo_conversao_reconstrucao",
        ):
            if self.meta(chave) is None:
                problemas.append(f"metadado obrigatorio ausente: {chave}")

        return problemas


def escrever_csv(saida: Path, decodificador: Decodificador) -> None:
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
        escritor.writerow(CABECALHO_CSV)
        escritor.writerow(
            (
                "INICIO",
                "",
                inicio[0],
                "",
                "",
                "",
                "",
                "tempo_inicio_monotonico",
                inicio[0],
                inicio[1],
                inicio[2],
            )
        )

        for indice, tensao, corrente in decodificador.amostras:
            tempo_tensao = primeiro + 2.0 * indice * intervalo_conversao
            tempo_corrente = tempo_tensao + intervalo_conversao
            escritor.writerow(
                (
                    "AMOSTRA",
                    indice,
                    "",
                    f"{tempo_tensao:.3f}",
                    f"{tempo_corrente:.3f}",
                    tensao,
                    corrente,
                    "",
                    "",
                    "",
                    "timestamps_estimados_no_computador",
                )
            )

        escritor.writerow(
            (
                "FIM",
                "",
                final[0],
                "",
                "",
                "",
                "",
                "tempo_final_monotonico",
                final[0],
                final[1],
                final[2],
            )
        )

        for chave in decodificador.ordem_metadados:
            if chave in {"tempo_inicio_monotonico", "tempo_final_monotonico"}:
                continue
            valor, unidade, observacao = decodificador.metadados[chave]
            escritor.writerow(
                (
                    "ESTATISTICA",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    chave,
                    valor,
                    unidade,
                    observacao,
                )
            )

        escritor.writerow(
            (
                "ESTATISTICA",
                "",
                "",
                "",
                "",
                "",
                "",
                "checksum_fnv1a_transmissao",
                f"{decodificador.checksum_calculado:08X}",
                "hex",
                "validado_no_computador",
            )
        )
        escritor.writerow(
            (
                "ESTATISTICA",
                "",
                "",
                "",
                "",
                "",
                "",
                "checksum_fnv1a_metadados",
                f"{decodificador.checksum_metadados_calculado:08X}",
                "hex",
                "validado_no_computador",
            )
        )

    temporario.replace(saida)


def main() -> int:
    args = argumentos()
    saida = args.saida or Path(
        datetime.now().strftime("coleta_esp32_%Y%m%d_%H%M%S.csv")
    )

    if saida.exists():
        print(f"Erro: o arquivo ja existe: {saida.resolve()}")
        print("Escolha outro nome com --saida para evitar sobrescreve-lo.")
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

    problemas = decodificador.validar()
    duracao = (
        0.0
        if inicio_recepcao is None
        else time.monotonic() - inicio_recepcao
    )

    if problemas:
        if decodificador.iniciou:
            parcial = saida.with_suffix(saida.suffix + ".parcial.txt")
            parcial.write_bytes(decodificador.bruto)
            print(f"Fluxo bruto parcial salvo em: {parcial.resolve()}")
        print("Captura rejeitada; nenhum CSV final foi criado:")
        for problema in problemas:
            print(f"  - {problema}")
        return 2

    try:
        escrever_csv(saida, decodificador)
    except (OSError, ValueError) as erro:
        print(f"Erro ao criar o CSV final: {erro}")
        return 1

    print(f"Arquivo salvo: {saida.resolve()}")
    print("Captura completa: 51.200 amostras de cada canal.")
    print(
        "Checksum confirmado: "
        f"{decodificador.checksum_calculado:08X}."
    )
    print(f"Tempo de transmissao: {duracao:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
