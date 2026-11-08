// Roda o codigo real do firmware (estados.h + posicao_degrau.h + config.h) sobre os contextos
// deslizantes das 600 capturas de treino e imprime os eventos confirmados.
#include <cstdio>
#include <cstdint>
#include <vector>
#include <map>
#include "estados.h"
#include "posicao_degrau.h"
static const int ANTES[13][2]={{0,0},{0,0},{1,1},{1,0},{0,1},{0,0},{1,0},{0,0},{0,1},{0,1},{1,1},{1,0},{1,1}};
int main(int argc,char**argv){
  int usarFaixa=atoi(argv[1]), desconhecido=atoi(argv[2]);
  FILE*f=fopen("contextos.txt","r");
  int i,c,j,atual=-1; float p[5],ir[25];
  tcc::Estados e; std::vector<std::pair<int,int>> evs; 
  auto fim=[&](){ if(atual<0)return; printf("%d",atual); for(auto&x:evs)printf(" %d:%d",x.first,x.second); printf("\n"); };
  while(fscanf(f,"%d %d %d",&i,&c,&j)==3){
    for(int k=0;k<5;++k)fscanf(f,"%f",&p[k]); for(int k=0;k<25;++k)fscanf(f,"%f",&ir[k]);
    if(i!=atual){ fim(); atual=i; evs.clear(); e=tcc::Estados();
      if(desconhecido) e.resetar(); else e.informar(ANTES[c][0],ANTES[c][1]); }
    unsigned classe=0; float segunda=-1;
    for(unsigned k=1;k<5;++k) if(p[k]>p[classe]) classe=k;
    for(unsigned k=0;k<5;++k) if(k!=classe&&p[k]>segunda) segunda=p[k];
    unsigned pos=tcc::posicaoDegrau(ir,25,1);
    bool ok=usarFaixa? tcc::posicaoDegrauValida(pos,POSICAO_DEGRAU_MIN,POSICAO_DEGRAU_MAX):true;
    auto d=e.observar(classe,p[classe],p[classe]-segunda,(uint32_t)j*200,true,false,ok);
    if(d.aceito&&d.alterados) evs.push_back({j,(int)classe});
  }
  fim();
}
