// Copie este arquivo para segredos.h (na mesma pasta) e preencha.
// segredos.h esta no .gitignore e nao deve ser publicado.
// Sem segredos.h o firmware compila e funciona como antes: somente USB.
#pragma once

// Rede Wi-Fi de 2,4 GHz (o ESP32 nao conecta em 5 GHz).
#define TCC_WIFI_SSID "nome_da_rede"
#define TCC_WIFI_SENHA "senha_da_rede"

// Broker MQTT acessivel pela rede: o notebook com servidor.py (porta 1883, que ja escuta em
// 0.0.0.0) ou outro broker (Mosquitto, Raspberry Pi, Home Assistant). Use o IP, nao localhost.
#define TCC_MQTT_URI "mqtt://192.168.0.10:1883"
#define TCC_MQTT_USUARIO ""
#define TCC_MQTT_SENHA ""

// Mesmo topico base do servidor.py (--topico-mqtt).
#define TCC_MQTT_TOPICO "tcc/eventos5/esp32_01"
