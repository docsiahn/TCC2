# Perda de estados apos reconhecer uma transicao

Esta atualizacao corrige duas indicacoes do painel e acrescenta diagnostico.
Ainda nao determina nem corrige a causa fisica/firmware da perda observada.
Nao modifica o firmware, a rede neural, as features ou os dados de captura.

## Instalar

1. Feche aquisicao.py e servidor.py.
2. Guarde copias dos dois arquivos Python atuais.
3. Copie python/aquisicao.py e python/servidor.py deste ZIP para a subpasta
   python do seu projeto. Mantenha config/modelo.json e todo o firmware atual.
4. Abra os programas como antes. Com o Mosquitto existente:

       py python\servidor.py --mqtt-externo

   Em outro terminal:

       py python\aquisicao.py

   Use o mesmo comando Python que ja funciona no seu computador.
5. Atualize o painel no navegador; no computador, Ctrl+F5.

## Reproduzir e enviar evidencia

Informe os estados reais dos aparelhos, aguarde o aquecimento e faca uma unica
transicao no modo teste. Quando os estados forem perdidos, o painel mostrara
"Ultima perda de estado" com o motivo observado. Essa informacao permanece
mesmo quando a rede volta a produzir resultados.

Na janela aquisicao.py, clique Abrir pasta dos dados. Envie o arquivo
diagnostico.log dessa pasta e uma imagem da mensagem de perda de estado.
O log e limitado/rotativo e registra mudancas de boot, epoch, estados, falhas,
previsoes de transicao e falhas USB. Nao registra as amostras brutas.

Interpretacao:
- ESP32 reiniciou: mudou o identificador de boot. Ainda e necessario investigar
  a causa do reinicio (o log nao distingue sozinho alimentacao e watchdog).
- Contexto de aquisicao reiniciado: epoch mudou; houve invalidacao do pipeline.
- janela_ou_sensores_invalidos: o firmware recusou a janela. Pode envolver
  integridade/tempo, tensao minima ou saturacao; essa mensagem nao distingue
  qual verificacao falhou e pode exigir diagnostico adicional no firmware.
- USB/programa sem resposta: perda de comunicacao observada pelo software.

## Correcoes confirmadas no painel

Resultados do modo teste nao sao mais exibidos como Recebendo dados de uma
captura. O painel mostra Monitoramento continuo, sem gravacao de captura.
Resultados sem_evento_alvo aparecem como Sem mudanca de estado, em vez de
Evento aceito. Isso nao altera o reconhecimento nem os estados enviados pelo ESP32.

Os estados continuam sendo invalidados quando a placa reporta falha ou quando
ha perda de comunicacao. A atualizacao nao inventa estados nem conserva valores
antigos como se fossem confirmacoes atuais.

## Testes

Cinco testes novos cobrem retencao do motivo, falha, boot/epoch, desconexao,
registro serial, separacao de modo teste/captura e heartbeat de dois segundos.
Os testes existentes de servidor e aquisicao tambem foram executados.
Nao houve validacao com a placa fisica nesta sessao.
