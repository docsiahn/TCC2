# Diagnostico detalhado da janela recusada — 23/10/2026

## O que o log enviado confirma

- 18:46:31,093: operador informou estados [0,0].
- 18:46:36,439: microondas_ligando confirmado, confianca 0,987908,
  estados [0,1].
- 18:46:38,126: janela_ou_sensores_invalidos; estados [-1,-1].
  Intervalo entre os registros: 1,687 segundo.
- 18:46:39 a 18:46:42: repeticoes da mesma falha generica.
- 18:46:55,641: microondas_desligando confirmado, estados [-1,0]. Isso
  demonstra que um evento ja consegue inicializar o aparelho antes desconhecido.

Boot 207312761 e epoch 1 permaneceram iguais nesses registros. Os resultados
registrados antes e depois da falha apresentam frames_perdidos=0 e
inferencias_descartadas=0. A placa informou o motor rede_eventos5_float32:
continua usando a P10 em C++, sem necessidade de trocar a rede neste diagnostico.

A causa imediata da perda e a rejeicao em prepararEntrada(), seguida de
estados.resetar(). O log antigo nao informa qual condicao de prepararEntrada
falhou. Saturacao e uma hipotese, nao uma conclusao. Ha avisos de linhas seriais
invalidas em outros horarios, mas eles nao demonstram a causa dessa rejeicao.

## Aplicar

Este pacote e uma atualizacao para o firmware tcc_monitor entregue neste chat.
Guarde uma copia do projeto atual. Feche aquisicao.py e servidor.py.
Copie os quatro arquivos de codigo mantendo as subpastas:

- firmware/tcc_monitor/tcc_monitor.ino
- firmware/tcc_monitor/validacao_entrada.h (novo)
- python/aquisicao.py
- python/servidor.py

Mantenha os outros arquivos, incluindo estados.h corrigido, pipeline.h,
rede_eventos5.h, eventual biblioteca Edge Impulse e config/modelo.json.

Abra tcc_monitor.ino na Arduino IDE, compile com a placa/core que ja usa e
grave no ESP32. Esta revisao identifica-se como diagnostico_entrada_v1_20261023.
Reabra servidor e aquisicao e atualize o painel.

No PowerShell, na raiz do projeto, se py for o comando Python em uso:

    py python\servidor.py --mqtt-externo

Em outro terminal:

    py python\aquisicao.py

Informe os estados reais, aguarde ao menos cinco segundos e reproduza a
transicao uma vez. Envie a nova mensagem Ultima perda de estado e o novo trecho
de diagnostico.log (Abrir pasta dos dados na janela de aquisicao).

## O que muda

As regras de rejeicao continuam iguais. A primeira falha passa a indicar:

- saturacao_adc_corrente ou saturacao_adc_tensao: contagem maior que 10 em
  2048 amostras por canal na janela. O painel informa contagem e limite.
- vrms_abaixo_minimo: RMS de tensao abaixo de 5 em unidades ADC, nao volts.
- epoch_divergente, quantidade_frames_janela, sequencia_janelas_invalida,
  sequencia_frames_invalida, quantidade_frames_contexto: inconsistencia temporal.
- tempo_interno_invalido ou intervalo_amostras_invalido: carimbos de tempo invalidos.
- feature_nao_finita ou formatacao_feature_invalida: problema numerico.

O log inclui diagnostico_entrada: motivo, janela e feature (indices base zero;
-1 significa nao aplicavel), medido, limite_ou_esperado, contagens de saturacao,
RMS e sequencia da janela afetada. Valores nao finitos sao serializados como
null para manter o JSON valido. Tambem registra revisao do firmware e contadores.

Uma janela de 200 ms participa de um contexto de 5 s. Um bloco ruim pode causar
varias rejeicoes enquanto permanece nesse contexto; isso explica uma possivel
duracao da falha, mas nao prova qual regra foi acionada no ensaio.

Esta revisao melhora o diagnostico. Ela ainda invalida os estados diante da
janela recusada; nao aumenta os limites, nao modifica a rede e nao declara a
falha de aquisicao corrigida. O proximo ajuste depende do motivo especifico.

## Testes realizados

- C++ nativo: limites exatos de saturacao/RMS, mensagens de rejeicao e 2000
  comparacoes deterministicas com as condicoes anteriores. Mesmo resultado de
  aceitacao/rejeicao, inclusive calculo do intervalo nos contextos aceitos.
- Funcoes de serializacao extraidas do .ino compiladas em um ensaio nativo:
  JSON valido tanto para falha numerica quanto para contagem de saturacao.
- Seis testes Python do diagnostico passaram: motivos, boot/epoch, comunicacao,
  captura versus monitoramento, detalhes no painel e log.
- Ainda nao houve compilacao completa para ESP32 nem teste fisico nesta sessao.

O pacote inclui os testes e esta explicacao. Nenhuma captura do usuario foi
incluida ou alterada. O log original continua preservado.
