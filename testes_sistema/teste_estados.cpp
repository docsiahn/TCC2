#include <cassert>
#include <iostream>
#include "../firmware/tcc_monitor/estados.h"

static tcc::Decisao confirmar(tcc::Estados &s, int classe, uint32_t agora) {
  tcc::Decisao d;
  for (unsigned i=0; i<CONFIRMACOES_EVENTO; ++i)
    d=s.observar(classe, .95f, .8f, agora+i);
  return d;
}

int main() {
  // Cada evento deve inicializar somente o aparelho correspondente.
  for (int classe=1; classe<=4; ++classe) {
    tcc::Estados s;
    assert(s.valores[0]==-1 && s.valores[1]==-1);
    auto d=s.observar(classe,.95f,.8f,10000);
    assert(!d.aceito && s.valores[0]==-1 && s.valores[1]==-1);
    d=s.observar(classe,.95f,.8f,10200);
    int dispositivo=classe<=2?0:1;
    int alvo=(classe==1 || classe==3)?1:0;
    assert(d.aceito && d.alterados==(1U<<dispositivo));
    assert(s.valores[dispositivo]==alvo && s.valores[1-dispositivo]==-1);
    assert(s.eventos==1);
  }
  // Sem evento nao informa estados absolutos.
  tcc::Estados s;
  for (int i=0;i<10;++i) assert(s.observar(0,.98f,.9f,i*200).aceito);
  assert(s.valores[0]==-1 && s.valores[1]==-1);
  // Confianca, margem e integridade continuam sendo exigidas.
  for (int i=0;i<4;++i) {
    assert(!s.observar(1,.1f,.8f,3000+i).aceito);
    assert(!s.observar(1,.95f,.01f,4000+i).aceito);
    assert(!s.observar(1,.95f,.8f,5000+i,false).aceito);
  }
  assert(s.valores[0]==-1 && s.valores[1]==-1);
  assert(confirmar(s,1,10000).aceito);
  // Uma repeticao nao duplica evento; inversao exige retorno ao fundo.
  assert(!confirmar(s,1,12000).aceito && s.eventos==1);
  assert(!confirmar(s,2,13000).aceito && s.valores[0]==1);
  for (unsigned i=0;i<CONFIRMACOES_SEM_EVENTO;++i) s.observar(0,.98f,.9f,14000+i);
  assert(confirmar(s,2,15000).aceito && s.valores[0]==0);
  assert(confirmar(s,3,16000).aceito && s.valores[1]==1);
  // Reset volta a desconhecido, recuperavel pelo proximo evento confirmado.
  s.resetar();
  assert(s.valores[0]==-1 && s.valores[1]==-1);
  assert(confirmar(s,4,20000).aceito && s.valores[1]==0 && s.valores[0]==-1);
  // Estados manuais e aquisicao guiada mantem o comportamento.
  s.informar(0,1);
  assert(s.observar(1,.95f,.8f,25000,true,true).aceito);
  assert(s.valores[0]==1 && s.valores[1]==1);
  std::cout << "OK: inicializacao pelas quatro classes, filtros, repeticao, bloqueio, reset e modo guiado\n";
}
