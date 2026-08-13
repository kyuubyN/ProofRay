# D28A — Auditoria da geometria do espaço candidato

**Status:** implementação/gates congelados antes da primeira medição.
**Natureza:** diagnóstico no treino saturado; nunca resultado de generalização.
**Modelo/API/holdout:** nenhum.

## Pergunta

Antes de treinar um seletor ou medir cargas D30:

1. algum programa enumerado consegue reproduzir a denotação de treino?
2. quantas classes estruturais/contrafactuais continuam possíveis?
3. qual distância mínima separa essas classes?

## Espaços comparados

- **untyped:** todos os literais numéricos do parágrafo formam uma fibra `*`;
- **typed-D26:** eventos usam os predicados de gauge recorrentes congelados em D26.

O primeiro mede teto com alta ambiguidade; o segundo mede o efeito da tipagem imperfeita. Nenhum escolhe
programa pela pergunta.

DSL fechada por fibra:

- `lookup` de um operando;
- `count` e `sum` da fibra completa;
- `argmax` e `argmin` da fibra completa;
- `difference` de cada par ordenado de identidades distintas.

Máximo de 40 literais e 4.096 programas por parágrafo. Exceder orçamento é falha de reachability, não
caso removido do denominador. São usados sete mundos contrafactuais determinísticos D28.

Gold participa somente depois da enumeração para identificar programas denotacionalmente consistentes.
Ele não altera a fibra, os operadores, o orçamento nem os mundos.

## Classes e distância

Uma classe é definida por:

```text
(operador, predicado, aridade, assinatura contrafactual)
```

Predicados diferentes não colapsam apenas porque coincidem numericamente. A distância é Hamming sobre
quatro grupos conservados: operador, predicado, aridade e assinatura contrafactual. Esta é a geometria
antes de adicionar cargas relacionais D29.

## Gates herdados de D28/D30

- reachability `>=0,95`;
- identificabilidade entre alcançáveis `>=0,90`;
- maioria dos codebooks ambíguos com distância mínima `>=3` antes de gastar modelo.

Falha de reachability exige novo gerador de eventos/programas. Falha de identificabilidade com boa
reachability justifica D29/D30. Distância menor que três demonstra que as cargas atuais não corrigem nem
um erro e que Qwen/Granite ainda seriam gasto prematuro.
