# -*- coding: utf-8 -*-
import os
import time
import datadog
from boto3.session import Session


def handle(event, context):
    config = {
        'dd_api_key'     : os.environ['DD_API_KEY'],
        'dd_app_key'     : os.environ['DD_APP_KEY'],
        'metrics_prefix' : os.environ['METRICS_PREFIX'],
        'region'         : os.environ['REGION'],
    }
    agent = AwsEc2Cost(config)
    agent.check()


class EC2InstancePrice():
    # EC2 の 1 時間あたりの価格(USD)
    # - Jun 22, 2017 時点
    # - PriceListAPI に置き換えたい
    #    - http://docs.aws.amazon.com/ja_jp/awsaccountbilling/latest/aboutv2/price-changes.html
    # - 値は RI 1year All Upfront の時間毎の料金
    __price = {
        "c1.medium"   : 0.106,
        "c1.xlarge"   : 0.427,
        "c3.2xlarge"  : 0.344,
        "c3.4xlarge"  : 0.687,
        "c3.8xlarge"  : 1.374,
        "c3.large"    : 0.086,
        "c3.xlarge"   : 0.172,
        "c4.2xlarge"  : 0.338,
        "c4.4xlarge"  : 0.675,
        "c4.8xlarge"  : 1.350,
        "c4.large"    : 0.084,
        "c4.xlarge"   : 0.169,
        "cc2.8xlarge" : 1.267,
        "cr1.8xlarge" : 1.748,
        "d2.2xlarge"  : 0.886,
        "d2.4xlarge"  : 1.772,
        "d2.8xlarge"  : 3.543,
        "d2.xlarge"   : 0.443,
        "g2.2xlarge"  : 0.587,
        "g2.8xlarge"  : 2.347,
        "hi1.4xlarge" : 1.573,
        "hs1.8xlarge" : 2.725,
        "i2.2xlarge"  : 1.044,
        "i2.4xlarge"  : 2.086,
        "i2.8xlarge"  : 4.174,
        "i2.xlarge"   : 0.522,
        "i3.16xlarge" : 3.730,
        "i3.2xlarge"  : 0.466,
        "i3.4xlarge"  : 0.933,
        "i3.8xlarge"  : 1.865,
        "i3.large"    : 0.117,
        "i3.xlarge"   : 0.233,
        "m1.large"    : 0.129,
        "m1.medium"   : 0.065,
        "m1.small"    : 0.032,
        "m1.xlarge"   : 0.256,
        "m2.2xlarge"  : 0.255,
        "m2.4xlarge"  : 0.507,
        "m2.xlarge"   : 0.127,
        "m3.2xlarge"  : 0.436,
        "m3.large"    : 0.109,
        "m3.medium"   : 0.054,
        "m3.xlarge"   : 0.218,
        "m4.10xlarge" : 1.627,
        "m4.16xlarge" : 2.603,
        "m4.2xlarge"  : 0.325,
        "m4.4xlarge"  : 0.651,
        "m4.large"    : 0.081,
        "m4.xlarge"   : 0.163,
        "r3.2xlarge"  : 0.509,
        "r3.4xlarge"  : 1.018,
        "r3.8xlarge"  : 2.036,
        "r3.large"    : 0.127,
        "r3.xlarge"   : 0.255,
        "r4.16xlarge" : 3.011,
        "r4.2xlarge"  : 0.376,
        "r4.4xlarge"  : 0.753,
        "r4.8xlarge"  : 1.505,
        "r4.large"    : 0.094,
        "r4.xlarge"   : 0.188,
        "t1.micro"    : 0.016,
        "t2.2xlarge"  : 0.370,
        "t2.large"    : 0.092,
        "t2.medium"   : 0.046,
        "t2.micro"    : 0.012,
        "t2.nano"     : 0.006,
        "t2.small"    : 0.024,
        "t2.xlarge"   : 0.185,
        "x1.16xlarge" : 5.562,
        "x1.32xlarge" : 11.123,
    }

    # 為替レート JPY/USD
    __jpy_usd = 110

    @classmethod
    def get(cls, itype):
        if itype not in cls.__price:
            raise TypeError('unknown instance type : {}'.format(itype))

        return cls.__price[itype] * cls.__jpy_usd * 24 * 365


class InstanceCounter():
    def __init__(self, tag_keys=[]):
        self.__tag_keys = tag_keys
        self.__instances = {}

    def __tag_encode(self, tags):
        tag_text = ''
        for key in self.__tag_keys:
            if key not in tags:
                raise TypeError('undefined tag value : {}'.format(key))
            tag_text += "{}\t".format(tags[key])

        return tag_text

    def __tag_decode(self, tag_text):
        tag_values = tag_text.split("\t")
        tags = {}
        for key in self.__tag_keys:
            tags[key] = tag_values.pop(0)

        return tags

    def incr_count(self, tags):
        tag_text = self.__tag_encode(tags)
        if tag_text not in self.__instances:
            self.__instances[tag_text] = 1
        else:
            self.__instances[tag_text] += 1

        return self.__instances[tag_text]

    def dump(self):
        instances = []
        for tag_text in self.__instances:
            instance = self.__tag_decode(tag_text)
            instance['count'] = self.__instances[tag_text]
            instances.append(instance)

        return instances


class InstanceFetcher():
    def __init__(self, region):
        session = Session(region_name=region)
        self.__ec2 = session.client('ec2')

    def get_running_instances(self, tag_keys):
        instances = InstanceCounter(tag_keys + ['type'])

        next_token = ''
        while True:
            running_instances = self.__ec2.describe_instances(
                Filters=[
                    { 'Name' : 'instance-state-name', 'Values' : [ 'running' ] },
                    { 'Name' : 'tenancy',             'Values' : [ 'default' ] },
                ],
                MaxResults=100,
                NextToken=next_token,
            )

            for reservation in running_instances['Reservations']:
                for running_instance in reservation['Instances']:
                    # exclude SpotInstance
                    if 'SpotInstanceRequestId' in running_instance:
                        continue
                    # exclude not 'Linux/UNIX' Platform
                    if 'Platform' in running_instance:
                        continue

                    tags = {}
                    if 'Tags' in running_instance:
                        for tag in running_instance['Tags']:
                            for key in tag_keys:
                                if tag['Key'] == key:
                                    tags[key] = tag['Value']
                                    break

                    tags['type'] = running_instance['InstanceType']
                    instances.incr_count(tags)

            if 'NextToken' in running_instances:
                next_token = running_instances['NextToken']
            else:
                break

        return instances


class AwsEc2Cost():
    def __init__(self, config):
        dd_options = {
            'api_key': config['dd_api_key'],
            'app_key': config['dd_app_key'],
        }
        datadog.initialize(**dd_options)

        self.__region         = config['region']
        self.__metrics_prefix = config['metrics_prefix']

        self.__include_tags = [ 'Function', 'CategoryName', 'Environment' ]

        self.__metrics = []

    def check(self):
        self.__now = time.time()

        fetcher = InstanceFetcher(self.__region)
        instances = fetcher.get_running_instances(self.__include_tags)

        for instance in instances.dump():
            tags = []
            for key in ['type'] + self.__include_tags:
                tags.append('ac-{}:{}'.format(key, instance[key]))

            self.__set_gauge('count', instance['count'], tags)
            self.__set_gauge('price', EC2InstancePrice.get(instance['type']) * instance['count'], tags)

        self.__send_metrics()

    def __set_gauge(self, metric, value, tags):
        metric = self.__metrics_prefix + '.' + metric
        self.__metrics.append({
            'metric': metric,
            'points': (self.__now, value),
            'tags'  : tags,
        })

    def __send_metrics(self):
        datadog.api.Metric.send(self.__metrics)
