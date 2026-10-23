# Usar o ZIP do Edge Impulse como biblioteca da Arduino IDE

Esta atualizacao complementa o pacote integracao_mqtt_edge_impulse_20261023.
Ela inclui a correcao dos estados desconhecidos. Nao inclui uma rede nova.

## 1. Atualizar o projeto uma vez

Feche aquisicao.py e servidor.py. Guarde uma copia do projeto atual.
Extraia este pacote na raiz do projeto, preservando as subpastas. Os arquivos
de codigo que substituem versoes anteriores sao:

- firmware/tcc_monitor/estados.h
- firmware/tcc_monitor/modelo_inferencia.h
- python/importar_edge_impulse.py

Os demais arquivos sao documentacao, testes e um perfil de exemplo. Este pacote
nao substitui tcc_monitor.ino, pesos existentes nem config/modelo.json.

## 2. Exportar a rede

No projeto e impulse corretos do Edge Impulse, abra Deployment, escolha Arduino
library, exporte float32 e gere o ZIP com o modelo TensorFlow Lite convencional,
sem EON. Esta versao do importador nao suporta int8 nem EON.

Use a exportacao do modelo que voce quer executar. A exportacao antiga
arduino-1.0.2-impulse-#2 encontrada anteriormente era V1 de 12/10, nao a P10.
O pacote nao baixa nem escolhe automaticamente uma rede no Studio.

## 3. Instalar o ZIP na IDE

Na Arduino IDE: Sketch > Include Library > Add .ZIP Library...
(Sketch > Incluir Biblioteca > Adicionar biblioteca .ZIP...). Escolha o ZIP
exportado. Nao descompacte ou altere os fontes dessa biblioteca.

Ao trocar a rede, instale a nova exportacao. Se houver varias bibliotecas com
o mesmo header *_inferencing.h, confira o caminho usado no log de compilacao
e retire a versao antiga da pasta libraries, guardando-a fora dessa pasta.
O script abaixo nao gerencia a instalacao global da Arduino IDE.

## 4. Preparar o firmware para o mesmo ZIP

Salve uma copia do ZIP como firmware/modelo_edge_impulse.zip. Na raiz do projeto,
por exemplo C:\TCC, abra PowerShell e execute:

```powershell
.\.venv\Scripts\python.exe python\importar_edge_impulse.py --biblioteca-arduino --verificar
.\.venv\Scripts\python.exe python\importar_edge_impulse.py --biblioteca-arduino
```

Tambem e possivel usar o caminho original:

```powershell
.\.venv\Scripts\python.exe python\importar_edge_impulse.py "C:\Users\iigel\Downloads\minha-rede.zip" --biblioteca-arduino
```

O script usa apenas a biblioteca padrao do Python. Se nao usar .venv, substitua
.\.venv\Scripts\python.exe pelo comando do seu Python, por exemplo py -3.12.

Ele verifica as cinco classes, dimensoes, hash do modelo e contrato das features.
Gera modelo_edge_impulse_config.h com o include publico REAL encontrado no ZIP,
sem exigir que voce escreva o nome. Atualiza config/modelo.json para o Python.
Se houver SDK em firmware/tcc_monitor/src/edge_impulse, move-o para backups_modelo
para evitar compilar a mesma biblioteca duas vezes. Conserva backups da
configuracao anterior. Nao altera o ZIP nem copia o SDK para o sketch nesse modo.

O firmware confere projeto, versao de deploy e dimensoes na compilacao. Esses
metadados nao constituem uma verificacao criptografica da biblioteca instalada:
instale exatamente o mesmo ZIP que foi usado no comando e nao edite seus pesos.

Se falhar com Modelo novo, o tamanho de entrada sozinho nao permite determinar
a ordem e a normalizacao das features. Copie config/perfil_edge_impulse_exemplo.json
para um novo perfil, preencha o hash exibido e confira os indices e pre-processamento
com o treinamento. Entao use:

```powershell
.\.venv\Scripts\python.exe python\importar_edge_impulse.py --biblioteca-arduino --perfil config\meu_perfil.json
```

O perfil de exemplo usa 14 features por janela. Nao assuma que ele serve para
qualquer rede. O sistema aceita 25 janelas de 200 ms com 14 ou 106 features por
janela; a normalizacao deve estar no SDK/modelo exportado. Os nomes das classes
devem corresponder ao projeto atual: sem_evento_alvo, liquidificador_ligando,
liquidificador_desligando, microondas_ligando e microondas_desligando.

## 5. Compilar e gravar

Abra firmware/tcc_monitor/tcc_monitor.ino, selecione ESP32 Dev Module e a porta
USB correta. Mantenha inicialmente a versao de core usada no firmware funcional;
o projeto foi preparado para 3.3.10. Compile e envie. Nao substitua a versao do
core por 2.x automaticamente: o pipeline ADC/DMA deste firmware depende das
APIs do core atual. Se o SDK exportado apresentar incompatibilidade, guarde o
primeiro erro completo para ajustar essa combinacao.

Reabra servidor.py e aquisicao.py. Se mudou o modelo, use uma nova pasta de
campanha de capturas. Teste ligar/desligar cada aparelho, confira o evento e o
estado correspondente. Sem evento mantem o historico e nao identifica estados
absolutos. Veja CORRECAO_ESTADOS.md para a correcao incluida.

Cada nova exportacao exige repetir instalacao do ZIP, preparacao, compilacao e
gravacao. Apenas baixar outro ZIP nao troca o modelo que esta no ESP32.

## Validacao desta atualizacao

- Cinco testes estruturais do importador passaram, com ZIP sintetico: preparacao
  externa, backup do SDK anterior, compatibilidade do modo local, rejeicao de
  biblioteca sem header e restauracao apos falha de escrita.
- Um desses testes compila e executa o adaptador C++ com uma interface simulada
  do SDK, validando selecao das features, limites do callback, ordem das classes
  e rejeicao de deploy divergente. Nao executa TensorFlow nem mede acuracia.
- Oito testes existentes de integracao passaram.
- O teste C++ dos estados passou.
- Ainda nao houve compilacao ESP32 com sua biblioteca real nem gravacao fisica.

Para executar os testes especificos:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s testes_sistema -p test_biblioteca_arduino.py -v
```

O teste C++ interno sera ignorado se g++ nao estiver disponivel no PATH.

Referencias oficiais:
- https://docs.edgeimpulse.com/hardware/deployments/run-arduino-2-0
- https://support.arduino.cc/hc/en-us/articles/5145457742236-Install-libraries-in-the-Arduino-IDE
