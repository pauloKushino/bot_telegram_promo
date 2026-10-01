# Fase 3 do Bot de Promoções de Anime

*Ideias, decisões pendentes e implantação na VPS*

Este documento reúne as recomendações para a fase 3 do bot (personalização e premium) e para a implantação do sistema em uma VPS. O bot já está funcionando; o foco aqui é a ordem de trabalho e as quatro decisões pendentes da fase 3.

---

## 1. Suba na VPS agora, antes de implementar a fase 3

O histórico de preço só cresce enquanto o worker está rodando. Cada dia desligado é um dia a menos de dados para a regra de "menor preço em 60 dias". Como o bot já funciona e usa polling (sem webhook), dá para subir hoje, sem domínio e sem portas abertas além do SSH.

---

## 2. Resumo das quatro decisões

| Decisão | Recomendação |
|---|---|
| 1. O que o premium inclui | Modelo freemium: grátis com limites; premium com recursos ilimitados, ofertas antecipadas e canal VIP. |
| 2. Preço do premium | R$ 9,90/mês, com plano anual para diluir a taxa fixa (por exemplo, R$ 89). |
| 3. Stripe em modo live | Só cartão no lançamento: a Stripe no Brasil não oferece Pix recorrente. |
| 4. Domínio | DuckDNS grátis no começo, com Caddy para o SSL; domínio próprio se o premium engrenar. |

---

## 3. Detalhamento das decisões

### 3.1 O que o premium inclui

Sugestão de modelo freemium:

| Plano | O que inclui |
|---|---|
| Grátis | Canal público, seguir até 3 franquias e 1 alerta de preço. |
| Premium | Franquias e alertas ilimitados, ofertas antecipadas e resumo semanal personalizado. |
| Canal VIP privado | Reaproveita o fluxo de acesso a canal pago do `subbotTelegram`, com ofertas postadas ali minutos antes do canal público. |

Não esconda tudo atrás do paywall. Quem recebe DM de uma franquia que acompanha clica mais, e a comissão de afiliado vai ser a receita principal no começo. O premium vira um segundo degrau, quando houver base ativa.

### 3.2 Preço do premium

R$ 9,90/mês é razoável, mas é preciso olhar as taxas: a Stripe no Brasil cobra cerca de 3,99% + R$ 0,39 por transação no cartão. Nesse valor, isso dá algo perto de **8% por cobrança** (conta aproximada). Um plano anual dilui a taxa fixa.

### 3.3 Stripe em modo live

- Contas brasileiras da Stripe suportam só Pix pontual, sem Pix Automático (recorrente).
- Para habilitar Pix, a conta precisa estar em dia e ter processado pagamentos por pelo menos 60 dias.
- Na prática, no lançamento será só cartão. Muita gente do público pode não ter cartão, então vale pesquisar depois um gateway com Pix recorrente (nenhum específico foi verificado).

### 3.4 Domínio

O domínio só serve para o webhook do Stripe, porque o usuário nunca o vê (ele usa o Telegram). DuckDNS grátis resolve no começo. Prefira **Caddy** ao Nginx + certbot, porque ele emite e renova o certificado SSL sozinho. Se o premium engrenar, um domínio próprio é mais confiável para pagamentos.

---

## 4. Ideias extras para a fase 3

- **Botão em cada post:** "Acompanhar este item". Cria o alerta com um toque, sem o usuário digitar nada, e é a forma mais fácil de popular a tabela de alertas.
- **`sub_id` por usuário** nos links das DMs, para saber quais usuários convertem de fato.
- **Controle de envio:** limite de DMs por dia por usuário, fila de envio respeitando os limites do Telegram, `/pausar` e `/parar` fáceis, e marcar como inativo quem bloquear o bot.
- **Resumo semanal** em vez de DM a cada oferta, para quem seguir muitas franquias.
- **Privacidade:** guardar só o mínimo (ID do Telegram, franquias, alertas) e oferecer um comando `/apagar_meus_dados`.

---

## 5. Antes de cobrar assinatura

Confira os termos do programa de afiliados da Shopee para ver se há restrição a cobrar por acesso a ofertas com link de afiliado. Não sei se existe, mas é o tipo de coisa que pode derrubar a conta, então leia antes de lançar o premium.

---

## 6. Ordem sugerida de trabalho

1. **VPS agora:** Docker Compose em produção, firewall só com SSH, chave SSH (sem senha), `pg_dump` diário com cópia fora do servidor, limite de tamanho dos logs e um alerta ao admin se o worker cair.
2. **Fase 3a:** usuários, franquias e alertas gratuitos com limites, mais o botão nos posts.
3. **Fase 3b:** domínio + Caddy + webhook do Stripe em modo teste.
4. **Fase 3c:** Stripe live, premium e canal VIP.

---

## 7. Fontes consultadas

- Stripe Support: como habilitar Pix no Brasil (Pix pontual, sem Pix Automático, requisito de 60 dias): https://support.stripe.com/questions/how-to-enable-pix-as-a-payment-method-in-brazil
- Mind Group: comparativo de gateways de pagamento no Brasil em 2026 (taxas aproximadas, sujeitas a alteração): https://mindconsulting.com.br/2026/07/gateways-pagamento-online-brasil-comparativo-2026/
