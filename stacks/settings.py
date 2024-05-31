from enum import Enum
import os
from typing import List, Optional
from getpass import getuser

import aws_cdk
from pydantic import Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GrafanaRoles(str, Enum):
    viewer = "Viewer"
    editor = "Editor"
    admin = "Admin"
    grafana_admin = "GrafanaAdmin"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("DOTENV", ".env"),
        extra="ignore",
    )

    stage: str = Field(
        description="Unique identifier for this deployment, e.g. 'dev', 'prod'",
        default_factory=getuser,
    )

    vpc_id: str

    project: Optional[str] = "GHGC"
    grafana_domain_name: Optional[str] = None

    grafana_certificate_arn: Optional[str] = None

    permissions_boundary_arn: str

    # Github auth provider configuration
    github_oauth_secret_name: Optional[str] = Field(
        None,
        description=(
            "Name of AWS Secrets Manager Secret containing client_id and client_secret "
            "of Github OAuth application"
        ),
        alias="gh_oauth_secret_name",
    )
    github_allowed_orgs: Optional[List[str]] = Field(
        ["nasa-impact"],
        description=(
            "List of comma- or space-separated organizations. User must be a member of "
            "at least one organization to log in. If unset, all Github users will be "
            "granted access. Used when using Github auth provider."
        ),
        alias="gh_allowed_orgs",
    )
    github_admin_group: Optional[str] = Field(
        None,
        description=(
            "Name of Github group. When user is a member of the group, they are granted "
            'the ServerAdmin role. Example: "@my-org/my-group". Used when using Github '
            "auth provider."
        ),
        alias="gh_admin_group",
    )
    github_editor_group: Optional[str] = Field(
        None,
        description=(
            "Name of Github group. When user is a member of the group, they are granted "
            'the Editor role. Example: "@my-org/my-group". Used when using Github '
            "auth provider."
        ),
        alias="gh_editor_group",
    )
    default_user_role: Optional[GrafanaRoles] = Field(
        GrafanaRoles.viewer,
        description=(
            "Role assigned to users who are not members of the specified Github admin "
            "group. Used when using Github auth provider."
        ),
    )

    grafana_alb_subnet_mask: int = Field(
        description="Subnet mask for the Grafana load balancer. Required to"
        "filter down the number of subnets to one per availability zone",
        default=24,
    )

    namespace_arn: Optional[str] = Field(
        description="ARN of the private namespace to use for service discovery",
        default=None,
    )

    namespace_id: Optional[str] = Field(
        description="ID of the private namespace to use for service discovery",
        default=None,
    )
    namespace_name: str = Field(
        description="Name of the private namespace to use for service discovery",
        default="GHGC.internal",
    )

    honeycomb_api_key: str

    trace_exporters: str = Field(
        description="Where to export trace data in opentelemetry collector",
        default="awsxray",
    )

    # Provided automatically when called with AWS_PROFILE=...
    cdk_deploy_account: Optional[str] = Field(
        ..., default_factory=lambda: os.environ["CDK_DEFAULT_ACCOUNT"]
    )
    cdk_deploy_region: Optional[str] = Field(
        ..., default_factory=lambda: os.environ["CDK_DEFAULT_REGION"]
    )

    def stack_name(self,service: str) -> str:
        return f"{self.project}-{service}-{self.stage}"

    @property
    def grafana_stack_name(self) -> str:
        return self.stack_name("grafana")
    @property
    def private_subnet_ids(self) -> list:
        return [self.private_subnet_id1, self.private_subnet_id2]
    @property
    def otel_stack_name(self) -> str:
        return self.stack_name("otel")

    @property
    def env(self) -> aws_cdk.Environment:
        return aws_cdk.Environment(
            account=self.cdk_deploy_account,
            region=self.cdk_deploy_region,
        )

    @validator("github_allowed_orgs", pre=True)
    def split_comma_separated(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            return [] if v == "" else v.split(",")
        return v
