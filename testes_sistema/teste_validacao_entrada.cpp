#include <array>
#include <cassert>
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>
#include "../firmware/tcc_monitor/validacao_entrada.h"
struct Frame {
  double valores[106]={};
  uint32_t epoch=1,seq=0,primeiroFrame=0,ultimoFrame=0;
  int64_t inicioFramesUs=0,fimFramesUs=0;
  bool tempoInternoValido=true;
  uint16_t saturadasV=0,saturadasI=0;
};
using Contexto=std::array<Frame,25>;
Contexto bom(){
  Contexto c;
  for(unsigned j=0;j<c.size();++j){
    auto &f=c[j]; f.seq=j+1;f.primeiroFrame=j*16+1;f.ultimoFrame=f.primeiroFrame+15;
    f.inicioFramesUs=f.primeiroFrame*12500;f.fimFramesUs=f.ultimoFrame*12500;
    f.valores[0]=130; f.valores[1]=300;
  }
  return c;
}
bool antigo(const Contexto &f,double &intervalo){
  for(unsigned j=0;j<25;++j){
    if(f[j].epoch!=f[0].epoch || f[j].ultimoFrame-f[j].primeiroFrame!=15 || !f[j].tempoInternoValido)return false;
    if(j && (f[j].seq!=f[j-1].seq+1 || f[j].primeiroFrame!=f[j-1].ultimoFrame+1))return false;
    if(f[j].valores[0]<5 || f[j].saturadasV>10 || f[j].saturadasI>10)return false;
  }
  if(f[24].ultimoFrame-f[0].primeiroFrame!=399)return false;
  intervalo=static_cast<double>(f[24].fimFramesUs-f[0].inicioFramesUs)/(399.0*256);
  return intervalo>0 && std::isfinite(intervalo);
}
bool novo(const Contexto &c,double &i,tcc::DiagnosticoEntrada &d){
  return tcc::validarContextoEntrada(c.data(),c.size(),16,256,5,10,i,d);
}
int main(){
  tcc::DiagnosticoEntrada d;double intervalo;
  auto c=bom();assert(novo(c,intervalo,d));assert(std::fabs(intervalo-48.828125)<1e-9);
  c[7].saturadasI=10;assert(novo(c,intervalo,d));
  c[7].saturadasI=11;assert(!novo(c,intervalo,d));
  assert(!strcmp(d.motivo,"saturacao_adc_corrente") && d.janela==7 && d.medido==11 && d.limite==10);
  c=bom();c[4].saturadasV=12;assert(!novo(c,intervalo,d));assert(!strcmp(d.motivo,"saturacao_adc_tensao"));
  c=bom();c[9].valores[0]=5;assert(novo(c,intervalo,d));
  c[9].valores[0]=4.99;assert(!novo(c,intervalo,d));assert(!strcmp(d.motivo,"vrms_abaixo_minimo"));
  c=bom();c[3].tempoInternoValido=false;assert(!novo(c,intervalo,d));assert(!strcmp(d.motivo,"tempo_interno_invalido"));
  c=bom();c[3].epoch=2;assert(!novo(c,intervalo,d));assert(!strcmp(d.motivo,"epoch_divergente"));
  c=bom();c[3].seq+=1;assert(!novo(c,intervalo,d));assert(!strcmp(d.motivo,"sequencia_janelas_invalida"));
  c=bom();c[3].primeiroFrame+=1;c[3].ultimoFrame+=1;assert(!novo(c,intervalo,d));assert(!strcmp(d.motivo,"sequencia_frames_invalida"));
  c=bom();c[24].fimFramesUs=0;assert(!novo(c,intervalo,d));assert(!strcmp(d.motivo,"intervalo_amostras_invalido"));
  // Equivalencia com as condicoes originais em combinacoes deterministicas.
  for(unsigned k=0;k<2000;++k){
    c=bom();auto &f=c[k%25];
    switch(k%10){
      case 0:f.saturadasI=k%30;break;case 1:f.saturadasV=k%30;break;
      case 2:f.valores[0]=(k%12)*.5;break;case 3:f.tempoInternoValido=k%3==0;break;
      case 4:f.epoch+=1;break;case 5:f.seq+=2;break;case 6:f.ultimoFrame+=1;break;
      case 7:f.primeiroFrame+=1;f.ultimoFrame+=1;break;
      case 8:c[24].fimFramesUs=k%2?-100:0;break;
      case 9:f.valores[0]=std::numeric_limits<double>::quiet_NaN();break;
    }
    double a=0,b=0;bool old=antigo(c,a),updated=novo(c,b,d);
    assert(old==updated);if(old)assert(a==b);else assert(strcmp(d.motivo,"ok"));
  }
  std::cout<<"OK: motivos, limites e 2000 comparacoes com a validacao anterior\n";
}
