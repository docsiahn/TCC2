#pragma once
#include <cstdint>

namespace tcc {
// Sem delays: um pulso por segundo no teste, dois pulsos por ciclo na aquisicao.
class IndicadorLed {
 public:
  bool atualizar(bool teste, bool capturando, uint32_t agora) {
    unsigned estado = capturando ? 2U : (teste ? 0U : 1U);
    if (!iniciado || estado != anterior) {
      iniciado = true; anterior = estado; inicio = agora;
    }
    if (capturando) return true;
    uint32_t fase = static_cast<uint32_t>(agora - inicio) % (teste ? 1000U : 2000U);
    return fase < 150U || (!teste && fase >= 300U && fase < 450U);
  }
 private:
  bool iniciado = false;
  unsigned anterior = 0;
  uint32_t inicio = 0;
};
}
