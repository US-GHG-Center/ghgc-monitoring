# GHGC Monitoring System

## Summary

The purpose of this system is to manage the state of all applications within GHGC, by consolidating and tracking performance metrics and logs into actionable data and alerts.

## Design

All significant design decisions are captured in Architectural Decision Records (ADR). Currently, the following ADRs exist:

* [#1 Observability Platform Selection](./docs/adr/001-observability-platform-selection.md)
* [#2 Testing Strategy](./docs/adr/002-testing-strategy.md)

## Deployment

Deployment of monitoring services is managed via [AWS CDK](https://aws.amazon.com/cdk/).

### dotenv

Configuration is provided via environment variables. These environment variables can be provided to the application in a number of ways:

- set on the environment manually prior to running CDK commands (e.g. `export STAGE=my-stage`)
- provided inline when running CDK (e.g. `STAGE=my-stage cdk diffnpx `)
- specified within a dotenv file. When our settings class initializes, it will attempt to load a dotenv file (located at `.env` by default, configurable via the `DOTENV` environment variable). Note that some environment variables such as `AWS_PROFILE` are best provided via methods other than a dotenv file as CDK will make available required related environment variables (e.g. `CDK_DEFAULT_ACCOUNT`, `CDK_DEFAULT_REGION`) before initializing our settings class

An example of the environment variables used by our settings class can be found in `.env.example`.

### Useful commands

- `cdk ls` list all stacks in the app
- `cdk synth` emits the synthesized CloudFormation template
- `cdk deploy` deploy this stack to your default AWS account/region
- `cdk diff` compare deployed stack with current state
- `cdk docs` open CDK documentation
