#include <Arduino.h>
#include "esp_timer.h"

// =====================================================
// CONFIGURAÇÕES
// =====================================================

// Use preferencialmente pinos do ADC1 no ESP32
constexpr uint8_t PINO_TENSAO  = 36;
constexpr uint8_t PINO_CORRENTE = 39;

constexpr uint32_t BAUD = 921600;
constexpr uint32_t FS = 1920;  // Pares de amostras por segundo

// 1.000.000 / 1.920 = 520,833... µs
constexpr uint32_t INTERVALO_BASE_US = 1000000UL / FS;
constexpr uint32_t RESTO_DIVISAO = 1000000UL % FS;

int64_t proximaAmostraUs = 0;
uint32_t acumuladorFracao = 0;

// =====================================================
// ATUALIZA O INSTANTE DA PRÓXIMA AMOSTRA
// =====================================================

void agendarProximaAmostra()
{
    proximaAmostraUs += INTERVALO_BASE_US;

    // Compensa a parte fracionária de 520,833... µs
    acumuladorFracao += RESTO_DIVISAO;

    if (acumuladorFracao >= FS)
    {
        proximaAmostraUs++;
        acumuladorFracao -= FS;
    }
}

// =====================================================
// CONFIGURAÇÃO
// =====================================================

void setup()
{
    Serial.begin(BAUD);

    pinMode(PINO_TENSAO, INPUT);
    pinMode(PINO_CORRENTE, INPUT);

    // ADC de 12 bits: valores entre 0 e 4095
    analogReadResolution(12);

    // Permite medir aproximadamente toda a faixa de 0 a 3,3 V
    analogSetPinAttenuation(PINO_TENSAO, ADC_11db);
    analogSetPinAttenuation(PINO_CORRENTE, ADC_11db);

    delay(1000);

    // O Python ignora automaticamente esta linha
    Serial.println("#OSC_GPIO36_39_V1,pinos=36/39,fs=1920");
    Serial.println("tempo_us,gpio36_adc,gpio39_adc");

    proximaAmostraUs = esp_timer_get_time();
}

// =====================================================
// AQUISIÇÃO E TRANSMISSÃO
// =====================================================

void loop()
{
    static uint32_t ultimoAvisoMs = 0;
    if (millis() - ultimoAvisoMs >= 2000) {
        ultimoAvisoMs = millis();
        Serial.println("#OSC_GPIO36_39_V1,pinos=36/39,fs=1920");
    }
    int64_t agoraUs = esp_timer_get_time();

    if (agoraUs < proximaAmostraUs)
    {
        return;
    }

    // Se houver atraso muito grande, reinicia o agendamento
    // para não transmitir várias amostras acumuladas de uma vez.
    if ((agoraUs - proximaAmostraUs) >
        (4 * INTERVALO_BASE_US))
    {
        proximaAmostraUs = agoraUs;
        acumuladorFracao = 0;
    }

    // Instante aproximado de aquisição do par de canais
    int64_t tempoAmostraUs = esp_timer_get_time();

    // No Python:
    // VP recebe a tensão
    // VN recebe a corrente
    uint16_t adcTensao = analogRead(PINO_TENSAO);
    uint16_t adcCorrente = analogRead(PINO_CORRENTE);

    agendarProximaAmostra();

    // Formato esperado:
    // tempo_em_us,tensão_adc,corrente_adc
    Serial.printf(
        "%lld,%u,%u\n",
        static_cast<long long>(tempoAmostraUs),
        static_cast<unsigned int>(adcTensao),
        static_cast<unsigned int>(adcCorrente)
    );
}