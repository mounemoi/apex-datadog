# -*- coding: utf-8 -*-
import os
import re
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
    agent = EC2CPUCredit(config)
    agent.check()


class EC2CPUCredit():
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
        ec2_list = []
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
                    iid   = instance['InstanceId']
                    itype = instance['InstanceType']

                    if re.search('^t2\.', itype):
                        ec2_list.append({ 'name': name, 'instance_id': iid, 'environment': environment, 'type': itype })

            if 'NextToken' in instances:
                next_token = instances['NextToken']
            else:
                break

        cloudwatch = session.client('cloudwatch')
        for ec2 in ec2_list:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='CPUCreditBalance',
                Dimensions=[ { 'Name': 'InstanceId', 'Value': ec2['instance_id'] } ],
                StartTime=datetime.datetime.utcnow() - datetime.timedelta(minutes=30),
                EndTime=datetime.datetime.utcnow(),
                Period=300,
                Statistics=[ 'Minimum' ],
                Unit='Count',
            )

            if len(response['Datapoints']) != 0:
                metrics.append({
                    'host'  : 'dummy.example.com',
                    'metric': self.__metrics_name,
                    'points': (now, sorted(response['Datapoints'], key=lambda k: k['Timestamp'])[-1]['Minimum']),
                    'tags'  : [
                        'ac-name:{name}'.format(**ec2),
                        'ac-environment:{environment}'.format(**ec2),
                        'ac-type:{type}'.format(**ec2),
                    ],
                })

        datadog.api.Metric.send(metrics)
