> Para esta revisão, veja primeiro `../LEIA_PRIMEIRO_INTEGRACAO.md`. Um Mosquitto já instalado pode escutar apenas em localhost; a escuta HTTP não muda a configuração desse serviço.

# Usar pelo notebook, PC e celular

## Iniciar no notebook

1. Instale as dependências com `python -m pip install -r requirements.txt`.
2. Inicie `python python/servidor.py`.
3. Inicie `python python/aquisicao.py` em outro terminal e conecte a USB.
4. Na janela Python ou no painel, confira **MQTT conectado**. O botão **Conectar MQTT** permite iniciar ou tentar novamente; **Desconectar MQTT** encerra o publicador e, se houver, o broker interno iniciado pelo programa. Um Mosquitto existente não é encerrado.

O botão controla o serviço no notebook. O processamento continua na placa e o salvamento continua no coletor Python. O servidor permanece responsivo enquanto o MQTT conecta ou encerra.

## Abrir no celular ou em outro PC

O terminal do servidor e o painel mostram o endereço deste notebook, por exemplo **http://192.168.1.20:8765**. Use o endereço que aparecer na sua instalação; o exemplo pode ser diferente do seu IP.

- Conecte o celular ao Wi-Fi da mesma rede. Um PC cabeado no mesmo roteador também pode acessar.
- Abra o endereço mostrado no navegador do outro dispositivo.
- No Windows do notebook, permita **Python** no Firewall em **redes privadas**. Não é necessário desativar o Firewall inteiro.
- Mantenha os dois programas abertos no notebook. Somente ele recebe a USB; os outros dispositivos usam o navegador.

`localhost` e `127.0.0.1` apontam para o próprio dispositivo que abriu o navegador. `0.0.0.0` configura a escuta do servidor; não é o endereço para digitar no celular. Use o IPv4 do notebook.

Se não abrir, verifique se os dispositivos estão na mesma rede, se o Firewall permite Python e se o Wi-Fi não usa isolamento de clientes ou rede de visitantes. Se o IP mudar, use o novo endereço exibido ao reiniciar o servidor.

## Conectar um aplicativo MQTT

Em um cliente MQTT instalado no PC/celular, use:

| Campo | Valor |
|---|---|
| Servidor | IPv4 do notebook mostrado pelo servidor |
| Porta | `1883` |
| Transporte | MQTT TCP |
| Tópico de assinatura | `tcc/eventos5/esp32_01/#` |
| Usuário/senha | Em branco, no broker interno deste protótipo |

O navegador usa HTTP para abrir o painel; não precisa de aplicativo MQTT ou WebSocket. Os clientes MQTT recebem os mesmos resultados e estados publicados pelo servidor. Não existe assinatura MQTT para comandos da placa nesta revisão; use os controles do painel.

O broker interno é destinado a uma LAN privada e confiável, sem autenticação. O pacote não configura encaminhamento de portas nem publicação na internet. A configuração de escuta TCP segue a [documentação do aMQTT 0.11.3](https://amqtt.readthedocs.io/en/v0.11.3/references/broker_config/).

## Quando MQTT não conecta

- **Dependências ausentes:** use o mesmo Python para instalar `requirements.txt` e executar os programas.
- **Porta 1883 ocupada:** feche o broker duplicado. Se deseja usar o broker que já existe, inicie com `--mqtt-externo`; esse broker precisa estar configurado para aceitar os outros dispositivos.
- **MQTT conecta no notebook, mas não no celular:** confira Firewall, rede de visitantes e o IP digitado.
- **Somente acesso local desejado:** use `python python/servidor.py --somente-local`.

`config/config_exemplo.json` é carregado no início. O padrão `host: 0.0.0.0` habilita a escuta na rede local. As portas padrão são 8765 para HTTP e 1883 para MQTT.
