#pragma once
#include <cstddef>
#include <cstdint>
#include <cmath>

namespace tcc {
struct DiagnosticoEntrada {
  const char *motivo="ok";
  int janela=-1, feature=-1;
  double medido=0, limite=0;
  bool falhar(const char *m,int j,double v,double l,int k=-1) {
    motivo=m; janela=j; medido=v; limite=l; feature=k; return false;
  }
};

// Mesmos criterios do firmware anterior; a primeira rejeicao agora tem motivo.
template <class Frame>
bool validarContextoEntrada(const Frame *f,size_t n,uint32_t framesPorJanela,
                           uint32_t conversoesPorFrame,double vrmsMin,
                           unsigned saturacaoMax,double &intervalo,DiagnosticoEntrada &d) {
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
    if (f[j].saturadasV>saturacaoMax)
      return d.falhar("saturacao_adc_tensao",j,f[j].saturadasV,saturacaoMax);
    if (f[j].saturadasI>saturacaoMax)
      return d.falhar("saturacao_adc_corrente",j,f[j].saturadasI,saturacaoMax);
  }
  const uint32_t span=f[n-1].ultimoFrame-f[0].primeiroFrame;
  const uint32_t esperado=static_cast<uint32_t>(n)*framesPorJanela-1;
  if (span!=esperado)
    return d.falhar("quantidade_frames_contexto",-1,span,esperado);
  intervalo=static_cast<double>(f[n-1].fimFramesUs-f[0].inicioFramesUs)/
            (static_cast<double>(esperado)*conversoesPorFrame);
  if (!(intervalo>0) || !std::isfinite(intervalo))
    return d.falhar("intervalo_amostras_invalido",-1,intervalo,0);
  return true;
}
}
