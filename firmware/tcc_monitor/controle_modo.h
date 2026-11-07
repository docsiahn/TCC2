#pragma once
#include <stdint.h>

namespace tcc {
// Atualizar a cada passagem pelo loop; nenhum delay ou interrupcao necessario.
// Aguarda 350 ms depois da ultima soltura: 1/2 cliques nao antecipam o triplo.
// Retorna 0 quando nao ha gesto; pressao longa e sequencias de 4+ sao ignoradas.
class BotaoModo {
 public:
  static constexpr uint32_t DEBOUNCE_MS = 40;
  static constexpr uint32_t INTERVALO_MS = 350;
  static constexpr uint32_t LONGO_MS = 1200;

  unsigned atualizar(bool pressionado, uint32_t agora) {
    if (pressionado != leitura_) {
      leitura_ = pressionado;
      mudouEm_ = agora;
    }
    if (leitura_ != estavel_ && decorrido(agora, mudouEm_) >= DEBOUNCE_MS) {
      estavel_ = leitura_;
      if (estavel_) {
        pressionouEm_ = agora;
      } else {
        if (decorrido(agora, pressionouEm_) >= LONGO_MS) invalido_ = true;
        if (cliques_ < 4) ++cliques_;
        soltouEm_ = agora;
      }
    }
    if (estavel_ && decorrido(agora, pressionouEm_) >= LONGO_MS) invalido_ = true;
    if (cliques_ && !estavel_ && !leitura_ && decorrido(agora, soltouEm_) >= INTERVALO_MS) {
      unsigned evento = !invalido_ && cliques_ <= 3 ? cliques_ : 0;
      cliques_ = 0;
      invalido_ = false;
      return evento;
    }
    return 0;
  }

 private:
  bool leitura_ = false, estavel_ = false, invalido_ = false;
  unsigned cliques_ = 0;
  uint32_t mudouEm_ = 0, soltouEm_ = 0, pressionouEm_ = 0;
  static uint32_t decorrido(uint32_t agora, uint32_t antes) { return agora - antes; }
};
}
