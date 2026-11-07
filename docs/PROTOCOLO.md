# Contrato USB e supervisão local

O ESP32 envia um objeto JSON por linha, a 115200 baud, identificado por `protocolo=TCC_SISTEMA_V2`. Cada pacote identifica modelo, SHA-256, inicialização (`boot`), geração do pipeline (`epoch`), modo, origem do modo, condição selecionada, captura e estados. Firmware e Python antigos utilizam outro contrato: grave o firmware deste pacote para operar esta interface.

## Comandos USB

| Linha | Efeito |
|---|---|
| `PING` | Solicita identificação e estado |
| `MODE teste` | Seleciona teste contínuo |
| `MODE aquisicao` | Seleciona aquisição |
| `SCENARIO n` | Seleciona condição 1–12 |
| `CAPTURE` | Arma captura na condição selecionada |
| `CAPTURE n` | Arma captura na condição informada |
| `CANCEL` | Cancela captura em andamento |
| `STATE a b` | Informa estado real de liquidificador e micro-ondas, 0/1 |
| `RESET` | Reinicia o contexto dos estados, conforme lógica preservada do firmware |

Modo inicial: teste; condição inicial: 0, ainda não selecionada. Durante captura ou exportação, mudanças de modo, condição, estados e novas capturas são recusadas. A recusa não fica pendente para execução posterior. `CANCEL` pode interromper a exportação entre linhas.

## Ciclo de captura

`captura_armada` → `captura_inicio` → `acao` (somente transição) → `resultado` → 25 mensagens `entrada` → `entrada_fim`.

`captura_id` é formado por identificador de inicialização e contador da placa. Cada `entrada` contém índice `janela=0..24` e vetor `valores` com 106 elementos finitos. `entrada_fim` informa 25 janelas e conserva o identificador. Só então o coletor confirma a gravação no manifesto. `captura_cancelada`, falha, mudança de inicialização/geração ou dados fora de ordem impedem confirmar uma captura incompleta.

O campo `classe_esperada` descreve o rótulo experimental, enquanto `classe_id` em `resultado` descreve a previsão. São independentes.

## HTTP local

| Requisição | Uso |
|---|---|
| `GET /` | Painel |
| `GET /api/estado` | Estado, modo confirmado e identificador da instância do servidor |
| `GET /api/config` | Contrato e lista de condições |
| `GET /api/comandos` | Comandos pendentes para o coletor |
| `POST /api/comando` | Comando limitado a `mode`, `scenario`, `start`, `cancel`, `states`, `reset` |
| `POST /api/evento` | Mensagem serial, conexão, captura salva ou confirmação de transporte |
| `POST /api/mqtt` | `{"action":"connect"}` ou `{"action":"disconnect"}`; controle assíncrono do MQTT |

Exemplo de comando: `{"op":"mode","value":"aquisicao"}`. O coletor confirma que escreveu o comando na USB; somente a resposta da placa confirma a mudança de modo. Pedidos expiram após 15 segundos. Sem supervisão do cliente por 8 segundos, o painel invalida a conexão e os estados exibidos.

O painel aceita conexões pela LAN. O navegador deve usar o próprio endereço do servidor e a mesma origem. `/api/evento` e `/api/comandos` são reservados ao coletor executado no notebook, via loopback; os outros dispositivos podem acompanhar e enviar comandos pelo painel. A ponte USB é um cliente confiável da API; a API não autentica uma placa fisicamente. O modo `--demonstracao` marca a interface e os tópicos com `simulation=true` e `/demo`; ele não cria aquisições físicas nem dados de treinamento.

## Organização das tarefas

A aquisição e as features são calculadas na placa, que também executa inferência e lógica de estados. O coletor Python é o único dono da USB e confirma os arquivos locais. HTTP e MQTT rodam no segundo processo. A publicação não bloqueia a recepção USB; filas têm limites e a aquisição local continua quando a supervisão está indisponível.
