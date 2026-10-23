#include <Arduino.h>
#include <stdarg.h>
#include "modelo_inferencia.h"
#include "pipeline.h"
#include "estados.h"
#include "controle_modo.h"
#include "indicador_led.h"
#include "validacao_entrada.h"

namespace tcc {
static_assert(tcc_modelo::CONTEXTO == N_CONTEXTO && tcc_modelo::FEATURES_ORIGINAIS == N_FEATURES, "Contrato das features divergente");
static const char *PROTOCOLO = "TCC_SISTEMA_V2";
static const char *MODELO = tcc_modelo::NOME;
static const char *SHA_MODELO = tcc_modelo::SHA256;
static FrameFeatures *leitura = nullptr;
static float vetorModelo[N_INPUT];
static Estados estados;
static DiagnosticoEntrada diagnosticoEntrada;
static SemaphoreHandle_t mutexSerial = nullptr;
static QueueHandle_t comandos = nullptr;
static bool iniciou = false;
struct Comando { char tipo; int a, b; bool botao, recebidoOcupado; };
static char mensagem[4096];
static size_t usado = 0;
static bool mensagemValida = true;
static volatile bool modoTeste = true;
static const char *origemModo = "inicializacao", *origemCaptura = "";
static volatile bool capturaAtiva = false;
static bool capturaIniciou = false, avisoEnviado = false;
static int cenarioSelecionado = 0, capturaCenario = 0, capturaEsperada = -1;
static uint32_t contadorCapturas = 0, capturaEpoch = 0, capturaPrimeiroSeq = 0;
static char capturaId[32] = "";
static int64_t pedidoCapturaUs = 0, estadoInformadoUs = 0;
static volatile uint32_t comandosDescartados = 0;

static const char *nomeModo() { return modoTeste ? "teste" : "aquisicao"; }
static void enviar(const char *texto) {
  xSemaphoreTake(mutexSerial, portMAX_DELAY);
  Serial.println(texto);
  xSemaphoreGive(mutexSerial);
}
static void acrescentar(const char *formato, ...) {
  if (!mensagemValida) return;
  va_list args;
  va_start(args, formato);
  int n = vsnprintf(mensagem + usado, sizeof(mensagem) - usado, formato, args);
  va_end(args);
  if (n < 0 || static_cast<size_t>(n) >= sizeof(mensagem) - usado) { mensagemValida = false; return; }
  usado += static_cast<size_t>(n);
}
static void cabecalho(const char *tipo, bool identificarCaptura = false) {
  usado = 0; mensagemValida = true;
  bool temCaptura = capturaAtiva || identificarCaptura;
  acrescentar(
    "{\"protocolo\":\"%s\",\"modelo\":\"%s\",\"sha256\":\"%s\",\"tipo\":\"%s\",\"dispositivo\":\"%s\","
    "\"boot\":%lu,\"epoch\":%lu,\"uptime_ms\":%lu,\"modo\":\"%s\",\"origem_modo\":\"%s\","
    "\"cenario_selecionado\":%d,\"captura_ativa\":%s,\"captura_id\":\"%s\",\"teste_id\":%lu,"
    "\"cenario\":%d,\"classe_esperada\":%d,\"origem_captura\":\"%s\",\"estados\":[%d,%d],\"heap_livre\":%u",
    PROTOCOLO, MODELO, SHA_MODELO, tipo, DISPOSITIVO,
    (unsigned long)bootId, (unsigned long)epochAtual(), (unsigned long)millis(), nomeModo(), origemModo,
    cenarioSelecionado, capturaAtiva ? "true" : "false", temCaptura ? capturaId : "",
    (unsigned long)(temCaptura ? contadorCapturas : 0), temCaptura ? capturaCenario : 0,
    temCaptura ? capturaEsperada : -1, temCaptura ? origemCaptura : "", estados.valores[0], estados.valores[1], ESP.getFreeHeap());
  acrescentar(",\"revisao_firmware\":\"diagnostico_entrada_v1_20261023\"");
}
static bool concluirMensagem() {
  acrescentar("}");
  if (!mensagemValida) return false;
  enviar(mensagem); return true;
}
static void numeroDiagnostico(const char *nome,double valor) {
  if (std::isfinite(valor)) acrescentar(",\"%s\":%.9g",nome,valor);
  else acrescentar(",\"%s\":null",nome);
}
static void acrescentarDiagnosticoEntrada() {
  const DiagnosticoEntrada &d=diagnosticoEntrada;
  acrescentar(",\"diagnostico_entrada\":{\"motivo\":\"%s\",\"janela_base_zero\":%d,\"feature_base_zero\":%d",
              d.motivo,d.janela,d.feature);
  numeroDiagnostico("medido",d.medido); numeroDiagnostico("limite_ou_esperado",d.limite);
  if (d.janela>=0 && static_cast<unsigned>(d.janela)<N_CONTEXTO) {
    const FrameFeatures &f=leitura[d.janela];
    acrescentar(",\"seq\":%lu,\"saturadas_v\":%u,\"saturadas_i\":%u,\"amostras_por_canal\":%u,\"tempo_interno_valido\":%s",
                (unsigned long)f.seq,(unsigned)f.saturadasV,(unsigned)f.saturadasI,
                (unsigned)FFT_N,f.tempoInternoValido?"true":"false");
    numeroDiagnostico("vrms_adc",f.valores[0]); numeroDiagnostico("irms_adc",f.valores[1]);
  }
  acrescentar("},\"frames_perdidos\":%lu,\"inferencias_descartadas\":%lu",
              (unsigned long)framesPerdidos,(unsigned long)inferenciasDescartadas);
}
static void status(const char *tipo, const char *motivo, uint32_t n = 0,
                   const char *origem = "sistema", bool identificarCaptura = false,
                   bool detalharEntrada = false) {
  cabecalho(tipo, identificarCaptura);
  acrescentar(",\"motivo\":\"%s\",\"origem\":\"%s\",\"janelas\":%lu,\"features_exportadas\":106,\"features_previsao\":%u",
              motivo, origem, (unsigned long)n, tcc_modelo::FEATURES_PREVISAO);
  if (detalharEntrada) acrescentarDiagnosticoEntrada();
  concluirMensagem();
}
static void cancelarCaptura(const char *motivo, const char *origem = "sistema", bool detalharEntrada = false) {
  if (!capturaAtiva) { status("aviso", "nenhuma_captura_em_andamento", 0, origem); return; }
  capturaAtiva = false;
  status("captura_cancelada", motivo, 0, origem, true, detalharEntrada);
}
static uint32_t copiarContexto(uint32_t epoch) {
  xSemaphoreTake(mutexContexto,portMAX_DELAY);
  uint32_t n=contextoEpoch==epoch?contextoContagem:0;
  if (n) {
    uint32_t primeiro=n==N_CONTEXTO?contextoProximo:0;
    for (uint32_t j=0;j<n;++j) leitura[j]=contexto[(primeiro+j)%N_CONTEXTO];
  }
  xSemaphoreGive(mutexContexto); return n;
}
static bool prepararEntrada(double &intervalo) {
  if (!validarContextoEntrada(leitura,N_CONTEXTO,FRAMES_JANELA,CONVERSOES_FRAME,
                             VRMS_MINIMO_ADC,10,intervalo,diagnosticoEntrada)) return false;
  const double angulo=-2.0*PI_F*60.0*intervalo/1000000.0;
  const double c=cos(angulo),s=sin(angulo);
  char numero[64];
  for (size_t j=0;j<N_CONTEXTO;++j) {
    FrameFeatures &f=leitura[j];
    const double ir=f.iFund.real*c-f.iFund.imag*s;
    const double ii=f.iFund.real*s+f.iFund.imag*c;
    f.valores[3]=(f.vFund.imag*ir-f.vFund.real*ii)*POLARIDADE_CORRENTE;
    for (size_t k=0;k<N_FEATURES;++k) {
      double valor=f.valores[k];
      if (!std::isfinite(valor)) return diagnosticoEntrada.falhar("feature_nao_finita",j,valor,0,k);
      // Mesmo arredondamento usado nos CSVs da campanha de treinamento.
      int escrito=snprintf(numero,sizeof(numero),k==2 || k==3?"%.9f":"%.6f",valor);
      if (escrito<1 || escrito>=static_cast<int>(sizeof(numero)))
        return diagnosticoEntrada.falhar("formatacao_feature_invalida",j,escrito,sizeof(numero)-1,k);
      vetorModelo[j*N_FEATURES+k]=strtof(numero,nullptr);
    }
  }
  return true;
}
static void processarPedidos() {
  Comando cmd;
  if (comandosDescartados) { comandosDescartados = 0; status("aviso", "fila_comandos_cheia"); }
  while (xQueueReceive(comandos, &cmd, 0) == pdTRUE) {
    const char *origem = cmd.botao ? "botao" : "python";
    if (cmd.tipo == 'P') { status("pronto", tcc_modelo::MOTOR, 0, origem); continue; }
    if (cmd.tipo == 'E') { status("aviso", "comando_invalido", 0, origem); continue; }
    if (cmd.tipo == 'X') { cancelarCaptura("cancelado_operador", origem); continue; }
    // Um comando recebido durante a captura nao e aplicado tardiamente apos a exportacao.
    if (capturaAtiva || cmd.recebidoOcupado) { status("aviso", "ocupado", 0, origem); continue; }
    if (cmd.tipo == 'R') { estados.resetar(); estadoInformadoUs = 0; status("estados", "reiniciado", 0, origem); continue; }
    if (cmd.tipo == 'S') {
      estados.informar(cmd.a, cmd.b); estadoInformadoUs = esp_timer_get_time();
      status("estados", "informado_operador", 0, origem); continue;
    }
    if (cmd.tipo == 'M') {
      modoTeste = cmd.a != 0; origemModo = origem;
      estadoInformadoUs = esp_timer_get_time();
      status("modo", nomeModo(), 0, origem); continue;
    }
    if (cmd.tipo == 'F') { cenarioSelecionado = cmd.a; status("cenario", "selecionado", 0, origem); continue; }
    if (cmd.tipo == 'T') {
      if (modoTeste) {
        // Clique simples em teste nao inicia aquisicao e nao altera o modo.
        if (!cmd.botao) status("aviso", "modo_incompativel", 0, origem);
        continue;
      }
      int escolhido = cmd.a ? cmd.a : cenarioSelecionado;
      if (escolhido < 1 || escolhido > 12) { status("aviso", "cenario_nao_selecionado", 0, origem); continue; }
      if (contadorCapturas == UINT32_MAX) { status("aviso", "contador_esgotado_reinicie", 0, origem); continue; }
      cenarioSelecionado = escolhido; capturaCenario = escolhido;
      capturaEsperada = CENARIOS[escolhido - 1].esperada;
      ++contadorCapturas;
      snprintf(capturaId, sizeof(capturaId), "%08lx:%lu", (unsigned long)bootId, (unsigned long)contadorCapturas);
      origemCaptura = origem; capturaAtiva = true; capturaIniciou = false; avisoEnviado = false;
      capturaEpoch = epochAtual(); pedidoCapturaUs = esp_timer_get_time();
      const Cenario &cl = CENARIOS[escolhido - 1];
      estados.informar(cl.antes[0], cl.antes[1]);
      status("captura_armada", "aguardando_proxima_janela", 0, origem);
    }
  }
}
static bool enviarEntradaCaptura(const FrameFeatures &fim) {
  for (unsigned j = 0; j < N_CONTEXTO; ++j) {
    processarPedidos();
    if (!capturaAtiva) return false;
    if (fim.epoch != epochAtual()) { cancelarCaptura("falha_aquisicao"); return false; }
    cabecalho("entrada", true);
    acrescentar(",\"janela\":%u,\"timestamp\":%u,\"valores\":[", j, j * 200);
    for (unsigned k = 0; k < N_FEATURES; ++k)
      acrescentar("%s%.9g", k ? "," : "", static_cast<double>(vetorModelo[j * N_FEATURES + k]));
    acrescentar("]");
    if (!concluirMensagem()) { cancelarCaptura("mensagem_excede_buffer"); return false; }
  }
  processarPedidos();
  if (!capturaAtiva) return false;
  if (fim.epoch != epochAtual()) { cancelarCaptura("falha_aquisicao"); return false; }
  capturaAtiva = false;
  status("entrada_fim", "captura_concluida", N_CONTEXTO, origemCaptura, true);
  return true;
}
static void inferir(const FrameFeatures &fim, bool guiado) {
  double intervalo;
  if (!prepararEntrada(intervalo)) {
    estados.resetar();
    if (guiado) cancelarCaptura(diagnosticoEntrada.motivo,"sistema",true);
    else status("falha",diagnosticoEntrada.motivo,0,"sistema",false,true);
    return;
  }
  float probs[5] = {};
  int64_t inicio = esp_timer_get_time();
  bool ok = tcc_modelo::inferir(vetorModelo, probs);
  double inferenciaMs = (esp_timer_get_time() - inicio) / 1000.0;
  if (!ok) {
    if (guiado) cancelarCaptura("inferencia"); else status("falha", "inferencia");
    return;
  }
  if (fim.epoch != epochAtual()) { if (guiado) cancelarCaptura("falha_aquisicao"); return; }
  unsigned classe = 0; float segunda = -1;
  for (unsigned k = 1; k < 5; ++k) if (probs[k] > probs[classe]) classe = k;
  for (unsigned k = 0; k < 5; ++k) if (k != classe && probs[k] > segunda) segunda = probs[k];
  float confianca = probs[classe];
  Decisao d = estados.observar(classe, confianca, confianca - segunda, millis(), true, guiado);
  cabecalho("resultado", guiado);
  acrescentar(
    ",\"seq\":%lu,\"fim_janela_us\":%llu,\"classe_id\":%u,\"classe\":\"%s\",\"confianca\":%.6f,\"margem\":%.6f,"
    "\"decisao\":\"%s\",\"origem_estado\":\"%s\",\"alterados\":%u,\"aceito\":%s,\"contexto_ms\":5000,\"janela_ms\":200,"
    "\"inferencia_ms\":%.3f,\"atraso_ms\":%.3f,\"vrms_adc\":%.6f,\"irms_adc\":%.6f,"
    "\"frames_perdidos\":%lu,\"inferencias_descartadas\":%lu,\"eventos\":%lu,\"repetidos\":%lu,\"incompativeis\":%lu,"
    "\"features_exportadas\":106,\"features_previsao\":%u,\"entrada_a_seguir\":%s,\"probabilidades\":[",
    (unsigned long)fim.seq, (unsigned long long)fim.fimFramesUs, classe, tcc_modelo::ROTULOS[classe], confianca, confianca - segunda,
    d.motivo, d.origem, d.alterados, d.aceito ? "true" : "false", inferenciaMs,
    (esp_timer_get_time() - fim.fimFramesUs) / 1000.0, fim.valores[0], fim.valores[1],
    (unsigned long)framesPerdidos, (unsigned long)inferenciasDescartadas, (unsigned long)estados.eventos,
    (unsigned long)estados.repetidos, (unsigned long)estados.incompativeis, tcc_modelo::FEATURES_PREVISAO, guiado ? "true" : "false");
  for (unsigned k = 0; k < 5; ++k) acrescentar("%s%.6f", k ? "," : "", probs[k]);
  acrescentar("]");
  if (!concluirMensagem()) {
    if (guiado) cancelarCaptura("mensagem_excede_buffer"); else status("falha", "mensagem_excede_buffer");
    return;
  }
  if (guiado) enviarEntradaCaptura(fim);
}
static void tarefaInferencia(void *) {
  uint32_t ultimoEpoch = 0, ultimoSeq = 0; int64_t ultimoStatus = 0;
  for (;;) {
    vTaskDelay(1); ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(50));
    uint32_t epoch = epochAtual();
    if (epoch != ultimoEpoch) {
      estados.resetar(); ultimoSeq = 0; ultimoEpoch = epoch;
      if (capturaAtiva && epoch != capturaEpoch) cancelarCaptura("falha_aquisicao");
    }
    processarPedidos();
    uint32_t n = copiarContexto(epoch);
    if (!fatalGlobal && esp_timer_get_time() - ultimoStatus > 1000000) {
      status("heartbeat", nomeModo(), n); ultimoStatus = esp_timer_get_time();
    }
    if (fatalGlobal) {
      if (esp_timer_get_time() - ultimoStatus > 1000000) { status("falha", nomeFalha(falhaGlobal)); ultimoStatus = esp_timer_get_time(); }
      continue;
    }
    if (!n || leitura[n - 1].seq == ultimoSeq) continue;
    FrameFeatures &fim = leitura[n - 1];
    if (modoTeste && ultimoSeq && fim.seq > ultimoSeq + 1) somarContador(inferenciasDescartadas, fim.seq - ultimoSeq - 1);
    ultimoSeq = fim.seq;
    if (capturaAtiva) {
      if (!capturaIniciou) {
        // O primeiro frame completo deve comecar depois do comando.
        if (fim.inicioFramesUs - pedidoCapturaUs < 25000) continue;
        capturaPrimeiroSeq = fim.seq; capturaIniciou = true;
        status("captura_inicio", "capturando_5_segundos", 1, origemCaptura);
      }
      uint32_t prontas = fim.seq - capturaPrimeiroSeq + 1;
      if (!avisoEnviado && prontas >= 10 && CENARIOS[capturaCenario - 1].evento) {
        avisoEnviado = true; status("acao", "agora", prontas, origemCaptura);
      }
      if (prontas < N_CONTEXTO) continue;
      if (prontas != N_CONTEXTO || n != N_CONTEXTO || leitura[0].seq != capturaPrimeiroSeq) {
        cancelarCaptura("sequencia_incompleta"); continue;
      }
      inferir(fim, true); continue;
    }
    if (!modoTeste) continue;
    // Um estado manual ou uma troca de modo nunca usa uma janela anterior.
    if (estadoInformadoUs && fim.fimFramesUs - estadoInformadoUs < 5000000) continue;
    if (n < N_CONTEXTO) { status("aquecendo", "aguardando_5_segundos", n); continue; }
    inferir(fim, false);
  }
}
static void enfileirar(Comando cmd) {
  cmd.recebidoOcupado = capturaAtiva;
  if (xQueueSend(comandos, &cmd, 0) != pdTRUE) { ++comandosDescartados; return; }
  xTaskNotifyGive(taskInferencia);
}
}

void setup() {
  using namespace tcc;
  pinMode(PINO_BOTAO_MODO, INPUT_PULLUP);
  pinMode(PINO_LED_STATUS, OUTPUT);
  digitalWrite(PINO_LED_STATUS, LED_ATIVO_ALTO ? LOW : HIGH);
  Serial.setTxBufferSize(4096); Serial.begin(BAUD_SERIAL);
  mutexSerial = xSemaphoreCreateMutex(); comandos = xQueueCreate(16, sizeof(Comando));
  bootId = esp_random();
  raw = static_cast<BlocoRaw *>(heap_caps_calloc(3, sizeof(BlocoRaw), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  contexto = static_cast<FrameFeatures *>(heap_caps_calloc(N_CONTEXTO, sizeof(FrameFeatures), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  leitura = static_cast<FrameFeatures *>(heap_caps_calloc(N_CONTEXTO, sizeof(FrameFeatures), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  livresRaw = xQueueCreate(3, sizeof(int)); prontosRaw = xQueueCreate(3, sizeof(int)); mutexContexto = xSemaphoreCreateMutex();
  if (!mutexSerial || !comandos || !raw || !contexto || !leitura || !livresRaw || !prontosRaw || !mutexContexto) {
    Serial.println("ERRO: memoria de inicializacao"); return;
  }
  for (int slot = 0; slot < 3; ++slot) xQueueSend(livresRaw, &slot, 0);
  if (xTaskCreatePinnedToCore(tarefaInferencia, "rede_eventos5", 16384, nullptr, 2, &taskInferencia, 0) != pdPASS ||
      xTaskCreatePinnedToCore(tarefaFeatures, "features", 6144, nullptr, 3, nullptr, 1) != pdPASS ||
      xTaskCreatePinnedToCore(tarefaAquisicao, "adc_dma", 4096, nullptr, 6, nullptr, 1) != pdPASS) {
    fatalGlobal = true; falhaGlobal = INICIALIZACAO; return;
  }
  iniciou = true;
}
void loop() {
  using namespace tcc;
  if (!iniciou) { delay(5); return; }
  static IndicadorLed led;
  bool aceso = led.atualizar(modoTeste, capturaAtiva, millis());
  digitalWrite(PINO_LED_STATUS, aceso == LED_ATIVO_ALTO ? HIGH : LOW);
  static BotaoModo botao;
  unsigned gesto = botao.atualizar(digitalRead(PINO_BOTAO_MODO) == LOW, millis());
  if (gesto) {
    Comando cmd = {}; cmd.botao = true;
    if (gesto == 1) cmd.tipo = 'T';
    else { cmd.tipo = 'M'; cmd.a = gesto == 2 ? 1 : 0; }
    enfileirar(cmd);
  }
  static char linha[80]; static unsigned n = 0; static bool excedida = false;
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\r') continue;
    if (c != '\n') {
      if (!excedida && n < sizeof(linha) - 1) linha[n++] = c;
      else excedida = true;
      continue;
    }
    linha[n] = 0; n = 0; Comando cmd = {}; cmd.tipo = 'E';
    unsigned cenario = 0; int a = 0, b = 0; char sobra;
    if (!excedida) {
      if (!strcmp(linha, "PING")) cmd.tipo = 'P';
      else if (!strcmp(linha, "RESET")) cmd.tipo = 'R';
      else if (!strcmp(linha, "CANCEL")) cmd.tipo = 'X';
      else if (!strcmp(linha, "MODE teste")) { cmd.tipo = 'M'; cmd.a = 1; }
      else if (!strcmp(linha, "MODE aquisicao")) { cmd.tipo = 'M'; cmd.a = 0; }
      else if (!strcmp(linha, "CAPTURE")) cmd.tipo = 'T';
      else if (sscanf(linha, "STATE %d %d %c", &a, &b, &sobra) == 2 && a >= 0 && a <= 1 && b >= 0 && b <= 1) { cmd.tipo = 'S'; cmd.a = a; cmd.b = b; }
      else if (sscanf(linha, "SCENARIO %u %c", &cenario, &sobra) == 1 && cenario >= 1 && cenario <= 12) { cmd.tipo = 'F'; cmd.a = cenario; }
      else if (sscanf(linha, "CAPTURE %u %c", &cenario, &sobra) == 1 && cenario >= 1 && cenario <= 12) { cmd.tipo = 'T'; cmd.a = cenario; }
    }
    excedida = false;
    enfileirar(cmd);
  }
  delay(5);
}
