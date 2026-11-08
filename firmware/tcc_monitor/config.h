#pragma once
constexpr uint32_t BAUD_SERIAL=115200, LIMITE_INTERVALO_IRQ_US=50000;
constexpr const char *DISPOSITIVO="esp32_01";
// Limiares iniciais, mantidos do pacote anterior; ainda exigem teste em campo.
constexpr float CONFIANCA_MINIMA=.60f, MARGEM_MINIMA=.10f, VRMS_MINIMO_ADC=5.f;
constexpr unsigned CONFIRMACOES_EVENTO=2, CONFIRMACOES_SEM_EVENTO=2;
constexpr uint32_t INTERVALO_EVENTOS_MS=1500;
// Posicao do degrau de corrente (janelas 0..24, da mais antiga) em que um evento e aceito no
// modo teste. Nas capturas de treino o degrau ficou entre as janelas 11 e 17 em 93% dos casos.
// Fora dessa faixa a rede extrapola: logo apos um desligamento ela passa ~1 s pela classe de
// desligar do outro aparelho. POSICAO_DEGRAU_MIN=1 e POSICAO_DEGRAU_MAX=24 desligam o filtro.
// A aquisicao guiada nao usa o filtro.
constexpr unsigned POSICAO_DEGRAU_MIN=11, POSICAO_DEGRAU_MAX=17;
// Saturacao do ADC: amostras lidas como 0 ou 4095 (sinal fora da faixa do canal).
// Uma janela de 200 ms com mais de SATURACAO_AMOSTRAS_JANELA amostras saturadas conta como saturada.
// A partida do micro-ondas satura poucas janelas; ate SATURACAO_JANELAS_MAX delas no contexto
// de 5 s a inferencia e a captura continuam, e a quantidade e informada nas mensagens.
// Acima disso a corrente esta fora da faixa do condicionamento: a placa nao classifica,
// mas mantem os estados. Com SATURACAO_JANELAS_MAX=0 volta o criterio anterior (sem tolerancia).
constexpr unsigned SATURACAO_AMOSTRAS_JANELA=10, SATURACAO_JANELAS_MAX=3;
// Botao normalmente aberto entre GPIO4 e GND; pull-up interno.
constexpr uint8_t PINO_BOTAO_MODO = 4;
// LED de usuario do ESP32 Dev Module. Ajuste se sua placa usar outro GPIO.
constexpr uint8_t PINO_LED_STATUS = 2;
constexpr bool LED_ATIVO_ALTO = true;
