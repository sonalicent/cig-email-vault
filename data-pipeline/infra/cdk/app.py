#!/usr/bin/env python3

import aws_cdk as cdk

from stack import EmailPipelineStack


app = cdk.App()

EmailPipelineStack(
    app,
    "CigEmailPipelineStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region"),
    ),
)

app.synth()