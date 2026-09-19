# The environment every team gets. Built once as a prebuild, opened in seconds.
#
# Everything slow or network-dependent happens HERE, at image build time, so
# that nothing slow or network-dependent happens at 2am on venue wifi.
# Python 3.13: Netra's API and worker require it (.python-version, pyproject.toml).
FROM mcr.microsoft.com/devcontainers/python:3.13-bookworm

# --- cloudflared: how a team shows a judge a live URL, with no account -------
ARG TARGETARCH=amd64
RUN curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${TARGETARCH}" \
 && chmod +x /usr/local/bin/cloudflared \
 && cloudflared --version

# --- python deps: the kit and Netra share one environment ------------------
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# --- bake the embedding model into the image --------------------------------
# Without this, a team's first `ingest` downloads ~80MB from Hugging Face.
# On a filtered or slow network that is a stack trace at the worst moment.
# We found this the hard way; you should not have to.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN mkdir -p /opt/fastembed \
 && python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
 && chmod -R a+rX /opt/fastembed

USER vscode
