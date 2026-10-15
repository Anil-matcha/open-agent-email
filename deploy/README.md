# Deploying to a server

A production example for one Linux host with Docker and nginx. The API and dashboard listen on localhost behind nginx, and inbound mail is published on port 25.

1. Create `.env.prod` in the repository directory on the server (it is never synced):

   ```
   SHARED_DOMAIN=mail.example.com
   MAIL_HOSTNAME=mail.example.com
   PUBLIC_BASE_URL=https://mail.example.com
   # Optional; generated once into the data volume when unset:
   # ADMIN_API_KEY=
   # SMTP_CONFIG_ENCRYPTION_KEY=
   ```

2. DNS: an `A` record for the host, and an `MX` record for the mail domain pointing at it.
3. nginx: copy `deploy/nginx/email.conf`, replace `SERVER_NAME`, get a certificate with certbot, then `nginx -t && systemctl reload nginx`.
4. Deploy: `DEPLOY_HOST=ubuntu@<ip> DEPLOY_KEY=<key> deploy/deploy.sh`. The first start prints the admin key once in the server log: `docker compose -f deploy/docker-compose.prod.yml logs server | grep "Admin key"`.
5. Backups: install the cron line from `deploy/backup/oae-backup.sh` and copy the backup directory off the host. Keep `secrets.env`: the encryption key protects stored credentials.
6. Open TCP 25 in the firewall or security group to receive mail from the internet. Cloud providers often block outbound port 25, so send through a relay.
