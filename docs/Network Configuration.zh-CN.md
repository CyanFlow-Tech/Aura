# 网络配置指南

## 总览

Aura 的边缘服务通常运行在 Docker 容器中。请求链路如下：

1. **公网入口**：请求先到达路由器的公网 IP 和外部端口。
2. **NAT 转发**：路由器将流量转发到宿主机的外部端口。
3. **Docker 路由**：宿主机通过 `iptables` 规则把流量重定向到容器内部端口。
4. **反向代理**：容器内的 **Nginx** 再将请求转发到具体服务端口。

> **说明**：Docker 带来隔离性的同时也增加了配置复杂度。若想简化流程，也可以直接部署在宿主机。生产环境建议采用微服务方式：将不同组件拆分到独立容器中，并通过 **Docker Network** 互联，以符合“单一职责”原则。

## 边缘服务器配置

### 1. 配置 Nginx（容器内）

先安装 Nginx：

```shell
apt update && apt install nginx -y
```

编辑默认配置文件：

```shell
vim /etc/nginx/sites-available/default
```

应用如下配置以启用 SSL 并优化流式传输：

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

        # SSE（流式文本）与音频流优化
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

该配置让 Nginx 在 `443` 端口终止 SSL，并把流量代理到本地 `8000` 端口的 FastAPI 服务。

### 2. 生成 SSL 证书（容器内）

由于我们使用公网 IP 而不是域名，这里采用自签名证书（边缘服务器公网 IP 以 `xx.xx.xx.xx` 表示）。**关键点**：Android 校验证书时要求 `subjectAltName`（SAN）与 IP 地址匹配。

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

验证证书中是否正确包含 IP：

```shell
openssl x509 -in /etc/nginx/ssl/aura.crt -text -noout
```

请确认输出中包含：

`X509v3 Subject Alternative Name: IP Address:xx.xx.xx.xx`

### 3. 配置网络桥接（宿主机）

先获取容器内网 IP：

```shell
docker inspect <Container_ID_or_Name> | grep IPAddress
```

假设容器内网 IP 为 `172.17.0.x`，容器端口为 `443`，宿主机对外端口为 `8443`，添加如下 `iptables` 规则：

```shell
sudo iptables -t nat -A DOCKER -p tcp --dport 8443 ! -i docker0 -j DNAT --to-destination 172.17.0.x:443
```

验证规则是否生效：

```shell
sudo iptables -t nat -vnL DOCKER --line-number
```

### 4. 路由器端口转发

最后进入路由器管理界面，配置 **端口转发**（Port Forwarding / Virtual Server）：

- **外部端口（External Port）**：设置为你希望暴露的 WAN 端口。
- **内部 IP（Internal IP）**：宿主机的局域网 IP。
- **内部端口（Internal Port）**：`8443`。
