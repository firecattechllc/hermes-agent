FROM ubuntu:24.04

ARG BROWSEROS_UID=987
ARG BROWSEROS_GID=987

COPY BrowserOS.deb /tmp/BrowserOS.deb

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        alsa-topology-conf \
        alsa-ucm-conf \
        ca-certificates \
        curl \
        jq \
        libasound2-data \
        libasound2t64 \
        xvfb \
        /tmp/BrowserOS.deb \
    && rm -f /tmp/BrowserOS.deb \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${BROWSEROS_GID}" browseros \
    && useradd --uid "${BROWSEROS_UID}" --gid "${BROWSEROS_GID}" \
        --home-dir /home/browseros --create-home --shell /usr/sbin/nologin browseros

COPY browseros-container-entrypoint.sh /usr/local/bin/browseros-container-entrypoint
COPY browseros-container-healthcheck.sh /usr/local/bin/browseros-container-healthcheck
RUN chmod 0755 /usr/local/bin/browseros-container-entrypoint \
    /usr/local/bin/browseros-container-healthcheck

USER browseros:browseros
ENV HOME=/home/browseros \
    XDG_CONFIG_HOME=/home/browseros/.config \
    XDG_CACHE_HOME=/home/browseros/.cache \
    DISPLAY=:99

VOLUME ["/home/browseros"]
EXPOSE 9000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=4 \
    CMD ["/usr/local/bin/browseros-container-healthcheck"]

ENTRYPOINT ["/usr/local/bin/browseros-container-entrypoint"]
CMD ["about:blank"]
