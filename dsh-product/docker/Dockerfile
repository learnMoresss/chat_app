# DSH runtime image — pin matches docs/MIGRATION_DSH.md (0.2.1-rc.3).
# 不改上游源码：只装官方 CLI；产品插件由 volume 挂载，热更新不必重建镜像。
# 服务器访问 Docker Hub 常超时；用 DaoCloud 代理官方 library
FROM docker.m.daocloud.io/library/node:24-bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates tini \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 仅锁定 runtime；产品 packages/ 不打进镜像层
COPY package.json package-lock.json .npmrc ./
# 允许原生依赖 postinstall（pty / koffi / subprocess helper）
RUN npm approve-scripts --allow-scripts-pending 2>/dev/null || true \
  && npm ci --omit=dev

ENV PRODUCT_ROOT=/app
ENV DSH_HOME=/dsh-home
ENV NODE_ENV=production

# companion profile 模板进镜像；cordis.patch 可被 volume 覆盖
COPY profiles/ /app/profiles/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
  && mkdir -p /dsh-home

VOLUME ["/dsh-home", "/app/packages"]

EXPOSE 3080
ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
# 默认：SDK companion（供 product-gateway / 冒烟脚本驱动）
CMD ["sdk"]
