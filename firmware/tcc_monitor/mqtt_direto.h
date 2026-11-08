#pragma once
// Publicacao direta pelo Wi-Fi do ESP32, para a placa funcionar sem o notebook ligado pela USB.
// Usa o cliente MQTT do ESP-IDF (incluso no core Arduino-ESP32 3.x); nao precisa de biblioteca extra.
// Fica ativa somente quando existe segredos.h com TCC_WIFI_SSID e TCC_MQTT_URI.
//
// Topicos (base TCC_MQTT_TOPICO, a mesma do servidor.py):
//   <base>/placa/status  retido, QoS 1: {"online":true|false,...}; "false" vem do testamento (LWT).
//   <base>/estado        retido, QoS 1: modo e estados atuais da placa.
//   <base>/telemetria    QoS 0: mensagens "resultado" (mesmo JSON da USB + "transporte"), no maximo
//                        uma por segundo (pela USB continuam as cinco por segundo).
//   <base>/eventos       QoS 1: resultados aceitos que alteraram estado, com "evento_id". Sem conexao
//                        nao sao guardados; o estado retido, republicado ao reconectar, cobre a perda.
// As 106 features de captura (mensagens "entrada") continuam somente na USB.
#if __has_include("segredos.h")
#include "segredos.h"
#endif

#if defined(TCC_WIFI_SSID) && defined(TCC_MQTT_URI)
#define TCC_MQTT_DIRETO 1
#include <WiFi.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "mqtt_client.h"
#ifndef TCC_WIFI_SENHA
#define TCC_WIFI_SENHA ""
#endif
#ifndef TCC_MQTT_USUARIO
#define TCC_MQTT_USUARIO ""
#endif
#ifndef TCC_MQTT_SENHA
#define TCC_MQTT_SENHA ""
#endif
#ifndef TCC_MQTT_TOPICO
#define TCC_MQTT_TOPICO "tcc/eventos5/esp32_01"
#endif
#else
#define TCC_MQTT_DIRETO 0
#endif

namespace tcc {
namespace mqtt_direto {
#if TCC_MQTT_DIRETO
static esp_mqtt_client_handle_t cliente = nullptr;
static volatile bool conectado = false;
// Pedido para a tarefa de inferencia republicar o estado (apos conectar ou reconectar).
static volatile bool republicarEstado = false;
// publicadas/descartadas: so a tarefa de inferencia escreve; conexoes: tarefa do MQTT.
static uint32_t publicadas = 0, descartadas = 0;
static volatile uint32_t conexoes = 0;
// O cliente do ESP-IDF envia cerca de uma mensagem enfileirada por volta do seu laco (ate 1 s
// quando nada chega do broker). Por isso a telemetria pelo Wi-Fi sai no maximo uma vez por
// segundo; eventos e estado (QoS 1) saem sempre. Pela USB continuam os 5 resultados por segundo.
static constexpr int64_t INTERVALO_TELEMETRIA_US = 1000000;
static int64_t ultimaTelemetriaUs = 0;
// Telemetria e descartada se a fila de saida do cliente passar disto (memoria da placa).
static constexpr int LIMITE_FILA_BYTES = 6144;
static char clientId[40];
static const char ONLINE[] = "{\"online\":true,\"transporte\":\"wifi_esp32\"}";
static const char OFFLINE[] = "{\"online\":false,\"transporte\":\"wifi_esp32\"}";

static void nomeTopico(char *destino, size_t n, const char *sufixo) {
  snprintf(destino, n, "%s%s", TCC_MQTT_TOPICO, sufixo);
}

static void aoEvento(void *, esp_event_base_t, int32_t id, void *) {
  if (id == MQTT_EVENT_CONNECTED) {
    char t[160]; nomeTopico(t, sizeof(t), "/placa/status");
    esp_mqtt_client_publish(cliente, t, ONLINE, 0, 1, 1);
    conectado = true; conexoes = conexoes + 1; republicarEstado = true;
  } else if (id == MQTT_EVENT_DISCONNECTED) {
    conectado = false;
  }
}

// Inicia Wi-Fi e MQTT. Nao bloqueia: a conexao e as reconexoes acontecem em segundo plano.
static void iniciar(uint32_t bootId) {
  // Os logs internos do ESP-IDF (mqtt_client, esp-tls, transport_base, wifi) saem pela mesma
  // UART do protocolo JSON. Com o broker fora do ar, "E (...) mqtt_client: ..." cortava linhas
  // de resultado no meio (visto na placa em 07/11/2026). O estado do MQTT segue no heartbeat.
  esp_log_level_set("*", ESP_LOG_NONE);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);          // menor latencia; a placa e alimentada pela rede
  WiFi.setAutoReconnect(true);
  WiFi.begin(TCC_WIFI_SSID, TCC_WIFI_SENHA);
  snprintf(clientId, sizeof(clientId), "tcc_esp32_%08lx", (unsigned long)bootId);
  static char topicoTestamento[160];
  nomeTopico(topicoTestamento, sizeof(topicoTestamento), "/placa/status");
  esp_mqtt_client_config_t cfg = {};
  cfg.broker.address.uri = TCC_MQTT_URI;
  cfg.credentials.client_id = clientId;
  if (TCC_MQTT_USUARIO[0]) cfg.credentials.username = TCC_MQTT_USUARIO;
  if (TCC_MQTT_SENHA[0]) cfg.credentials.authentication.password = TCC_MQTT_SENHA;
  cfg.session.keepalive = 15;
  cfg.session.last_will.topic = topicoTestamento;
  cfg.session.last_will.msg = OFFLINE;
  cfg.session.last_will.qos = 1;
  cfg.session.last_will.retain = 1;
  cfg.network.reconnect_timeout_ms = 5000;
  // Prazo de conexao e escrita. Na LAN bastam milissegundos; um prazo curto limita o tempo em
  // que o cliente segura a propria trava (ver publicar()).
  cfg.network.timeout_ms = 2500;
  // Memoria: a placa ja usa ~16 kB no DMA do ADC e ~45 kB nos contextos; com os valores padrao
  // (pilha de 6 kB e buffers de 4 kB) o heap acabava durante a conexao do Wi-Fi.
  cfg.task.stack_size = 4096;
  cfg.buffer.size = 1024;        // a placa nao assina topicos
  cfg.buffer.out_size = 2304;    // um "resultado" tem cerca de 1,5 kB
  cliente = esp_mqtt_client_init(&cfg);
  if (!cliente) return;
  esp_mqtt_client_register_event(cliente, (esp_mqtt_event_id_t)ESP_EVENT_ANY_ID, aoEvento, nullptr);
  esp_mqtt_client_start(cliente);
}

// Chamado pela tarefa de inferencia. Sem conexao nao toca no cliente MQTT: as chamadas da API
// esperam a trava do cliente, presa durante as tentativas de conexao, e a tarefa de inferencia
// parava 10 a 22 s, sem heartbeat na USB (visto na placa em 07/11/2026). Eventos perdidos sem
// conexao sao cobertos pelo estado retido, republicado ao reconectar.
static bool publicar(const char *sufixo, const char *dados, int qos, bool reter) {
  if (!cliente || !conectado) { ++descartadas; return false; }
  if (qos == 0) {
    const int64_t agora = esp_timer_get_time();
    if (agora - ultimaTelemetriaUs < INTERVALO_TELEMETRIA_US) return false;   // reducao, nao perda
    if (esp_mqtt_client_get_outbox_size(cliente) > LIMITE_FILA_BYTES) { ++descartadas; return false; }
    ultimaTelemetriaUs = agora;
  }
  char t[160]; nomeTopico(t, sizeof(t), sufixo);
  if (esp_mqtt_client_enqueue(cliente, t, dados, 0, qos, reter ? 1 : 0, true) < 0) { ++descartadas; return false; }
  ++publicadas; return true;
}
static bool configurado() { return true; }
static bool estaConectado() { return conectado; }
// Retorna true uma vez apos cada conexao, para a tarefa de inferencia republicar o estado.
static bool consumirPedidoEstado() { if (!republicarEstado) return false; republicarEstado = false; return true; }
static int rssi() { return WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0; }
static uint32_t totalPublicadas() { return publicadas; }
static uint32_t totalDescartadas() { return descartadas; }
static uint32_t totalConexoes() { return conexoes; }
#else
static inline void iniciar(uint32_t) {}
static inline bool publicar(const char *, const char *, int, bool) { return false; }
static inline bool configurado() { return false; }
static inline bool estaConectado() { return false; }
static inline bool consumirPedidoEstado() { return false; }
static inline int rssi() { return 0; }
static inline uint32_t totalPublicadas() { return 0; }
static inline uint32_t totalDescartadas() { return 0; }
static inline uint32_t totalConexoes() { return 0; }
#endif
}
}
