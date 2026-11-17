# Deploy numa VM do GCP

Uma instalação roda numa VM com Docker Compose: Postgres (pgvector), Temporal, Hindsight,
o app (`python -m brq_at_work.serve`) e o Caddy, que é o único serviço com portas
públicas e termina o TLS com certificado automático. O app e o Hindsight chamam a Vertex AI
com a conta de serviço da VM, sem arquivo de chave.

Projeto, região, modelos e preços vêm de `deploy/.env`. São decisão de deploy, por
cliente. Por padrão, use a região mais barata que atenda à residência exigida pelo cliente.

## 1. Projeto, conta de serviço e VM

```sh
PROJETO=meu-projeto      # projeto GCP da instalação
REGIAO=us-central1       # região da VM
ZONA=us-central1-a
gcloud config set project "$PROJETO"
gcloud services enable compute.googleapis.com aiplatform.googleapis.com

gcloud iam service-accounts create pxos-vm --display-name="pxOS VM"
gcloud projects add-iam-policy-binding "$PROJETO" \
  --member="serviceAccount:pxos-vm@$PROJETO.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud compute addresses create pxos-ip --region="$REGIAO"
gcloud compute instances create pxos \
  --zone="$ZONA" --machine-type=e2-standard-4 \
  --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=50GB \
  --address=pxos-ip --tags=pxos-https \
  --service-account="pxos-vm@$PROJETO.iam.gserviceaccount.com" --scopes=cloud-platform
```

Se o modelo dos agentes for Claude, habilite-o no Model Garden da Vertex, na região
configurada, antes da primeira subida.

## 2. Firewall

Só HTTP e HTTPS ficam abertos ao mundo. A porta 80 serve ao desafio ACME e ao
redirecionamento para HTTPS. O SSH entra pelo IAP.

```sh
gcloud compute firewall-rules create pxos-https --network=default \
  --target-tags=pxos-https --allow=tcp:80,tcp:443,udp:443 --source-ranges=0.0.0.0/0
gcloud compute firewall-rules create pxos-ssh-iap --network=default \
  --target-tags=pxos-https --allow=tcp:22 --source-ranges=35.235.240.0/20
```

Se a rede tiver `default-allow-ssh` aberta para `0.0.0.0/0`, remova-a.

## 3. DNS e TLS

Crie um registro A do domínio do bot (`PXOS_DOMAIN`) para o IP reservado:

```sh
gcloud compute addresses describe pxos-ip --region="$REGIAO" --format='value(address)'
```

O Caddy obtém e renova o certificado sozinho na primeira subida, desde que o DNS já
aponte para a VM e as portas 80 e 443 estejam abertas.

## 4. Adaptadores: SharePoint, Granola e Teams

Os três são obrigatórios. Sem alguma variável obrigatória, o app não sobe e o log nomeia a
que falta. Todas vão em `deploy/.env` (modelo e comentários em `.env.example`). Todo
conector entrega suas fontes com as tags de acesso da distro (`corporate`), as mesmas do
`recall_tags` do assistente.

| Variável | Uso |
| --- | --- |
| `PXOS_SHAREPOINT_TENANT`, `PXOS_SHAREPOINT_CLIENT_ID` | Tenant e app do Entra que lê a biblioteca |
| `PXOS_SHAREPOINT_CLIENT_SECRET` | Segredo do app; ou `PXOS_SHAREPOINT_CERTIFICATE_KEY` + `PXOS_SHAREPOINT_CERTIFICATE` (PEM da chave e do certificado) |
| `PXOS_SHAREPOINT_DRIVE_ID`, `PXOS_SHAREPOINT_FOLDER_ID` | Drive da biblioteca; pasta opcional |
| `PXOS_GRANOLA_API_KEY` | Chave de API do workspace do Granola |
| `PXOS_GRANOLA_SPACE_ID`, `PXOS_GRANOLA_FOLDER_ID` | Opcionais: restringem a um espaço ou pasta |
| `PXOS_TEAMS_APP_ID`, `PXOS_TEAMS_TENANT_ID` | Azure Bot (single tenant) e seu tenant |
| `PXOS_TEAMS_SERVICE_URL` | Bot Connector da região, ex. `https://smba.trafficmanager.net/teams/` |
| `PXOS_TEAMS_SECRET` | Segredo do bot; ou `PXOS_TEAMS_CERTIFICATE_KEY` + `PXOS_TEAMS_CERTIFICATE_THUMBPRINT` |
| `PXOS_TEAMS_CONVERSATION` | Conversa da thread do projeto (canal ou cadeia de respostas) |

Cada app usa um segredo ou um certificado, nunca os dois. Um PEM vai entre aspas duplas
no `.env`, com as quebras de linha do arquivo. O compose define `PXOS_TEAMS_CARDS_PATH`
num volume (`app-state`): é onde o canal guarda a mensagem de cada card, para
substituí-la depois de um restart.

Passos da TI do cliente:

1. **SharePoint.** No Entra, registre um app com a permissão de aplicação
   `Sites.Selected` do Microsoft Graph e dê o consentimento de administrador. Conceda ao
   app leitura só no site da biblioteca (`POST /sites/{site-id}/permissions`, papel
   `read`). Crie um segredo ou envie um certificado. O drive id sai de
   `GET /sites/{site-id}/drives`.
2. **Teams.** Crie um Azure Bot single tenant e, nele, o canal Microsoft Teams. O
   endpoint de mensagens é `https://<PXOS_DOMAIN>/api/messages`. Crie o segredo ou envie
   o certificado no app do bot no Entra. Publique o pacote do app do Teams na
   organização. Instale-o na equipe do projeto e no escopo pessoal de quem revisa (o PM),
   que recebe os cards em chat 1:1. O id do canal do projeto (`19:...@thread.tacv2`) vai
   em `PXOS_TEAMS_CONVERSATION`. O id de objeto do Entra de cada pessoa vai em
   `identities.yaml` como `teams:<id>`.
3. **Granola.** Um administrador do workspace gera uma chave de API. A chave lê só as
   notas compartilhadas nos espaços do workspace. Compartilhe as reuniões do projeto num
   espaço aberto à API.

## 5. Subir

```sh
gcloud compute ssh pxos --zone="$ZONA" --tunnel-through-iap
# na VM:
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
git clone <repositório> pxos && cd pxos
cp .env.example deploy/.env   # preencha as variáveis (veja a seção 4); o arquivo nunca é commitado
chmod 600 deploy/.env
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml ps
```

A cada subida o app migra o banco, grava identidades, pacotes e a rota do modelo, e só
então inicia o worker do Temporal, o publicador da outbox e o endpoint HTTP. As senhas do
Postgres valem na criação do volume. Para trocá-las depois, use `ALTER ROLE` no banco.

Para atualizar o código, rode `git pull` e repita o `up -d --build`. O app recebe SIGTERM
e encerra com graça.

A UI do Temporal é opcional. Ela escuta só em loopback na VM e é acessada por túnel:

```sh
docker compose -f deploy/compose.yaml --profile ui up -d temporal-ui
gcloud compute ssh pxos --zone="$ZONA" --tunnel-through-iap -- -L 8080:127.0.0.1:8080
# abra http://localhost:8080
```

## 6. Publicar a versão do assistente

Uma pessoa propõe o arquivo de definição do assistente do projeto
(`brq-at-work/src/brq_at_work/project_assistant.yaml`) como próxima versão. Um administrador
da plataforma aprova a versão no ciclo de revisão. Só a versão aprovada roda, e ela agenda
os ciclos.

```sh
docker compose -f deploy/compose.yaml exec app sh -c \
  'python -m brq_at_work.publish_agent --dsn "$PXOS_DATABASE_URL" --author <ator>'
```

O comando imprime o id da proposta.

## Rodar no localhost (desenvolvimento)

O mesmo compose roda na máquina do desenvolvedor com `deploy/compose.local.yaml`: sem
Caddy, com o app em `127.0.0.1:8000` e com as credenciais Google do desenvolvedor no lugar
da conta de serviço da VM. O projeto do compose se chama `pxos-local`, para não dividir
containers nem volumes com o `compose.yaml` da raiz, que é o dos testes. Para não depender
de admin do tenant nem de endpoint público, use as opções de desenvolvimento dos
adaptadores: o login delegado do SharePoint, uma chave pessoal do Granola (plano Business
ou Enterprise) e o Telegram no lugar do Teams. Não use essas opções com dados de cliente.

1. **Vertex e identidades.** Use um projeto seu com a Vertex AI habilitada e copie
   para `.secrets/` suas credenciais de aplicação e as identidades da distro, legíveis
   pelo usuário dos containers. Os dois arquivos precisam existir antes de subir: sem
   eles, o Docker cria diretórios no lugar.

   ```sh
   gcloud auth application-default login
   gcloud auth application-default set-quota-project <projeto>
   install -D -m 644 ~/.config/gcloud/application_default_credentials.json .secrets/adc.json
   install -m 644 brq-at-work/src/brq_at_work/identities.yaml .secrets/identities.yaml
   ```

   A cópia das credenciais carrega o seu refresh token do Google e fica legível por
   qualquer usuário da máquina: use só numa estação sua. Para desfazer, apague-a e rode
   `gcloud auth application-default revoke`.

2. **`deploy/.env`.** Parta do `.env.example`. Preencha a rota do modelo, as senhas e
   `PXOS_DOMAIN=localhost`, que o compose base exige mesmo sem o Caddy. O caminho dos
   cards do Telegram já vem do override.
3. **Granola.** No app, gere uma chave em Settings → Connectors → API keys → Personal
   API key e coloque-a em `PXOS_GRANOLA_API_KEY`. A chave lê as suas notas e as
   compartilhadas com você.
4. **SharePoint sem admin.** Registre o app no Entra como o `.env.example` descreve
   (cliente público, permissões delegadas). Preencha `PXOS_SHAREPOINT_TENANT`,
   `PXOS_SHAREPOINT_CLIENT_ID` e `PXOS_SHAREPOINT_TOKEN_PATH=/var/lib/pxos/sharepoint-token`,
   sem segredo nem certificado, e faça o login uma vez, gravando o token no volume do
   app. Com `--link`, o comando também imprime `PXOS_SHAREPOINT_DRIVE_ID` e
   `PXOS_SHAREPOINT_FOLDER_ID`; copie os dois para o `deploy/.env`. Sem o drive, o app
   não sobe:

   ```sh
   C="docker compose -f deploy/compose.yaml -f deploy/compose.local.yaml"
   $C run --rm --no-deps app python -m pxos_sharepoint.login \
     --tenant <tenant> --client-id <app> --token-path /var/lib/pxos/sharepoint-token \
     --link '<link copiado da pasta>'
   ```

5. **Telegram.** Crie o bot com o `@BotFather`, desative o privacy mode (`/setprivacy`),
   adicione-o a um grupo e mande `/start` ao bot no privado. Antes de subir o app, leia
   os ids em `https://api.telegram.org/bot<token>/getUpdates`:
   - `chat.id` do grupo vai em `PXOS_TELEGRAM_CHAT_ID`;
   - `from.id` de cada pessoa vai nos `channel_ids` dela em `.secrets/identities.yaml`,
     como `telegram:<id>`. O arquivo versionado da distro fica como está.

   Defina `PXOS_CHANNEL=telegram` e `PXOS_TELEGRAM_BOT_TOKEN`. Quem revisa (PM e
   administrador da plataforma) precisa ter mandado `/start` ao bot.
6. **Subir e publicar.** Suba com `$C up -d --build` e publique o assistente como na
   seção 6, trocando o compose pelo `$C`. O card de aprovação da versão chega no privado
   do administrador.
7. **Disparar um ciclo agora**, sem esperar a agenda. O agendamento existe depois que a
   versão é aprovada:

   ```sh
   $C run --rm --no-deps --entrypoint temporal temporal-namespace \
     schedule trigger --schedule-id pxos-cycle-project-assistant --address temporal:7233
   ```

   O ciclo sincroniza as fontes, roda o agente e posta as perguntas no grupo ou manda o
   report ao PM. A primeira resposta no grupo já acorda o ciclo. Para ver o histórico,
   suba a UI do Temporal com `$C --profile ui up -d temporal-ui` e abra
   http://localhost:8080.

## Limites atuais

- O canal liga a thread do arquivo do assistente (`thread` em `project_assistant.yaml`)
  à conversa do `.env`, na subida. Se uma versão aprovada trouxer outra thread, o passo
  que posta as perguntas falha e o ciclo para; publique a versão a partir do arquivo do
  deploy.
- Não há mapeamento de fontes por projeto: o assistente lê a base corporativa inteira e
  busca nela o que é do seu projeto.
- O Hindsight valida a Vertex ao iniciar. Sem a conta de serviço com acesso, ele reinicia
  em laço e o app não sobe.
