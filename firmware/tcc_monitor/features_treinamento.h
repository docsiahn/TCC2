#pragma once
// Matematica extraida do coletor original; somente o acesso ao buffer foi trocado.
#include <cmath>
#include <cstdint>
#include <cstring>
namespace tcc {
constexpr uint32_t FFT_N=2048, QUANTIDADE_CANAIS=2, HARMONICA_MAXIMA=50;
constexpr uint32_t BINS_POR_HARMONICA=12, FREQUENCIA_FUNDAMENTAL_NOMINAL_HZ=60, RAIO_SUBGRUPO_BINS=1;
constexpr float PI_F=3.14159265358979323846f;
constexpr bool APLICAR_CALIBRACAO_FISICA_PQ=false;
constexpr double VOLTS_POR_CONTAGEM_ADC=1.0, AMPERES_POR_CONTAGEM_ADC=1.0, POLARIDADE_CORRENTE=1.0;
struct ComplexoF { float real; float imag; };
static ComplexoF bufferFFT[FFT_N];
static const uint16_t *amostrasTensao, *amostrasCorrente;
static uint16_t lerAmostra(const uint16_t *buffer,uint32_t indice) { return buffer[indice]; }
void fftRadix2(ComplexoF *dados, uint32_t tamanho);
void calcularFeaturesJanela(
    uint32_t janela,
    float magnitudes[QUANTIDADE_CANAIS][HARMONICA_MAXIMA + 1UL],
    float rms[QUANTIDADE_CANAIS],
    double &potenciaAtiva,
    double &potenciaReativa,
    double intervaloConversaoUs);
float magnitudeBinRms(uint32_t bin, uint8_t canal);
ComplexoF fasorBinRms(uint32_t bin, uint8_t canal);
double calcularTHD50(
    const float magnitudes[HARMONICA_MAXIMA + 1UL]);

void fftRadix2(ComplexoF *dados, uint32_t tamanho) {
  // Permutacao bit-reversal.
  for (uint32_t i = 1, j = 0; i < tamanho; ++i) {
    uint32_t bit = tamanho >> 1U;
    for (; j & bit; bit >>= 1U) {
      j ^= bit;
    }
    j ^= bit;
    if (i < j) {
      const ComplexoF temporario = dados[i];
      dados[i] = dados[j];
      dados[j] = temporario;
    }
  }

  for (uint32_t comprimento = 2; comprimento <= tamanho;
       comprimento <<= 1U) {
    const float angulo = -2.0f * PI_F / (float)comprimento;
    const float passoReal = cosf(angulo);
    const float passoImag = sinf(angulo);
    const uint32_t metade = comprimento >> 1U;

    for (uint32_t inicio = 0; inicio < tamanho; inicio += comprimento) {
      float wReal = 1.0f;
      float wImag = 0.0f;
      for (uint32_t j = 0; j < metade; ++j) {
        const ComplexoF u = dados[inicio + j];
        const ComplexoF original = dados[inicio + j + metade];
        const ComplexoF v = {
            original.real * wReal - original.imag * wImag,
            original.real * wImag + original.imag * wReal,
        };

        dados[inicio + j] = {u.real + v.real, u.imag + v.imag};
        dados[inicio + j + metade] =
            {u.real - v.real, u.imag - v.imag};

        const float proximoReal =
            wReal * passoReal - wImag * passoImag;
        wImag = wReal * passoImag + wImag * passoReal;
        wReal = proximoReal;
      }
    }
  }
}

void calcularFeaturesJanela(
    uint32_t janela,
    float magnitudes[QUANTIDADE_CANAIS][HARMONICA_MAXIMA + 1UL],
    float rms[QUANTIDADE_CANAIS],
    double &potenciaAtiva,
    double &potenciaReativa,
    double intervaloConversaoUs) {
  const uint32_t primeiroIndice = janela * FFT_N;
  double somaTensao = 0.0;
  double somaCorrente = 0.0;

  for (uint32_t n = 0; n < FFT_N; ++n) {
    somaTensao +=
        lerAmostra(amostrasTensao, primeiroIndice + n);
    somaCorrente +=
        lerAmostra(amostrasCorrente, primeiroIndice + n);
  }

  const float mediaTensao = (float)(somaTensao / (double)FFT_N);
  const float mediaCorrente = (float)(somaCorrente / (double)FFT_N);
  double somaQuadradosTensao = 0.0;
  double somaQuadradosCorrente = 0.0;
  double somaProdutos = 0.0;

  for (uint32_t n = 0; n < FFT_N; ++n) {
    const float tensaoSemDC =
        (float)lerAmostra(amostrasTensao, primeiroIndice + n) -
        mediaTensao;
    const float correnteSemDC =
        (float)lerAmostra(amostrasCorrente, primeiroIndice + n) -
        mediaCorrente;
    bufferFFT[n].real = tensaoSemDC;
    bufferFFT[n].imag = correnteSemDC;
    somaQuadradosTensao +=
        (double)tensaoSemDC * (double)tensaoSemDC;
    somaQuadradosCorrente +=
        (double)correnteSemDC * (double)correnteSemDC;

    // O padrao do ADC coleta tensao e depois corrente. A corrente do mesmo
    // indice ocorre um intervalo de conversao depois da tensao. Para P,
    // interpola-se a tensao no instante da corrente usando as amostras de
    // tensao anterior e seguinte no tempo. Como existem dois canais
    // alternados, esse instante fica aproximadamente no meio das duas.
    const uint32_t proximoN = (n + 1UL) % FFT_N;
    const float proximaTensaoSemDC =
        (float)lerAmostra(amostrasTensao,
                         primeiroIndice + proximoN) -
        mediaTensao;
    const double tensaoAlinhadaComCorrente =
        0.5 * ((double)tensaoSemDC + (double)proximaTensaoSemDC);
    somaProdutos +=
        tensaoAlinhadaComCorrente * (double)correnteSemDC;
  }

  rms[0] = (float)sqrt(somaQuadradosTensao / (double)FFT_N);
  rms[1] = (float)sqrt(somaQuadradosCorrente / (double)FFT_N);

  // Nao e aplicada Hann: esta versao usa janela retangular de 200 ms e soma
  // energetica do bin da harmonica com os dois bins adjacentes.
  fftRadix2(bufferFFT, FFT_N);

  const double escalaPotencia =
      APLICAR_CALIBRACAO_FISICA_PQ
          ? VOLTS_POR_CONTAGEM_ADC * AMPERES_POR_CONTAGEM_ADC
          : 1.0;

  // P representa a potencia ativa total no intervalo, calculada no dominio
  // do tempo. Ela inclui a contribuicao das harmonicas presentes no sinal.
  potenciaAtiva =
      somaProdutos / (double)FFT_N *
      POLARIDADE_CORRENTE * escalaPotencia;

  // Q usa os fasores RMS da componente fundamental. A fase do fasor de
  // corrente e recuada para compensar o intervalo entre a conversao da
  // tensao e a conversao da corrente no ADC multiplexado.
  const ComplexoF fasorTensao =
      fasorBinRms(BINS_POR_HARMONICA, 0);
  const ComplexoF fasorCorrenteMedido =
      fasorBinRms(BINS_POR_HARMONICA, 1);
  const double anguloCorrecaoRad =
      -2.0 * (double)PI_F *
      (double)FREQUENCIA_FUNDAMENTAL_NOMINAL_HZ *
      intervaloConversaoUs / 1000000.0;
  const double cosseno = cos(anguloCorrecaoRad);
  const double seno = sin(anguloCorrecaoRad);
  const double correnteRealCorrigida =
      (double)fasorCorrenteMedido.real * cosseno -
      (double)fasorCorrenteMedido.imag * seno;
  const double correnteImagCorrigida =
      (double)fasorCorrenteMedido.real * seno +
      (double)fasorCorrenteMedido.imag * cosseno;

  // S = V * conjugado(I). Convencao: Q positivo para corrente atrasada
  // em relacao a tensao, caracteristica de carga indutiva.
  potenciaReativa =
      ((double)fasorTensao.imag * correnteRealCorrigida -
       (double)fasorTensao.real * correnteImagCorrigida) *
      POLARIDADE_CORRENTE * escalaPotencia;

  memset(magnitudes, 0,
         sizeof(float) * QUANTIDADE_CANAIS *
             (HARMONICA_MAXIMA + 1UL));

  for (uint32_t h = 1; h <= HARMONICA_MAXIMA; ++h) {
    const uint32_t binCentral = h * BINS_POR_HARMONICA;
    for (uint8_t canal = 0; canal < QUANTIDADE_CANAIS; ++canal) {
      double somaQuadrados = 0.0;
      for (int32_t deslocamento = -(int32_t)RAIO_SUBGRUPO_BINS;
           deslocamento <= (int32_t)RAIO_SUBGRUPO_BINS;
           ++deslocamento) {
        const uint32_t bin =
            (uint32_t)((int32_t)binCentral + deslocamento);
        const double componente = magnitudeBinRms(bin, canal);
        somaQuadrados += componente * componente;
      }
      magnitudes[canal][h] = (float)sqrt(somaQuadrados);
    }
  }
}

float magnitudeBinRms(uint32_t bin, uint8_t canal) {
  const ComplexoF fasor = fasorBinRms(bin, canal);
  return sqrtf(fasor.real * fasor.real + fasor.imag * fasor.imag);
}

ComplexoF fasorBinRms(uint32_t bin, uint8_t canal) {
  const ComplexoF zPositivo = bufferFFT[bin];
  const ComplexoF zNegativo = bufferFFT[FFT_N - bin];

  float real = 0.0f;
  float imag = 0.0f;
  if (canal == 0) {
    // X[k] = (Z[k] + conj(Z[N-k])) / 2
    real = 0.5f * (zPositivo.real + zNegativo.real);
    imag = 0.5f * (zPositivo.imag - zNegativo.imag);
  } else {
    // Y[k] = (Z[k] - conj(Z[N-k])) / (2j)
    real = 0.5f * (zPositivo.imag + zNegativo.imag);
    imag = 0.5f * (zNegativo.real - zPositivo.real);
  }

  const float escalaRms = sqrtf(2.0f) / (float)FFT_N;
  return {escalaRms * real, escalaRms * imag};
}

double calcularTHD50(
    const float magnitudes[HARMONICA_MAXIMA + 1UL]) {
  const double fundamental = magnitudes[1];
  if (!(fundamental > 1.0e-9) || !std::isfinite(fundamental)) {
    return NAN;
  }

  double somaQuadrados = 0.0;
  for (uint32_t h = 2; h <= HARMONICA_MAXIMA; ++h) {
    const double valor = magnitudes[h];
    somaQuadrados += valor * valor;
  }
  return sqrt(somaQuadrados) / fundamental * 100.0;
}
} // namespace tcc
