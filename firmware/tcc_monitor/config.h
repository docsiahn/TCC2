#pragma once
constexpr uint32_t BAUD_SERIAL=115200, LIMITE_INTERVALO_IRQ_US=50000;
constexpr const char *DISPOSITIVO="esp32_01";
// Limiares iniciais, mantidos do pacote anterior; ainda exigem teste em campo.
constexpr float CONFIANCA_MINIMA=.60f, MARGEM_MINIMA=.10f, VRMS_MINIMO_ADC=5.f;
constexpr unsigned CONFIRMACOES_EVENTO=2, CONFIRMACOES_SEM_EVENTO=2;
constexpr uint32_t INTERVALO_EVENTOS_MS=1500;
// Botao normalmente aberto entre GPIO4 e GND; pull-up interno.
constexpr uint8_t PINO_BOTAO_MODO = 4;
// LED de usuario do ESP32 Dev Module. Ajuste se sua placa usar outro GPIO.
constexpr uint8_t PINO_LED_STATUS = 2;
constexpr bool LED_ATIVO_ALTO = true;
