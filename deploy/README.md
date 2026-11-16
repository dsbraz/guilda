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

## 4. Subir

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

## 5. Publicar a versão do agente

Uma pessoa propõe o arquivo de definição do agente como próxima versão. Um administrador
da plataforma aprova a versão no ciclo de revisão. Só a versão aprovada roda, e ela agenda
os ciclos.

```sh
docker compose -f deploy/compose.yaml exec app sh -c \
  'python -m brq_at_work.publish_agent --dsn "$PXOS_DATABASE_URL" --author <ator>'
```

O comando imprime o id da proposta.

## Limites atuais

- Os adaptadores de SharePoint, Granola e Teams entram em `adapters()`, em
  `brq-at-work/src/brq_at_work/serve.py`. Até lá a instalação não sincroniza fontes, o
  Caddy só alcança o health check, e o canal recusa cards e posts. Os eventos ficam na
  outbox até haver um canal.
- O Hindsight valida a Vertex ao iniciar. Sem a conta de serviço com acesso, ele reinicia
  em laço e o app não sobe.
