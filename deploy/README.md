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
cp .env.example deploy/.env   # preencha todas as variáveis; o arquivo nunca é commitado
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

## Limites atuais

- O canal liga a thread do arquivo do assistente (`thread` em `project_assistant.yaml`)
  à conversa do `.env`, na subida. Se uma versão aprovada trouxer outra thread, o passo
  que posta as perguntas falha e o ciclo para; publique a versão a partir do arquivo do
  deploy.
- Não há mapeamento de fontes por projeto: o assistente lê a base corporativa inteira e
  busca nela o que é do seu projeto.
- O Hindsight valida a Vertex ao iniciar. Sem a conta de serviço com acesso, ele reinicia
  em laço e o app não sobe.
