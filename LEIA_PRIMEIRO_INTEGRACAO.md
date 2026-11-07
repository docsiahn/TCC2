# Integração MQTT e Edge Impulse — 23/10/2026

Esta é uma revisão preparada do sistema. O firmware alterado ainda precisa ser compilado e testado no ESP32. As capturas e o modelo P10 de referência foram preservados. Os resultados físicos de 15/10 não validam automaticamente esta revisão.

## MQTT: causa encontrada e correção

O computador consultado tem Mosquitto escutando em `127.0.0.1:1883`. O pacote anterior tentava iniciar aMQTT em `0.0.0.0:1883`, disputando a porta. O Mosquitto respondeu ao teste de conexão e entregou uma mensagem publicada ao assinante. Nenhuma configuração do serviço foi alterada.

No servidor revisado, a inicialização verifica se existe um broker MQTT na porta e o reutiliza. Se não houver, tenta iniciar o broker interno. Desconectar pelo painel interrompe o publicador; um Mosquitto existente continua executando.

Para usar exclusivamente o Mosquitto:

```powershell
python -m pip install -r requirements-mosquitto.txt
python python/servidor.py --mqtt-externo
```

Em outro terminal:

```powershell
python python/aquisicao.py
```

O painel fica em `http://127.0.0.1:8765`. Um cliente MQTT no mesmo notebook deve usar `127.0.0.1`, porta `1883`, tópico `tcc/eventos5/esp32_01/#`.

O Mosquitto observado escuta somente no próprio computador. Isso não habilita clientes MQTT externos. O acesso HTTP do celular e a escuta MQTT na LAN são configurações distintas; ainda precisam de ensaio no notebook. Esta revisão não altera Firewall nem o serviço Mosquitto.

Diagnóstico sem dependências externas:

```powershell
python python/diagnostico_mqtt.py
```

Esse comando testa conexão, assinatura e entrega de uma mensagem de diagnóstico sem retenção. Ele não mede sensores nem valida o firmware.

## Rede importada do Edge Impulse

O novo adaptador usa o SDK exportado e `run_classifier`. Não converte a rede para matrizes de pesos escritas à mão.

1. No Edge Impulse, abra o projeto e impulse corretos. Em **Deployment**, exporte **Arduino library**, **float32**.
2. Coloque o ZIP dentro de `firmware/`, com o nome `modelo_edge_impulse.zip`. Use uma pasta curta para o projeto, por exemplo `C:\TCC`.
3. Feche os programas Python e execute, na raiz:

   ```powershell
   python python/importar_edge_impulse.py
   ```

   Também pode indicar o arquivo original, sem renomear:

   ```powershell
   python python/importar_edge_impulse.py "C:\Downloads\exportacao-arduino.zip"
   ```

4. Abra `firmware/tcc_monitor/tcc_monitor.ino` na Arduino IDE, compile para ESP32 Dev Module com core 3.3.10 e grave a placa.
5. Reinicie servidor e aquisição. Use uma nova pasta de campanha quando mudar o modelo.

O importador coloca o SDK em `firmware/tcc_monitor/src/edge_impulse`, gera `modelo_edge_impulse_config.h` e atualiza `config/modelo.json`. O Arduino compila os fontes em `src/` recursivamente. O importador ajusta apenas caminhos de includes do SDK para essa organização; o modelo binário e os cálculos não são modificados. Uma cópia da configuração anterior fica em `backups_modelo/`.

Para conferir o ZIP antes de aplicar:

```powershell
python python/importar_edge_impulse.py "exportacao.zip" --verificar
```

### Diferença importante entre as versões

A exportação local `...arduino-1.0.2-impulse-#2.zip` contém **eventos5_20261012**, a V1 anterior ao ajuste P10. Seu hash começa com `0ddae062`. Ela foi usada para testar a importação em uma pasta temporária. Não foi ativada neste pacote principal.

O modelo P10 atual tem hash `dd5a7cef19f07b1cdf6c4bd5b62aa321f81d18283c6094dd074c227178dc1cdb`. Para mantê-lo usando o SDK, é preciso exportar do Edge Impulse a versão que contém esses pesos. Não basta escolher um arquivo com cinco classes ou nome semelhante. O acesso ao Studio foi bloqueado nesta sessão, portanto essa exportação atual não foi obtida.

Sem importar um ZIP, o firmware continua usando a rede P10 de referência em C++.

### Contrato de entrada

As capturas continuam com 25 × 106 features. Para os modelos V1 conhecidos, o adaptador seleciona as 14 features por janela, em ordem temporal, e entrega 350 valores ao SDK. A padronização já está dentro do modelo; não é aplicada novamente pelo adaptador. O intervalo genérico de 1 ms do modelo pré-treinado no SDK não altera a aquisição física de 200 ms por janela.

O adaptador reconhece as cinco classes pelos nomes, mesmo se a ordem no export mudar. Modelos com 8 ou 12 classes são recusados. Int8 e EON não são habilitados nesta revisão.

Para um modelo novo, o importador exige um perfil explícito. Copie `config/perfil_edge_impulse_exemplo.json`, ajuste o hash e confira com o treinamento quais features entram e onde ocorre o pré-processamento:

```powershell
python python/importar_edge_impulse.py "exportacao.zip" --perfil "config/meu_perfil.json"
```

São suportados perfis com 25 janelas e 14 ou 106 features por janela. A ordem dos índices deve ser exatamente a do treino. O perfil é uma declaração do contrato; não constitui validação da acurácia de uma rede nova.

## VS Code e instalação

Use Python 3.12, crie `.venv` na raiz e instale as dependências. As tarefas incluídas no VS Code usam esse ambiente:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Em **Terminal > Run Task**, estão disponíveis servidor com Mosquitto, aquisição, diagnóstico MQTT, importação Edge Impulse e testes. A instalação do Python e das dependências não pôde ser concluída automaticamente nesta sessão.

## O que ainda falta

- Executar com o Python de uso do notebook e suas dependências; conferir as interfaces abertas.
- Obter a exportação Arduino atual da rede P10, ou escolher explicitamente outro modelo.
- Compilar e gravar o novo firmware; verificar RAM, inferência, USB, botão e LED.
- Repetir os ensaios com os aparelhos e o acesso pela LAN.
- Vincular ao repositório GitHub correto. Nenhum commit ou push foi realizado.

Veja `docs/VALIDACAO.md` para separar os testes desta revisão dos resultados anteriores.

Referências: [exportação Arduino](https://docs.edgeimpulse.com/hardware/deployments/run-arduino-2-0) e [entrada do classificador C++](https://docs.edgeimpulse.com/hardware/deployments/run-cpp-overview).
