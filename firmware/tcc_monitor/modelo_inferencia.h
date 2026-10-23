#pragma once
#include <cmath>
#include <cstring>
#include <cstddef>

// Gerado pelo importador somente depois de validar o ZIP e seu contrato.
#if __has_include("modelo_edge_impulse_config.h")
#include "modelo_edge_impulse_config.h"
#if !defined(TCC_EI_BIBLIOTECA_ARDUINO)
#include "src/edge_impulse/edge-impulse-sdk/classifier/ei_run_classifier.h"
#endif

namespace tcc_modelo {
static constexpr unsigned CONTEXTO=25, FEATURES_ORIGINAIS=106, CLASSES=5;
static constexpr unsigned FEATURES_PREVISAO=TCC_EI_FEATURES_POR_JANELA;
static constexpr const char *NOME=TCC_EI_NOME, *SHA256=TCC_EI_SHA256;
static constexpr const char *MOTOR="edge_impulse_sdk";
static constexpr const char *ROTULOS[5]={"sem_evento_alvo","liquidificador_ligando",
    "liquidificador_desligando","microondas_ligando","microondas_desligando"};
static_assert(EI_CLASSIFIER_LABEL_COUNT == 5, "A rede deve ter as cinco classes do sistema");
static_assert(EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE == CONTEXTO*FEATURES_PREVISAO,
              "Quantidade de entradas diferente do contrato importado");
static_assert(EI_CLASSIFIER_OBJECT_DETECTION == 0, "Requer classificacao, nao deteccao de objetos");
static const float *entradaAtual=nullptr;
static int lerEntrada(size_t offset, size_t count, float *out) {
    if (!entradaAtual || offset > EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE ||
        count > EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE-offset) return -1;
    for (size_t i=0; i<count; ++i) {
        const size_t n=offset+i;
        const float value=entradaAtual[(n/FEATURES_PREVISAO)*106 + TCC_EI_CANAIS[n%FEATURES_PREVISAO]];
        if (!std::isfinite(value)) return -1;
        out[i]=value;
    }
    return 0;
}
// Chamado somente pela tarefa de inferencia. O SDK executa o pre-processamento
// exportado: nao reaplicar MEDIA/DESVIO da implementacao C++ anterior.
static bool inferir(const float *entrada, float *probs) {
    entradaAtual=entrada;
    ei::signal_t signal;
    signal.total_length=EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE;
    signal.get_data=&lerEntrada;
    ei_impulse_result_t result={};
    EI_IMPULSE_ERROR error=run_classifier(&signal, &result, false);
    entradaAtual=nullptr;
    if (error != EI_IMPULSE_OK) return false;
    bool vistos[5]={};
    float soma=0;
    for (size_t i=0; i<5; ++i) {
        int destino=-1;
        for (size_t k=0; k<5; ++k)
            if (result.classification[i].label && !strcmp(result.classification[i].label, ROTULOS[k])) destino=k;
        const float value=result.classification[i].value;
        if (destino<0 || vistos[destino] || !std::isfinite(value) || value<0 || value>1) return false;
        vistos[destino]=true; probs[destino]=value; soma+=value;
    }
    return std::isfinite(soma) && std::fabs(soma-1.f)<.02f;
}
}
#else
#include "rede_eventos5.h"
namespace tcc_modelo {
static constexpr unsigned CONTEXTO=25, FEATURES_ORIGINAIS=106, FEATURES_PREVISAO=14, CLASSES=5;
static constexpr const char *NOME="eventos5_v1_p10_20261015";
static constexpr const char *SHA256="dd5a7cef19f07b1cdf6c4bd5b62aa321f81d18283c6094dd074c227178dc1cdb";
static constexpr const char *MOTOR="rede_eventos5_float32";
static const auto &ROTULOS=tcc_eventos5::ROTULOS;
static bool inferir(const float *entrada, float *probs) { return tcc_eventos5::inferir(entrada, probs); }
}
#endif
