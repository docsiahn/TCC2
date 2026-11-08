#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include "../firmware/tcc_monitor/controle_modo.h"
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "Falhou: %s (linha %d)\n", #c, __LINE__); std::exit(1); } } while (false)

struct Ensaio {
  tcc::BotaoModo botao;
  uint32_t tempo = 0;
  unsigned eventos[4] = {};
  void passar(bool pressionado, unsigned duracao) {
    for (unsigned i = 0; i < duracao; ++i, ++tempo) {
      unsigned evento = botao.atualizar(pressionado, tempo);
      CHECK(evento <= 3);
      ++eventos[evento];
    }
  }
  void clique() { passar(true, 100); passar(false, 100); }
  void conferir(unsigned um, unsigned dois, unsigned tres) {
    CHECK(eventos[1] == um); CHECK(eventos[2] == dois); CHECK(eventos[3] == tres);
  }
};
int main() {
  { Ensaio t; t.passar(false, 1000); t.conferir(0, 0, 0); }
  { Ensaio t; t.passar(true, 100); t.passar(false, 389); t.conferir(0, 0, 0); t.passar(false, 20); t.conferir(1, 0, 0); t.passar(false, 1000); t.conferir(1, 0, 0); }
  { Ensaio t; t.clique(); t.clique(); t.conferir(0, 0, 0); t.passar(false, 500); t.conferir(0, 1, 0); }
  { Ensaio t; t.clique(); t.clique(); t.clique(); t.conferir(0, 0, 0); t.passar(false, 500); t.conferir(0, 0, 1); }
  { Ensaio t; t.passar(true, 20); t.passar(false, 10); t.passar(true, 15); t.passar(false, 500); t.conferir(0, 0, 0); }
  { Ensaio t; t.passar(true, 100); t.passar(false, 10); t.passar(true, 10); t.passar(false, 1000); t.conferir(1, 0, 0); }
  { Ensaio t; t.passar(true, 2000); t.conferir(0, 0, 0); t.passar(false, 1000); t.conferir(0, 0, 0); }
  { Ensaio t; t.clique(); t.passar(true, 2000); t.passar(false, 1000); t.conferir(0, 0, 0); }
  { Ensaio t; for (unsigned i = 0; i < 2; ++i) { t.clique(); t.passar(false, 1000); } t.conferir(2, 0, 0); }
  { Ensaio t; t.tempo = UINT32_MAX - 150; t.clique(); t.passar(false, 1000); t.conferir(1, 0, 0); }
  { Ensaio t; t.tempo = UINT32_MAX - 250; t.clique(); t.clique(); t.passar(false, 1000); t.conferir(0, 1, 0); }
  { Ensaio t; t.tempo = UINT32_MAX - 250; t.clique(); t.clique(); t.clique(); t.passar(false, 1000); t.conferir(0, 0, 1); }
  { Ensaio t; for (unsigned i = 0; i < 4; ++i) t.clique(); t.passar(false, 1000); t.conferir(0, 0, 0); }
  { Ensaio t; for (unsigned i = 0; i < 5; ++i) t.clique(); t.passar(false, 1000); t.conferir(0, 0, 0); t.clique(); t.passar(false, 1000); t.conferir(1, 0, 0); }
  { Ensaio t; t.passar(true, 100); t.passar(false, 370); t.passar(true, 100); t.passar(false, 1000); t.conferir(0, 1, 0); }
  { Ensaio t; t.passar(true, 100); t.passar(false, 410); t.passar(true, 100); t.passar(false, 1000); t.conferir(2, 0, 0); }
  std::puts("16 testes de botao passaram");
}
