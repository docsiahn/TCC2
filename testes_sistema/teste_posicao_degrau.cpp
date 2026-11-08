#include <cassert>
#include <iostream>
#include "../firmware/tcc_monitor/posicao_degrau.h"

int main() {
  constexpr unsigned N=25, PASSO=3;
  float v[N*PASSO];
  // Degrau limpo: posicao = primeira janela com o novo nivel, em qualquer lugar do contexto.
  for (unsigned p=1; p<N; ++p) {
    for (unsigned j=0; j<N; ++j) { v[j*PASSO]=j<p?10.f:120.f; v[j*PASSO+1]=-1e9f; v[j*PASSO+2]=1e9f; }
    assert(tcc::posicaoDegrau(v,N,PASSO)==p);
  }
  // Desligamento com ruido e rampa curta (como o liquidificador): 118 -> 37 -> 28 -> 10.
  for (unsigned j=0; j<N; ++j) v[j*PASSO]=j<20?118.f+(j%3):(j==20?37.f:(j==21?28.f:10.f+(j%2)));
  unsigned p=tcc::posicaoDegrau(v,N,PASSO);
  assert(p==20 || p==21);
  assert(!tcc::posicaoDegrauValida(p,11,17));
  // Sinal constante: sem degrau, posicao 1 (fora da faixa).
  for (unsigned j=0; j<N; ++j) v[j*PASSO]=50.f;
  assert(tcc::posicaoDegrau(v,N,PASSO)==1);
  assert(tcc::posicaoDegrauValida(11,11,17) && tcc::posicaoDegrauValida(17,11,17));
  assert(!tcc::posicaoDegrauValida(10,11,17) && !tcc::posicaoDegrauValida(18,11,17));
  assert(tcc::posicaoDegrau(nullptr,N,PASSO)==0);
  std::cout << "OK: posicao do degrau (limpo, com ruido, constante) e faixa\n";
}
