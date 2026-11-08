#pragma once
#include <cstddef>
#include <cstdint>
#include <cmath>

namespace tcc {
struct DiagnosticoEntrada {
  const char *motivo="ok";
  int janela=-1, feature=-1;
  double medido=0, limite=0;
  // Resumo da saturacao do ADC no contexto, preenchido tambem quando a entrada e aceita.
  // Uma janela conta como saturada quando tem mais amostras em 0 ou 4095 que o limite por janela.
  unsigned janelasSaturadasV=0, janelasSaturadasI=0, maxSaturadasV=0, maxSaturadasI=0;
  int primeiraSaturadaV=-1, primeiraSaturadaI=-1;
  bool falhar(const char *m,int j,double v,double l,int k=-1) {
    motivo=m; janela=j; medido=v; limite=l; feature=k; return false;
  }
};

// Motivos que indicam sinal fora da faixa do canal, e nao perda de dados.
inline bool motivoSaturacao(const char *m) {
  if (!m) return false;
  const char prefixo[]="saturacao_adc_";
  for (size_t k=0;k+1<sizeof(prefixo);++k) if (m[k]!=prefixo[k]) return false;
  return true;
}

// Com janelasSaturadasMax = 0, vale o criterio anterior: a primeira janela com mais de
// saturacaoMax amostras saturadas invalida o contexto inteiro.
// Com janelasSaturadasMax > 0, picos curtos, como a partida do micro-ondas, sao tolerados:
// o contexto so e recusado se mais de janelasSaturadasMax janelas estiverem saturadas.
// As verificacoes de integridade (epoch, frames, sequencia, tempo e VRMS) nao mudam e
// continuam tendo precedencia sobre a saturacao.
template <class Frame>
bool validarContextoEntrada(const Frame *f,size_t n,uint32_t framesPorJanela,
                           uint32_t conversoesPorFrame,double vrmsMin,
                           unsigned saturacaoMax,unsigned janelasSaturadasMax,
                           double &intervalo,DiagnosticoEntrada &d) {
  d=DiagnosticoEntrada{};
  if (!f || !n || !framesPorJanela || !conversoesPorFrame)
    return d.falhar("contexto_ausente",-1,n,1);
  for (size_t j=0;j<n;++j) {
    if (f[j].epoch!=f[0].epoch)
      return d.falhar("epoch_divergente",j,f[j].epoch,f[0].epoch);
    const uint32_t span=f[j].ultimoFrame-f[j].primeiroFrame;
    if (span!=framesPorJanela-1)
      return d.falhar("quantidade_frames_janela",j,span,framesPorJanela-1);
    if (!f[j].tempoInternoValido)
      return d.falhar("tempo_interno_invalido",j,0,1);
    if (j && f[j].seq!=f[j-1].seq+1)
      return d.falhar("sequencia_janelas_invalida",j,f[j].seq,f[j-1].seq+1);
    if (j && f[j].primeiroFrame!=f[j-1].ultimoFrame+1)
      return d.falhar("sequencia_frames_invalida",j,f[j].primeiroFrame,f[j-1].ultimoFrame+1);
    if (f[j].valores[0]<vrmsMin)
      return d.falhar("vrms_abaixo_minimo",j,f[j].valores[0],vrmsMin);
    if (f[j].saturadasV>d.maxSaturadasV) d.maxSaturadasV=f[j].saturadasV;
    if (f[j].saturadasI>d.maxSaturadasI) d.maxSaturadasI=f[j].saturadasI;
    if (f[j].saturadasV>saturacaoMax) {
      if (!janelasSaturadasMax)
        return d.falhar("saturacao_adc_tensao",j,f[j].saturadasV,saturacaoMax);
      if (!d.janelasSaturadasV++) d.primeiraSaturadaV=static_cast<int>(j);
    }
    if (f[j].saturadasI>saturacaoMax) {
      if (!janelasSaturadasMax)
        return d.falhar("saturacao_adc_corrente",j,f[j].saturadasI,saturacaoMax);
      if (!d.janelasSaturadasI++) d.primeiraSaturadaI=static_cast<int>(j);
    }
  }
  const uint32_t span=f[n-1].ultimoFrame-f[0].primeiroFrame;
  const uint32_t esperado=static_cast<uint32_t>(n)*framesPorJanela-1;
  if (span!=esperado)
    return d.falhar("quantidade_frames_contexto",-1,span,esperado);
  intervalo=static_cast<double>(f[n-1].fimFramesUs-f[0].inicioFramesUs)/
            (static_cast<double>(esperado)*conversoesPorFrame);
  if (!(intervalo>0) || !std::isfinite(intervalo))
    return d.falhar("intervalo_amostras_invalido",-1,intervalo,0);
  if (d.janelasSaturadasV>janelasSaturadasMax)
    return d.falhar("saturacao_adc_tensao_persistente",d.primeiraSaturadaV,d.janelasSaturadasV,janelasSaturadasMax);
  if (d.janelasSaturadasI>janelasSaturadasMax)
    return d.falhar("saturacao_adc_corrente_persistente",d.primeiraSaturadaI,d.janelasSaturadasI,janelasSaturadasMax);
  return true;
}

// Assinatura anterior, sem tolerancia; mantida para os testes de equivalencia.
template <class Frame>
bool validarContextoEntrada(const Frame *f,size_t n,uint32_t framesPorJanela,
                           uint32_t conversoesPorFrame,double vrmsMin,
                           unsigned saturacaoMax,double &intervalo,DiagnosticoEntrada &d) {
  return validarContextoEntrada(f,n,framesPorJanela,conversoesPorFrame,vrmsMin,saturacaoMax,0u,intervalo,d);
}
}
