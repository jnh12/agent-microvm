"""CloudWatch reads shared by direct CLI and remote backend."""

import datetime as dt

import boto3


def read_logs(identifier, region=None, log_group=None):
    group = log_group or f"/aws/lambda-microvms/{identifier}"
    client = boto3.client("logs", region_name=region)
    streams = client.describe_log_streams(
        logGroupName=group, orderBy="LastEventTime", descending=True, limit=1
    ).get("logStreams", [])
    if not streams:
        return []
    return client.get_log_events(logGroupName=group, logStreamName=streams[0]["logStreamName"]).get(
        "events", []
    )


def read_metrics(identifier, region=None):
    client = boto3.client("cloudwatch", region_name=region)
    end = dt.datetime.now(dt.UTC)
    result = {}
    for metric in ("CPUUtilization", "MemoryUtilization"):
        points = client.get_metric_statistics(
            Namespace="AWS/LambdaMicroVMs",
            MetricName=metric,
            Dimensions=[{"Name": "MicrovmId", "Value": identifier}],
            StartTime=end - dt.timedelta(minutes=30),
            EndTime=end,
            Period=60,
            Statistics=["Average"],
        ).get("Datapoints", [])
        points.sort(key=lambda point: point["Timestamp"])
        result[metric] = points[-1]["Average"] if points else None
    return result
