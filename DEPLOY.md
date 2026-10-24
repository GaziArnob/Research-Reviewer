# Deploy on a free server (Oracle Cloud "Always Free")

Cost: $0. You need an Oracle Cloud account (a card is asked for identity; the Always Free resources are not charged) and a free domain name.
Vercel and similar serverless hosts cannot run this project: reviews take minutes, and the language model and PostgreSQL need a normal server.

## 1. Create the server (in the Oracle Cloud website)

1. Create an account at cloud.oracle.com. Choose a home region and **remember it: Always Free resources only exist there**.
2. Compute -> Instances -> Create instance.
   - Image: **Ubuntu 22.04** (or 24.04).
   - Shape: **Ampere (VM.Standard.A1.Flex)**, 4 OCPU, 24 GB RAM (the Always Free maximum). If it says "out of capacity", try again later or another availability domain.
   - Add your SSH public key (or let Oracle generate one and download it).
   - Boot volume: 100 GB is fine (free up to 200 GB total).
3. After it starts, note the **public IP**.
4. Open the ports: Networking -> your VCN -> Security List -> Add Ingress Rules: source `0.0.0.0/0`, TCP, destination port **80**, and again for **443**. Do NOT open 5432 or 11434.

## 2. Free domain name

Go to duckdns.org, sign in, create a name such as `myreviewer` -> `myreviewer.duckdns.org`, set its IP to the server's public IP. (Any domain you own also works: add an A record.)

## 3. Prepare the server

```bash
ssh ubuntu@<PUBLIC_IP>
# Ubuntu's own firewall also blocks 80/443 on Oracle images:
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && exit        # log in again so the group applies
```

## 4. Get the code and configure

```bash
ssh ubuntu@<PUBLIC_IP>
git clone https://github.com/GaziArnob/Research-Reviewer.git && cd Research-Reviewer
cp .env.prod.example .env.prod
sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$(openssl rand -hex 32)/" .env.prod
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 16)/" .env.prod
nano .env.prod        # set DOMAIN=myreviewer.duckdns.org and CONTACT_EMAIL=you@example.com
```

## 5. Start everything

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml logs -f ollama-pull    # wait until the model is downloaded (about 2 GB), then Ctrl+C
```

Open `https://myreviewer.duckdns.org`. The first account you create becomes the **admin**. Then set `ALLOW_SIGNUP=false` in `.env.prod` and run `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d` again if you do not want strangers to register.

## 6. Daily use

| Task | Command |
|---|---|
| Logs | `docker compose -f docker-compose.prod.yml logs -f web` |
| Update to the newest code | `git pull && docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build` |
| Backup the database | `docker compose -f docker-compose.prod.yml exec db pg_dump -U reviewer reviewer > backup_$(date +%F).sql` |
| Security updates | `sudo apt update && sudo apt upgrade -y` once a month |

## What to expect (honest)

- On the free ARM CPU the 3B model needs roughly **10 to 40 minutes per paper**, one paper at a time (`JOB_WORKERS=1`). Rule-based checks and Convert are fast. With several users, papers wait in a queue.
- Oracle can reclaim Always Free instances that are idle for a long time. Keep the backup above off the server.
- Manuscripts are confidential. Uploaded PDFs are deleted after the review, but you are now the operator of the server: keep it updated and read the Terms/Privacy pages in the app (`/legal/terms`) before inviting others.
- Not built yet: e-mail verification and password reset (needs an SMTP service, e.g. a free tier of Brevo) and payments. Add them before a public launch.
