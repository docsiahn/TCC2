# Filtro de posição do degrau e MQTT direto — 07/11/2026

- Firmware `posicao_degrau_v6_20261107`. No modo teste, uma saída de evento só conta para a confirmação quando o degrau de IRMS do contexto de 5 s está nas janelas 11–17 (`POSICAO_DEGRAU_MIN/MAX` em `config.h`), faixa em que o degrau ficou em 93% das capturas de treino. Fora dela a decisão é `fora_da_posicao`. A aquisição guiada não usa o filtro.
- Motivo: logo após um desligamento a rede passava ~1 s pela classe de desligar do outro aparelho (degrau nas janelas 18–24), e após o micro-ondas desligar voltava a indicar `liq_desligando` (janelas 3–9). Com estados desconhecidos ou compatíveis, isso virava evento falso.
- Simulação com o código do firmware sobre as 600 capturas deslizando pelo contexto: 400/400 eventos detectados nos dois casos; eventos falsos 120 → 12 (estados conhecidos) e 207 → 19 (estados desconhecidos). A confirmação passa a ocorrer com o degrau na janela 16 (~1,6 s após o degrau; antes 1,0–1,4 s).
- `resultado` ganhou `posicao_degrau`, `faixa_posicao` e `fora_da_posicao` (contador).
- MQTT direto (v5): logs do ESP-IDF desligados na USB, cliente MQTT não é chamado sem conexão, telemetria pelo Wi-Fi a 1 Hz e Wi-Fi iniciado depois do ADC.
- Testes C++: `teste_posicao_degrau.cpp` (novo) e casos de posição em `teste_estados.cpp`.
- Pasta `ensaios/`: coletor e scripts de análise dos ensaios finais; resultados em `docs/ENSAIOS_20261107.md`.
- `config/` e testes restaurados do commit fce8f5c; tolerância a até 3 janelas com saturação do ADC (`SATURACAO_JANELAS_MAX`).

# Exportação automática das cinco classes — 27/10/2026

- Coletor salva uma cópia de cada captura em `edge_impulse/<classe>/`, preservando os originais das 12 condições.
- Rótulo derivado da condição experimental e incluído no nome para importação; todas as 106 features permanecem no CSV.
- Agrupamento automático de campanhas existentes ao retomar, sem duplicar amostras.
- Botão para abrir as cinco classes e opção `--somente-exportar`, sem USB ou servidor.
- 11 testes de exportação aprovados. Firmware, rede, MQTT e osciloscópio não foram alterados nesta revisão.

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
