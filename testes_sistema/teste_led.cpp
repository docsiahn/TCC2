#include <cassert>
#include <cstdint>
#include <cstdio>
#include "../firmware/tcc_monitor/indicador_led.h"

int main() {
  tcc::IndicadorLed led;
  assert(led.atualizar(true, false, 0));
  assert(led.atualizar(true, false, 149));
  assert(!led.atualizar(true, false, 150));
  assert(!led.atualizar(true, false, 999));
  assert(led.atualizar(true, false, 1000));
  assert(led.atualizar(false, false, 1050));
  assert(!led.atualizar(false, false, 1200));
  assert(led.atualizar(false, false, 1350));
  assert(led.atualizar(false, false, 1499));
  assert(!led.atualizar(false, false, 1500));
  assert(!led.atualizar(false, false, 3049));
  assert(led.atualizar(false, false, 3050));
  for (uint32_t i = 3200; i < 9000; ++i) assert(led.atualizar(false, true, i));
  assert(led.atualizar(false, false, 9000));
  assert(!led.atualizar(false, false, 9150));
  assert(led.atualizar(false, true, 9300));
  assert(led.atualizar(true, false, 9400));
  assert(!led.atualizar(true, false, 9550));
  tcc::IndicadorLed rollover;
  assert(rollover.atualizar(true, false, UINT32_MAX - 99));
  assert(!rollover.atualizar(true, false, 100));
  assert(rollover.atualizar(true, false, 900));
  std::puts("LED: pulso simples, duplo, captura, mudancas e rollover OK.");
}
