from typing import Dict, Optional, Sequence, Union

from constructs import Construct
from aws_cdk import (
    Duration,
    Stack,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_efs as efs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
)

from .settings import Settings, GrafanaRoles


EcsEnv = Dict[str, Union[str, ecs.Secret]]


def envify(grafana_key: str) -> str:
    """
    Convert a Grafana config value to a Grafana-friendly environment variable.
    https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#override-configuration-with-environment-variables
    """
    return f"GF_{grafana_key.replace('.', '_').upper()}"


class GrafanaStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        settings: Settings,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Apply global permissions boundary
        boundary = iam.ManagedPolicy.from_managed_policy_arn(
            self, "Boundary", settings.permissions_boundary_arn
        )
        iam.PermissionsBoundary.of(self).apply(boundary)

        vpc = ec2.Vpc.from_lookup(self, "vpc", vpc_id=settings.vpc_id)


        container_name = "grafana"

        service = self.build_service(
            vpc=vpc,
            container_name=container_name,
            cluster_name=settings.grafana_stack_name
        )

        container = service.task_definition.find_container(container_name)

        # Create durable storage to hold state across deployments
        mount_point = self.add_efs_mount(
            vpc=vpc,
            service=service.service,
            container=container,
            container_path="/gf-data",
        )

        # Add Cloudfront Distribution
        distro = self.create_cloudfront_distribution(
            lb=service.load_balancer,
            domain_name=settings.grafana_domain_name,
            certificate_arn=settings.grafana_certificate_arn,
        )

        # Add environment variables to container
        env: EcsEnv = {
            envify("paths.data"): mount_point.container_path,
            envify("server.root_url"): (
                f"https://{settings.grafana_domain_name}" 
                if settings.grafana_domain_name 
                else f"https://{distro.distribution_domain_name}"
            ),
        }
        if settings.github_oauth_secret_name:
            env.update(
                self.github_oauth_settings(
                    allowed_orgs=settings.github_allowed_orgs,
                    admin_group=settings.github_admin_group,
                    editor_group=settings.github_editor_group,
                    default_role=settings.default_user_role,
                    oauth_secret_name=settings.github_oauth_secret_name,
                )
            )
        for k, v in env.items():
            if isinstance(v, ecs.Secret):
                container.add_secret(k, v)
            else:
                container.add_environment(k, v)




    def build_service(
        self,
        vpc: ec2.Vpc,
        cluster_name: str,
        container_name: str
    ):
        # Production has a public NAT Gateway subnet, which causes the
        # default load balancer creation to fail with too many subnets
        # being selected per AZ. We create our own load balancer to
        # allow us to select subnets and avoid the issue.
        load_balancer: elbv2.ApplicationLoadBalancer = elbv2.ApplicationLoadBalancer(
            self,
            "load-balancer",
            vpc=vpc,
            internet_facing=True,
            vpc_subnets=ec2.SubnetSelection(
                one_per_az=True,
                subnet_type=ec2.SubnetType.PUBLIC
            ),
        )
        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "Service",
            cluster=ecs.Cluster(
                self,
                "cluster",
                cluster_name=cluster_name,
                vpc=vpc,
            ),
            load_balancer=load_balancer,
            task_subnets=ec2.SubnetSelection(
                one_per_az=True,
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
            ),
            service_name="grafana",
            desired_count=1,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_asset("grafana"),
                container_name=container_name,
                container_port=3000,
            ),
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64
            ),
        )

        # Setup health check with Grafana endpoint, give long timeout to support boot-up
        service.target_group.configure_health_check(
            path="/api/health",
            timeout=Duration.seconds(30),
            interval=Duration.seconds(60),
        )

        # Ensure service can interact with other AWS resources
        for policy in (
            iam.PolicyStatement(
                # https://grafana.com/grafana/plugins/grafana-x-ray-datasource
                sid="xrayPermissions",
                actions=[
                    "xray:BatchGetTraces",
                    "xray:GetTraceSummaries",
                    "xray:GetTraceGraph",
                    "xray:GetGroups",
                    "xray:GetTimeSeriesServiceStatistics",
                    "xray:GetInsightSummaries",
                    "xray:GetInsight",
                    "xray:GetServiceGraph",
                    "ec2:DescribeRegions",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                # https://grafana.com/docs/grafana/latest/datasources/aws-cloudwatch/#configure-aws-authentication
                sid="cloudwatchPermissions",
                actions=[
                    # Allow reading metrics from cloud watch
                    "cloudwatch:DescribeAlarmsForMetric",
                    "cloudwatch:DescribeAlarmHistory",
                    "cloudwatch:DescribeAlarms",
                    "cloudwatch:ListMetrics",
                    "cloudwatch:GetMetricData",
                    "cloudwatch:GetInsightRuleReport",
                    # Allow reading logs from cloud watch
                    "logs:DescribeLogGroups",
                    "logs:GetLogGroupFields",
                    "logs:StartQuery",
                    "logs:StopQuery",
                    "logs:GetQueryResults",
                    "logs:GetLogEvents",
                    # Allow reading tags instances regions from ec2
                    "ec2:DescribeTags",
                    "ec2:DescribeInstances",
                    "ec2:DescribeRegions",
                    # Allow reading resources for tags
                    "tag:GetResources",
                    "athena:*",
                    "glue:*",
                    "s3:*"
                ],
                resources=["*"],
            ),
        ):
            service.task_definition.add_to_task_role_policy(policy)

        return service

    def add_efs_mount(
        self,
        vpc: ec2.Vpc,
        service: ecs.FargateService,
        container: ecs.ContainerDefinition,
        container_path: str,
    ) -> ecs.MountPoint:
        # Create EFS FileSystem for persistent storage of app state (dashboards, users, etc)
        file_system = efs.FileSystem(
            self,
            "FileSystem",
            vpc=vpc,
            encrypted=True,
        )

        # Create Access Point
        access_point = file_system.add_access_point(
            "access_point",
            path=container_path,
            create_acl=efs.Acl(
                # This is the Grafana user id as per docker image layers:
                # https://hub.docker.com/layers/grafana/grafana/latest/images/sha256-40aaa21a9f7602816b754eb293139c3173629b83829faf1f510e19f76e486e41?context=explore
                owner_uid="472",
                owner_gid="472",
                permissions="700",
            ),
        )

        # Add Volume to Container
        volume_name = "grafana_storage"
        container.task_definition.add_volume(
            name=volume_name,
            efs_volume_configuration=ecs.EfsVolumeConfiguration(
                file_system_id=file_system.file_system_id,
                transit_encryption="ENABLED",
                authorization_config=ecs.AuthorizationConfig(
                    access_point_id=access_point.access_point_id
                ),
            ),
        )

        # Attach MountPoint to container
        mount_point = ecs.MountPoint(
            container_path=container_path,
            read_only=False,
            source_volume=volume_name,
        )
        container.add_mount_points(mount_point)

        # Allow container to use access point & file system
        container.task_definition.add_to_execution_role_policy(
            iam.PolicyStatement(
                actions=[
                    "elasticfilesystem:ClientMount",
                    "elasticfilesystem:ClientWrite",
                    "elasticfilesystem:ClientRootAccess",
                ],
                resources=[access_point.access_point_arn, file_system.file_system_arn],
            )
        )

        file_system.connections.allow_default_port_from(service.connections)

        return mount_point

    def create_cloudfront_distribution(
        self,
        lb: elbv2.ILoadBalancerV2,
        domain_name: Optional[str] = None,
        certificate_arn: Optional[str] = None,
    ):
        return cloudfront.Distribution(
            self,
            "CloudFrontDistribution",
            comment=self.stack_name,
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.LoadBalancerV2Origin(
                    origin_id="grafana",
                    load_balancer=lb,
                    protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
                ),
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_AND_CLOUDFRONT_2022,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            ),
            domain_names=[domain_name] if domain_name else [],
            certificate=(
                acm.Certificate.from_certificate_arn(
                    self, "Certificate", certificate_arn
                )
                if certificate_arn
                else None
            ),
        )
    def github_oauth_settings(
        self,
        allowed_orgs: Sequence[str],
        admin_group: Optional[str],
        editor_group: Optional[str],
        default_role: GrafanaRoles,
        oauth_secret_name: str,
    ) -> EcsEnv:
        """
        Generate settings to configure Grafana to authenticate with Github OAuth application
        """
        oauth_details = secretsmanager.Secret.from_secret_name_v2(
            self,
            "oauth-secret-gh",
            oauth_secret_name,
        )
        role_attr_path = (
            # Admin Group
            f"contains(groups[*], {admin_group!r}) && {GrafanaRoles.grafana_admin.value!r} "
            +
            # Editor Group
            (
                f"|| contains(groups[*], {editor_group!r}) && {GrafanaRoles.editor.value!r} "
                if editor_group
                else ""
            )
            +
            # Default Role
            f"|| {default_role.value!r}"
        )
        github_settings: EcsEnv = {
            # Customized
            "allowed_organizations": ",".join(allowed_orgs),
            "role_attribute_path": role_attr_path,
            "client_id": ecs.Secret.from_secrets_manager(
                oauth_details,
                "client_id",
            ),
            "client_secret": ecs.Secret.from_secrets_manager(
                oauth_details,
                "client_secret",
            ),
            # Standard
            "enabled": "true",
            "auto_login": "true",
            "auth_url": "https://github.com/login/oauth/authorize",
            "token_url": "https://github.com/login/oauth/access_token",
            "api_url": "https://api.github.com/user",
        }
        return {
            envify(f"auth.github.{key}"): value
            for key, value in github_settings.items()
        }
