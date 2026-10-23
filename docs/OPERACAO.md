# Condições de aquisição e operação

Há 12 condições experimentais e cinco classes de saída da rede. As quatro condições permanentes pertencem à classe `sem_evento_alvo`; os eventos são agrupados por aparelho e sentido da mudança.

| ID | Condição durante a captura | Classe neural |
|---:|---|---|
| 1 | Ambos parados | 0 — sem evento alvo |
| 2 | Ambos funcionando | 0 — sem evento alvo |
| 3 | Somente liquidificador funcionando | 0 — sem evento alvo |
| 4 | Somente micro-ondas funcionando | 0 — sem evento alvo |
| 5 | Liquidificador ligando; micro-ondas parado | 1 — liquidificador ligando |
| 6 | Liquidificador desligando; micro-ondas parado | 2 — liquidificador desligando |
| 7 | Micro-ondas ligando; liquidificador parado | 3 — micro-ondas ligando |
| 8 | Micro-ondas desligando; liquidificador parado | 4 — micro-ondas desligando |
| 9 | Liquidificador ligando; micro-ondas funcionando | 1 — liquidificador ligando |
| 10 | Liquidificador desligando; micro-ondas funcionando | 2 — liquidificador desligando |
| 11 | Micro-ondas ligando; liquidificador funcionando | 3 — micro-ondas ligando |
| 12 | Micro-ondas desligando; liquidificador funcionando | 4 — micro-ondas desligando |

A sugestão de sequência é **1 → 5 → 3 → 11 → 2 → 10 → 4 → 8 → 7 → 9 → 12 → 6**. O estado final de uma etapa é o estado inicial da seguinte. É uma sugestão de organização: o operador prepara os aparelhos e inicia cada captura. Não existe acionamento automático das cargas.

A meta padrão é 50 capturas por condição, totalizando 600. Capturas adicionais continuam sendo salvas, sem sobrescrever anteriores. O botão de início permanece manual. A meta não impede capturas extras direcionadas.

## O que fica salvo

- `campanha.json`: identificação da campanha, condições, meta e lista dos registros confirmados.
- `csv/<condicao>/<identificador>.csv`: `timestamp` em milissegundos e 106 features, em 25 linhas.
- `detalhes/<identificador>.json`: rótulo definido pela condição escolhida, resposta da rede, origem do gatilho, condições e hash do CSV.
- `diagnostico.log`: registro técnico para investigação de falhas; a tela principal exibe apenas as orientações necessárias.

Ordem das features: `VRMS, IRMS, P, Q, VTHD50, ITHD50`, 50 amplitudes `VFFT_H1..H50` e 50 amplitudes `IFFT_H1..H50`. Valores RMS e espectrais são derivados do ADC. Não interpretar os números como grandezas calibradas sem calibração específica do circuito.

Uma previsão incorreta da rede **não descarta** a amostra. O rótulo é a condição que o operador selecionou; a previsão é registrada separadamente. Uma transmissão incompleta não conta como captura salva. As verificações de formato ocorrem internamente, sem confirmação manual de salvar/descartar.

A mesma pasta só pode ser aberta por uma instância do coletor. Para retomar, mantenha `campanha.json`, os CSVs e os detalhes juntos. Arquivos parciais de uma interrupção não contam na campanha. Não edite os CSVs registrados: seus hashes são conferidos na retomada.

## Antes do ensaio físico da integração

1. Com o sistema pronto e uma condição selecionada, confirme que três cliques mudam para aquisição sem iniciar captura.
2. Dê um clique e confira uma captura salva com 25 linhas e 106 features.
3. Confira dois cliques para teste e a atualização do modo nas duas telas. Um clique no teste não deve capturar.
4. Informe os estados iniciais reais e verifique as quatro transições, com o outro aparelho parado e funcionando.
5. Pare e reinicie apenas o servidor: a aquisição deve continuar disponível e o painel deve recuperar o estado.

Registre o resultado desse ensaio separadamente dos testes de software. Nesta entrega o firmware foi compilado, mas a placa não foi regravada nem o botão físico foi acionado.
