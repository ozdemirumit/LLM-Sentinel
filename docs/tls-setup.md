# TLS Setup

## Self-Signed Certificate (Development)

```bash
python main.py --gen-cert
```

Creates `certs/server.crt` and `certs/server.key`. Set in `.env`:
```
PROXY_TLS_ENABLED=true
PROXY_TLS_CERT=certs/server.crt
PROXY_TLS_KEY=certs/server.key
```

## Let's Encrypt (Production)

Use certbot to obtain certificates, then configure:
```
PROXY_TLS_CERT=/etc/letsencrypt/live/sentinel.example.com/fullchain.pem
PROXY_TLS_KEY=/etc/letsencrypt/live/sentinel.example.com/privkey.pem
```

## Internal CA

Place CA-signed cert and key in the `certs/` directory and update paths in `.env`.

## mTLS (Mutual TLS)

For client certificate verification:
```
PROXY_MTLS_ENABLED=true
PROXY_TLS_CA=certs/ca.crt
```

## Client Configuration

When using self-signed certs, clients must trust the CA or disable verification:
```python
client = OpenAI(
    base_url="https://sentinel.example.com/v1",
    api_key="sk-proxy-xxx",
    http_client=httpx.Client(verify=False),  # dev only
)
```
