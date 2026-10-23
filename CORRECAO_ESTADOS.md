# Correcao dos estados desconhecidos — 23/10/2026

O firmware anterior rejeitava eventos confirmados quando o estado previo do
aparelho era desconhecido (`estado_inicial_indefinido`). Isso exigia informar
os estados manualmente antes de acompanhar as transicoes.

Agora um evento confirmado de ligar define o respectivo aparelho como ligado;
um evento confirmado de desligar o define como desligado, mesmo sem estado
anterior. O estado do outro aparelho permanece intacto. Os filtros de confianca,
margem, confirmacoes consecutivas, repeticoes e bloqueio entre eventos continuam.
Sem evento alvo nao determina o estado absoluto: mantem o historico existente.
Reinicio ou falha de aquisicao ainda podem retornar os estados a desconhecido.

## Aplicar

1. Feche aquisicao.py e o Monitor Serial para liberar a porta USB.
2. Guarde uma copia do seu firmware atual fora da pasta do sketch.
3. Substitua somente firmware/tcc_monitor/estados.h pelo arquivo deste ZIP.
   Mantenha a rede importada, os demais arquivos e as configuracoes locais.
4. Abra firmware/tcc_monitor/tcc_monitor.ino na Arduino IDE. Selecione a placa
   ESP32 Dev Module e a porta correta; compile e envie para a placa.
5. Reabra aquisicao.py. O servidor pode continuar em execucao.

No modo teste, aguarde o aquecimento de 5 segundos. Ao reconhecer e confirmar
uma transicao, o estado do aparelho correspondente deve mudar. Aguarde retorno
a sem evento antes da proxima transicao. O segundo aparelho so deixa de ser
desconhecido depois de um evento proprio ou de uma informacao manual.

Alternativa sem atualizar o firmware: na janela de aquisicao do computador,
use Informar estados iniciais e indique os estados reais dos aparelhos.

## Validacao

O teste testes_sistema/teste_estados.cpp foi compilado com g++ C++11 e passou.
Cobre as quatro classes de transicao a partir de desconhecido, preservacao do
outro aparelho, sem evento, baixa confianca/margem, dados invalidos, repeticoes,
bloqueio ate sem evento, reset, estado manual e modo guiado.

Comando a partir da raiz, se houver g++ instalado:

    g++ -std=c++11 -Wall -Wextra -pedantic testes_sistema/teste_estados.cpp -o teste_estados
    ./teste_estados

O firmware completo nao foi compilado para ESP32 nem gravado nesta sessao.
A suite Python preexistente foi executada neste ambiente Linux: 38 passaram,
2 foram ignorados por dependencias ausentes e 1 teste de rede LAN encontrou
Network is unreachable. Nenhum arquivo Python foi alterado por esta correcao.

## Git

Apos substituir o arquivo, na raiz do seu repositorio:

    git add firmware/tcc_monitor/estados.h
    git commit -m "Permite inicializar estados por eventos confirmados"
    git push

Se desejar versionar tambem a documentacao e o teste, copie-os para a raiz e
testes_sistema, respectivamente, e adicione-os ao commit.
