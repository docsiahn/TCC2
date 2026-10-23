#pragma once
#include <Arduino.h>
#include "esp_adc/adc_continuous.h"
#include "esp_idf_version.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "soc/soc_caps.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "features_treinamento.h"
#include "config.h"

#if ESP_IDF_VERSION_MAJOR < 5 || !CONFIG_IDF_TARGET_ESP32
#error "Requer ESP32 classico Dev Module com pacote Espressif 3.x"
#endif

namespace tcc {
constexpr uint32_t SPS_CANAL=10240, CONVERSOES_FRAME=256, FRAMES_JANELA=16;
constexpr size_t BYTES_FRAME=CONVERSOES_FRAME*SOC_ADC_DIGI_RESULT_BYTES;
constexpr uint32_t N_CONTEXTO=25, N_FEATURES=106, N_INPUT=2650;
static_assert(CONVERSOES_FRAME*FRAMES_JANELA==FFT_N*2, "Bloco deve ter 2048 amostras por canal");
static_assert(SOC_ADC_DIGI_RESULT_BYTES==2, "Conferir formato ADC do alvo");
struct BlocoRaw {
  uint16_t v[FFT_N], i[FFT_N];
  uint32_t epoch, seq, primeiroFrame, ultimoFrame;
  int64_t inicioFramesUs, fimFramesUs;
  bool tempoInternoValido;
  uint8_t primeiroCanal;
};
struct FrameFeatures {
  double valores[N_FEATURES];
  ComplexoF vFund, iFund;
  uint32_t epoch, seq, primeiroFrame, ultimoFrame;
  int64_t inicioFramesUs, fimFramesUs;
  bool tempoInternoValido;
  uint8_t primeiroCanal;
  uint32_t calculoUs;
  uint16_t saturadasV, saturadasI;
};
struct CarimboDma { uint32_t seq; int64_t fimUs; uint32_t conversoes; };
enum Falha : uint8_t { NENHUMA, DMA_OVERFLOW, TIMESTAMP, ORDEM_ADC, FILA_RAW, FEATURES, LEITURA_ADC, INICIALIZACAO, SEQUENCIA };
static const char *nomeFalha(Falha f) {
  static const char *nomes[]={"nenhuma","overflow_dma","timestamp_dma","ordem_adc","fila_raw_cheia","features_invalidas","leitura_adc","inicializacao","sequencia"};
  return nomes[static_cast<unsigned>(f)];
}
static portMUX_TYPE estadoMux=portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t epochGlobal=1, framesPerdidos=0, inferenciasDescartadas=0, mqttDescartadas=0;
static volatile Falha falhaGlobal=NENHUMA;
static volatile bool fatalGlobal=false, solicitarCsv=false;
static volatile uint32_t framesIsr=0, overflowsIsr=0, atrasosIsr=0;
static volatile int64_t ultimoIrqUs=0;
static CarimboDma carimbos[128];
static TaskHandle_t taskInferencia=nullptr;
static QueueHandle_t livresRaw=nullptr, prontosRaw=nullptr;
static SemaphoreHandle_t mutexContexto=nullptr;
// O ESP32 classico limita .bss mesmo havendo outra RAM interna disponivel.
// Reservados uma unica vez no setup, antes de iniciar DMA/WiFi; nunca na ISR.
static BlocoRaw *raw=nullptr;
static FrameFeatures *contexto=nullptr;
static uint32_t contextoEpoch=0, contextoContagem=0, contextoProximo=0;
static adc_continuous_handle_t adc=nullptr;
static uint32_t bootId=0;

static uint32_t epochAtual() {
  portENTER_CRITICAL(&estadoMux); uint32_t e=epochGlobal; portEXIT_CRITICAL(&estadoMux); return e;
}
static void somarContador(volatile uint32_t &c,uint32_t n=1) {
  portENTER_CRITICAL(&estadoMux); c+=n; portEXIT_CRITICAL(&estadoMux);
}
static void invalidar(uint32_t epochEsperado,Falha motivo,uint32_t perdidos=1) {
  portENTER_CRITICAL(&estadoMux);
  framesPerdidos+=perdidos;
  if (epochGlobal==epochEsperado) { ++epochGlobal; falhaGlobal=motivo; }
  portEXIT_CRITICAL(&estadoMux);
  if (taskInferencia) xTaskNotifyGive(taskInferencia);
}
static bool IRAM_ATTR aoFrame(adc_continuous_handle_t,const adc_continuous_evt_data_t *evt,void *) {
  const int64_t agora=esp_timer_get_time();
  portENTER_CRITICAL_ISR(&estadoMux);
  if (ultimoIrqUs && agora-ultimoIrqUs>LIMITE_INTERVALO_IRQ_US) ++atrasosIsr;
  ultimoIrqUs=agora;
  const uint32_t seq=++framesIsr;
  carimbos[seq%128]={seq,agora,static_cast<uint32_t>(evt->size/SOC_ADC_DIGI_RESULT_BYTES)};
  portEXIT_CRITICAL_ISR(&estadoMux);
  // Sem FFT, CSV, alocacao, Serial ou MQTT na interrupcao.
  return false;
}
static bool IRAM_ATTR aoOverflow(adc_continuous_handle_t,const adc_continuous_evt_data_t *,void *) {
  portENTER_CRITICAL_ISR(&estadoMux); ++overflowsIsr; portEXIT_CRITICAL_ISR(&estadoMux); return false;
}
static bool iniciarAdc() {
  adc_continuous_handle_cfg_t h={};
  h.max_store_buf_size=BYTES_FRAME*32; // 400 ms de margem para o leitor, sem parar o DMA.
  h.conv_frame_size=BYTES_FRAME;
  if (adc_continuous_new_handle(&h,&adc)!=ESP_OK) return false;
  adc_digi_pattern_config_t p[2]={};
  for (int j=0;j<2;++j) { p[j].atten=ADC_ATTEN_DB_12; p[j].unit=ADC_UNIT_1; p[j].bit_width=SOC_ADC_DIGI_MAX_BITWIDTH; }
  p[0].channel=ADC_CHANNEL_0; p[1].channel=ADC_CHANNEL_3;
  adc_continuous_config_t c={};
  c.pattern_num=2; c.adc_pattern=p; c.sample_freq_hz=SPS_CANAL*2;
  c.conv_mode=ADC_CONV_SINGLE_UNIT_1; c.format=ADC_DIGI_OUTPUT_FORMAT_TYPE1;
  if (adc_continuous_config(adc,&c)!=ESP_OK) return false;
  adc_continuous_evt_cbs_t cb={}; cb.on_conv_done=aoFrame; cb.on_pool_ovf=aoOverflow;
  if (adc_continuous_register_event_callbacks(adc,&cb,nullptr)!=ESP_OK) return false;
  return adc_continuous_start(adc)==ESP_OK; // Unico start; criado na mesma task/core que le.
}
struct SnapshotIsr { uint32_t seq, overflow, atraso; };
static SnapshotIsr snapshotIsr() {
  portENTER_CRITICAL(&estadoMux); SnapshotIsr s={framesIsr,overflowsIsr,atrasosIsr}; portEXIT_CRITICAL(&estadoMux); return s;
}
// Ressincroniza metadados e FIFO somente quando nenhum IRQ ocorreu durante a drenagem.
// Nao usa stop/start nem flush_pool, que exige ADC parado nesta versao do driver.
static bool ressincronizar(uint8_t *buffer,uint32_t &consumidos,SnapshotIsr &referencia) {
  for (int tentativa=0;tentativa<80;++tentativa) {
    SnapshotIsr antes=snapshotIsr(); uint32_t descartados=0;
    for (;;) {
      uint32_t bytes=0;
      esp_err_t e=adc_continuous_read(adc,buffer,BYTES_FRAME,&bytes,0);
      if (e==ESP_ERR_TIMEOUT || (e==ESP_OK && !bytes)) break;
      if (e!=ESP_OK) return false;
      descartados+=static_cast<uint32_t>((bytes+BYTES_FRAME-1)/BYTES_FRAME);
    }
    if (descartados) somarContador(framesPerdidos,descartados);
    SnapshotIsr depois=snapshotIsr();
    if (antes.seq==depois.seq && antes.overflow==depois.overflow && antes.atraso==depois.atraso) {
      consumidos=depois.seq; referencia=depois; return true;
    }
    taskYIELD();
  }
  return false;
}
static void tarefaAquisicao(void *) {
  alignas(4) uint8_t buffer[BYTES_FRAME];
  if (!iniciarAdc()) {
    portENTER_CRITICAL(&estadoMux); fatalGlobal=true; falhaGlobal=INICIALIZACAO; portEXIT_CRITICAL(&estadoMux);
    xTaskNotifyGive(taskInferencia); vTaskDelete(nullptr); return;
  }
  uint32_t consumidos=0, epochLocal=epochAtual(), nFrames=0, nV=0, nI=0, seqJanela=0;
  uint32_t sequenciaCanal=99; int slot=-1;
  SnapshotIsr referencia={0,0,0};
  bool recuperar=false;
  int64_t ultimoDadoUs=esp_timer_get_time(), anteriorFrameUs=0;
  for (;;) {
    if (epochAtual()!=epochLocal) recuperar=true;
    if (recuperar) {
      if (slot>=0) { xQueueSend(livresRaw,&slot,0); slot=-1; }
      nFrames=nV=nI=0; sequenciaCanal=99; anteriorFrameUs=0;
      if (!ressincronizar(buffer,consumidos,referencia)) {
        portENTER_CRITICAL(&estadoMux); fatalGlobal=true; falhaGlobal=LEITURA_ADC; portEXIT_CRITICAL(&estadoMux);
        xTaskNotifyGive(taskInferencia); vTaskDelete(nullptr); return;
      }
      epochLocal=epochAtual(); recuperar=false; ultimoDadoUs=esp_timer_get_time();
    }
    if (slot<0 && xQueueReceive(livresRaw,&slot,0)!=pdTRUE) {
      invalidar(epochLocal,FILA_RAW); recuperar=true; vTaskDelay(1); continue;
    }
    uint32_t bytes=0;
    esp_err_t e=adc_continuous_read(adc,buffer,BYTES_FRAME,&bytes,25);
    if (e==ESP_ERR_TIMEOUT) {
      if (esp_timer_get_time()-ultimoDadoUs>500000) { invalidar(epochLocal,LEITURA_ADC); recuperar=true; }
      continue;
    }
    ultimoDadoUs=esp_timer_get_time();
    SnapshotIsr atual=snapshotIsr();
    if (atual.overflow!=referencia.overflow || atual.atraso!=referencia.atraso) {
      uint32_t n=atual.overflow-referencia.overflow;
      invalidar(epochLocal,n?DMA_OVERFLOW:TIMESTAMP,n?n:1); recuperar=true; continue;
    }
    if (e!=ESP_OK || bytes!=BYTES_FRAME || epochLocal!=epochAtual()) {
      invalidar(epochLocal,LEITURA_ADC); recuperar=true; continue;
    }
    ++consumidos;
    portENTER_CRITICAL(&estadoMux); CarimboDma carimbo=carimbos[consumidos%128]; portEXIT_CRITICAL(&estadoMux);
    if (carimbo.seq!=consumidos || carimbo.conversoes!=CONVERSOES_FRAME) {
      invalidar(epochLocal,TIMESTAMP); recuperar=true; continue;
    }
    BlocoRaw &bloco=raw[slot];
    if (nFrames==0) {
      bloco.epoch=epochLocal; bloco.primeiroFrame=consumidos;
      bloco.inicioFramesUs=carimbo.fimUs; bloco.tempoInternoValido=true;
      nV=nI=0;
    } else if (carimbo.fimUs<=anteriorFrameUs) bloco.tempoInternoValido=false;
    anteriorFrameUs=carimbo.fimUs;
    uint32_t contV=0,contI=0;
    bool ok=true;
    for (size_t off=0;off<bytes;off+=SOC_ADC_DIGI_RESULT_BYTES) {
      const auto *d=reinterpret_cast<const adc_digi_output_data_t *>(buffer+off);
      uint32_t canal=d->type1.channel; uint16_t valor=d->type1.data&0xFFF;
      if (sequenciaCanal==99) sequenciaCanal=canal;
      if (canal!=sequenciaCanal || (canal!=ADC_CHANNEL_0 && canal!=ADC_CHANNEL_3)) { ok=false; break; }
      sequenciaCanal=canal==ADC_CHANNEL_0?ADC_CHANNEL_3:ADC_CHANNEL_0;
      if (!nFrames && off==0) bloco.primeiroCanal=static_cast<uint8_t>(canal);
      if (canal==ADC_CHANNEL_0 && nV<FFT_N) { bloco.v[nV++]=valor; ++contV; }
      else if (canal==ADC_CHANNEL_3 && nI<FFT_N) { bloco.i[nI++]=valor; ++contI; }
      else { ok=false; break; }
    }
    if (!ok || contV!=128 || contI!=128) { invalidar(epochLocal,ORDEM_ADC); recuperar=true; continue; }
    ++nFrames;
    if (nFrames==FRAMES_JANELA) {
      if (nV!=FFT_N || nI!=FFT_N) { invalidar(epochLocal,ORDEM_ADC); recuperar=true; continue; }
      bloco.seq=++seqJanela; bloco.ultimoFrame=consumidos; bloco.fimFramesUs=carimbo.fimUs;
      if (xQueueSend(prontosRaw,&slot,0)!=pdTRUE) { invalidar(epochLocal,FILA_RAW); recuperar=true; continue; }
      slot=-1; nFrames=nV=nI=0;
    }
  }
}
static bool extrair(const BlocoRaw &r,FrameFeatures &f) {
  uint64_t inicio=esp_timer_get_time();
  amostrasTensao=r.v; amostrasCorrente=r.i;
  f.saturadasV=f.saturadasI=0;
  for (unsigned k=0;k<FFT_N;++k) {
    if (r.v[k]==0 || r.v[k]==4095) ++f.saturadasV;
    if (r.i[k]==0 || r.i[k]==4095) ++f.saturadasI;
  }
  float magnitudes[2][51],rms[2]; double p,q;
  calcularFeaturesJanela(0,magnitudes,rms,p,q,1000000.0/(SPS_CANAL*2));
  f.valores[0]=rms[0]; f.valores[1]=rms[1]; f.valores[2]=p; f.valores[3]=q;
  f.valores[4]=calcularTHD50(magnitudes[0]); f.valores[5]=calcularTHD50(magnitudes[1]);
  for (size_t h=1;h<=50;++h) { f.valores[5+h]=magnitudes[0][h]; f.valores[55+h]=magnitudes[1][h]; }
  for (double v:f.valores) if (!std::isfinite(v)) return false;
  f.vFund=fasorBinRms(BINS_POR_HARMONICA,0); f.iFund=fasorBinRms(BINS_POR_HARMONICA,1);
  f.epoch=r.epoch; f.seq=r.seq; f.primeiroFrame=r.primeiroFrame; f.ultimoFrame=r.ultimoFrame;
  f.inicioFramesUs=r.inicioFramesUs; f.fimFramesUs=r.fimFramesUs;
  f.tempoInternoValido=r.tempoInternoValido; f.primeiroCanal=r.primeiroCanal;
  f.calculoUs=static_cast<uint32_t>(esp_timer_get_time()-inicio); return true;
}
static void tarefaFeatures(void *) {
  uint32_t ultimaSeq=0,ultimoEpoch=0;
  FrameFeatures f;
  for (;;) {
    int slot;
    if (xQueueReceive(prontosRaw,&slot,portMAX_DELAY)!=pdTRUE) continue;
    uint32_t e=raw[slot].epoch,seq=raw[slot].seq;
    if (e!=epochAtual()) { xQueueSend(livresRaw,&slot,0); continue; }
    if (e==ultimoEpoch && seq!=ultimaSeq+1) {
      invalidar(e,SEQUENCIA); xQueueSend(livresRaw,&slot,0); continue;
    }
    bool ok=extrair(raw[slot],f);
    xQueueSend(livresRaw,&slot,0);
    if (!ok) { invalidar(e,FEATURES); continue; }
    if (e!=epochAtual()) continue;
    xSemaphoreTake(mutexContexto,portMAX_DELAY);
    if (contextoEpoch!=e) { contextoEpoch=e; contextoContagem=0; contextoProximo=0; }
    contexto[contextoProximo]=f; contextoProximo=(contextoProximo+1)%N_CONTEXTO;
    if (contextoContagem<N_CONTEXTO) ++contextoContagem;
    xSemaphoreGive(mutexContexto);
    ultimaSeq=seq; ultimoEpoch=e;
    xTaskNotifyGive(taskInferencia); // Um bloco novo a cada 2048 amostras/canal (200 ms nominais).
  }
}
} // namespace tcc
