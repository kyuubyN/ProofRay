# D31 — Completação algébrica guiada pelo resíduo

**Status:** teoria/núcleo v0.1 congelados depois do diagnóstico D28A e antes de qualquer medição D31.
**Holdout/modelo/API:** nenhum.

## 1. Resultado que exige esta geração

D28A mediu a geometria antes de seleção:

| braço | reachability | identificável entre alcançáveis | distância mediana ambígua |
|---|---:|---:|---:|
| literais não tipados | 0,673962 | 0,303683 | 1 |
| predicados D26 | 0,452409 | 0,272128 | 1 |

Correção de erro D30 não cria programa ausente. D29 não pode transportar uma relação que não existe. O
primeiro termo do orçamento de 99%, `P(C)`, falha antes de qualquer modelo.

## 2. Inversão

Até aqui a DSL foi entrada. D31 transforma a DSL em saída verificada do treino:

```text
resíduo: nenhum programa conhecido produz o gold
  -> busca reversa/fatorada por circuitos tipados mínimos
  -> circuitos denotacionalmente consistentes
  -> classes separadas por mundos contrafactuais
  -> formas conservadas por constelações D29
  -> novo gerador algébrico promovido
```

O gold não escolhe um circuito para runtime. Ele define uma fronteira de treino. Um circuito só ganha
existência semântica se a mesma forma fechar resíduos independentes e mantiver covariância D29.

## 3. E-graph contrafactual

Enumerar árvores sintáticas explode e conta `a+b`, `b+a` e outras equivalências várias vezes. D31 usa
classes fatoradas por:

```text
(dimensão, conjunto de FactIds, assinatura em mundos contrafactuais)
```

Expressões com o mesmo comportamento causal e as mesmas fontes compartilham uma classe; guarda-se a
prova de menor custo. Identidades de fonte nunca podem ser duplicadas dentro de um circuito.

Base inicial finita:

- `add`, `subtract`, `multiply`, `exact_divide`, `maximum`, `minimum`;
- profundidade e número de classes limitados;
- soma/subtração/extremos exigem dimensão igual;
- multiplicação/divisão propagam dimensões;
- divisão por zero e conversão implícita são proibidas.

Essas operações são uma base de circuitos, não famílias linguísticas. Operadores promovidos para o atlas
são formas compostas recorrentes, como percentual, margem, duração ou conversão, descobertas no resíduo.

## 4. Relação com D29 e D30

- D31 aumenta candidate reachability;
- D28 separa circuitos que coincidem só no mundo observado;
- D29 exige que a forma do circuito transporte covariantemente entre perguntas/mundos;
- D30 mede distância entre as classes restantes e corrige observações ruidosas;
- o executor verifica FactIds, unidades, spans e recomputação.

Nenhuma camada substitui a outra.

## 5. Gates

Antes de modelos:

- reachability acumulada `>=0,9975` na região declarada;
- ao menos 90% dos novos alcances explicados por formas recorrentes em mundos independentes;
- zero circuito com FactId duplicado, unidade inválida ou divisão inexata promovido;
- distância mediana ambígua `>=3` depois de cargas D29;
- custo de runtime do atlas compilado, não custo da síntese de treino, cabe em 2K tokens.

## 6. Condições de abandono

- Reachability não sobe: faltam eventos/valores implícitos, não aritmética.
- Reachability sobe apenas por circuitos únicos de cada caso: síntese memorizou denotações.
- Formas recorrentes falham em famílias lexicais isoladas: fechamento é fitting.
- E-graph excede orçamento: reduzir busca por pressão de prova; nunca ocultar a explosão.
- Mesmo com reachability, distância continua 1: faltam observações semânticas independentes.

## 7. Leitura Q-HDRE

O resíduo é radiação: carrega a informação mínima do que a álgebra atual não conserva. O buraco branco
não responde à pergunta; ele emite candidatos novos. A interferência funde circuitos causalmente
equivalentes, a repulsão separa dimensões incompatíveis, D29 fornece música entre mundos, e D30 localiza
a interpretação no atlas. A DSL evolui somente por recompensa verificada e recorrente.
