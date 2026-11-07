#pragma once
#include <stdint.h>
#include <cmath>
#include "config.h"
namespace tcc {
struct Cenario { int8_t antes[2],depois[2]; bool evento; unsigned esperada; };
static constexpr Cenario CENARIOS[12]={
 {{0,0},{0,0},false,0},{{1,1},{1,1},false,0},{{1,0},{1,0},false,0},{{0,1},{0,1},false,0},
 {{0,0},{1,0},true,1},{{1,0},{0,0},true,2},{{0,0},{0,1},true,3},{{0,1},{0,0},true,4},
 {{0,1},{1,1},true,1},{{1,1},{0,1},true,2},{{1,0},{1,1},true,3},{{1,1},{1,0},true,4}
};
struct Decisao { const char *motivo="aguardando",*origem="nenhuma"; uint8_t alterados=0; bool aceito=false; };
class Estados {
 public:
  int8_t valores[2]={-1,-1};
  uint32_t eventos=0,repetidos=0,incompativeis=0;
  void resetar(){informar(-1,-1);}
  void informar(int a,int b){
    valores[0]=a;valores[1]=b;candidato_=-1;confirmacoes_=0;fundo_=0;
    for(unsigned j=0;j<2;++j){bloqueado_[j]=false;temEvento_[j]=false;ultimoEvento_[j]=0;}
  }
  Decisao observar(int classe,float confianca,float margem,uint32_t agora,bool integra=true,bool guiado=false){
    Decisao d;
    if(!integra){limpar();d.motivo="dados_invalidos";return d;}
    if(classe<0||classe>4||!std::isfinite(confianca)||!std::isfinite(margem)||confianca<CONFIANCA_MINIMA||margem<MARGEM_MINIMA){
      limpar();d.motivo="baixa_confianca";return d;
    }
    if(classe==0){
      candidato_=-1;confirmacoes_=0;if(fundo_<1000)++fundo_;
      if(fundo_>=CONFIRMACOES_SEM_EVENTO||guiado)bloqueado_[0]=bloqueado_[1]=false;
      d.motivo="sem_evento";d.origem="historico";d.aceito=true;return d;
    }
    fundo_=0;
    if(candidato_!=classe){candidato_=classe;confirmacoes_=1;}else if(confirmacoes_<1000)++confirmacoes_;
    if(confirmacoes_<(guiado?1U:CONFIRMACOES_EVENTO)){d.motivo="confirmando";return d;}
    unsigned dispositivo=classe<=2?0:1;
    int alvo=(classe==1||classe==3)?1:0;
    // Um evento confirmado informa o estado final mesmo sem estado inicial.
    // O outro aparelho permanece desconhecido ate seu proprio evento ou STATE.
    if(valores[dispositivo]==alvo){++repetidos;d.motivo="evento_repetido";return d;}
    if(bloqueado_[dispositivo]||(temEvento_[dispositivo]&&static_cast<uint32_t>(agora-ultimoEvento_[dispositivo])<INTERVALO_EVENTOS_MS)){
      ++incompativeis;d.motivo="aguardando_sem_evento";return d;
    }
    valores[dispositivo]=alvo;bloqueado_[dispositivo]=true;temEvento_[dispositivo]=true;ultimoEvento_[dispositivo]=agora;
    ++eventos;d.alterados=1U<<dispositivo;d.aceito=true;d.origem="evento";d.motivo="evento_confirmado";return d;
  }
 private:
  int candidato_=-1;unsigned confirmacoes_=0,fundo_=0;
  bool bloqueado_[2]={false,false},temEvento_[2]={false,false};uint32_t ultimoEvento_[2]={0,0};
  void limpar(){candidato_=-1;confirmacoes_=fundo_=0;}
};
}
