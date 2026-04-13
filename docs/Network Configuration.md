# Network Configuration

## Overview
The Aura edge service operates within a Docker container. The request flow is structured as follows:

1.  **Public Traffic**: Requests hit the router's public IP and external port.
2.  **NAT Forwarding**: The router forwards traffic to the host machine's external port.
3.  **Docker Routing**: `iptables` rules on the host machine redirect the traffic to the container's internal port.
4.  **Reverse Proxy**: Inside the container, **Nginx** forwards the requests to the specific port bound by the service.

> **Note**: While Docker provides isolation, you may deploy Aura directly on the host to reduce complexity. For production environments, we recommend a microservices approach: deploying different components in separate containers and interconnecting them via a **Docker Network** to adhere to the "Single Responsibility Principle."

## Edge Server Setup

### 1. Configure Nginx (Inside Container)
First, install Nginx:
```shell
apt update && apt install nginx -y
```

Edit the default configuration file:
```shell
vim /etc/nginx/sites-available/default
```

Apply the following configuration to enable SSL and optimize for streaming:
```nginx
server {
    listen 443 ssl;
    server_name _;

    ssl_certificate /etc/nginx/ssl/aura.crt;
    ssl_certificate_key /etc/nginx/ssl/aura.key;

    location / {
        proxy_pass http://127.0.0.1:8000;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Optimizations for SSE (Streaming) and Audio Streams
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```
This configuration allows Nginx to terminate SSL on port `443` and proxy traffic to the local FastAPI service on port `8000`.

### 2. Generate SSL Certificate (Inside Container)
Since we are using a public IP without a domain name, we generate a self-signed certificate (The public IP of our edge server is denoted as `xx.xx.xx.xx`). **Crucially**, Android requires the `subjectAltName` (SAN) field to match the IP address for validation. 

```shell
mkdir -p /etc/nginx/ssl/

openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/aura.key \
  -out /etc/nginx/ssl/aura.crt \
  -subj "/C=CN/ST=Lab/L=Lab/O=CyanFlow/CN=xx.xx.xx.xx" \
  -addext "subjectAltName=IP:xx.xx.xx.xx" \
  -addext "keyUsage=digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth"
```

Verify that the certificate correctly includes the IP address:
```shell
openssl x509 -in /etc/nginx/ssl/aura.crt -text -noout
```
Ensure the output contains:
`X509v3 Subject Alternative Name: IP Address:xx.xx.xx.xx`

### 3. Configure Network Bridge (Host Machine)
Locate the container's internal IP address:
```shell
docker inspect <Container_ID_or_Name> | grep IPAddress
```

Assuming the internal IP is `172.17.0.x`, the container port is `443`, and the host's external port is `8443`, add the following `iptables` rule:
```shell
sudo iptables -t nat -A DOCKER -p tcp --dport 8443 ! -i docker0 -j DNAT --to-destination 172.17.0.x:443
```

Verify the rule was added successfully:
```shell
sudo iptables -t nat -vnL DOCKER --line-number
```

### 4. Router Port Forwarding
Finally, access your router's management interface and configure **Port Forwarding** (or Virtual Server):
* **External Port**: Match the WAN port you wish to expose.
* **Internal IP**: The IP address of your **Host Machine**.
* **Internal Port**: `8443`.
