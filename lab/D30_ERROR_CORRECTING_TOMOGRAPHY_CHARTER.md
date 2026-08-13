# D30 — Tomografia semântica com correção de erros

**Status:** contrato causal v0.1 congelado antes de qualquer medição/modelo D30.
**Dependências:** D28 fornece programas e assinaturas; D29 fornece o atlas de transformações.
**Holdout:** nenhum holdout existente será aberto nesta geração.

## 1. O componente que falta

D28 cria hipóteses executáveis e D29 aprende coordenadas relacionais. Isso ainda não localiza com
segurança uma pergunta nova no atlas. Uma única decisão do modelo pequeno pode escolher um programa
semanticamente errado que, mesmo assim, executa e tem spans válidos. O verificador prova que o programa
foi executado corretamente; ele não prova sozinho que o programa expressa a intenção da pergunta.

O componente faltante é **redundância semântica com correção de erros**.

Cada programa vira uma palavra-código cujos símbolos são cargas independentes:

```text
(operador, predicado, papéis, escopo, tempo, polaridade, unidade,
 assinatura contrafactual, deltas D29, requisitos de completude)
```

Um modelo local não escreve a resposta e não escolhe livremente um programa. Ele mede uma carga finita
por vez. O decodificador aceita somente uma palavra-código dentro do raio corretivo declarado. Depois,
o executor Horizon reabre e verifica a prova.

## 2. Por que isso pode ultrapassar a precisão do modelo

Se a distância mínima entre palavras-código é `d`, medições com até

```text
t = floor((d - 1) / 2)
```

grupos independentes errados podem ser corrigidas com unicidade. O ganho não vem de perguntar a mesma
coisa repetidamente. Toda medição possui um `evidence_group`; erros dentro do mesmo grupo contam uma vez,
e repetir o mesmo sensor não aumenta distância nem confiança.

Diversidade válida pode vir de:

- leitura direta da pergunta;
- transformação contrafactual D28;
- caminho relacional D29;
- papel/evento testemunhado no parágrafo;
- segundo modelo, executado posteriormente e descarregado entre execuções.

Essas fontes só são independentes depois de auditoria de correlação. Parafrasear o mesmo prompt para o
mesmo modelo permanece um único grupo até prova contrária.

## 3. Metavisão ativa

O sistema mede primeiro a carga que mais separa as hipóteses sobreviventes. Depois de cada observação:

1. elimina palavras incompatíveis apenas dentro do orçamento de erros;
2. recalcula distância e raio corretivo;
3. para quando existe uma palavra única decodificável;
4. expira em silêncio quando falta distância ou sobra simetria.

Isto combina o `AdaptiveSyndromeDecoder` existente com D28/D29, mas substitui sua fragilidade a um único
erro por decodificação limitada e comprovável.

## 4. Condição matemática para 99% end-to-end

Quatro eventos de falha cobrem o pipeline em escopo:

- `C`: programa/prova corretos ausentes do espaço candidato;
- `S`: seleção semântica errada ou não resolvida;
- `P`: prova/executor não fecha;
- `I`: integridade, serialização ou renderização falha.

Pela desigualdade da união:

```text
P(erro end-to-end) <= P(C) + P(S) + P(P) + P(I)
```

O gate inicial reserva `0,0025` para cada camada. Logo, cada uma deve demonstrar taxa de falha no máximo
`0,25%`, e a soma dos limites superiores de confiança deve ser `<=1%`. Isso inclui abstenções em casos
positivos; elas não desaparecem dentro de precisão seletiva.

Não basta uma estimativa pontual. O relatório deve publicar limite superior unilateral de 95% para cada
falha e para o end-to-end. Ruído/ambiguidade de anotação é medido como parte do limite do benchmark, não
apagado como erro do sistema.

## 5. Gates antes dos modelos

1. O decodificador corrige exaustivamente todo padrão de até `t` grupos errados em codebooks sintéticos.
2. Medições correlacionadas no mesmo grupo nunca aumentam distância.
3. Distância insuficiente, empate e observação fora do alfabeto terminam abertos/conflito.
4. D28+D29 produzem codebooks cuja distribuição de distância é publicada.
5. Candidate reachability é medida antes de qualquer seleção.

Se a maioria dos codebooks reais tiver `d<3`, um erro não é corrigível: faltam cargas independentes e
não há justificativa para gastar Qwen ou Granite.

## 6. Protocolo futuro dos modelos

Somente depois dos gates estruturais:

1. avisar o usuário e carregar Qwen;
2. medir cargas, latência, tokens, correlação e erro por grupo;
3. descarregar Qwen;
4. congelar o resultado;
5. avisar e repetir com Granite;
6. descarregar Granite;
7. testar cascata sequencial apenas no resíduo, sem manter dois modelos carregados.

Nenhuma API é necessária para D30. Se algum uso futuro for proposto, o usuário deve ser avisado antes.

## 7. Condições de abandono

- Candidate miss acima de `0,25%`: ampliar representação/eventos; correção de erro não cria candidatos.
- Distância baixa: criar cargas causalmente novas; repetição/paráfrase não vale.
- Erros altamente correlacionados: agrupar a fonte; não alegar ganho de ensemble.
- Decoder resolve seletivamente mas abstém acima do orçamento: não declarar 99% end-to-end.
- Limite de confiança total acima de 1%: objetivo não demonstrado, mesmo se o ponto estimado for 99%.

## 8. Síntese Q-HDRE

- D29 é o mapa/metavisão;
- D28 são mundos contrafactuais e interferência;
- D30 é localização por triangulação e código de correção;
- `evidence_group` conserva FactId e impede energia duplicada;
- distância é a repulsão mínima entre interpretações;
- raio corretivo é o medo operacional: fora dele, o sistema não arrisca;
- prova verificada é a recompensa;
- parada adaptativa é flow;
- resíduo chama um segundo especialista apenas quando necessário.

O alvo de 99% deixa de ser esperança em um modelo e vira um orçamento de falhas demonstrável por camada.
