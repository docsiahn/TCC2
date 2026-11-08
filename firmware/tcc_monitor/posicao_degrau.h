#pragma once
#include <cmath>
#include <cstddef>
namespace tcc {
// Posicao do degrau de corrente no contexto de 5 s (estimador classico de ponto de mudanca).
// Retorna p em 1..n-1 que maximiza |media(depois) - media(antes)| * sqrt(p*(n-p)/n):
// o degrau fica entre as janelas p-1 e p, contadas da mais antiga (0) para a mais recente (n-1).
// valores[j*passo] e o IRMS da janela j. Sem degrau (sinal constante) o resultado e 1.
inline unsigned posicaoDegrau(const float *valores, unsigned n, unsigned passo) {
  if (!valores || n < 2) return 0;
  double total = 0;
  for (unsigned j = 0; j < n; ++j) total += valores[j * passo];
  double antes = 0, melhor = -1;
  unsigned posicao = 1;
  for (unsigned p = 1; p < n; ++p) {
    antes += valores[(p - 1) * passo];
    const double mediaAntes = antes / p, mediaDepois = (total - antes) / (n - p);
    const double escore = std::fabs(mediaDepois - mediaAntes) * std::sqrt(double(p) * (n - p) / n);
    if (escore > melhor) { melhor = escore; posicao = p; }
  }
  return posicao;
}
// Eventos so sao aceitos no modo teste quando o degrau esta na faixa de posicoes vista no treino.
inline bool posicaoDegrauValida(unsigned posicao, unsigned minimo, unsigned maximo) {
  return posicao >= minimo && posicao <= maximo;
}
}
