# Grafana Service

This directory stores the files necessary for deploying Grafana into an AWS environment.

## Out of the box...

When initially deployed, the system is preconfigured with the following:

### Plugins

- [AWS X-Ray data source](https://github.com/grafana/x-ray-datasource)

Additional plugins configured for auto-installation by appending them to the `GF_INSTALL_PLUGINS` environment variable in the [`Dockerfile`](Dockerfile). Plugins should be added in a comma-separated format, eg: `GF_INSTALL_PLUGINS=plugin-1,plugin-2,plugin-3`.

### Data Sources

- AWS CloudWatch
- AWS X-Ray

These data sources are auto-provisioned via files located in [`/provisioning/datasources`](provisioning/datasources/).
