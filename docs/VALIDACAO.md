# Verificação desta revisão — 23/10/2026

Esta revisão está preparada em uma cópia de trabalho. Não foi instalada na pasta Desktop/Engenharia/TCC/github nem gravada no ESP32.

- **41 testes Python: 39 aprovados e 2 ignorados.** Executados com Python 3.8.1, o interpretador disponível para esta sessão. Os dois testes de integração aMQTT/Paho foram ignorados por ausência das dependências. Isso não equivale a validar toda a aplicação no Python 3.12 recomendado.
- **Mosquitto real:** conexão MQTT aceita e uma mensagem publicada e recebida no mesmo computador, usando um tópico temporário sem retenção. A configuração do serviço não foi alterada. A ponte Paho completa não pôde ser executada nesta sessão.
- **Reutilização do broker:** teste de unidade confirmou que um broker detectado não provoca inicialização de outro broker nem é encerrado ao desconectar o publicador. O cliente Paho foi substituído por um objeto controlado nesse teste.
- **Edge Impulse:** exportação Arduino real de cinco classes inspecionada e importada em uma pasta temporária. O modelo foi identificado pelo hash como V1 de 12/10; entradas 350, seleção 14 × 25, normalização no modelo. Os contratos do coletor e do servidor coincidiram após importar. Arquivos com classes incompatíveis não devem ser ativados.
- **Firmware:** novo adaptador preparado, mas não compilado nem gravado. Falta validar compatibilidade do SDK com core ESP32 3.3.10, consumo de memória e equivalência da inferência no hardware.
- **Modelo principal:** permanece a V1 P10 em C++ até importar sua exportação correta. A biblioteca antiga não foi ativada no pacote entregue.
- **Interface e rede externa:** não houve novo ensaio de janela Tk, Chrome, celular, Firewall, USB, botão ou LED.

As validações anteriores foram conservadas nos arquivos VALIDACAO_ANTERIOR_20261023.* e se referem à revisão anterior. O resultado físico 24/24 de 15/10 pertence à versão então utilizada.

O acesso ao controle de aplicativos, ao Edge Impulse no navegador e ao download de dependências foi bloqueado nesta sessão. Nenhum commit/push foi feito.

Verificacao posterior: Arduino CLI instalado localizado, mas sua execucao foi negada. COM3, COM6 e COM7 constam como modems Bluetooth; COM1 e a serial do computador. Nenhuma porta USB do ESP32 foi identificada.
