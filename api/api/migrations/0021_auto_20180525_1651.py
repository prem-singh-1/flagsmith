# -*- coding: utf-8 -*-
# Generated by Django 1.11.13 on 2018-05-25 16:51
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0020_auto_20180525_1541'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='feature',
            name='project',
        ),
        migrations.AlterUniqueTogether(
            name='featurestate',
            unique_together=set([]),
        ),
        migrations.RemoveField(
            model_name='featurestate',
            name='environment',
        ),
        migrations.RemoveField(
            model_name='featurestate',
            name='feature',
        ),
        migrations.RemoveField(
            model_name='featurestate',
            name='identity',
        ),
        migrations.DeleteModel(
            name='Feature',
        ),
        migrations.DeleteModel(
            name='FeatureState',
        ),
    ]