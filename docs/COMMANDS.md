# Comandos e linguagem natural

Este documento descreve exemplos de mensagens aceitas pelo CofrinIA. O usuário não precisa memorizar comandos exatos: frases equivalentes em português brasileiro são encaminhadas pelo parser local ou pelo Hermes quando necessário.

## Registro de movimentações

```text
Gastei R$ 42,50 no almoço via Pix
Paguei 25 reais na farmácia no crédito
Comprei um capacete de R$ 350 no cartão Nubank
Recebi R$ 3.000 de salário
```

O sistema identifica:

- despesa ou receita;
- valor em reais, com vírgula decimal e separador de milhar;
- descrição;
- categoria;
- forma de pagamento;
- data do lançamento.

Categorias automáticas incluem alimentação, transporte, moradia, lazer, saúde, salário e outros.

## Consultas

```text
Quanto gastei hoje?
Quanto eu gastei esta semana?
Quanto gastei este mês?
Quantas despesas eu tenho?
Me mostre meus lançamentos recentes
```

## Relatório por categoria

```text
Relatório de alimentação
Quero consultar meus gastos com alimentação
Quanto gastei com alimentação este mês?
Me mostre os gastos de transporte esta semana
Quero ver quanto foi gasto com saúde hoje
```

Os filtros aceitam `hoje`, `semana` e `mês`. A categoria é comparada sem depender de acentos.

## Correção de lançamento

Depois de uma imagem ou de um lançamento já registrado:

```text
Corrigir categoria para alimentação
Corrija a categoria para saúde
Ajuste a categoria para transporte
Alterar valor para R$ 50,00
Corrigir pagamento para Pix
```

A correção de categoria, valor ou pagamento altera o último lançamento do próprio telefone. Nenhum outro usuário é afetado.

## Confirmação humana

Mensagens ambíguas não são gravadas automaticamente:

```text
Gastei por volta de R$ 240 em uma coisa no cartão
```

O CofrinIA cria uma pendência e aguarda:

```text
Confirmar
Cancelar
Corrigir categoria para alimentação
```

A pendência expira após 24 horas. A confirmação grava a transação de forma atômica e idempotente.

## Pagamentos recorrentes

Criar:

```text
Internet de R$ 100 todo dia 10
```

Consultar:

```text
Pagamento recorrente
Consultar pagamentos recorrentes
Ver meus pagamentos recorrentes
```

## Parcelamentos

```text
Comprei uma TV de R$ 2.400 em 12 vezes no cartão
```

## Lembretes

```text
Me lembre de pagar a conta daqui a 10 minutos
Lembre-me de ligar para o banco em 5 horas
Me lembre de revisar o orçamento em 2 dias
```

Também são aceitas formas sem espaço, como `10minutos`. Quando nenhuma duração é informada, o padrão é de 3 horas.

## Áudio

Envie um áudio curto falando naturalmente, por exemplo:

> Gastei trinta reais no almoço via Pix.

O áudio é baixado pela Evolution, transcrito localmente pelo `faster-whisper` e encaminhado ao mesmo fluxo de texto. O modelo atual é o `base`, executado em CPU.

Em caso de transcrição incompleta, o bot pede o valor ou uma nova mensagem; não grava um lançamento sem dados suficientes.

## Imagens de comprovantes

Envie uma foto JPEG ou PNG do comprovante. O OCR tenta identificar valor, estabelecimento e pagamento. Se a categoria ficar como `Outros`, corrija pelo texto:

```text
Corrigir categoria para alimentação
```

PDF não faz parte do fluxo atual; comprovantes devem ser enviados como imagem.

## Perguntas gerais

Perguntas fora do parser determinístico são encaminhadas ao Hermes quando necessário:

```text
Você pode me ajudar a organizar meu orçamento?
Como posso controlar melhor meus gastos?
O que devo fazer para reduzir despesas?
```

O Hermes responde em português brasileiro e não grava dados diretamente.
