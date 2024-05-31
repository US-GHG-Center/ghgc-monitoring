import hashlib
from constructs import Construct
from aws_cdk import (
    Duration,
    Stack,
    aws_iam as iam,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_servicediscovery as service_discovery,
    aws_ssm as ssm,
)

from .settings import Settings


class OtelStack(Stack):
    """
    A CDK stack for deploying an OpenTelemetry Collector in Fargate.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        settings: Settings,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        boundary = iam.ManagedPolicy.from_managed_policy_arn(
            self, "Boundary", settings.permissions_boundary_arn
        )
        iam.PermissionsBoundary.of(self).apply(boundary)

        vpc = ec2.Vpc.from_lookup(self, "vpc", vpc_id=settings.vpc_id)

        otel_config_name = settings.stack_name("OTELConfig")

        with open("./otel/otel-config.yaml", "r") as otel_file:
            otel_config_raw_content = otel_file.read()
            otel_config_content = otel_config_raw_content.replace(
                "##HONEYCOMB_API_KEY##", settings.honeycomb_api_key
            ).replace("##TRACE_EXPORTERS##", settings.trace_exporters)
            otel_config_hash = hashlib.sha256(otel_config_content.encode()).hexdigest()

        self.otel_config = ssm.StringParameter(
            self,
            "OtelConfig",
            string_value=otel_config_content,
            parameter_name=otel_config_name,
            tier=ssm.ParameterTier.ADVANCED,
        )

        otel_env_vars = {
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_PROPAGATORS": "xray",
            "OTEL_PYTHON_ID_GENERATOR": "xray",
            "OTEL_LOGS_EXPORTER": "otlp",
        }

        if settings.namespace_arn and settings.namespace_name:
            dns_namespace = service_discovery.PrivateDnsNamespace.from_private_dns_namespace_attributes(
                self,
                "dns",
                namespace_name=settings.namespace_name,
                namespace_id=settings.namespace_id,
                namespace_arn=settings.namespace_arn,
            )
        else:
            dns_namespace = service_discovery.PrivateDnsNamespace(
                self,
                "dns",
                name=settings.namespace_name,
                vpc=vpc,
            )

        cluster = ecs.Cluster(
            self,
            "cluster",
            vpc=vpc,
            cluster_name=settings.stack_name("otel"),
        )

        image = ecs.ContainerImage.from_registry("amazon/aws-otel-collector:latest")
        task_definition: ecs.FargateTaskDefinition = ecs.FargateTaskDefinition(
            self,
            "api-definition",
            memory_limit_mib=2048,
            cpu=1024,
        )
        task_definition.add_container(
            "container",
            image=image,
            environment={
                "AWS_REGION": Stack.of(self).region,
                # Store config hash in order to force new version and
                # new deployment when the config is updated
                "OTEL_CONFIG_HASH": otel_config_hash,
                **otel_env_vars,
            },
            container_name="OtelCollector",
            port_mappings=[
                ecs.PortMapping(container_port=4317),
                ecs.PortMapping(container_port=4318),
                ecs.PortMapping(container_port=8125),
            ],
            secrets={
                "AOT_CONFIG_CONTENT": ecs.Secret.from_ssm_parameter(self.otel_config)
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix=settings.otel_stack_name),
            stop_timeout=Duration.seconds(2),
        )

        sg = ec2.SecurityGroup(
            self,
            "sg",
            vpc=vpc,
            security_group_name=settings.stack_name("sg"),
            allow_all_outbound=True,
        )
        sg.connections.allow_from_any_ipv4(port_range=ec2.Port.tcp_range(4317, 4318))
        sg.connections.allow_from_any_ipv4(port_range=ec2.Port.tcp(8125))

        service: ecs.FargateService = ecs.FargateService(
            self,
            "OtelCollector",
            service_name=settings.stack_name("otel-collector-svc"),
            task_definition=task_definition,
            cluster=cluster,
            assign_public_ip=False,
            security_groups=[sg],
            cloud_map_options=ecs.CloudMapOptions(
                name="otel",
                cloud_map_namespace=dns_namespace,
                dns_record_type=service_discovery.DnsRecordType.A,
            ),
        )
        self.grant_permissions(service.task_definition)

    def grant_permissions(self, task_definition: ecs.TaskDefinition):
        """
        Ensure service is able to interact with other AWS services required for monitoring.
        """
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                # https://grafana.com/grafana/plugins/grafana-x-ray-datasource
                sid="xrayPermissions",
                actions=[
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                    "xray:GetSamplingStatisticSummaries",
                    "ssm:GetParameters",
                ],
                resources=["*"],
            )
        )
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                # https://grafana.com/docs/grafana/latest/datasources/aws-cloudwatch/#configure-aws-authentication
                sid="cloudwatchPermissions",
                actions=[
                    # Allow writing logs to cloud watch
                    "logs:PutLogEvents",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                ],
                resources=["*"],
            )
        )
