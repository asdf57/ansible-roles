FROM openresty/openresty:alpine-fat

RUN /usr/local/openresty/luajit/bin/luarocks install lua-resty-http

COPY entrypoint.sh /entrypoint.sh
COPY default.conf /etc/nginx/conf.d/default.conf
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
