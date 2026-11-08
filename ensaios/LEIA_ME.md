# Ensaios finais (07/11/2026)

Ferramentas usadas nos ensaios do capítulo 6. Nenhuma delas altera a placa ou a rede.

| Arquivo | Função |
|---|---|
| `coletor_ensaios.py` | Lê `http://127.0.0.1:8765/api/estado` (cerca de 12 vezes por segundo) e grava resultados, estados e marcas em `registros/registro_*.jsonl`. Também tira prints da tela quando aparece um arquivo `pedidos/*.print`. |
| `analise/a_guiado.py` | Ensaios guiados: matrizes 5×5 e 12×5, precisão, revocação, F1 e IC 95 %. |
| `analise/b_continuo.py` | Ensaio contínuo: eventos corretos, perdidos e falsos, tempo de resposta e linhas do tempo. |
| `analise/c_sistema.py` | Desempenho embarcado (inferência, atraso, memória, janelas perdidas) e tabela do filtro de posição. |
| `analise/sim_treino.py`, `analise/harness.cpp` | Simulação do modo contínuo nas 600 capturas de treino, com o degrau deslizando pelo contexto. O `harness.cpp` compila `estados.h` e `posicao_degrau.h` do firmware. |
| `analise/base.py`, `comum.py`, `estilo.py` | Leitura dos dados, rede em NumPy (pesos de `rede_eventos5.h`) e estilo das figuras. |

## Como foi feito

1. Servidor e coletor, cada um no seu terminal:

   ```powershell
   python python\servidor.py --porta-mqtt 1884 --sem-navegador
   python ensaios\coletor_ensaios.py
   ```

   A porta 1884 foi usada porque o PC dos ensaios já tinha um Mosquitto em 127.0.0.1:1883. Com a porta livre, use o padrão (1883). O `segredos.h` da placa deve apontar para a mesma porta.

2. Ensaio guiado, uma pasta por potência:

   ```powershell
   python python\aquisicao.py --dados "C:\TCC\ensaios\guiado_P10" --repeticoes 3 --potencia P10 --velocidade 1 --fundo "cargas usuais; micro-ondas vazio"
   ```

3. Ensaio contínuo: modo **Teste contínuo**, oito ações por rodada, cerca de 10 s entre elas. A sequência é: liga liquidificador, liga micro-ondas, desliga liquidificador, liga liquidificador, desliga micro-ondas, desliga liquidificador, liga micro-ondas, desliga micro-ondas.

4. Análise, na pasta dos ensaios:

   ```powershell
   $env:TCC_ENSAIOS_DADOS = "C:\TCC\ensaios"     # guiado_P10\, guiado_P3\, registros\
   $env:TCC_BASE600 = "C:\TCC\capturas_liquidificador_microondas"
   python ensaios\analise\a_guiado.py
   python ensaios\analise\b_continuo.py
   python ensaios\analise\c_sistema.py
   ```

   As figuras (PNG e PDF) e as tabelas (`.tex` e `.csv`) saem em `resultados\`. Os arquivos intermediários ficam em `analise_trabalho\` (`TCC_ANALISE_TRAB`).

Critérios do ensaio contínuo: um evento é correto quando é confirmado com o aparelho e o sentido certos em até 5 s após o degrau de corrente. Um evento confirmado sem degrau correspondente, ou de outro aparelho, é falso.
