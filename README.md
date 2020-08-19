## Set up/Deployment

### 1. Prepare `.env` file
```
cp .env.sample .env
```
paste server token there

### 2. Make sure caddy is configured
your Caddyfile must contain  
**important**: "example.com" means your domain/url
```
example.com {
    handle /api/* {
        reverse_proxy humanbios-websocket:8080
    }
}
```
don't forget to run `docker-compose restart` in the **caddy's** folder

### 3. Deploy docker
```
./deploy.sh
```
##### Done.
