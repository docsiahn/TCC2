#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <arduinoFFT.h>
#include "esp_timer.h"
#include <math.h>

// =====================================================
// CONFIGURAÇÕES DE REDE
// =====================================================

const char* WIFI_SSID = "A56 de Iahn";
const char* WIFI_PASS = "naotemsenha";

// IP do PC onde está rodando o Mosquitto
const char* MQTT_BROKER = "10.233.205.5";
const uint16_t MQTT_PORT = 1883;

// =====================================================
// TÓPICOS MQTT
// =====================================================

const char* TOPIC_STATUS = "ied/status";
const char* TOPIC_EVENTOS = "ied/eventos";

const char* TOPIC_OSC_INICIO = "ied/osc/inicio";
const char* TOPIC_OSC_BATCH = "ied/osc/batch";
const char* TOPIC_OSC_FIM = "ied/osc/fim";

const char* TOPIC_FEATURE_INICIO = "ied/features/inicio";
const char* TOPIC_FEATURE_CICLO = "ied/features/ciclo";
const char* TOPIC_FEATURE_RESUMO = "ied/features/resumo";
const char* TOPIC_FEATURE_FIM = "ied/features/fim";

// =====================================================
// PINOS
// =====================================================

#define PIN_VP 36
#define PIN_VN 39
#define PIN_BOTAO 4

#ifndef LED_BUILTIN
#define LED_BUILTIN 2
#endif

#define PIN_LED LED_BUILTIN

// =====================================================
// CONFIGURAÇÕES DA AQUISIÇÃO
// =====================================================

#define F_REDE_HZ 60
#define AMOSTRAS_POR_CICLO 32
#define FS_HZ (F_REDE_HZ * AMOSTRAS_POR_CICLO)

#define TEMPO_AQUISICAO_S 5
#define NUM_REGISTROS (F_REDE_HZ * TEMPO_AQUISICAO_S)
#define NUM_AMOSTRAS_TOTAL (NUM_REGISTROS * AMOSTRAS_POR_CICLO)

#define BAUD_SERIAL 921600

#define ADC_MAX_VALUE 4095.0
#define ADC_REF_V 3.3

#define DEBOUNCE_MS 50
#define DOUBLE_CLICK_MS 350

#define MQTT_RECONNECT_INTERVAL_MS 3000
#define OSC_LED_INTERVAL_MS 120

// Envia 16 amostras por mensagem MQTT no modo osciloscópio
#define OSC_BATCH_SIZE 16

// =====================================================
// CALIBRAÇÃO
// =====================================================
//
// Inicialmente deixe 1.0.
// Depois calibre com:
// V_CAL = tensão real RMS / tensão ADC RMS
// I_CAL = corrente real RMS / corrente ADC RMS

const double V_CAL = 1.0;
const double I_CAL = 1.0;

// =====================================================
// ESTRUTURAS
// =====================================================

struct FeaturesCiclo {
  double vRMS;
  double iRMS;
  double vFundRMS;
  double iFundRMS;
  double thdV;
  double thdI;
  double pAtiva;
  double sAparente;
  double fp;
};

struct ResumoFeatures {
  double vRMS;
  double iRMS;
  double vFundRMS;
  double iFundRMS;
  double thdV;
  double thdI;
  double pAtiva;
  double sAparente;
  double fp;
};

enum AcaoBotao {
  BOTAO_NENHUM,
  BOTAO_CLIQUE_SIMPLES,
  BOTAO_CLIQUE_DUPLO
};

// =====================================================
// BUFFERS DE AQUISIÇÃO E PROCESSAMENTO
// =====================================================

uint16_t dadosV[NUM_AMOSTRAS_TOTAL];
uint16_t dadosI[NUM_AMOSTRAS_TOTAL];

FeaturesCiclo features[NUM_REGISTROS];

double sinalV[AMOSTRAS_POR_CICLO];
double sinalI[AMOSTRAS_POR_CICLO];

double fftRealV[AMOSTRAS_POR_CICLO];
double fftImagV[AMOSTRAS_POR_CICLO];

double fftRealI[AMOSTRAS_POR_CICLO];
double fftImagI[AMOSTRAS_POR_CICLO];

ArduinoFFT<double> FFT_V = ArduinoFFT<double>(
  fftRealV,
  fftImagV,
  AMOSTRAS_POR_CICLO,
  FS_HZ
);

ArduinoFFT<double> FFT_I = ArduinoFFT<double>(
  fftRealI,
  fftImagI,
  AMOSTRAS_POR_CICLO,
  FS_HZ
);

// =====================================================
// BUFFERS DO OSCILOSCÓPIO MQTT
// =====================================================

uint16_t oscVpBatch[OSC_BATCH_SIZE];
uint16_t oscVnBatch[OSC_BATCH_SIZE];

uint32_t oscT0BatchUs = 0;
uint16_t oscBatchCount = 0;
uint32_t oscSeq = 0;

// =====================================================
// WIFI E MQTT
// =====================================================

WiFiClient espClient;
PubSubClient mqttClient(espClient);

unsigned long ultimaTentativaMQTT = 0;

// =====================================================
// ESTADOS
// =====================================================

bool modoOsciloscopio = false;

int64_t oscTempoInicioUs = 0;
uint32_t oscN = 0;

unsigned long ultimoPiscaLed = 0;

// =====================================================
// PROTÓTIPOS
// =====================================================

void configurarHardware();

void conectarWiFi();
void configurarMQTT();
void manterConexoes();
bool conectarMQTT();

void publicarStatus(const char* msg);
void publicarEvento(const char* msg);

AcaoBotao lerBotao();

void iniciarModoOsciloscopio();
void pararModoOsciloscopio();
void executarModoOsciloscopioMQTT();
void publicarBatchOsciloscopio();
void limparBatchOsciloscopio();
void piscarLEDRapido(unsigned long agoraMs);

void executarColetaProcessamentoEnvio();
void adquirirAmostras();
void processarTodosOsCiclos();
void processarCiclo(int registro);

double calcularMediaRaw(const uint16_t* x, int inicio, int n);

void converterCicloParaSinal(
  const uint16_t* raw,
  double* sinal,
  int inicio,
  int n,
  double mediaRaw,
  double fatorCalibracao
);

double calcularRMS(const double* x, int n);
double calcularPotenciaAtiva(const double* v, const double* i, int n);

void calcularFFTArduino(
  const double* sinal,
  double* fftReal,
  double* fftImag,
  ArduinoFFT<double>& FFT
);

double calcularFundamentalRMS(const double* mag);
double calcularTHD(const double* mag);

void publicarFeaturesMQTT();
ResumoFeatures calcularResumo();
void publicarResumoMQTT(const ResumoFeatures& r);

// =====================================================
// SETUP
// =====================================================

void setup() {
  Serial.setTxBufferSize(8192);
  Serial.begin(BAUD_SERIAL);

  configurarHardware();

  conectarWiFi();
  configurarMQTT();
  conectarMQTT();

  publicarStatus("IED iniciado");
}

// =====================================================
// LOOP PRINCIPAL
// =====================================================

void loop() {
  manterConexoes();

  AcaoBotao acao = lerBotao();

  if (acao == BOTAO_CLIQUE_DUPLO) {
    if (modoOsciloscopio) {
      pararModoOsciloscopio();
    } else {
      iniciarModoOsciloscopio();
    }
  }

  if (acao == BOTAO_CLIQUE_SIMPLES) {
    if (modoOsciloscopio) {
      pararModoOsciloscopio();
    }

    executarColetaProcessamentoEnvio();
  }

  if (modoOsciloscopio) {
    executarModoOsciloscopioMQTT();
    piscarLEDRapido(millis());
  } else {
    digitalWrite(PIN_LED, LOW);
  }

  mqttClient.loop();
}

// =====================================================
// HARDWARE
// =====================================================

void configurarHardware() {
  pinMode(PIN_BOTAO, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);

  analogReadResolution(12);

  analogSetPinAttenuation(PIN_VP, ADC_11db);
  analogSetPinAttenuation(PIN_VN, ADC_11db);
}

// =====================================================
// WIFI / MQTT
// =====================================================

void conectarWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  Serial.println();
  Serial.println("Conectando ao WiFi...");

  unsigned long inicio = millis();

  while (WiFi.status() != WL_CONNECTED && millis() - inicio < 20000) {
    digitalWrite(PIN_LED, !digitalRead(PIN_LED));
    delay(250);
    Serial.print(".");
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("WiFi conectado.");
    Serial.print("IP do ESP32: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Falha ao conectar no WiFi.");
  }

  digitalWrite(PIN_LED, LOW);
}

void configurarMQTT() {
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);

  // Necessário para os pacotes JSON do osciloscópio
  mqttClient.setBufferSize(1024);

  mqttClient.setKeepAlive(30);
}

void manterConexoes() {
  if (WiFi.status() != WL_CONNECTED) {
    conectarWiFi();
  }

  if (!mqttClient.connected()) {
    unsigned long agora = millis();

    if (agora - ultimaTentativaMQTT >= MQTT_RECONNECT_INTERVAL_MS) {
      ultimaTentativaMQTT = agora;
      conectarMQTT();
    }
  }
}

bool conectarMQTT() {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  uint64_t chipid = ESP.getEfuseMac();

  String clientId = "IED-ESP32-";
  clientId += String((uint32_t)(chipid & 0xFFFFFFFF), HEX);

  Serial.print("Conectando ao MQTT em ");
  Serial.print(MQTT_BROKER);
  Serial.print(":");
  Serial.print(MQTT_PORT);
  Serial.print(" ... ");

  bool ok = mqttClient.connect(clientId.c_str());

  if (ok) {
    Serial.println("conectado.");
    publicarStatus("MQTT conectado");
  } else {
    Serial.print("falhou. Estado: ");
    Serial.println(mqttClient.state());
  }

  return ok;
}

void publicarStatus(const char* msg) {
  if (!mqttClient.connected()) {
    return;
  }

  char payload[256];

  snprintf(
    payload,
    sizeof(payload),
    "{\"status\":\"%s\",\"uptime_ms\":%lu,\"ip\":\"%s\"}",
    msg,
    millis(),
    WiFi.localIP().toString().c_str()
  );

  mqttClient.publish(TOPIC_STATUS, payload, true);
}

void publicarEvento(const char* msg) {
  if (!mqttClient.connected()) {
    return;
  }

  char payload[256];

  snprintf(
    payload,
    sizeof(payload),
    "{\"evento\":\"%s\",\"uptime_ms\":%lu}",
    msg,
    millis()
  );

  mqttClient.publish(TOPIC_EVENTOS, payload, false);
}

// =====================================================
// BOTÃO
// =====================================================

AcaoBotao lerBotao() {
  if (digitalRead(PIN_BOTAO) == HIGH) {
    return BOTAO_NENHUM;
  }

  delay(DEBOUNCE_MS);

  if (digitalRead(PIN_BOTAO) == HIGH) {
    return BOTAO_NENHUM;
  }

  while (digitalRead(PIN_BOTAO) == LOW) {
    mqttClient.loop();
    delay(5);
  }

  unsigned long soltouMs = millis();

  while (millis() - soltouMs < DOUBLE_CLICK_MS) {
    if (digitalRead(PIN_BOTAO) == LOW) {
      delay(DEBOUNCE_MS);

      if (digitalRead(PIN_BOTAO) == LOW) {
        while (digitalRead(PIN_BOTAO) == LOW) {
          mqttClient.loop();
          delay(5);
        }

        return BOTAO_CLIQUE_DUPLO;
      }
    }

    mqttClient.loop();
    delay(5);
  }

  return BOTAO_CLIQUE_SIMPLES;
}

// =====================================================
// OSCILOSCÓPIO VIA MQTT
// =====================================================

void iniciarModoOsciloscopio() {
  modoOsciloscopio = true;

  oscTempoInicioUs = esp_timer_get_time();
  oscN = 0;
  oscSeq = 0;

  limparBatchOsciloscopio();

  publicarEvento("modo_osciloscopio_mqtt_iniciado");

  mqttClient.publish(
    TOPIC_OSC_INICIO,
    "{\"evento\":\"inicio_osciloscopio\",\"fs\":1920,\"canais\":[\"VP\",\"VN\"]}",
    false
  );

  Serial.println("Osciloscopio MQTT iniciado.");
}

void pararModoOsciloscopio() {
  if (oscBatchCount > 0) {
    publicarBatchOsciloscopio();
  }

  modoOsciloscopio = false;
  digitalWrite(PIN_LED, LOW);

  publicarEvento("modo_osciloscopio_mqtt_parado");

  mqttClient.publish(
    TOPIC_OSC_FIM,
    "{\"evento\":\"fim_osciloscopio\"}",
    false
  );

  Serial.println("Osciloscopio MQTT parado.");
}

void limparBatchOsciloscopio() {
  oscBatchCount = 0;
  oscT0BatchUs = 0;
}

void executarModoOsciloscopioMQTT() {
  int64_t tempoAlvoUs =
    oscTempoInicioUs + ((int64_t)oscN * 1000000LL) / FS_HZ;

  if (esp_timer_get_time() < tempoAlvoUs) {
    return;
  }

  int64_t agoraUs = esp_timer_get_time();
  uint32_t tempoRelativoUs = (uint32_t)(agoraUs - oscTempoInicioUs);

  uint16_t vp = analogRead(PIN_VP);
  uint16_t vn = analogRead(PIN_VN);

  if (oscBatchCount == 0) {
    oscT0BatchUs = tempoRelativoUs;
  }

  oscVpBatch[oscBatchCount] = vp;
  oscVnBatch[oscBatchCount] = vn;
  oscBatchCount++;

  oscN++;

  if (oscBatchCount >= OSC_BATCH_SIZE) {
    publicarBatchOsciloscopio();
  }
}

void publicarBatchOsciloscopio() {
  if (!mqttClient.connected()) {
    limparBatchOsciloscopio();
    return;
  }

  char payload[1024];
  int pos = 0;

  uint32_t dtUs = 1000000UL / FS_HZ;

  pos += snprintf(
    payload + pos,
    sizeof(payload) - pos,
    "{\"seq\":%lu,\"fs\":%d,\"n\":%d,\"t0_us\":%lu,\"dt_us\":%lu,\"vp\":[",
    (unsigned long)oscSeq,
    FS_HZ,
    oscBatchCount,
    (unsigned long)oscT0BatchUs,
    (unsigned long)dtUs
  );

  for (int i = 0; i < oscBatchCount; i++) {
    pos += snprintf(
      payload + pos,
      sizeof(payload) - pos,
      "%s%u",
      (i == 0 ? "" : ","),
      oscVpBatch[i]
    );
  }

  pos += snprintf(
    payload + pos,
    sizeof(payload) - pos,
    "],\"vn\":["
  );

  for (int i = 0; i < oscBatchCount; i++) {
    pos += snprintf(
      payload + pos,
      sizeof(payload) - pos,
      "%s%u",
      (i == 0 ? "" : ","),
      oscVnBatch[i]
    );
  }

  pos += snprintf(
    payload + pos,
    sizeof(payload) - pos,
    "]}"
  );

  mqttClient.publish(TOPIC_OSC_BATCH, payload, false);
  mqttClient.loop();

  oscSeq++;
  limparBatchOsciloscopio();
}

void piscarLEDRapido(unsigned long agoraMs) {
  if (agoraMs - ultimoPiscaLed >= OSC_LED_INTERVAL_MS) {
    ultimoPiscaLed = agoraMs;
    digitalWrite(PIN_LED, !digitalRead(PIN_LED));
  }
}

// =====================================================
// COLETA, PROCESSAMENTO E FEATURES VIA MQTT
// =====================================================

void executarColetaProcessamentoEnvio() {
  publicarEvento("coleta_5s_iniciada");

  adquirirAmostras();

  publicarEvento("processamento_iniciado");

  processarTodosOsCiclos();

  publicarEvento("envio_mqtt_features_iniciado");

  publicarFeaturesMQTT();

  publicarEvento("coleta_processamento_envio_finalizados");
}

void adquirirAmostras() {
  int64_t tempoInicioUs = esp_timer_get_time();
  unsigned long ultimoLedMs = millis();

  digitalWrite(PIN_LED, HIGH);

  for (int n = 0; n < NUM_AMOSTRAS_TOTAL; n++) {
    int64_t tempoAlvoUs =
      tempoInicioUs + ((int64_t)n * 1000000LL) / FS_HZ;

    while (esp_timer_get_time() < tempoAlvoUs) {
      // Espera ativa para manter a taxa de amostragem
    }

    dadosV[n] = analogRead(PIN_VP);
    dadosI[n] = analogRead(PIN_VN);

    unsigned long agoraMs = millis();

    if (agoraMs - ultimoLedMs >= 1000) {
      ultimoLedMs = agoraMs;
      digitalWrite(PIN_LED, !digitalRead(PIN_LED));
    }
  }

  digitalWrite(PIN_LED, LOW);
}

void processarTodosOsCiclos() {
  for (int registro = 0; registro < NUM_REGISTROS; registro++) {
    processarCiclo(registro);
  }
}

void processarCiclo(int registro) {
  int inicio = registro * AMOSTRAS_POR_CICLO;

  double mediaV = calcularMediaRaw(dadosV, inicio, AMOSTRAS_POR_CICLO);
  double mediaI = calcularMediaRaw(dadosI, inicio, AMOSTRAS_POR_CICLO);

  converterCicloParaSinal(
    dadosV,
    sinalV,
    inicio,
    AMOSTRAS_POR_CICLO,
    mediaV,
    V_CAL
  );

  converterCicloParaSinal(
    dadosI,
    sinalI,
    inicio,
    AMOSTRAS_POR_CICLO,
    mediaI,
    I_CAL
  );

  double vRMS = calcularRMS(sinalV, AMOSTRAS_POR_CICLO);
  double iRMS = calcularRMS(sinalI, AMOSTRAS_POR_CICLO);

  double pAtiva = calcularPotenciaAtiva(sinalV, sinalI, AMOSTRAS_POR_CICLO);
  double sAparente = vRMS * iRMS;

  double fp = 0.0;

  if (sAparente > 1e-12) {
    fp = pAtiva / sAparente;
  }

  calcularFFTArduino(sinalV, fftRealV, fftImagV, FFT_V);
  calcularFFTArduino(sinalI, fftRealI, fftImagI, FFT_I);

  double vFundRMS = calcularFundamentalRMS(fftRealV);
  double iFundRMS = calcularFundamentalRMS(fftRealI);

  double thdV = calcularTHD(fftRealV);
  double thdI = calcularTHD(fftRealI);

  features[registro].vRMS = vRMS;
  features[registro].iRMS = iRMS;
  features[registro].vFundRMS = vFundRMS;
  features[registro].iFundRMS = iFundRMS;
  features[registro].thdV = thdV;
  features[registro].thdI = thdI;
  features[registro].pAtiva = pAtiva;
  features[registro].sAparente = sAparente;
  features[registro].fp = fp;
}

// =====================================================
// CÁLCULOS
// =====================================================

double calcularMediaRaw(const uint16_t* x, int inicio, int n) {
  double soma = 0.0;

  for (int i = 0; i < n; i++) {
    soma += x[inicio + i];
  }

  return soma / n;
}

void converterCicloParaSinal(
  const uint16_t* raw,
  double* sinal,
  int inicio,
  int n,
  double mediaRaw,
  double fatorCalibracao
) {
  for (int i = 0; i < n; i++) {
    double adcCentralizado = (double)raw[inicio + i] - mediaRaw;
    double tensaoADC = adcCentralizado * (ADC_REF_V / ADC_MAX_VALUE);

    sinal[i] = tensaoADC * fatorCalibracao;
  }
}

double calcularRMS(const double* x, int n) {
  double somaQuadrados = 0.0;

  for (int i = 0; i < n; i++) {
    somaQuadrados += x[i] * x[i];
  }

  return sqrt(somaQuadrados / n);
}

double calcularPotenciaAtiva(const double* v, const double* i, int n) {
  double soma = 0.0;

  for (int k = 0; k < n; k++) {
    soma += v[k] * i[k];
  }

  return soma / n;
}

void calcularFFTArduino(
  const double* sinal,
  double* fftReal,
  double* fftImag,
  ArduinoFFT<double>& FFT
) {
  for (int i = 0; i < AMOSTRAS_POR_CICLO; i++) {
    fftReal[i] = sinal[i];
    fftImag[i] = 0.0;
  }

  FFT.compute(FFTDirection::Forward);
  FFT.complexToMagnitude();
}

double calcularFundamentalRMS(const double* mag) {
  double amplitudePico = (2.0 * mag[1]) / AMOSTRAS_POR_CICLO;
  return amplitudePico / sqrt(2.0);
}

double calcularTHD(const double* mag) {
  double fundamental = mag[1];

  if (fundamental <= 1e-12) {
    return 0.0;
  }

  double somaHarmonicas = 0.0;

  for (int k = 2; k < AMOSTRAS_POR_CICLO / 2; k++) {
    somaHarmonicas += mag[k] * mag[k];
  }

  return 100.0 * sqrt(somaHarmonicas) / fundamental;
}

// =====================================================
// PUBLICAÇÃO DAS FEATURES VIA MQTT
// =====================================================

void publicarFeaturesMQTT() {
  manterConexoes();

  if (!mqttClient.connected()) {
    publicarEvento("mqtt_nao_conectado_features_nao_publicadas");
    Serial.println("MQTT nao conectado. Features nao publicadas.");
    return;
  }

  mqttClient.publish(
    TOPIC_FEATURE_INICIO,
    "{\"evento\":\"inicio_features\",\"registros\":300}",
    false
  );

  mqttClient.loop();

  char payload[512];

  for (int registro = 0; registro < NUM_REGISTROS; registro++) {
    FeaturesCiclo& f = features[registro];

    double tempoS = (double)registro / F_REDE_HZ;

    snprintf(
      payload,
      sizeof(payload),
      "{"
        "\"ciclo\":%d,"
        "\"tempo_s\":%.6f,"
        "\"vrms\":%.6f,"
        "\"irms\":%.6f,"
        "\"vfund_rms\":%.6f,"
        "\"ifund_rms\":%.6f,"
        "\"thdv\":%.6f,"
        "\"thdi\":%.6f,"
        "\"p_ativa\":%.6f,"
        "\"s_aparente\":%.6f,"
        "\"fp\":%.6f"
      "}",
      registro,
      tempoS,
      f.vRMS,
      f.iRMS,
      f.vFundRMS,
      f.iFundRMS,
      f.thdV,
      f.thdI,
      f.pAtiva,
      f.sAparente,
      f.fp
    );

    mqttClient.publish(TOPIC_FEATURE_CICLO, payload, false);
    mqttClient.loop();

    delay(3);
  }

  ResumoFeatures resumo = calcularResumo();
  publicarResumoMQTT(resumo);

  mqttClient.publish(
    TOPIC_FEATURE_FIM,
    "{\"evento\":\"fim_features\",\"registros\":300}",
    false
  );

  mqttClient.loop();
}

ResumoFeatures calcularResumo() {
  ResumoFeatures r;

  r.vRMS = 0.0;
  r.iRMS = 0.0;
  r.vFundRMS = 0.0;
  r.iFundRMS = 0.0;
  r.thdV = 0.0;
  r.thdI = 0.0;
  r.pAtiva = 0.0;
  r.sAparente = 0.0;
  r.fp = 0.0;

  for (int registro = 0; registro < NUM_REGISTROS; registro++) {
    r.vRMS += features[registro].vRMS;
    r.iRMS += features[registro].iRMS;
    r.vFundRMS += features[registro].vFundRMS;
    r.iFundRMS += features[registro].iFundRMS;
    r.thdV += features[registro].thdV;
    r.thdI += features[registro].thdI;
    r.pAtiva += features[registro].pAtiva;
    r.sAparente += features[registro].sAparente;
    r.fp += features[registro].fp;
  }

  r.vRMS /= NUM_REGISTROS;
  r.iRMS /= NUM_REGISTROS;
  r.vFundRMS /= NUM_REGISTROS;
  r.iFundRMS /= NUM_REGISTROS;
  r.thdV /= NUM_REGISTROS;
  r.thdI /= NUM_REGISTROS;
  r.pAtiva /= NUM_REGISTROS;
  r.sAparente /= NUM_REGISTROS;
  r.fp /= NUM_REGISTROS;

  return r;
}

void publicarResumoMQTT(const ResumoFeatures& r) {
  if (!mqttClient.connected()) {
    return;
  }

  char payload[512];

  snprintf(
    payload,
    sizeof(payload),
    "{"
      "\"vrms_medio\":%.6f,"
      "\"irms_medio\":%.6f,"
      "\"vfund_rms_medio\":%.6f,"
      "\"ifund_rms_medio\":%.6f,"
      "\"thdv_medio\":%.6f,"
      "\"thdi_medio\":%.6f,"
      "\"p_ativa_media\":%.6f,"
      "\"s_aparente_media\":%.6f,"
      "\"fp_medio\":%.6f"
    "}",
    r.vRMS,
    r.iRMS,
    r.vFundRMS,
    r.iFundRMS,
    r.thdV,
    r.thdI,
    r.pAtiva,
    r.sAparente,
    r.fp
  );

  mqttClient.publish(TOPIC_FEATURE_RESUMO, payload, false);
}