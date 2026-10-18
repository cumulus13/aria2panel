# Optional: a plain aria2 daemon for the "local-aria2" compose profile.
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends aria2 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1000 --home /home/aria2 --create-home aria2 \
    && mkdir /downloads && chown aria2:aria2 /downloads
USER aria2
VOLUME /downloads
EXPOSE 6800
CMD ["sh", "-c", "exec aria2c --enable-rpc --rpc-listen-all --rpc-secret=\"$RPC_SECRET\" --dir=/downloads --continue=true --max-concurrent-downloads=50"]
