# Cinco classes prontas para importação

O programa `python/aquisicao.py` organiza os dados automaticamente, sem outro script e sem acessar a conta do Edge Impulse.

## Pasta gerada

Dentro da pasta da campanha:

```text
csv/                         Originais separados nas 12 condições
edge_impulse/
  sem_evento_alvo/
  liquidificador_ligando/
  liquidificador_desligando/
  microondas_ligando/
  microondas_desligando/
```

Cada captura salva gera uma cópia na classe correspondente. O arquivo se chama, por exemplo, `microondas_desligando.8e0735e14e4244a89814621e8b7d2b09.csv`. A parte anterior ao primeiro ponto é o rótulo; o identificador mantém as amostras distintas, mesmo entre campanhas.

Os arquivos mantêm exatamente os bytes do CSV original: **timestamp e 106 features, em 25 janelas de 200 ms**. Não há seleção de 14 canais, normalização ou alteração dos valores nessa exportação. A rede atualmente embarcada permanece a mesma; treinar uma nova rede é outra etapa.

## Agrupamento

| Condições de aquisição | Rótulo de treinamento |
|---|---|
| 1, 2, 3, 4 — regimes permanentes | `sem_evento_alvo` |
| 5, 9 — liquidificador ligando | `liquidificador_ligando` |
| 6, 10 — liquidificador desligando | `liquidificador_desligando` |
| 7, 11 — micro-ondas ligando | `microondas_ligando` |
| 8, 12 — micro-ondas desligando | `microondas_desligando` |

Com 50 capturas por condição, haverá 200 amostras de `sem_evento_alvo` e 100 de cada evento: 600 no total. São cópias de 600 capturas, não mais 600 medições. A resposta da rede não determina o rótulo; quem determina é a condição selecionada pelo operador.

## Importar no Studio

1. No coletor, clique em **Abrir 5 classes para Edge Impulse**.
2. No Edge Impulse Studio, abra **Data acquisition → Upload data**.
3. Selecione os CSVs das cinco pastas e use **Infer from filename** para os rótulos. Se o assistente CSV perguntar, não existe uma coluna de rótulo; `timestamp` é o tempo em milissegundos e as demais colunas são os 106 canais.
4. Importe somente os arquivos de `edge_impulse/`. Enviar também `csv/` duplicaria as mesmas capturas.

O uso do prefixo do arquivo como rótulo é documentado pelo [Edge Impulse](https://www.edgeimpulse.com/blog/import-your-data-as-csv/). Esta atualização não envia arquivos nem inicia treinamento automaticamente.

A exportação não decide quais aquisições serão treino ou teste. Mantenha todas as janelas de uma mesma captura no mesmo conjunto e, para uma avaliação independente, separe as sessões de aquisição entre treino e teste.

## Campanhas já coletadas

Ao abrir uma campanha com este coletor, as cópias das capturas já registradas são criadas automaticamente. A contagem não aumenta, os originais e seus rótulos das 12 condições permanecem intactos.

Para agrupar sem abrir janela, USB ou servidor:

```powershell
python python/aquisicao.py --dados "C:\TCC\minhas_capturas" --somente-exportar
```

Use a pasta que contém `campanha.json`. A retomada exige uma campanha compatível com o modelo identificado pelo coletor, conforme a verificação já existente no programa.

Só capturas confirmadas no manifesto são exportadas. Se faltar espaço ou ocorrer falha na cópia, a aquisição original continua salva e a tela avisa que a exportação está pendente. Abrir novamente a campanha ou usar o botão da pasta tenta completar as cópias. Uma cópia derivada ausente ou alterada é reconstruída a partir do original íntegro; os originais não são reescritos.
