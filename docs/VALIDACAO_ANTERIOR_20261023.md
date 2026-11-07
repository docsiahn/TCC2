# Verificação da entrega — 23/10/2026

## Firmware

Compilação da revisão com LED concluída para `esp32:esp32:esp32`, core Espressif 3.3.10: **362.152 bytes de programa** e **56.676 bytes de variáveis globais**, conforme o compilador. A compilação não realizou upload na placa.

Os cabeçalhos `rede_eventos5.h`, `features_treinamento.h`, `pipeline.h` e `estados.h` são idênticos aos da versão V1 P10 utilizada como referência. Em 221 capturas de comparação, as 221 classes previstas coincidiram com a referência; a maior diferença absoluta entre probabilidades foi aproximadamente `1,19 × 10⁻⁶`.

O controle do botão passou em 16 ensaios nativos, incluindo clique único, duplo, triplo, repique, pressão longa, excesso de cliques e retorno do contador de tempo. O teste C++ portátil está em `testes_sistema/teste_botao.cpp`; pode ser compilado com um compilador C++11 ou superior, separadamente do Arduino.

## Python, HTTP e MQTT

**33 testes automáticos passaram** em Windows com Python 3.12.14. A suíte inclui:

- recepção de linhas seriais fragmentadas e captura sem servidor;
- captura completa, duplicação, falta de dados e alteração de condição;
- cancelamento, reinício da placa e interrupção do pipeline;
- prevenção de confirmação após falha de escrita;
- retomada da campanha, integridade dos CSVs e bloqueio de duas instâncias;
- comandos HTTP e confirmação do modo pela placa;
- reinício do servidor com reutilização de números de comando;
- comunicação real com broker MQTT em loopback, reconexão e estado retido.
- acesso HTTP e cliente MQTT pelo IPv4 da interface de rede do computador;
- conectar, desconectar e reconectar MQTT pela API usada pelo botão.

A interface HTTP foi inspecionada no navegador. A janela Python dispõe de rolagem para telas de notebook. Os testes de rede usaram clientes no próprio computador acessando seu IPv4 da LAN; um celular físico e o Firewall do notebook ainda precisam ser conferidos na instalação. Os testes usam mensagens controladas ou reprodução de dados anteriores; não são uma medição de acurácia de campo da nova integração.

Foram reproduzidas as 24 capturas do ensaio final de 15/10/2026 no novo receptor, adaptando somente o envelope do protocolo antigo. As 24 foram salvas uma única vez, duas por condição. Os tempos e os valores das 106 features coincidiram numericamente com os CSVs originais. Os arquivos originais permaneceram intactos.

## Osciloscópio

O firmware, o programa Python e o arquivo de dependências do osciloscópio foram copiados sem alterações. A sintaxe Python foi verificada. Não houve nova aquisição física pelo osciloscópio nesta etapa.

## Limite da validação

**A nova integração ainda não passou por ensaio físico de USB, GPIO4, LED GPIO2 e acionamento dos aparelhos.** A rede preserva o modelo anterior; o resultado físico anterior de 24 acertos em 24 ensaios guiados pertence à versão usada em 15/10/2026. Não deve ser apresentado como ensaio físico desta revisão.

As condições de referência são micro-ondas P10 e liquidificador velocidade 1. O pacote não estabelece desempenho para P3, outras velocidades, outras instalações ou cargas diferentes. O ensaio físico sugerido está em `OPERACAO.md`.

Os números e o contrato técnico também estão em `VALIDACAO.json`. `SHA256SUMS.txt`, na raiz, relaciona os arquivos distribuídos para comparação de integridade.
