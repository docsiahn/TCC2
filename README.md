> **Revisão MQTT + Edge Impulse:** comece por [LEIA_PRIMEIRO_INTEGRACAO.md](LEIA_PRIMEIRO_INTEGRACAO.md). Esta cópia inclui reutilização do Mosquitto e um importador do SDK Arduino do Edge Impulse. A instalação e a compilação do novo firmware ainda estão pendentes; consulte [a validação desta revisão](docs/VALIDACAO.md).

# Monitor de equipamentos elétricos — TCC

Sistema experimental com ESP32 para identificar eventos de um liquidificador e um micro-ondas. O firmware calcula as features e executa a rede na placa; o notebook recebe as capturas, mostra os estados e publica os resultados em MQTT.

## Conteúdo

```text
firmware/tcc_monitor/       Firmware integrado e seus cabeçalhos
python/aquisicao.py         USB, aquisição, teste e salvamento
python/servidor.py          Painel HTTP e broker MQTT local
osciloscopio/               Firmware e programa de diagnóstico preservados
config/                    Configuração e identificação do modelo
docs/                      Operação, protocolo e registro de validação
testes_sistema/             Testes automáticos, sem placa conectada
```

## Começar no notebook

1. Extraia a pasta em um caminho curto, por exemplo `C:\TCC`. Abra essa pasta no VS Code.
2. Use **Python 3.12**, com Tcl/Tk habilitado. No terminal, instale:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Na Arduino IDE, instale **esp32 by Espressif Systems 3.3.10** no Gerenciador de Placas. Abra `firmware/tcc_monitor/tcc_monitor.ino`, selecione **ESP32 Dev Module**, flash de **4 MB**, a porta da placa e envie. Os arquivos `.h` devem continuar junto do `.ino`. O firmware usa as APIs do core 3.x.
4. Feche o Monitor Serial e o Plotter Serial da Arduino IDE. No primeiro terminal do VS Code:

   ```powershell
   python python/servidor.py
   ```

5. No segundo terminal:

   ```powershell
   python python/aquisicao.py
   ```

6. Na janela de aquisição, selecione a COM e clique em **Conectar**. O painel abre em [localhost:8765](http://127.0.0.1:8765). Mantenha os dois programas abertos.

Somente `aquisicao.py` abre a USB. O servidor recebe os dados desse programa; não disputa a porta com ele. Não há lançadores `.cmd`, instalação de Mosquitto ou gravação automática da placa.

## Botão e modos

Ligue um botão normalmente aberto entre **GPIO4 e GND**. O firmware usa o pull-up interno. Este botão é diferente do BOOT da placa.

| Gesto | Ação |
|---|---|
| 1 clique | Inicia uma captura, somente no modo aquisição e com uma condição selecionada |
| 2 cliques | Seleciona teste contínuo |
| 3 cliques | Seleciona aquisição |

Os cliques da sequência devem ser rápidos. A ação ocorre após terminar a sequência; uma sequência tripla não inicia uma captura intermediária. Pressões longas e quatro ou mais cliques são ignorados. Durante uma captura, aguarde o salvamento ou cancele antes de mudar o modo.

Os mesmos controles existem no Python e no painel HTTP. Ambos exibem o modo confirmado pela placa, inclusive quando a mudança vem do botão.

### LED de status

O LED de usuário fica no **GPIO2**, ativo em nível alto, conforme `config.h`:

| Situação | Indicação |
|---|---|
| Teste contínuo | Um piscar por segundo |
| Aquisição, aguardando | Dois piscas rápidos, seguidos de pausa, a cada 2 segundos |
| Captura em andamento | Aceso continuamente até concluir a captura e o envio |

Após salvar ou cancelar, o LED retoma o padrão do modo. A indicação não usa esperas que interrompam a aquisição. Se a sua placa tiver LED em outro pino ou com polaridade invertida, ajuste `PINO_LED_STATUS` e `LED_ATIVO_ALTO`.

## Fazer uma captura

1. Selecione **Aquisição** e uma das 12 condições. Aguarde a condição aparecer confirmada.
2. Prepare os aparelhos conforme o estado inicial mostrado.
3. Pressione **Enter**, **Iniciar captura** ou dê um clique no botão.
4. Nos ensaios de transição, mude somente o aparelho indicado no aviso **AGORA**, próximo de 2 segundos. Nos regimes permanentes, mantenha os estados durante os 5 segundos.
5. Aguarde **CAPTURA SALVA**. O arquivo é salvo automaticamente; não há pergunta de salvar/descartar.

O programa sugere a próxima condição sem iniciar outra captura. Desmarque **Sugerir próxima classe após salvar** para repetir a atual. F11 alterna tela cheia; Esc retorna. Veja a sequência e os rótulos em [docs/OPERACAO.md](docs/OPERACAO.md).

## Teste contínuo

Selecione **Teste contínuo** e use **Informar estados iniciais**, informando o estado real de cada aparelho. O painel também permite essa confirmação. A rede identifica eventos; o firmware combina os eventos com o contexto para atualizar os estados. Após reiniciar a placa, informe os estados novamente. Na falta de comunicação, as telas mostram estados desconhecidos.

## Dados e modelo

As capturas ficam por padrão em `Documents/TCC/capturas` do usuário do notebook. **Abrir pasta dos dados** mostra o local utilizado. A mesma pasta retoma a campanha; outra pasta cria uma campanha nova:

```powershell
python python/aquisicao.py --dados "C:\TCC\minhas_capturas" --repeticoes 50 --potencia P10 --velocidade 1 --fundo "geladeira e duas lampadas"
```

As opções descrevem as condições físicas; não alteram a potência ou velocidade dos aparelhos. Na retomada, o programa conserva as condições e a meta registradas em `campanha.json`. Para mudar essas condições, use uma pasta nova.

Cada captura contém **25 linhas × 106 features**, além do tempo. A rede preservada `eventos5_v1_p10_20261015` utiliza **14 features selecionadas** dessas 106 para prever cinco classes. Aquisição, seleção de features e número de classes são etapas diferentes. Esta integração não retreina nem altera os pesos da rede.

Configuração física de referência: TP em GPIO36, TC em GPIO39; liquidificador Philips Walita 1200 W na velocidade 1; micro-ondas Midea MRAS21 127 V em **P10**. A potência P3 produzia comutações internas que confundiam eventos. O pacote não resolve essa distinção em P3.

## HTTP e MQTT

O `servidor.py` reutiliza um broker MQTT existente na porta **1883**; se não houver, tenta iniciar o broker interno. O painel HTTP usa a porta **8765**. Por padrão ambos aceitam acesso pela rede local (`host: 0.0.0.0`). As portas e o tópico estão em `config/config_exemplo.json`. Não é necessário internet para a operação após instalar as dependências.

Use **Conectar MQTT / Desconectar MQTT** na janela Python ou no painel. O estado e a mensagem da conexão aparecem ao lado. O MQTT tenta conectar ao iniciar; o botão permite desligar ou tentar novamente após uma falha. Ele funciona também sem placa USB conectada.

Para abrir no PC ou celular, conecte o dispositivo à mesma rede do notebook. O servidor mostra **PC/celular na mesma rede: http://IP_DO_NOTEBOOK:8765** no terminal; o painel também exibe esse endereço. No Windows, permita Python no Firewall para **redes privadas**. O notebook deve permanecer ligado, executando os dois scripts. Consulte [docs/REDE_LOCAL.md](docs/REDE_LOCAL.md).

Tópico base: `tcc/eventos5/esp32_01`.

| Sufixo | Conteúdo |
|---|---|
| `/telemetria` | Resultado da rede recebido da placa |
| `/eventos` | Mudança de estado aceita pelo firmware |
| `/estado` | Estado confirmado, modo e conexão USB; mensagem retida |
| `/status` | Disponibilidade do publicador; mensagem retida e última vontade |

Os dados das 106 features ficam nos CSVs. MQTT publica resultados e estados. A perda do servidor ou MQTT não impede salvar uma aquisição local. Ao reiniciar o servidor, a ponte volta a publicar o estado atual.

Para usar um broker **local** já aberto, execute `python python/servidor.py --mqtt-externo`; a liberação de rede desse broker depende da configuração dele. Para iniciar com MQTT desligado e conectar depois pelo botão, use `--mqtt-desligado`. Para restringir tudo ao notebook, use `--somente-local`. Para adquirir sem servidor, use `python python/aquisicao.py --sem-servidor`.

## Osciloscópio

O programa de diagnóstico permanece separado. Grave `osciloscopio/firmware/osciloscopio_gpio36_39/osciloscopio_gpio36_39.ino`, instale as dependências próprias e execute:

```powershell
python -m pip install -r osciloscopio/requirements.txt
python osciloscopio/osciloscopio.py COM6
```

Substitua COM6 pela porta real. O osciloscópio usa **921600 baud**; o sistema integrado usa **115200 baud**. Feche a aquisição antes do diagnóstico. Depois, grave novamente `tcc_monitor.ino` para voltar ao sistema. O gráfico mostra contagens ADC, sem calibração em volts ou ampères.

## Verificação e limites

Execute os testes com:

```powershell
python -m unittest discover -s testes_sistema -v
```

A revisão anterior teve compilação e testes registrados. O novo adaptador de inferência ainda não foi compilado; os testes desta revisão estão documentados separadamente. A nova integração precisa do ensaio final com a placa e o botão físicos. Os resultados físicos anteriores pertencem à versão anterior do sistema. Consulte [docs/VALIDACAO.md](docs/VALIDACAO.md).

Se a COM não abrir, feche outras aplicações que a utilizem. Se abrir sem resposta, confira o firmware gravado e o cabo. Se a porta 1883 estiver ocupada, feche o broker duplicado ou utilize `--mqtt-externo`. Se mudar a porta HTTP, informe o mesmo endereço ao coletor com `--servidor http://127.0.0.1:PORTA`.

## Publicação

Os dados coletados, ambientes Python, compilações e logs estão excluídos pelo `.gitignore`. Antes de publicar, acrescente autoria, instituição e a licença de distribuição escolhida pelo autor. Nenhuma licença de redistribuição foi presumida nesta entrega.
