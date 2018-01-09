# -*- coding: utf-8 -*-
import os
import time
import datadog
from boto3.session import Session
import datetime


def handle(event, context):
    config = {
        'dd_api_key'   : os.environ['DD_API_KEY'],
        'dd_app_key'   : os.environ['DD_APP_KEY'],
        'metrics_name' : os.environ['METRICS_NAME'],
        'region'       : os.environ['REGION'],
    }
    agent = EBSBurstBalance(config)
    agent.check()
    print('END')


class EBSBurstBalance():
    def __init__(self, config):
        dd_options = {
            'api_key': config['dd_api_key'],
            'app_key': config['dd_app_key'],
        }
        datadog.initialize(**dd_options)

        self.__region       = config['region']
        self.__metrics_name = config['metrics_name']

        self.__metrics = []

    def check(self):
        now     = time.time()
        metrics = []

        session = Session(region_name=self.__region)

        ec2 = session.client('ec2')
        next_token = ''
        ebs_list = []
        while True:
            instances = ec2.describe_instances(
                Filters=[
                    { 'Name': 'instance-state-name', 'Values': ['running'] },
                ],
                MaxResults=100,
                NextToken=next_token,
            )

            for reservation in instances['Reservations']:
                for instance in reservation['Instances']:

                    name = ''
                    environment = 'unknown'
                    if 'Tags' in instance:
                        for tag in instance['Tags']:
                            if tag['Key'] == 'Name':
                                name        = tag['Value']
                            if tag['Key'] == 'Environment':
                                environment = tag['Value']

                    if 'BlockDeviceMappings' in instance:
                        for ebs in instance['BlockDeviceMappings']:
                            volume_id = ebs['Ebs']['VolumeId']
                            ebs_list.append({ 'name': name, 'volume_id': volume_id, 'environment': environment })

            if 'NextToken' in instances:
                next_token = instances['NextToken']
            else:
                break

        cloudwatch = session.client('cloudwatch')
        for ebs in ebs_list:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/EBS',
                MetricName='BurstBalance',
                Dimensions=[ { 'Name': 'VolumeId', 'Value': ebs['volume_id'] } ],
                StartTime=datetime.datetime.utcnow() - datetime.timedelta(minutes=30),
                EndTime=datetime.datetime.utcnow(),
                Period=300,
                Statistics=[ 'Minimum' ],
                Unit='Percent',
            )

            value = None
            if len(response['Datapoints']) != 0:
                value = sorted(response['Datapoints'], key=lambda k: k['Timestamp'])[-1]['Minimum']
            else:
                print('{name} : {volume_id} : failure to get'.format(**ebs))
                value = 100.0

            metrics.append({
                'host'  : 'dummy.example.com',
                'metric': self.__metrics_name,
                'points': (now, value),
                'tags'  : [
                    'ac-name:{name}'.format(**ebs),
                    'ac-volume-id:{volume_id}'.format(**ebs),
                    'ac-environment:{environment}'.format(**ebs),
                ],
            })

        datadog.api.Metric.send(metrics)
