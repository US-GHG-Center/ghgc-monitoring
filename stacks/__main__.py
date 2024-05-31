import aws_cdk as cdk

from .grafana import GrafanaStack
from .otel import OtelStack
from .settings import Settings

settings = Settings()

app = cdk.App()

GrafanaStack(
    app,
    construct_id=settings.grafana_stack_name,
    settings=settings,
    env=settings.env,
)

OtelStack(
    app,
    construct_id=settings.otel_stack_name,
    settings=settings,
    env=settings.env,
)

app.synth()
