// Testes da tolerancia a saturacao curta (partida do micro-ondas) em validacao_entrada.h.
// Compilar: g++ -std=c++17 -Wall -Wextra -o teste_saturacao teste_saturacao_tolerante.cpp
#include <array>
#include <cassert>
#include <cmath>
#include <cstring>
#include <iostream>
#include "../firmware/tcc_monitor/validacao_entrada.h"
struct Frame {
  double valores[106]={};
  uint32_t epoch=1,seq=0,primeiroFrame=0,ultimoFrame=0;
  int64_t inicioFramesUs=0,fimFramesUs=0;
  bool tempoInternoValido=true;
  uint16_t saturadasV=0,saturadasI=0;
};
using Contexto=std::array<Frame,25>;
static Contexto bom(){
  Contexto c;
  for(unsigned j=0;j<c.size();++j){
    auto &f=c[j]; f.seq=j+1;f.primeiroFrame=j*16+1;f.ultimoFrame=f.primeiroFrame+15;
    f.inicioFramesUs=f.primeiroFrame*12500;f.fimFramesUs=f.ultimoFrame*12500;
    f.valores[0]=130; f.valores[1]=300;
  }
  return c;
}
static bool tolerante(const Contexto &c,double &i,tcc::DiagnosticoEntrada &d,unsigned janelas=3){
  return tcc::validarContextoEntrada(c.data(),c.size(),16,256,5,10,janelas,i,d);
}
// Regra esperada, escrita de forma independente: integridade primeiro, depois saturacao.
static const char *referencia(const Contexto &f,unsigned janelasMax){
  unsigned jv=0,ji=0;
  for(unsigned j=0;j<25;++j){
    if(f[j].epoch!=f[0].epoch || f[j].ultimoFrame-f[j].primeiroFrame!=15 || !f[j].tempoInternoValido)return "integridade";
    if(j && (f[j].seq!=f[j-1].seq+1 || f[j].primeiroFrame!=f[j-1].ultimoFrame+1))return "integridade";
    if(!(f[j].valores[0]>=5))return "integridade";
    jv+=f[j].saturadasV>10; ji+=f[j].saturadasI>10;
  }
  if(f[24].ultimoFrame-f[0].primeiroFrame!=399)return "integridade";
  const double intervalo=static_cast<double>(f[24].fimFramesUs-f[0].inicioFramesUs)/(399.0*256);
  if(!(intervalo>0) || !std::isfinite(intervalo))return "integridade";
  if(jv>janelasMax)return "saturacao_adc_tensao_persistente";
  if(ji>janelasMax)return "saturacao_adc_corrente_persistente";
  return "ok";
}
int main(){
  tcc::DiagnosticoEntrada d;double intervalo=0;
  // Sem saturacao: aceito e resumo zerado.
  auto c=bom();assert(tolerante(c,intervalo,d));
  assert(d.janelasSaturadasI==0 && d.maxSaturadasI==0 && d.primeiraSaturadaI==-1 && !strcmp(d.motivo,"ok"));
  // Pico de partida: 3 janelas saturadas (600 ms) sao toleradas e informadas.
  c[10].saturadasI=180;c[11].saturadasI=40;c[12].saturadasI=11;c[13].saturadasI=10;
  assert(tolerante(c,intervalo,d));
  assert(d.janelasSaturadasI==3 && d.maxSaturadasI==180 && d.primeiraSaturadaI==10);
  assert(std::fabs(intervalo-48.828125)<1e-9);
  // Quarta janela saturada: corrente fora da faixa de forma persistente.
  c[20].saturadasI=500;assert(!tolerante(c,intervalo,d));
  assert(!strcmp(d.motivo,"saturacao_adc_corrente_persistente") && d.janela==10 && d.medido==4 && d.limite==3);
  assert(tcc::motivoSaturacao(d.motivo));
  // Tensao e avaliada antes da corrente.
  for(unsigned j=0;j<4;++j)c[j].saturadasV=50;
  assert(!tolerante(c,intervalo,d) && !strcmp(d.motivo,"saturacao_adc_tensao_persistente"));
  // Falha de integridade tem precedencia sobre a saturacao.
  c=bom();for(unsigned j=0;j<6;++j)c[j].saturadasI=300;c[15].seq+=1;
  assert(!tolerante(c,intervalo,d) && !strcmp(d.motivo,"sequencia_janelas_invalida"));
  assert(!tcc::motivoSaturacao(d.motivo) && !tcc::motivoSaturacao(nullptr));
  // Com tolerancia 0 vale o criterio anterior (primeira janela saturada invalida).
  c=bom();c[7].saturadasI=11;assert(!tolerante(c,intervalo,d,0));
  assert(!strcmp(d.motivo,"saturacao_adc_corrente") && d.janela==7 && d.medido==11 && d.limite==10);
  // Comparacao com a regra de referencia em combinacoes deterministicas.
  unsigned aceitos=0,persistentes=0;
  for(unsigned k=0;k<5000;++k){
    c=bom();
    const unsigned nSat=k%7;
    for(unsigned s=0;s<nSat;++s)c[(k*7+s*5)%25].saturadasI=static_cast<uint16_t>(5+(k+s*13)%400);
    if(k%11==0)c[(k/11)%25].saturadasV=static_cast<uint16_t>(k%40);
    if(k%13==0)c[(k/13)%25].seq+=1;
    if(k%17==0)c[(k/17)%25].valores[0]=(k%9)*.75;
    const unsigned janelasMax=1+k%4;
    const char *esperado=referencia(c,janelasMax);
    const bool ok=tolerante(c,intervalo,d,janelasMax);
    if(!strcmp(esperado,"ok")){assert(ok);++aceitos;}
    else if(!strcmp(esperado,"integridade")){assert(!ok && !tcc::motivoSaturacao(d.motivo));}
    else {assert(!ok && !strcmp(d.motivo,esperado));++persistentes;}
  }
  assert(aceitos>500 && persistentes>500);
  std::cout<<"OK: tolerancia a saturacao curta, saturacao persistente, precedencia da integridade e 5000 comparacoes ("
           <<aceitos<<" aceitos, "<<persistentes<<" persistentes)\n";
}
