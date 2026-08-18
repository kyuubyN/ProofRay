# DRAFT — LICENÇA HORIZON OpenRAIL-R (não adotada)

**Status: rascunho para revisão. Não é um documento jurídico definitivo. Não
foi revisado por advogado.** Este arquivo passou por duas rodadas de reação
do autor: a primeira apontou que a versão anterior presumia distribuição
pública já feita (não presumir mais — ver "Contexto" abaixo); a segunda
apontou que uma versão mais extensa, com citações legais detalhadas, tinha
mecanismos mais duros do que o objetivo declarado ("não quero ser
caracterizado como um Oppenheimer, mas também não quero ser hostil com as
pessoas"). Esta versão corta especificamente o que era desproporcional a
esse objetivo — ver "O que foi cortado, e por quê" ao final.

**Alerta de precisão, sério**: várias citações da versão anterior vinham de
blogs de escritório de advocacia (não fonte primária) e uma delas citava o
PL nº 2.338/2023 como se já fosse lei — não é, é projeto em tramitação.
Ambos os problemas foram removidos aqui. Ainda assim, **nenhuma citação
legal neste documento deve ser tratada como verificada até revisão por
advogado**; cite a lei pelo nome (LGPD, GDPR, Marco Civil, Lei do Software),
não pelo número exato de artigo, até essa revisão acontecer.

## Contexto

O repositório é privado, e o autor único é o único titular dos direitos
autorais. Nenhum terceiro recebeu cópia do código até a presente data — não
há concessão anterior que impeça a adoção direta desta licença. No momento
em que isso deixar de ser verdade (colaborador com acesso, repositório
público, qualquer terceiro recebendo cópia), esta premissa precisa ser
reavaliada.

## Por que existe

O objetivo original era que o Horizon fosse infraestrutura livre no espírito
do Linux: o núcleo aberto, e qualquer um constrói em cima — como Ubuntu,
Fedora, Red Hat e tantas outras distribuições e empresas fazem sobre o
kernel GPL, sem pedir permissão e sem que isso enfraqueça o núcleo livre.
Essa continua sendo a forma. O que mudou não foi a vontade de manter isso
aberto — foi perceber que o mecanismo por trás (busca precisa com prova de
proveniência, superando um leitor de LLM com um orçamento pequeno de bytes)
tem peso de uso dual real o suficiente pra merecer uma lista nomeada de usos
que não são aceitos, do mesmo jeito que o BigScience/Hugging Face fez com o
OpenRAIL. Isso não é hostilidade contra quem usa — é uma declaração pública
de "isso aqui, especificamente, eu não permito", exatamente pra não ser
confundido com quem permitiu.

## O que esta licença preserva

1. **Disponibilidade real do código-fonte**, no espírito da AGPL já
   escolhida: quem recebe o software, inclusive como serviço de rede
   modificado, recebe o código-fonte correspondente. Isso não muda.
2. **A restrição é uma lista nomeada de usos, não uma lista fechada de
   finalidade permitida.** Restringir a "só o propósito original" bloquearia
   uso legítimo nunca previsto — inclusive as próprias distribuições/empresas
   que a analogia do Linux pressupõe. O que se define e se aplica é uma lista
   de usos específicos, reconhecíveis, de má-fé ou alto dano — a mesma forma
   do OpenRAIL.

## Usos expressamente permitidos

1. **Uso pessoal e educacional** — aprendizado, ensino, projetos pessoais,
   pesquisa e estudo acadêmico.
2. **Projetos comunitários e da sociedade civil** — ONGs, cooperativas,
   tecnologia cívica e iniciativas de interesse público, inclusive por
   voluntários e organizações pequenas sem assessoria jurídica dedicada.
3. **Preservação cultural e patrimonial** — arquivos, bibliotecas, museus e
   instituições semelhantes organizando, recuperando ou preservando material
   cultural ou histórico com rastreabilidade de proveniência.
4. **Acessibilidade** — adaptar ou integrar pra tornar informação ou serviços
   acessíveis a pessoas com deficiência.
5. **Uso comercial e redistribuição**, incluindo construir produtos, serviços
   ou distribuições próprias sobre o núcleo — a analogia Ubuntu/Fedora acima
   é intencional, não retórica.

Estas categorias não sobrepõem os usos proibidos abaixo — um uso de
vigilância não vira lícito por ser rotulado "educacional" ou "comercial".

## Usos proibidos

Você não pode usar o Horizon Memory, ou versão modificada, para:

1. **Vigilância em massa ou clandestina** de indivíduos ou grupos sem base
   legal, supervisão independente e, quando exigido por lei, consentimento
   informado.
2. **Perfilamento ou rastreamento não consentido** de pessoa identificável,
   incluindo inferir características protegidas (saúde, biometria, convicção
   política ou religiosa, orientação sexual, histórico criminal) sem base
   legal.
3. **Armas autônomas ou sistemas de direcionamento militar** — qualquer uso
   no projeto, lógica de direcionamento ou operação de sistema destinado a
   selecionar ou engajar alvos para dano físico sem controle humano
   significativo no momento da decisão.
4. **Decisões automatizadas de alto impacto sem revisão humana** — em
   aplicação da lei, imigração, crédito, emprego, elegibilidade a benefícios,
   ou diagnóstico/tratamento médico, sem revisão humana qualificada daquela
   decisão específica.
5. **Burlar direitos do titular de dados** (acesso, correção, eliminação) por
   projeto — construir o sistema deliberadamente para tornar um pedido
   legítimo de eliminação ou acesso impossível ou impraticável.
6. **Desinformação em larga escala** — gerar ou distribuir conteúdo desenhado
   para enganar o público em escala sobre fato materialmente relevante.
7. **Discriminação ilícita** — negar a uma pessoa direito legal, benefício,
   emprego ou oportunidade econômica por característica protegida.
8. Qualquer uso **ilícito** sob a lei aplicável a quem opera o sistema.

## Efeito de uma violação

Violar a lista acima encerra a licença **para aquele uso específico**, o
mesmo mecanismo do OpenRAIL — é condição da licença, não promessa à parte.
Isso não impede o autor de buscar os remédios legais comuns que a lei já
oferece a qualquer titular de direito autoral; esta licença não precisa
listá-los para que existam, e listá-los como ameaça não serve ao objetivo
de "não ser hostil" — o ponto é dizer com clareza o que não é permitido, não
ameaçar quem usa.

## Obrigações de quem distribui

Quem distribuir o software, original ou modificado, deve:

1. Incluir cópia integral desta licença e da lista de usos proibidos.
2. Manter os cabeçalhos SPDX atualizados.
3. Distribuir sob esta mesma licença (copyleft).
4. Avisar de forma clara que o software está sujeito a restrições de uso.
5. Preservar avisos de direitos autorais, marcas e atribuição.

## Isenção de garantias e responsabilidade

O software é fornecido "no estado em que se encontra", sem garantia de
qualquer tipo. O autor não é responsável por danos decorrentes do uso ou da
impossibilidade de uso, ressalvadas as hipóteses que a lei não permite
excluir (dolo comprovado, e as proteções do Código de Defesa do Consumidor
em relação de consumo). Esta isenção é um instrumento diferente da lista de
usos proibidos — ambos continuam necessários, e nenhum substitui o outro
(ver `DISCLAIMER.md`).

## Proteção de dados

Quem usa o software para tratar dados pessoais é o responsável por cumprir a
LGPD, o GDPR quando aplicável, e a legislação setorial cabível — o autor não
assume responsabilidade pelo tratamento de dados feito por quem usa o
software, e esta licença não é, por si só, um contrato de tratamento de
dados entre autor e usuário.

## Licenciamento dual

O autor pode, a seu critério, oferecer o software sob a AGPL-3.0-or-later
sem as restrições de uso acima, para quem não precisa ou não quer as
limitações comportamentais — a escolha é de quem distribui, indicada com
clareza, e adotar uma opção não implica renunciar à outra em distribuições
futuras.

## Disposições gerais

- A invalidade de qualquer cláusula não afeta as demais (severabilidade).
- Tolerância quanto a um descumprimento não constitui renúncia.
- Esta licença pode ser atualizada por nova versão publicada, sem efeito
  retroativo sobre distribuições já feitas sob a versão anterior.

## O que ainda falta pra isso ser real

- Revisão por advogado das expressões que ainda são amplas de propósito
  ("revisão humana qualificada", "base legal") e verificação de toda
  citação legal antes de qualquer uma ser reafirmada com número de artigo.
- Decisão: substituir a AGPL ou manter as duas (dual-license)?
- Tradução pro inglês — o resto da documentação do projeto é em inglês; esta
  versão em português é a base porque foi assim que o rascunho evoluiu, mas
  a versão que efetivamente rege precisa existir nos dois idiomas, com uma
  marcada como a referência em caso de divergência.
- Adoção mecânica quando o texto estiver pronto: atualizar cabeçalho SPDX de
  todo arquivo, `LICENSE`, `LICENSE_POLICY.md` e `README.md` juntos, num
  commit só.

## O que foi cortado, e por quê

Removido da versão anterior, mais extensa, por ir além do objetivo
declarado (proteção contra culpa, não hostilidade):

- **Sanção penal explícita** (art. 184 do Código Penal) — invocar processo
  criminal contra violação de uma restrição de uso ética é desproporcional;
  o objetivo é dizer "eu não permito isso", não ameaçar prisão.
- **Lista de remédios enumerados** (indenização, tutela de urgência) —
  a lei já garante esses remédios a qualquer titular de direito autoral;
  enumerá-los como ameaça reforça uma postura de litígio que contradiz o
  próprio pedido de não ser duro.
- **Foro exclusivo, contrato de adesão e bloco de assinatura com
  CPF/CNPJ** — uma licença pública, no molde Linux/AGPL/OpenRAIL, não é um
  contrato bilateral que cada usuário assina; formatar assim contradiz a
  própria analogia do Ubuntu/Fedora e pode afastar exatamente quem o
  Capítulo de usos permitidos quer atrair (educação, ONG, acessibilidade).
- **Citação do PL nº 2.338/2023 como obrigação de conformidade** — é
  projeto de lei, não lei vigente; apresentar como exigível era impreciso.
- **Citações de blog de escritório de advocacia como fundamento legal** —
  substituídas por referência ao nome da lei, sem número de artigo, até
  revisão por advogado confirmar a citação exata.
