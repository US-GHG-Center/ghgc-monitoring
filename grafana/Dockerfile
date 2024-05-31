FROM --platform=arm64 grafana/grafana:latest

# List of plugins to install...
ENV GF_INSTALL_PLUGINS=grafana-x-ray-datasource

ADD provisioning/. /usr/local/grafana/provisioning

ENV GF_PATHS_PROVISIONING=/usr/local/grafana/provisioning
