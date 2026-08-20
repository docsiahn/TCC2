#include <Arduino.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_idf_version.h"
#include "esp_timer.h"
#include "sdkconfig.h"
#include "soc/soc_caps.h"

#if ESP_IDF_VERSION_MAJOR < 5
#error "Este sketch requer o pacote esp32 da Espressif 3.x (ESP-IDF 5.x)."
#endif

#if !CONFIG_IDF_TARGET_ESP32
#error "Este mapeamento foi preparado para o ESP32 classico DevKit."
#endif

#include "esp_adc/adc_continuous.h"

// ============================================================================
// PORTAS
// ============================================================================
constexpr uint8_t PIN_TENSAO = 36;   // VP = ADC1_CHANNEL_0
constexpr uint8_t PIN_CORRENTE = 39; // VN = ADC1_CHANNEL_3
constexpr uint8_t PIN_BOTAO = 4;     // Botao entre GPIO4 e GND

constexpr adc_channel_t CANAL_TENSAO = ADC_CHANNEL_0;
constexpr adc_channel_t CANAL_CORRENTE = ADC_CHANNEL_3;

#ifndef LED_BUILTIN
#define LED_BUILTIN 2
#endif
constexpr uint8_t PIN_LED = LED_BUILTIN;

// ============================================================================
// AQUISICAO
// ============================================================================
constexpr uint32_t DURACAO_COLETA_US = 5000000UL;
constexpr uint32_t TAXA_POR_CANAL_SPS = 10240UL;
constexpr uint32_t QUANTIDADE_CANAIS = 2UL;
constexpr uint32_t TAXA_TOTAL_CONVERSOES_SPS =
    TAXA_POR_CANAL_SPS * QUANTIDADE_CANAIS;

constexpr uint32_t AMOSTRAS_POR_CANAL =
    (uint64_t)TAXA_POR_CANAL_SPS * DURACAO_COLETA_US / 1000000ULL;
constexpr uint32_t TOTAL_CONVERSOES_ALVO =
    AMOSTRAS_POR_CANAL * QUANTIDADE_CANAIS;

constexpr double INTERVALO_NOMINAL_POR_CANAL_US =
    1000000.0 / (double)TAXA_POR_CANAL_SPS;
constexpr double INTERVALO_NOMINAL_CONVERSAO_US =
    1000000.0 / (double)TAXA_TOTAL_CONVERSOES_SPS;

constexpr uint32_t BAUD_SERIAL = 460800UL;
constexpr uint32_t DEBOUNCE_MS = 40UL;
constexpr uint32_t JANELA_DUPLO_CLIQUE_MS = 500UL;
constexpr uint32_t DURACAO_PISCADA_MODO_MS = 100UL;
constexpr int64_t TIMEOUT_TOTAL_COLETA_US = 7000000LL;

constexpr uint32_t CONVERSOES_POR_FRAME = 256UL;
constexpr size_t TAMANHO_FRAME_BYTES =
    CONVERSOES_POR_FRAME * SOC_ADC_DIGI_RESULT_BYTES;
constexpr size_t TAMANHO_POOL_DMA_BYTES = TAMANHO_FRAME_BYTES * 8UL;
constexpr size_t TAMANHO_LEITURA_DMA_BYTES = TAMANHO_FRAME_BYTES * 4UL;

constexpr uint32_t FRAMES_ALVO =
    TOTAL_CONVERSOES_ALVO / CONVERSOES_POR_FRAME;
constexpr uint32_t MAX_FRAMES_REGISTRADOS = FRAMES_ALVO + 64UL;

// Cada amostra possui 12 bits. Os dois canais ocupam 153.600 bytes.
constexpr size_t BYTES_COMPACTADOS_POR_CANAL =
    ((size_t)AMOSTRAS_POR_CANAL * 12UL + 7UL) / 8UL;

// ============================================================================
// FFT E THD
// ============================================================================
constexpr uint32_t FFT_N = 2048UL;
constexpr uint32_t QUANTIDADE_JANELAS = AMOSTRAS_POR_CANAL / FFT_N;
constexpr uint32_t FREQUENCIA_FUNDAMENTAL_NOMINAL_HZ = 60UL;
constexpr uint32_t HARMONICA_MAXIMA = 50UL;
constexpr uint32_t RESOLUCAO_FFT_HZ = TAXA_POR_CANAL_SPS / FFT_N;
constexpr uint32_t BINS_POR_HARMONICA =
    FREQUENCIA_FUNDAMENTAL_NOMINAL_HZ / RESOLUCAO_FFT_HZ;
constexpr float PI_F = 3.14159265358979323846f;

// Retangular e 200 ms: a magnitude de cada subgrupo e formada pelo bin central
// da harmonica e seus dois vizinhos imediatos (k-1, k, k+1).
constexpr uint32_t RAIO_SUBGRUPO_BINS = 1UL;
constexpr uint32_t LINHAS_FEATURES_ESPERADAS =
    QUANTIDADE_JANELAS * QUANTIDADE_CANAIS *
        (HARMONICA_MAXIMA + 2UL) +
    QUANTIDADE_CANAIS * (HARMONICA_MAXIMA + 2UL);

enum class ModoEnvio : uint8_t {
  APENAS_FEATURES,
  APENAS_RAW,
};

static_assert(AMOSTRAS_POR_CANAL == 51200UL,
              "Devem ser coletadas 51.200 amostras por canal.");
static_assert(AMOSTRAS_POR_CANAL % FFT_N == 0,
              "A coleta precisa conter um numero inteiro de janelas FFT.");
static_assert(QUANTIDADE_JANELAS == 25UL,
              "A coleta de 5 s deve produzir 25 janelas de 200 ms.");
static_assert(RESOLUCAO_FFT_HZ == 5UL,
              "A resolucao da FFT deve ser 5 Hz.");
static_assert(BINS_POR_HARMONICA == 12UL,
              "Cada harmonica de 60 Hz deve avancar 12 bins.");
static_assert(HARMONICA_MAXIMA * BINS_POR_HARMONICA +
                      RAIO_SUBGRUPO_BINS <
                  FFT_N / 2UL,
              "O subgrupo da 50a harmonica deve ficar abaixo de Nyquist.");
static_assert(TOTAL_CONVERSOES_ALVO % CONVERSOES_POR_FRAME == 0,
              "A coleta deve terminar na borda de um frame DMA.");
static_assert(BYTES_COMPACTADOS_POR_CANAL == 76800UL,
              "Cada canal compactado deve ocupar 76.800 bytes.");
static_assert(LINHAS_FEATURES_ESPERADAS == 2704UL,
              "O protocolo deve transmitir 2.704 linhas de features.");

struct ComplexoF {
  float real;
  float imag;
};

static_assert(sizeof(ComplexoF) == 8UL,
              "O buffer FFT deve usar dois floats por amostra.");

// ============================================================================
// ESTADO GLOBAL
// ============================================================================
adc_continuous_handle_t adcHandle = nullptr;
adc_digi_pattern_config_t padraoADC[QUANTIDADE_CANAIS] = {};

uint8_t *amostrasTensaoCompactadas = nullptr;
uint8_t *amostrasCorrenteCompactadas = nullptr;
ComplexoF *bufferFFT = nullptr;
bool amostrasNaPSRAM = false;
size_t heapLivreAposAlocacoes = 0;

alignas(4) uint8_t bufferLeituraDMA[TAMANHO_LEITURA_DMA_BYTES] = {};

volatile int64_t temposFimFrameUs[MAX_FRAMES_REGISTRADOS] = {};
volatile uint16_t conversoesEmCadaFrame[MAX_FRAMES_REGISTRADOS] = {};
volatile uint32_t quantidadeFramesISR = 0;
volatile uint32_t framesSemEspacoParaTimestamp = 0;
volatile uint32_t eventosOverflowPool = 0;

bool sistemaPronto = false;
const char *etapaErroFatal = "nenhuma";
esp_err_t codigoErroFatal = ESP_OK;
uint32_t checksumMetadadosTransmissao = 2166136261UL;

struct ResultadoColeta {
  bool adcFoiIniciado;
  bool coletaValida;
  esp_err_t erro;
  const char *etapaErro;

  uint32_t amostrasTensao;
  uint32_t amostrasCorrente;
  uint32_t resultadosCanalDesconhecido;
  uint32_t framesRegistrados;
  uint32_t framesNaoRegistrados;
  uint32_t overflowsPool;

  int64_t tempoAntesStartUs;
  int64_t tempoDepoisStartUs;
  int64_t tempoContagensAtingidasUs;
  int64_t tempoDepoisStopUs;
};

struct EstatisticasTempo {
  bool validas;
  uint32_t intervalosDeFrame;

  double intervaloConversaoEfetivoUs;
  double intervaloPorCanalMedioUs;
  double intervaloPorCanalDesvioPadraoUs;
  double intervaloPorCanalMinimoUs;
  double intervaloPorCanalMaximoUs;
  double taxaEfetivaPorCanalSps;

  int64_t tempoPrimeiraConversaoEstimadoUs;
  int64_t tempoUltimaConversaoEstimadoUs;
  int64_t tempoFimPrimeiroFrameUs;
  int64_t tempoFimUltimoFrameAlvoUs;
};

struct ResultadoEnvioFeatures {
  uint32_t linhasEnviadas;
  uint32_t checksum;
  int64_t tempoCalculoUs;
};

// ============================================================================
// PROTOTIPOS
// ============================================================================
bool configurarADCContinuo();
bool alocarBuffers();
bool detectarPressaoBotao();
bool reconhecerComandoBotao(ModoEnvio &modoSelecionado);
void sinalizarModoSelecionado(ModoEnvio modoSelecionado);
ResultadoColeta coletarAmostras();
EstatisticasTempo calcularEstatisticasTempo(const ResultadoColeta &resultado);

void enviarResultado(const ResultadoColeta &resultado,
                     const EstatisticasTempo &estatisticas,
                     ModoEnvio modoSelecionado);
void enviarErroFatal(ModoEnvio modoSelecionado);
ResultadoEnvioFeatures calcularEEnviarFeatures();
void enviarRaw(const ResultadoColeta &resultado,
               ModoEnvio modoSelecionado);

void gravar12Bits(uint8_t *buffer, uint32_t indice, uint16_t valor);
uint16_t ler12Bits(const uint8_t *buffer, uint32_t indice);

void fftRadix2(ComplexoF *dados, uint32_t tamanho);
void calcularFeaturesJanela(uint32_t janela,
                            float magnitudes[QUANTIDADE_CANAIS]
                                             [HARMONICA_MAXIMA + 1UL],
                            float rms[QUANTIDADE_CANAIS]);
float magnitudeBinRms(uint32_t bin, uint8_t canal);
double calcularTHD50(
    const float magnitudes[HARMONICA_MAXIMA + 1UL]);

void enviarMetaInteira(const char *nome, int64_t valor,
                       const char *unidade, const char *observacao = "");
void enviarMetaDouble(const char *nome, double valor, uint8_t casas,
                      const char *unidade, const char *observacao = "");
void enviarMetaTexto(const char *nome, const char *valor,
                     const char *observacao = "");
void enviarLinhaComChecksum(const char *linha, uint32_t &checksum,
                            uint32_t &quantidade);
uint32_t atualizarChecksumAmostra(uint32_t checksum, uint32_t indice,
                                 uint16_t tensao, uint16_t corrente);
uint32_t atualizarChecksumBytes(uint32_t checksum, const uint8_t *dados,
                                size_t tamanho);
const char *nomeModoEnvio(ModoEnvio modoSelecionado);

// ============================================================================
// CALLBACKS DO ADC CONTINUO
// ============================================================================
static bool IRAM_ATTR aoTerminarFrame(
    adc_continuous_handle_t handle,
    const adc_continuous_evt_data_t *dadosEvento,
    void *dadosUsuario) {
  (void)handle;
  (void)dadosUsuario;

  const uint32_t indice = quantidadeFramesISR;
  if (indice < MAX_FRAMES_REGISTRADOS) {
    temposFimFrameUs[indice] = esp_timer_get_time();
    conversoesEmCadaFrame[indice] =
        (uint16_t)(dadosEvento->size / SOC_ADC_DIGI_RESULT_BYTES);
    quantidadeFramesISR = indice + 1UL;
  } else {
    ++framesSemEspacoParaTimestamp;
  }
  return false;
}

static bool IRAM_ATTR aoEncherPool(
    adc_continuous_handle_t handle,
    const adc_continuous_evt_data_t *dadosEvento,
    void *dadosUsuario) {
  (void)handle;
  (void)dadosEvento;
  (void)dadosUsuario;
  ++eventosOverflowPool;
  return false;
}

// ============================================================================
// INICIALIZACAO E LOOP
// ============================================================================
void setup() {
  pinMode(PIN_BOTAO, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);

  Serial.setTxBufferSize(8192);
  Serial.begin(BAUD_SERIAL);

  if (!configurarADCContinuo()) {
    return;
  }

  if (!alocarBuffers()) {
    etapaErroFatal = "alocar_buffers_raw_fft";
    codigoErroFatal = ESP_ERR_NO_MEM;
    return;
  }

  sistemaPronto = true;
}

void loop() {
  ModoEnvio modoSelecionado = ModoEnvio::APENAS_RAW;
  if (!reconhecerComandoBotao(modoSelecionado)) {
    delay(1);
    return;
  }

  if (!sistemaPronto) {
    enviarErroFatal(modoSelecionado);
    return;
  }

  sinalizarModoSelecionado(modoSelecionado);
  digitalWrite(PIN_LED, HIGH);
  const ResultadoColeta resultado = coletarAmostras();
  digitalWrite(PIN_LED, LOW);

  const EstatisticasTempo estatisticas =
      calcularEstatisticasTempo(resultado);
  enviarResultado(resultado, estatisticas, modoSelecionado);
}

// ============================================================================
// CONFIGURACAO DO ADC / MEMORIA
// ============================================================================
bool configurarADCContinuo() {
  adc_continuous_handle_cfg_t configuracaoHandle = {};
  configuracaoHandle.max_store_buf_size = TAMANHO_POOL_DMA_BYTES;
  configuracaoHandle.conv_frame_size = TAMANHO_FRAME_BYTES;

  esp_err_t erro =
      adc_continuous_new_handle(&configuracaoHandle, &adcHandle);
  if (erro != ESP_OK) {
    etapaErroFatal = "adc_continuous_new_handle";
    codigoErroFatal = erro;
    return false;
  }

  padraoADC[0].atten = ADC_ATTEN_DB_12;
  padraoADC[0].channel = (uint8_t)CANAL_TENSAO;
  padraoADC[0].unit = ADC_UNIT_1;
  padraoADC[0].bit_width = SOC_ADC_DIGI_MAX_BITWIDTH;

  padraoADC[1].atten = ADC_ATTEN_DB_12;
  padraoADC[1].channel = (uint8_t)CANAL_CORRENTE;
  padraoADC[1].unit = ADC_UNIT_1;
  padraoADC[1].bit_width = SOC_ADC_DIGI_MAX_BITWIDTH;

  adc_continuous_config_t configuracaoADC = {};
  configuracaoADC.pattern_num = QUANTIDADE_CANAIS;
  configuracaoADC.adc_pattern = padraoADC;
  configuracaoADC.sample_freq_hz = TAXA_TOTAL_CONVERSOES_SPS;
  configuracaoADC.conv_mode = ADC_CONV_SINGLE_UNIT_1;
  configuracaoADC.format = ADC_DIGI_OUTPUT_FORMAT_TYPE1;

  erro = adc_continuous_config(adcHandle, &configuracaoADC);
  if (erro != ESP_OK) {
    etapaErroFatal = "adc_continuous_config";
    codigoErroFatal = erro;
    return false;
  }

  adc_continuous_evt_cbs_t callbacks = {};
  callbacks.on_conv_done = aoTerminarFrame;
  callbacks.on_pool_ovf = aoEncherPool;

  erro = adc_continuous_register_event_callbacks(adcHandle, &callbacks, nullptr);
  if (erro != ESP_OK) {
    etapaErroFatal = "adc_continuous_register_event_callbacks";
    codigoErroFatal = erro;
    return false;
  }
  return true;
}

bool alocarBuffers() {
  // Primeiro tenta colocar os 153,6 KiB de raw data na PSRAM, se existir.
  amostrasTensaoCompactadas = (uint8_t *)heap_caps_calloc(
      BYTES_COMPACTADOS_POR_CANAL, 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  amostrasCorrenteCompactadas = (uint8_t *)heap_caps_calloc(
      BYTES_COMPACTADOS_POR_CANAL, 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);

  if (amostrasTensaoCompactadas != nullptr &&
      amostrasCorrenteCompactadas != nullptr) {
    amostrasNaPSRAM = true;
  } else {
    if (amostrasTensaoCompactadas != nullptr) {
      heap_caps_free(amostrasTensaoCompactadas);
    }
    if (amostrasCorrenteCompactadas != nullptr) {
      heap_caps_free(amostrasCorrenteCompactadas);
    }

    amostrasTensaoCompactadas = (uint8_t *)heap_caps_calloc(
        BYTES_COMPACTADOS_POR_CANAL, 1, MALLOC_CAP_8BIT);
    amostrasCorrenteCompactadas = (uint8_t *)heap_caps_calloc(
        BYTES_COMPACTADOS_POR_CANAL, 1, MALLOC_CAP_8BIT);
    amostrasNaPSRAM = false;
  }

  if (amostrasTensaoCompactadas == nullptr ||
      amostrasCorrenteCompactadas == nullptr) {
    if (amostrasTensaoCompactadas != nullptr) {
      heap_caps_free(amostrasTensaoCompactadas);
      amostrasTensaoCompactadas = nullptr;
    }
    if (amostrasCorrenteCompactadas != nullptr) {
      heap_caps_free(amostrasCorrenteCompactadas);
      amostrasCorrenteCompactadas = nullptr;
    }
    return false;
  }

  // Uma FFT complexa recebe tensao na parte real e corrente na imaginaria.
  // Assim os dois espectros usam somente 16.384 bytes de area de trabalho.
  bufferFFT = (ComplexoF *)heap_caps_malloc(
      sizeof(ComplexoF) * FFT_N, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  if (bufferFFT == nullptr) {
    bufferFFT = (ComplexoF *)heap_caps_malloc(
        sizeof(ComplexoF) * FFT_N, MALLOC_CAP_8BIT);
  }

  if (bufferFFT == nullptr) {
    return false;
  }

  heapLivreAposAlocacoes = heap_caps_get_free_size(
      MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  return true;
}

// ============================================================================
// BOTAO
// ============================================================================
bool detectarPressaoBotao() {
  static bool leituraAnterior = HIGH;
  static bool estadoEstavel = HIGH;
  static uint32_t instanteMudancaMs = 0;

  const bool leituraAtual = digitalRead(PIN_BOTAO);
  const uint32_t agoraMs = millis();

  if (leituraAtual != leituraAnterior) {
    leituraAnterior = leituraAtual;
    instanteMudancaMs = agoraMs;
  }

  if ((uint32_t)(agoraMs - instanteMudancaMs) >= DEBOUNCE_MS &&
      leituraAtual != estadoEstavel) {
    estadoEstavel = leituraAtual;
    return estadoEstavel == LOW;
  }
  return false;
}

bool reconhecerComandoBotao(ModoEnvio &modoSelecionado) {
  if (!detectarPressaoBotao()) {
    return false;
  }

  const uint32_t instantePrimeiroCliqueMs = millis();
  while ((uint32_t)(millis() - instantePrimeiroCliqueMs) <
         JANELA_DUPLO_CLIQUE_MS) {
    if (detectarPressaoBotao()) {
      modoSelecionado = ModoEnvio::APENAS_FEATURES;
      return true;
    }
    delay(1);
  }

  modoSelecionado = ModoEnvio::APENAS_RAW;
  return true;
}

void sinalizarModoSelecionado(ModoEnvio modoSelecionado) {
  const uint8_t piscadas =
      modoSelecionado == ModoEnvio::APENAS_RAW ? 1U : 2U;
  for (uint8_t indice = 0; indice < piscadas; ++indice) {
    digitalWrite(PIN_LED, HIGH);
    delay(DURACAO_PISCADA_MODO_MS);
    digitalWrite(PIN_LED, LOW);
    delay(DURACAO_PISCADA_MODO_MS);
  }
}

// ============================================================================
// AQUISICAO
// ============================================================================
ResultadoColeta coletarAmostras() {
  ResultadoColeta resultado = {};
  resultado.erro = ESP_OK;
  resultado.etapaErro = "nenhuma";

  memset(amostrasTensaoCompactadas, 0, BYTES_COMPACTADOS_POR_CANAL);
  memset(amostrasCorrenteCompactadas, 0, BYTES_COMPACTADOS_POR_CANAL);

  quantidadeFramesISR = 0;
  framesSemEspacoParaTimestamp = 0;
  eventosOverflowPool = 0;

  esp_err_t erro = adc_continuous_flush_pool(adcHandle);
  if (erro != ESP_OK) {
    resultado.erro = erro;
    resultado.etapaErro = "adc_continuous_flush_pool";
    return resultado;
  }

  resultado.tempoAntesStartUs = esp_timer_get_time();
  erro = adc_continuous_start(adcHandle);
  resultado.tempoDepoisStartUs = esp_timer_get_time();

  if (erro != ESP_OK) {
    resultado.erro = erro;
    resultado.etapaErro = "adc_continuous_start";
    return resultado;
  }
  resultado.adcFoiIniciado = true;

  bool continuar = true;
  while (continuar &&
         (resultado.amostrasTensao < AMOSTRAS_POR_CANAL ||
          resultado.amostrasCorrente < AMOSTRAS_POR_CANAL)) {
    uint32_t bytesLidos = 0;
    erro = adc_continuous_read(adcHandle, bufferLeituraDMA,
                               TAMANHO_LEITURA_DMA_BYTES,
                               &bytesLidos, 1000);

    if (erro == ESP_ERR_TIMEOUT) {
      if (esp_timer_get_time() - resultado.tempoAntesStartUs >
          TIMEOUT_TOTAL_COLETA_US) {
        resultado.erro = ESP_ERR_TIMEOUT;
        resultado.etapaErro = "timeout_total_coleta";
        break;
      }
      continue;
    }

    if (erro != ESP_OK) {
      resultado.erro = erro;
      resultado.etapaErro = "adc_continuous_read";
      break;
    }

    for (uint32_t deslocamento = 0;
         deslocamento + SOC_ADC_DIGI_RESULT_BYTES <= bytesLidos;
         deslocamento += SOC_ADC_DIGI_RESULT_BYTES) {
      const adc_digi_output_data_t *dado =
          reinterpret_cast<const adc_digi_output_data_t *>(
              &bufferLeituraDMA[deslocamento]);

      const uint32_t canal = dado->type1.channel;
      const uint16_t valor = (uint16_t)(dado->type1.data & 0x0FFFU);

      if (canal == (uint32_t)CANAL_TENSAO) {
        if (resultado.amostrasTensao < AMOSTRAS_POR_CANAL) {
          gravar12Bits(amostrasTensaoCompactadas,
                       resultado.amostrasTensao, valor);
          ++resultado.amostrasTensao;
        }
      } else if (canal == (uint32_t)CANAL_CORRENTE) {
        if (resultado.amostrasCorrente < AMOSTRAS_POR_CANAL) {
          gravar12Bits(amostrasCorrenteCompactadas,
                       resultado.amostrasCorrente, valor);
          ++resultado.amostrasCorrente;
        }
      } else {
        ++resultado.resultadosCanalDesconhecido;
      }

      if (resultado.amostrasTensao == AMOSTRAS_POR_CANAL &&
          resultado.amostrasCorrente == AMOSTRAS_POR_CANAL) {
        continuar = false;
        break;
      }
    }
  }

  resultado.tempoContagensAtingidasUs = esp_timer_get_time();
  const esp_err_t erroStop = adc_continuous_stop(adcHandle);
  resultado.tempoDepoisStopUs = esp_timer_get_time();

  if (resultado.erro == ESP_OK && erroStop != ESP_OK) {
    resultado.erro = erroStop;
    resultado.etapaErro = "adc_continuous_stop";
  }

  resultado.framesRegistrados = quantidadeFramesISR;
  resultado.framesNaoRegistrados = framesSemEspacoParaTimestamp;
  resultado.overflowsPool = eventosOverflowPool;
  resultado.coletaValida =
      resultado.erro == ESP_OK &&
      resultado.amostrasTensao == AMOSTRAS_POR_CANAL &&
      resultado.amostrasCorrente == AMOSTRAS_POR_CANAL &&
      resultado.resultadosCanalDesconhecido == 0 &&
      resultado.overflowsPool == 0;
  return resultado;
}

// ============================================================================
// COMPACTACAO DE 12 BITS
// ============================================================================
void gravar12Bits(uint8_t *buffer, uint32_t indice, uint16_t valor) {
  valor &= 0x0FFFU;
  const size_t posicao = ((size_t)indice / 2UL) * 3UL;

  if ((indice & 1UL) == 0) {
    buffer[posicao] = (uint8_t)(valor & 0x00FFU);
    buffer[posicao + 1UL] =
        (uint8_t)((buffer[posicao + 1UL] & 0xF0U) |
                  ((valor >> 8U) & 0x0FU));
  } else {
    buffer[posicao + 1UL] =
        (uint8_t)((buffer[posicao + 1UL] & 0x0FU) |
                  ((valor & 0x000FU) << 4U));
    buffer[posicao + 2UL] = (uint8_t)((valor >> 4U) & 0x00FFU);
  }
}

uint16_t ler12Bits(const uint8_t *buffer, uint32_t indice) {
  const size_t posicao = ((size_t)indice / 2UL) * 3UL;
  if ((indice & 1UL) == 0) {
    return (uint16_t)buffer[posicao] |
           ((uint16_t)(buffer[posicao + 1UL] & 0x0FU) << 8U);
  }
  return ((uint16_t)(buffer[posicao + 1UL] >> 4U) & 0x000FU) |
         ((uint16_t)buffer[posicao + 2UL] << 4U);
}

// ============================================================================
// ESTATISTICAS TEMPORAIS
// ============================================================================
EstatisticasTempo calcularEstatisticasTempo(
    const ResultadoColeta &resultado) {
  EstatisticasTempo estatisticas = {};

  if (!resultado.adcFoiIniciado ||
      resultado.framesRegistrados < FRAMES_ALVO ||
      resultado.framesNaoRegistrados != 0 ||
      resultado.overflowsPool != 0) {
    return estatisticas;
  }

  estatisticas.tempoFimPrimeiroFrameUs = temposFimFrameUs[0];
  estatisticas.tempoFimUltimoFrameAlvoUs =
      temposFimFrameUs[FRAMES_ALVO - 1UL];
  if (estatisticas.tempoFimUltimoFrameAlvoUs <=
      estatisticas.tempoFimPrimeiroFrameUs) {
    return estatisticas;
  }

  uint64_t conversoesEntreFinsDeFrame = 0;
  double media = 0.0;
  double somaQuadradosDiferencas = 0.0;
  double minimo = INFINITY;
  double maximo = 0.0;

  for (uint32_t indice = 1; indice < FRAMES_ALVO; ++indice) {
    const uint32_t conversoesNoIntervalo = conversoesEmCadaFrame[indice];
    const int64_t deltaFrameUs =
        temposFimFrameUs[indice] - temposFimFrameUs[indice - 1UL];
    if (conversoesNoIntervalo == 0 || deltaFrameUs <= 0) {
      return EstatisticasTempo{};
    }

    conversoesEntreFinsDeFrame += conversoesNoIntervalo;
    const double intervaloPorCanalUs =
        (double)deltaFrameUs * (double)QUANTIDADE_CANAIS /
        (double)conversoesNoIntervalo;
    minimo = fmin(minimo, intervaloPorCanalUs);
    maximo = fmax(maximo, intervaloPorCanalUs);

    ++estatisticas.intervalosDeFrame;
    const double diferenca = intervaloPorCanalUs - media;
    media += diferenca / (double)estatisticas.intervalosDeFrame;
    somaQuadradosDiferencas +=
        diferenca * (intervaloPorCanalUs - media);
  }

  if (conversoesEntreFinsDeFrame == 0 ||
      estatisticas.intervalosDeFrame == 0) {
    return EstatisticasTempo{};
  }

  const int64_t tempoEntreFinsDeFrameUs =
      estatisticas.tempoFimUltimoFrameAlvoUs -
      estatisticas.tempoFimPrimeiroFrameUs;
  estatisticas.intervaloConversaoEfetivoUs =
      (double)tempoEntreFinsDeFrameUs /
      (double)conversoesEntreFinsDeFrame;
  estatisticas.intervaloPorCanalMedioUs = media;
  estatisticas.intervaloPorCanalDesvioPadraoUs =
      sqrt(somaQuadradosDiferencas /
           (double)estatisticas.intervalosDeFrame);
  estatisticas.intervaloPorCanalMinimoUs = minimo;
  estatisticas.intervaloPorCanalMaximoUs = maximo;
  estatisticas.taxaEfetivaPorCanalSps =
      ((double)conversoesEntreFinsDeFrame /
       (double)QUANTIDADE_CANAIS) *
      1000000.0 / (double)tempoEntreFinsDeFrameUs;

  estatisticas.tempoPrimeiraConversaoEstimadoUs =
      (int64_t)llround(
          (double)estatisticas.tempoFimPrimeiroFrameUs -
          ((double)conversoesEmCadaFrame[0] - 1.0) *
              estatisticas.intervaloConversaoEfetivoUs);
  estatisticas.tempoUltimaConversaoEstimadoUs =
      (int64_t)llround(
          (double)estatisticas.tempoPrimeiraConversaoEstimadoUs +
          ((double)TOTAL_CONVERSOES_ALVO - 1.0) *
              estatisticas.intervaloConversaoEfetivoUs);
  estatisticas.validas = true;
  return estatisticas;
}

// ============================================================================
// FFT: DOIS SINAIS REAIS EM UMA FFT COMPLEXA
// ============================================================================
void fftRadix2(ComplexoF *dados, uint32_t tamanho) {
  // Permutacao bit-reversal.
  for (uint32_t i = 1, j = 0; i < tamanho; ++i) {
    uint32_t bit = tamanho >> 1U;
    for (; j & bit; bit >>= 1U) {
      j ^= bit;
    }
    j ^= bit;
    if (i < j) {
      const ComplexoF temporario = dados[i];
      dados[i] = dados[j];
      dados[j] = temporario;
    }
  }

  for (uint32_t comprimento = 2; comprimento <= tamanho;
       comprimento <<= 1U) {
    const float angulo = -2.0f * PI_F / (float)comprimento;
    const float passoReal = cosf(angulo);
    const float passoImag = sinf(angulo);
    const uint32_t metade = comprimento >> 1U;

    for (uint32_t inicio = 0; inicio < tamanho; inicio += comprimento) {
      float wReal = 1.0f;
      float wImag = 0.0f;
      for (uint32_t j = 0; j < metade; ++j) {
        const ComplexoF u = dados[inicio + j];
        const ComplexoF original = dados[inicio + j + metade];
        const ComplexoF v = {
            original.real * wReal - original.imag * wImag,
            original.real * wImag + original.imag * wReal,
        };

        dados[inicio + j] = {u.real + v.real, u.imag + v.imag};
        dados[inicio + j + metade] =
            {u.real - v.real, u.imag - v.imag};

        const float proximoReal =
            wReal * passoReal - wImag * passoImag;
        wImag = wReal * passoImag + wImag * passoReal;
        wReal = proximoReal;
      }
    }
  }
}

void calcularFeaturesJanela(
    uint32_t janela,
    float magnitudes[QUANTIDADE_CANAIS][HARMONICA_MAXIMA + 1UL],
    float rms[QUANTIDADE_CANAIS]) {
  const uint32_t primeiroIndice = janela * FFT_N;
  double somaTensao = 0.0;
  double somaCorrente = 0.0;

  for (uint32_t n = 0; n < FFT_N; ++n) {
    somaTensao +=
        ler12Bits(amostrasTensaoCompactadas, primeiroIndice + n);
    somaCorrente +=
        ler12Bits(amostrasCorrenteCompactadas, primeiroIndice + n);
  }

  const float mediaTensao = (float)(somaTensao / (double)FFT_N);
  const float mediaCorrente = (float)(somaCorrente / (double)FFT_N);
  double somaQuadradosTensao = 0.0;
  double somaQuadradosCorrente = 0.0;

  for (uint32_t n = 0; n < FFT_N; ++n) {
    const float tensaoSemDC =
        (float)ler12Bits(amostrasTensaoCompactadas, primeiroIndice + n) -
        mediaTensao;
    const float correnteSemDC =
        (float)ler12Bits(amostrasCorrenteCompactadas, primeiroIndice + n) -
        mediaCorrente;
    bufferFFT[n].real = tensaoSemDC;
    bufferFFT[n].imag = correnteSemDC;
    somaQuadradosTensao +=
        (double)tensaoSemDC * (double)tensaoSemDC;
    somaQuadradosCorrente +=
        (double)correnteSemDC * (double)correnteSemDC;
  }

  rms[0] = (float)sqrt(somaQuadradosTensao / (double)FFT_N);
  rms[1] = (float)sqrt(somaQuadradosCorrente / (double)FFT_N);

  // Nao e aplicada Hann: esta versao usa janela retangular de 200 ms e soma
  // energetica do bin da harmonica com os dois bins adjacentes.
  fftRadix2(bufferFFT, FFT_N);

  memset(magnitudes, 0,
         sizeof(float) * QUANTIDADE_CANAIS *
             (HARMONICA_MAXIMA + 1UL));

  for (uint32_t h = 1; h <= HARMONICA_MAXIMA; ++h) {
    const uint32_t binCentral = h * BINS_POR_HARMONICA;
    for (uint8_t canal = 0; canal < QUANTIDADE_CANAIS; ++canal) {
      double somaQuadrados = 0.0;
      for (int32_t deslocamento = -(int32_t)RAIO_SUBGRUPO_BINS;
           deslocamento <= (int32_t)RAIO_SUBGRUPO_BINS;
           ++deslocamento) {
        const uint32_t bin =
            (uint32_t)((int32_t)binCentral + deslocamento);
        const double componente = magnitudeBinRms(bin, canal);
        somaQuadrados += componente * componente;
      }
      magnitudes[canal][h] = (float)sqrt(somaQuadrados);
    }
  }
}

float magnitudeBinRms(uint32_t bin, uint8_t canal) {
  const ComplexoF zPositivo = bufferFFT[bin];
  const ComplexoF zNegativo = bufferFFT[FFT_N - bin];

  float real = 0.0f;
  float imag = 0.0f;
  if (canal == 0) {
    // X[k] = (Z[k] + conj(Z[N-k])) / 2
    real = 0.5f * (zPositivo.real + zNegativo.real);
    imag = 0.5f * (zPositivo.imag - zNegativo.imag);
  } else {
    // Y[k] = (Z[k] - conj(Z[N-k])) / (2j)
    real = 0.5f * (zPositivo.imag + zNegativo.imag);
    imag = 0.5f * (zNegativo.real - zPositivo.real);
  }

  const float modulo = sqrtf(real * real + imag * imag);
  return (sqrtf(2.0f) / (float)FFT_N) * modulo;
}

double calcularTHD50(
    const float magnitudes[HARMONICA_MAXIMA + 1UL]) {
  const double fundamental = magnitudes[1];
  if (!(fundamental > 1.0e-9) || !std::isfinite(fundamental)) {
    return NAN;
  }

  double somaQuadrados = 0.0;
  for (uint32_t h = 2; h <= HARMONICA_MAXIMA; ++h) {
    const double valor = magnitudes[h];
    somaQuadrados += valor * valor;
  }
  return sqrt(somaQuadrados) / fundamental * 100.0;
}

// ============================================================================
// EXPORTACAO: METADADOS -> SECAO SELECIONADA PELO BOTAO
// ============================================================================
void enviarResultado(const ResultadoColeta &resultado,
                     const EstatisticasTempo &estatisticas,
                     ModoEnvio modoSelecionado) {
  double intervaloConversaoReconstrucaoUs =
      INTERVALO_NOMINAL_CONVERSAO_US;
  int64_t tempoPrimeiraConversaoReconstrucaoUs =
      resultado.tempoDepoisStartUs;
  if (estatisticas.validas) {
    intervaloConversaoReconstrucaoUs =
        estatisticas.intervaloConversaoEfetivoUs;
    tempoPrimeiraConversaoReconstrucaoUs =
        estatisticas.tempoPrimeiraConversaoEstimadoUs;
  }

  Serial.println("BEGIN_COLETA_BOTAO_V4");
  checksumMetadadosTransmissao = 2166136261UL;
  enviarMetaTexto("status_coleta", resultado.coletaValida ? "OK" : "ERRO");
  enviarMetaTexto("etapa_erro", resultado.etapaErro);
  enviarMetaTexto("codigo_erro_esp", esp_err_to_name(resultado.erro));
  enviarMetaTexto("modo_envio", nomeModoEnvio(modoSelecionado));
  enviarMetaInteira("cliques_botao",
                    modoSelecionado == ModoEnvio::APENAS_RAW ? 1 : 2,
                    "cliques");
  enviarMetaInteira("janela_duplo_clique",
                    JANELA_DUPLO_CLIQUE_MS, "ms");

  enviarMetaInteira("amostras_esperadas_por_canal",
                    AMOSTRAS_POR_CANAL, "amostras");
  enviarMetaInteira("amostras_tensao_coletadas",
                    resultado.amostrasTensao, "amostras");
  enviarMetaInteira("amostras_corrente_coletadas",
                    resultado.amostrasCorrente, "amostras");
  enviarMetaInteira("duracao_nominal", DURACAO_COLETA_US, "us");
  enviarMetaInteira("taxa_programada_por_canal",
                    TAXA_POR_CANAL_SPS, "amostras/s");
  enviarMetaInteira("taxa_programada_total_adc",
                    TAXA_TOTAL_CONVERSOES_SPS, "conversoes/s");
  enviarMetaDouble("intervalo_nominal_por_canal",
                   INTERVALO_NOMINAL_POR_CANAL_US, 9, "us");

  enviarMetaInteira("tempo_inicio_monotonico",
                    resultado.tempoAntesStartUs, "us",
                    "imediatamente_antes_de_adc_continuous_start");
  enviarMetaInteira("tempo_depois_start",
                    resultado.tempoDepoisStartUs, "us");
  enviarMetaInteira("tempo_contagens_atingidas",
                    resultado.tempoContagensAtingidasUs, "us");
  enviarMetaInteira("tempo_final_monotonico",
                    resultado.tempoDepoisStopUs, "us",
                    "imediatamente_depois_de_adc_continuous_stop");
  enviarMetaInteira("duracao_inicio_ate_final",
                    resultado.tempoDepoisStopUs - resultado.tempoAntesStartUs,
                    "us", "inclui_latencia_de_start_e_stop");

  enviarMetaInteira("tempo_primeira_tensao_estimado",
                    tempoPrimeiraConversaoReconstrucaoUs, "us");
  enviarMetaDouble("intervalo_conversao_reconstrucao",
                   intervaloConversaoReconstrucaoUs, 9, "us");
  enviarMetaDouble("intervalo_por_canal_reconstrucao",
                   intervaloConversaoReconstrucaoUs * QUANTIDADE_CANAIS,
                   9, "us");
  enviarMetaTexto(
      "metodo_tempos_amostras",
      estatisticas.validas ? "ESTIMADO_PELOS_BLOCOS_DMA" : "NOMINAL",
      "nao_sao_timestamps_individuais_de_hardware");

  enviarMetaInteira("frames_dma_alvo", FRAMES_ALVO, "frames");
  enviarMetaInteira("frames_dma_registrados",
                    resultado.framesRegistrados, "frames");
  enviarMetaInteira("frames_sem_timestamp",
                    resultado.framesNaoRegistrados, "frames");
  enviarMetaInteira("overflows_pool_dma",
                    resultado.overflowsPool, "eventos");
  enviarMetaInteira("resultados_canal_desconhecido",
                    resultado.resultadosCanalDesconhecido, "resultados");

  enviarMetaTexto("memoria_amostras",
                  amostrasNaPSRAM ? "PSRAM" : "RAM_INTERNA");
  enviarMetaInteira("bytes_raw_compactado_dois_canais",
                    2ULL * BYTES_COMPACTADOS_POR_CANAL, "bytes");
  enviarMetaInteira("bytes_buffer_fft",
                    sizeof(ComplexoF) * FFT_N, "bytes");
  enviarMetaInteira("heap_interno_livre_apos_alocacoes",
                    heapLivreAposAlocacoes, "bytes");

  enviarMetaInteira("fft_pontos", FFT_N, "pontos");
  enviarMetaInteira("fft_janelas", QUANTIDADE_JANELAS, "janelas");
  enviarMetaInteira("fft_duracao_janela", 200, "ms");
  enviarMetaInteira("fft_resolucao", RESOLUCAO_FFT_HZ, "Hz");
  enviarMetaInteira("harmonica_maxima", HARMONICA_MAXIMA, "ordem");
  enviarMetaInteira("features_linhas_esperadas",
                    LINHAS_FEATURES_ESPERADAS, "linhas");
  enviarMetaTexto("janela_fft", "RETANGULAR_200_MS");
  enviarMetaTexto("metodo_harmonicas",
                  "SUBGRUPO_ENERGETICO_BIN_CENTRAL_MAIS_ADJACENTES");
  enviarMetaTexto("metodo_thd50",
                  "SQRT_SOMA_H2_A_H50_QUADRADO_DIV_H1");
  enviarMetaTexto("metodo_vrms_irms",
                  "SQRT_MEDIA_QUADRADO_APOS_REMOCAO_DC_POR_JANELA");
  enviarMetaTexto("unidade_features_amplitude", "CONTAGEM_ADC_RMS",
                  "converter_para_V_e_A_exige_calibracao");

  if (estatisticas.validas) {
    enviarMetaDouble("taxa_efetiva_por_canal_blocos_dma",
                     estatisticas.taxaEfetivaPorCanalSps,
                     6, "amostras/s");
    enviarMetaDouble("intervalo_por_canal_medio_blocos_dma",
                     estatisticas.intervaloPorCanalMedioUs,
                     6, "us");
    enviarMetaDouble("intervalo_por_canal_desvio_padrao_blocos_dma",
                     estatisticas.intervaloPorCanalDesvioPadraoUs,
                     6, "us");
    enviarMetaDouble("intervalo_por_canal_minimo_blocos_dma",
                     estatisticas.intervaloPorCanalMinimoUs,
                     6, "us");
    enviarMetaDouble("intervalo_por_canal_maximo_blocos_dma",
                     estatisticas.intervaloPorCanalMaximoUs,
                     6, "us");
  }

  Serial.print("META_END,");
  Serial.printf("%08lX\n", (unsigned long)checksumMetadadosTransmissao);

  Serial.println("FEATURES_BEGIN");
  Serial.println(
      "tipo,janela,canal,harmonico,frequencia_nominal_hz,valor,unidade");
  ResultadoEnvioFeatures features = {};
  features.checksum = 2166136261UL;
  if (resultado.coletaValida &&
      modoSelecionado == ModoEnvio::APENAS_FEATURES) {
    features = calcularEEnviarFeatures();
  }
  Serial.print("FEATURES_END,");
  Serial.print(features.linhasEnviadas);
  Serial.print(',');
  Serial.printf("%08lX,", (unsigned long)features.checksum);
  Serial.printf("%lld\n", (long long)features.tempoCalculoUs);

  enviarRaw(resultado, modoSelecionado);
  Serial.println("END_COLETA_BOTAO_V4");
  Serial.flush();
}

ResultadoEnvioFeatures calcularEEnviarFeatures() {
  ResultadoEnvioFeatures resultado = {};
  resultado.checksum = 2166136261UL;

  double somaQuadradosFFT[QUANTIDADE_CANAIS]
                           [HARMONICA_MAXIMA + 1UL] = {};
  double somaQuadradosRMS[QUANTIDADE_CANAIS] = {};
  float magnitudes[QUANTIDADE_CANAIS][HARMONICA_MAXIMA + 1UL] = {};
  float rms[QUANTIDADE_CANAIS] = {};
  const char *nomesCanais[QUANTIDADE_CANAIS] = {"TENSAO", "CORRENTE"};
  const char *tiposFFT[QUANTIDADE_CANAIS] = {"VFFT", "IFFT"};
  const char *tiposTHD[QUANTIDADE_CANAIS] = {"VTHD50", "ITHD50"};
  const char *tiposRMS[QUANTIDADE_CANAIS] = {"VRMS", "IRMS"};
  const char *tiposFFT5s[QUANTIDADE_CANAIS] = {
      "VFFT_RMS_5S", "IFFT_RMS_5S"};
  const char *tiposTHD5s[QUANTIDADE_CANAIS] = {
      "VTHD50_5S", "ITHD50_5S"};
  const char *tiposRMS5s[QUANTIDADE_CANAIS] = {
      "VRMS_5S", "IRMS_5S"};
  char linha[128];

  for (uint32_t janela = 0; janela < QUANTIDADE_JANELAS; ++janela) {
    const int64_t inicioCalculoUs = esp_timer_get_time();
    calcularFeaturesJanela(janela, magnitudes, rms);
    resultado.tempoCalculoUs += esp_timer_get_time() - inicioCalculoUs;

    for (uint8_t canal = 0; canal < QUANTIDADE_CANAIS; ++canal) {
      somaQuadradosRMS[canal] += (double)rms[canal] * (double)rms[canal];
      snprintf(linha, sizeof(linha),
               "%s,%lu,%s,,,%.6f,contagem_adc_rms",
               tiposRMS[canal], (unsigned long)janela,
               nomesCanais[canal], (double)rms[canal]);
      enviarLinhaComChecksum(linha, resultado.checksum,
                             resultado.linhasEnviadas);

      for (uint32_t h = 1; h <= HARMONICA_MAXIMA; ++h) {
        const double valor = magnitudes[canal][h];
        somaQuadradosFFT[canal][h] += valor * valor;
        snprintf(linha, sizeof(linha),
                 "%s,%lu,%s,%lu,%lu,%.6f,contagem_adc_rms",
                 tiposFFT[canal], (unsigned long)janela,
                 nomesCanais[canal],
                 (unsigned long)h,
                 (unsigned long)(h * FREQUENCIA_FUNDAMENTAL_NOMINAL_HZ),
                 valor);
        enviarLinhaComChecksum(linha, resultado.checksum,
                               resultado.linhasEnviadas);
      }

      const double thd50 = calcularTHD50(magnitudes[canal]);
      snprintf(linha, sizeof(linha),
               "%s,%lu,%s,,,%.6f,percent",
               tiposTHD[canal], (unsigned long)janela,
               nomesCanais[canal], thd50);
      enviarLinhaComChecksum(linha, resultado.checksum,
                             resultado.linhasEnviadas);
    }
    delay(0);
  }

  for (uint8_t canal = 0; canal < QUANTIDADE_CANAIS; ++canal) {
    const double rmsGlobal =
        sqrt(somaQuadradosRMS[canal] / (double)QUANTIDADE_JANELAS);
    snprintf(linha, sizeof(linha),
             "%s,,%s,,,%.6f,contagem_adc_rms",
             tiposRMS5s[canal], nomesCanais[canal], rmsGlobal);
    enviarLinhaComChecksum(linha, resultado.checksum,
                           resultado.linhasEnviadas);

    double somaHarmonicasGlobais = 0.0;
    for (uint32_t h = 1; h <= HARMONICA_MAXIMA; ++h) {
      const double rms5s =
          sqrt(somaQuadradosFFT[canal][h] /
               (double)QUANTIDADE_JANELAS);
      if (h >= 2) {
        somaHarmonicasGlobais += somaQuadradosFFT[canal][h];
      }
      snprintf(linha, sizeof(linha),
               "%s,,%s,%lu,%lu,%.6f,contagem_adc_rms",
               tiposFFT5s[canal], nomesCanais[canal],
               (unsigned long)h,
               (unsigned long)(h * FREQUENCIA_FUNDAMENTAL_NOMINAL_HZ),
               rms5s);
      enviarLinhaComChecksum(linha, resultado.checksum,
                             resultado.linhasEnviadas);
    }

    const double fundamentalGlobalQuadrado =
        somaQuadradosFFT[canal][1];
    const double thdGlobal = fundamentalGlobalQuadrado > 1.0e-18
                                 ? sqrt(somaHarmonicasGlobais /
                                        fundamentalGlobalQuadrado) *
                                       100.0
                                 : NAN;
    snprintf(linha, sizeof(linha),
             "%s,,%s,,,%.6f,percent",
             tiposTHD5s[canal], nomesCanais[canal], thdGlobal);
    enviarLinhaComChecksum(linha, resultado.checksum,
                           resultado.linhasEnviadas);
  }

  return resultado;
}

void enviarRaw(const ResultadoColeta &resultado,
               ModoEnvio modoSelecionado) {
  Serial.println("RAW_BEGIN");
  Serial.println("indice,tensao_raw_gpio36,corrente_raw_gpio39");

  uint32_t checksum = 2166136261UL;
  uint32_t quantidadeEnviada = 0;
  if (resultado.coletaValida &&
      modoSelecionado == ModoEnvio::APENAS_RAW) {
    char linha[32];
    for (uint32_t indice = 0; indice < AMOSTRAS_POR_CANAL; ++indice) {
      const uint16_t tensao =
          ler12Bits(amostrasTensaoCompactadas, indice);
      const uint16_t corrente =
          ler12Bits(amostrasCorrenteCompactadas, indice);
      const int tamanho = snprintf(linha, sizeof(linha), "%lu,%u,%u\n",
                                   (unsigned long)indice,
                                   (unsigned int)tensao,
                                   (unsigned int)corrente);
      if (tamanho > 0 && tamanho < (int)sizeof(linha)) {
        Serial.write(reinterpret_cast<const uint8_t *>(linha),
                     (size_t)tamanho);
      }
      checksum =
          atualizarChecksumAmostra(checksum, indice, tensao, corrente);
      ++quantidadeEnviada;
      if ((indice & 1023UL) == 1023UL) {
        delay(0);
      }
    }
  }

  Serial.print("RAW_END,");
  Serial.print(quantidadeEnviada);
  Serial.print(',');
  Serial.printf("%08lX\n", (unsigned long)checksum);
}

void enviarErroFatal(ModoEnvio modoSelecionado) {
  Serial.println("BEGIN_COLETA_BOTAO_V4");
  checksumMetadadosTransmissao = 2166136261UL;
  enviarMetaTexto("status_coleta", "ERRO_FATAL");
  enviarMetaTexto("etapa_erro", etapaErroFatal);
  enviarMetaTexto("codigo_erro_esp", esp_err_to_name(codigoErroFatal));
  enviarMetaTexto("modo_envio", nomeModoEnvio(modoSelecionado));
  Serial.print("META_END,");
  Serial.printf("%08lX\n", (unsigned long)checksumMetadadosTransmissao);
  Serial.println("FEATURES_BEGIN");
  Serial.println(
      "tipo,janela,canal,harmonico,frequencia_nominal_hz,valor,unidade");
  Serial.println("FEATURES_END,0,811C9DC5,0");
  Serial.println("RAW_BEGIN");
  Serial.println("indice,tensao_raw_gpio36,corrente_raw_gpio39");
  Serial.println("RAW_END,0,811C9DC5");
  Serial.println("END_COLETA_BOTAO_V4");
  Serial.flush();
}

// ============================================================================
// CHECKSUMS E METADADOS
// ============================================================================
void enviarMetaInteira(const char *nome, int64_t valor,
                       const char *unidade, const char *observacao) {
  char linha[256];
  const int tamanho = snprintf(linha, sizeof(linha), "META,%s,%lld,%s,%s",
                               nome, (long long)valor,
                               unidade, observacao);
  if (tamanho <= 0 || tamanho >= (int)sizeof(linha)) {
    return;
  }
  checksumMetadadosTransmissao = atualizarChecksumBytes(
      checksumMetadadosTransmissao,
      reinterpret_cast<const uint8_t *>(linha), (size_t)tamanho);
  Serial.write(reinterpret_cast<const uint8_t *>(linha), (size_t)tamanho);
  Serial.write('\n');
}

void enviarMetaDouble(const char *nome, double valor, uint8_t casas,
                      const char *unidade, const char *observacao) {
  char linha[256];
  const int tamanho = snprintf(linha, sizeof(linha),
                               "META,%s,%.*f,%s,%s",
                               nome, (int)casas, valor,
                               unidade, observacao);
  if (tamanho <= 0 || tamanho >= (int)sizeof(linha)) {
    return;
  }
  checksumMetadadosTransmissao = atualizarChecksumBytes(
      checksumMetadadosTransmissao,
      reinterpret_cast<const uint8_t *>(linha), (size_t)tamanho);
  Serial.write(reinterpret_cast<const uint8_t *>(linha), (size_t)tamanho);
  Serial.write('\n');
}

void enviarMetaTexto(const char *nome, const char *valor,
                     const char *observacao) {
  char linha[256];
  const int tamanho = snprintf(linha, sizeof(linha), "META,%s,%s,,%s",
                               nome, valor, observacao);
  if (tamanho <= 0 || tamanho >= (int)sizeof(linha)) {
    return;
  }
  checksumMetadadosTransmissao = atualizarChecksumBytes(
      checksumMetadadosTransmissao,
      reinterpret_cast<const uint8_t *>(linha), (size_t)tamanho);
  Serial.write(reinterpret_cast<const uint8_t *>(linha), (size_t)tamanho);
  Serial.write('\n');
}

void enviarLinhaComChecksum(const char *linha, uint32_t &checksum,
                            uint32_t &quantidade) {
  const size_t tamanho = strlen(linha);
  checksum = atualizarChecksumBytes(
      checksum, reinterpret_cast<const uint8_t *>(linha), tamanho);
  Serial.write(reinterpret_cast<const uint8_t *>(linha), tamanho);
  Serial.write('\n');
  ++quantidade;
}

uint32_t atualizarChecksumAmostra(uint32_t checksum, uint32_t indice,
                                 uint16_t tensao, uint16_t corrente) {
  const uint8_t bytes[8] = {
      (uint8_t)(indice & 0xFFU),
      (uint8_t)((indice >> 8U) & 0xFFU),
      (uint8_t)((indice >> 16U) & 0xFFU),
      (uint8_t)((indice >> 24U) & 0xFFU),
      (uint8_t)(tensao & 0xFFU),
      (uint8_t)((tensao >> 8U) & 0xFFU),
      (uint8_t)(corrente & 0xFFU),
      (uint8_t)((corrente >> 8U) & 0xFFU),
  };
  return atualizarChecksumBytes(checksum, bytes, sizeof(bytes));
}

uint32_t atualizarChecksumBytes(uint32_t checksum, const uint8_t *dados,
                                size_t tamanho) {
  for (size_t indice = 0; indice < tamanho; ++indice) {
    checksum ^= dados[indice];
    checksum *= 16777619UL;
  }
  return checksum;
}

const char *nomeModoEnvio(ModoEnvio modoSelecionado) {
  switch (modoSelecionado) {
    case ModoEnvio::APENAS_FEATURES:
      return "APENAS_FEATURES";
    case ModoEnvio::APENAS_RAW:
    default:
      return "APENAS_RAW";
  }
}
