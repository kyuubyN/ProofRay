# D28 — Indução denotacional com assinatura contrafactual

**Status:** contrato causal v0.1 congelado antes de qualquer medição D28.
**Natureza:** nova linha; não é reparo de D24–D27.
**Holdout oficial DROP:** permanece selado e não lido.

## 1. O gargalo que D24–D27 realmente localizaram

D24–D27 tentaram adquirir semântica apenas de recorrência, sobreposição ou forma textual. Nenhum deles
recebeu um sinal que distinguisse semanticamente `COUNT(eventos)` de `COUNT(literais)`, nem corrigisse
um operador lexicalmente ambíguo como `last` (final versus duração) ou `first` (mínimo versus escopo).

O resultado de treino é supervisão legítima para aprender um compilador. O gold não pode participar da
inferência, mas bani-lo também do treino removeu o único erro semântico disponível. O novo objeto de
aprendizagem é o programa inteiro:

```text
pergunta -> operador + predicado + papéis + escopo
parágrafo -> eventos tipados + identidades + spans
programa(eventos) -> denotação
```

## 2. Hipótese

Programas latentes podem ser aprendidos de pares `(pergunta, parágrafo, resposta)` se três filtros forem
aplicados em ordem:

1. **execução denotacional:** no treino, sobrevivem apenas programas cuja execução reproduz a resposta;
2. **interferência contrafactual:** programas que só acertaram por coincidência são separados por suas
   execuções em mundos onde os valores mudam, mas a estrutura e as identidades permanecem;
3. **conservação entre exemplos:** uma interpretação abstrata só vira supervisão quando reaparece em
   exemplos independentes e mantém operador, alinhamento de papéis e assinatura contrafactual.

No runtime não existe gold. Um compilador pequeno apenas propõe programas; o executor Horizon reabre os
spans, verifica tipos, identidade, escopo, unidades e recomputa o valor. Falha de fechamento é abstenção.

## 3. Por que a interferência agora é independente

A sonda anterior comparou rotas diferentes no mesmo pool de números. Números comuns produziam falsa
concordância. D28 conserva `FactId`/papéis e troca os valores por uma sequência determinística de mundos.

Exemplo: no mundo observado, `7 - 3` e `LOOKUP(4)` chegam a `4`. Em mundos contrafactuais, as assinaturas
divergem. A concordância deixa de significar “o número 4 é comum” e passa a significar “os programas têm
o mesmo comportamento sob intervenções”. Isto é um teste causal de programa, não uma fusão de scores.

O filtro não afirma sozinho qual classe é correta. A autoridade vem da conservação da mesma classe entre
exemplos independentes, com alinhamentos não sobrepostos e tipos compatíveis. Classes ainda concorrentes
permanecem resíduos; não são desempates por frequência.

## 4. Separação dos gargalos

D28 deve publicar quatro taxas separadas:

1. **candidate reachability:** algum programa enumerado reproduz o gold de treino;
2. **identifiability:** resta uma classe semântica depois das intervenções e da conservação entre exemplos;
3. **compiler transfer:** o compilador escolhe essa classe em uma família lexical não vista;
4. **verified end-to-end:** o programa escolhido fecha e produz a resposta correta sem gold.

Uma taxa não pode ser usada como nome da outra. Em particular, oracle reachability e identificabilidade
nunca são precisão end-to-end.

## 5. Split de generalização

O treino oficial do DROP já foi usado repetidamente e não pode provar generalização. D28 dentro desse
split é apenas diagnóstico de viabilidade.

Antes de qualquer avaliação:

- perguntas são abstraídas mecanicamente (números e entidades viram slots);
- uma família inteira vai para um único fold por SHA-256;
- passagem e pergunta normalizada também não podem atravessar folds;
- gold é materializado somente na visão de treino; a API da visão de avaliação não possui esse campo;
- decisões de arquitetura usam apenas folds de treino/calibração;
- a primeira medição em corpus externo novo exige freeze de código e digest.

O holdout V84 não será aberto: o charter original proíbe abri-lo porque os gates de entrada falharam.
Generalização promovível exigirá um corpus/partição nova, não reparos orientados pelo dev oficial.

## 6. Braços e ablações declarados

Mesmo espaço de candidatos e mesmo orçamento:

1. denotação apenas;
2. denotação + assinatura contrafactual;
3. denotação + conservação entre exemplos;
4. **D28 completo:** denotação + contrafactual + conservação + verificador;
5. ablação que remove identidade de operandos;
6. ablação que permite sobreposição de alinhamentos.

Se os braços 2–4 não reduzirem programas espúrios fora do exemplo usado para indução, a analogia de
interferência é decoração e deve ser removida da alegação.

## 7. Métricas e gates

Diagnóstico de viabilidade, antes de treinar qualquer modelo:

- `candidate_reachability >= 0.95` na região declarada;
- `identifiable_given_reachable >= 0.90`;
- zero classe promovida com span, unidade ou recomputação inválidos;
- distribuição completa de classes concorrentes, não apenas média.

Generalização seletiva:

- pelo menos 1.000 casos resolvidos em famílias lexicais isoladas;
- pelo menos três operadores com 100 casos cada;
- precisão seletiva `>= 0.995` na calibração e `>= 0.99` no holdout novo;
- cobertura publicada separadamente;
- risco end-to-end conta toda abstenção em positivo como erro;
- contexto máximo `<= 2.048` tokens reais.

O alvo de 99% só pode ser declarado end-to-end se `(corretos / todos os casos em escopo) >= 0.99` em
holdout novo. Precisão condicional a abstenção não satisfaz esse objetivo.

## 8. Uso futuro de modelos locais

Qwen e Granite não serão autoridades. Cada um poderá, um por vez e com aviso prévio ao usuário, propor
um pequeno beam de IRs. O mesmo executor, intervenções e verificador julgam ambos. A comparação mede:

- candidate reachability do beam;
- precisão e cobertura verificadas;
- tokens, latência e energia;
- taxa de programas espúrios antes/depois do filtro.

Nenhum modelo ou API é necessário para a auditoria de identificabilidade inicial.

## 9. Condições de abandono

- Reachability baixa: melhorar enumeração/eventos; não treinar seletor.
- Reachability alta e identificabilidade baixa: denotação é insuficiente; obter supervisão forte de
  programas ou novas intervenções, sem escolher um programa espúrio por score.
- Identificabilidade alta e transferência baixa: o gargalo é representação/aprendizagem do compilador.
- Precisão seletiva alta e cobertura baixa: não declarar 99% end-to-end.
- Qualquer inspeção do holdout V84 invalida esta linha.

## 10. Relação com o Horizon

- **buraco branco:** geração limitada de programas candidatos;
- **pontes/entanglement:** alinhamento conservado entre slots da pergunta e papéis do evento;
- **interferência:** equivalência comportamental em múltiplos mundos, com identidades independentes;
- **silêncio/expiração:** abstenção quando classes concorrentes não colapsam;
- **ego/competição:** programas competem pela habilidade, mas não recebem recompensa sem verificação;
- **flow:** busca para quando a prova fecha, não quando o score parece suficiente;
- **estrategista:** reconstrói a regra executável em vez de reconhecer uma superfície.

Essa síntese produz um protocolo testável. Ela não transforma metáforas em evidência e não promete um
número antes da medição.
