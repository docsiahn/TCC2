# Revisão MQTT e importação Edge Impulse — 23/10/2026

- Detecção MQTT por CONNACK e reutilização de broker existente, com destino configurável.
- Diagnóstico de publicação/assinatura usando biblioteca padrão.
- Importação de biblioteca Arduino float32 do Edge Impulse no diretório do firmware; validação de classes, dimensões, perfil e hash.
- Adaptador para run_classifier e alinhamento das classes por nome; rede P10 original mantida quando nenhum ZIP é importado.
- Identidade do modelo lida de config/modelo.json também pelo coletor; campanha com hash diferente é recusada.
- Liberação do bloqueio de campanha quando a abertura falha.
- Tarefas de VS Code e instruções de instalação.
- 39 testes aprovados, 2 ignorados por dependências indisponíveis. Compilação, instalação e ensaio físico pendentes.

# Alterações

## Revisão LED e rede local — 23/10/2026

- LED GPIO2: um piscar por segundo no teste, dois piscas por ciclo na aquisição e aceso durante captura/envio.
- Botão Conectar/Desconectar MQTT na janela Python e no painel, com mensagem de estado.
- Controle MQTT assíncrono e possibilidade de reconectar sem reiniciar a aplicação.
- Escuta HTTP e MQTT habilitada para a LAN; endereço para PC/celular exibido no painel e no terminal.
- Rotas internas do coletor permanecem acessíveis somente no notebook.
- Firmware compilado e 33 testes Python aprovados. Ensaio físico do LED e acesso pelo celular ainda dependem da instalação.

Os pesos da rede, a extração de features e a lógica de estados foram preservados. O pacote anterior continua sendo uma revisão distinta; esta atualização requer gravar novamente o firmware para ativar o LED.
