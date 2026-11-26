# ADR 0007 — Identidade organizacional do especialista

**Status:** aceito em 25/11/2026.

## Contexto

Um especialista pode receber uma solicitação em uma conversa privada e concluir a
entrega em um workflow. A organização precisa reconhecer quem assumiu o trabalho
mesmo quando mudam o interlocutor, o canal ou a execução.

## Decisão

Cada especialista publicado pelo FDE tem uma identidade única e duradoura dentro
da organização. Pessoas e workflows se dirigem ao mesmo especialista. Abrir uma
conversa, iniciar uma execução ou atualizar as instruções do especialista preserva
essa identidade.

## Consequências

Os compromissos do especialista podem atravessar conversas e execuções. Cada
conversa privada mantém seu próprio histórico. Ao recuperar documentos ou memórias,
usar ferramentas ou entregar um resultado, o sistema aplica as permissões do
usuário ou do workflow responsável pela ação.

Por exemplo, Paula pede à Ana, especialista de Sales Ops, a previsão de vendas.
Um workflow aciona a mesma Ana para concluir o trabalho. Bruno pode consultar a
previsão se tiver acesso à entrega. A conversa de Paula continua privada.
