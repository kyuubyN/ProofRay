# D29 — Tomografia semântica por constelações de perguntas

**Status:** hipótese e contrato de sonda v0.1 congelados antes da primeira medição.
**Holdout DROP:** selado, não lido.
**Dependência:** D28 fornece programas latentes e assinaturas contrafactuais; D29 fornece restrições
relacionais entre exemplos.

## 1. A inversão

As linhas anteriores processaram uma pergunta de cada vez:

```text
parágrafo -> eventos -> casar uma pergunta
```

Mas um parágrafo do DROP carrega várias perguntas e respostas. Elas são projeções diferentes do mesmo
mundo. Não existe obrigação de que a estrutura correta esteja observável em uma projeção isolada; ela
pode ser reconstruída pela transformação entre projeções.

```text
um mundo + constelação de perguntas/respostas
    -> diferenças de superfície
    -> diferenças de denotação
    -> diferenças entre programas latentes
    -> geradores semânticos conservados
    -> atlas composicional
```

O schema deixa de ser uma lista de predicados. Ele passa a ser uma álgebra pequena de transformações.
Uma combinação nova pode ser compilada compondo geradores já verificados, sem memorizar a pergunta.

## 2. Hipótese

Se duas perguntas sobre o mesmo parágrafo diferem por uma alteração pequena, o mundo foi mantido fixo.
A diferença funciona como intervenção natural sobre a consulta. Em vários parágrafos independentes, a
mesma diferença de superfície deve induzir a mesma diferença abstrata de programa:

```text
Δ(pergunta)  --F-->  Δ(programa)
```

`F` é aceito somente quando a composição é covariante: caminhos diferentes entre perguntas produzem a
mesma transformação final. Um defeito de holonomia indica polissemia, ironia, escopo oculto ou análise
errada; o sistema não força a união.

Exemplos de geradores possíveis, não regras pré-declaradas:

- `first -> second` conserva operador/predicado e transporta o escopo;
- `how many -> total` pode transportar contagem para soma;
- entidade A -> entidade B conserva o programa e troca um papel;
- `made -> attempted` transporta polaridade/estado de conclusão, não apenas o verbo.

Esses exemplos ilustram cargas. Nenhum deles é aceito sem recorrência e fechamento medidos.

## 3. O “mapa de tudo” computável

Kolmogorov/Solomonoff universal continua incomputável. D29 não precisa dele. O corpus, a DSL, o número de
perguntas por mundo, a profundidade de composição e o orçamento são finitos. Portanto, a atribuição
global de programas é um problema finito de satisfação de restrições:

- variável: um programa candidato D28 para cada pergunta;
- fator local: execução do programa reproduz o gold somente no treino;
- fator causal: assinatura contrafactual separa coincidências;
- fator relacional: contrastes recorrentes conservam a mesma transformação de programa;
- fator de ciclo: composição ao redor de um ciclo deve voltar à identidade;
- fator de prova: spans, tipos, unidades, FactIds e completude precisam fechar.

Não se escolhe a atribuição de maior score. Propaga-se restrição até restar uma classe global; empate vira
resíduo. O problema pode ser grande, mas não é logicamente impossível e se decompõe por constelação e por
componentes de transformação.

## 4. Sonda de viabilidade congelada

A primeira sonda não gera programas e não usa modelo. Ela pergunta se existem intervenções naturais
suficientes no treino oficial já consumido.

Para cada parágrafo:

1. considerar somente perguntas com resposta numérica;
2. abstrair números e entidades mecanicamente;
3. calcular distância de edição em tokens entre todas as perguntas do parágrafo;
4. manter somente pares de vizinhos mais próximos **únicos e mútuos**, sem threshold ajustável;
5. extrair a delta de superfície por alinhamento determinístico;
6. declarar uma delta recorrente somente se aparecer em pelo menos três parágrafos distintos;
7. usar gold apenas depois do pareamento para relatar quantos contrastes mudam a denotação.

O pareamento não lê resposta, operador ou rótulo. O gold não decide qual aresta existe.

## 5. Predição e gate da sonda

Há suporte estrutural para D29 se:

- pelo menos 500 arestas pertencerem a deltas recorrentes;
- pelo menos 5% das perguntas numéricas participarem dessas arestas;
- deltas recorrentes aparecerem em pelo menos 50 parágrafos;
- existirem tanto contrastes que conservam quanto contrastes que alteram a denotação.

Falhar o gate não refuta indução denotacional D28; refuta o DROP como fonte suficiente de constelações
naturais para aprender o atlas D29.

## 6. Experimento completo futuro

Se a sonda passar:

1. enumerar programas D28 por pergunta;
2. construir o grafo de constelação sem gold;
3. no treino, filtrar candidatos por denotação;
4. induzir `Δpergunta -> Δprograma` somente quando recorrente em mundos independentes;
5. impor consistência de ciclos e assinatura contrafactual;
6. remover gold e compilar famílias abstratas não vistas;
7. verificar execução e publicar precisão, cobertura e resíduo.

Controles:

- perguntas isoladas, sem arestas;
- arestas embaralhadas entre parágrafos;
- arestas sem consistência de ciclo;
- denotação sem contrafactuais;
- D29 completo.

## 7. Condições de abandono

- Sonda sem massa recorrente: não construir solver global neste corpus.
- Arestas existem, mas não reduzem classes espúrias D28: constelação é decoração.
- Reduz classes no treino e falha em famílias isoladas: atlas memorizou superfícies.
- Ciclos apresentam defeito sistemático: manter conexões locais por escopo; nunca fundir globalmente.
- Precisão seletiva alta com cobertura baixa continua não sendo 99% end-to-end.

## 8. Leitura Q-HDRE

- cada pergunta é uma projeção tomográfica;
- o parágrafo é o corpo compartilhado;
- a sequência de projeções é a música, não as notas isoladas;
- diferenças recorrentes são pontes entre referenciais;
- consistência de ciclo é curvatura/holonomia;
- programas latentes são matéria escura inferida por seus efeitos;
- contradições repelem fusões incorretas;
- o atlas é metavisão: posição semântica inferida pelas relações com as outras posições.

Essa analogia gera invariantes, controles e condições de falha. Só por isso ela entra no laboratório.
