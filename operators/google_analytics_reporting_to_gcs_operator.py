import json
from datetime import datetime
from tempfile import NamedTemporaryFile

from airflow.contrib.hooks.gcs_hook import GoogleCloudStorageHook
from airflow.models import BaseOperator

from hooks.google_analytics_hook import GoogleAnalyticsHook


class GoogleAnalyticsReportingToGCSOperator(BaseOperator):
    """
    Google Analytics Reporting To GCS Operator

    :param google_analytics_conn_id:    The Google Analytics connection id.
    :type google_analytics_conn_id:     string
    :param view_id:                     The view id for associated report.
    :type view_id:                      string/array
    :param since:                       The date up from which to pull GA data.
                                        This can either be a string in the format
                                        of '%Y-%m-%d %H:%M:%S' or '%Y-%m-%d'
                                        but in either case it will be
                                        passed to GA as '%Y-%m-%d'.
    :type since:                        string
    :param until:                       The date up to which to pull GA data.
                                        This can either be a string in the format
                                        of '%Y-%m-%d %H:%M:%S' or '%Y-%m-%d'
                                        but in either case it will be
                                        passed to GA as '%Y-%m-%d'.
    :type until:                        string
    :param gcs_conn_id:                  The GCS connection id.
    :type gcs_conn_id:                   string
    :param gcs_bucket:                   The GCS bucket to be used to store
                                        the Google Analytics data.
    :type gcs_bucket:                    string
    :param gcs_key:                      The GCS key to be used to store
                                        the data.
    :type gcs_key:                       string
    """

    template_fields = ('gcs_key',
                       'since',
                       'until')

    def __init__(self,
                 google_analytics_conn_id,
                 view_id,
                 since,
                 until,
                 dimensions,
                 metrics,
                 gcs_conn_id,
                 gcs_bucket,
                 gcs_objname,
                 page_size=1000,
                 include_empty_rows=True,
                 sampling_level=None,
                 *args,
                 **kwargs):
        super().__init__(*args, **kwargs)

        self.google_analytics_conn_id = google_analytics_conn_id
        self.view_id = view_id
        self.since = since
        self.until = until
        self.sampling_level = sampling_level
        self.dimensions = dimensions
        self.metrics = metrics
        self.page_size = page_size
        self.include_empty_rows = include_empty_rows
        self.gcs_conn_id = gcs_conn_id
        self.gcs_bucket = gcs_bucket
        self.gcs_objname = gcs_objname

        self.metricMap = {
            'METRIC_TYPE_UNSPECIFIED': 'varchar(255)',
            'CURRENCY': 'decimal(20,5)',
            'INTEGER': 'int(11)',
            'FLOAT': 'decimal(20,5)',
            'PERCENT': 'decimal(20,5)',
            'TIME': 'time'
        }

        if self.page_size > 10000:
            raise Exception('Please specify a page size equal to or lower than 10000.')

        if not isinstance(self.include_empty_rows, bool):
            raise Exception('Please specificy "include_empty_rows" as a boolean.')

    def execute(self, context):
        ga_conn = GoogleAnalyticsHook(self.google_analytics_conn_id)
        gcs_conn = GoogleCloudStorageHook(self.gcs_conn_id)
        try:
            since_formatted = datetime.strptime(self.since, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
        except:
            since_formatted = str(self.since)
        try:
            until_formatted = datetime.strptime(self.until, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
        except:
            until_formatted = str(self.until)
        report = ga_conn.get_analytics_report(self.view_id,
                                              since_formatted,
                                              until_formatted,
                                              self.sampling_level,
                                              self.dimensions,
                                              self.metrics,
                                              self.page_size,
                                              self.include_empty_rows)

        columnHeader = report.get('columnHeader', {})
        # Right now all dimensions are hardcoded to varchar(255), will need a map if any non-varchar dimensions are used in the future
        # Unfortunately the API does not send back types for Dimensions like it does for Metrics (yet..)
        dimensionHeaders = [
            {'name': header.replace('ga:', ''), 'type': 'varchar(255)'}
            for header
            in columnHeader.get('dimensions', [])
        ]
        metricHeaders = [
            {'name': entry.get('name').replace('ga:', ''),
             'type': self.metricMap.get(entry.get('type'), 'varchar(255)')}
            for entry
            in columnHeader.get('metricHeader', {}).get('metricHeaderEntries', [])
        ]

        with NamedTemporaryFile("w") as ga_file:
            rows = report.get('data', {}).get('rows', [])

            for row_counter, row in enumerate(rows):
                root_data_obj = {}
                dimensions = row.get('dimensions', [])
                metrics = row.get('metrics', [])

                for index, dimension in enumerate(dimensions):
                    header = dimensionHeaders[index].get('name').lower()
                    root_data_obj[header] = dimension

                for metric in metrics:
                    data = {}
                    data.update(root_data_obj)

                    for index, value in enumerate(metric.get('values', [])):
                        header = metricHeaders[index].get('name').lower()
                        data[header] = value

                    data['viewid'] = self.view_id
                    data['timestamp'] = self.since

                    ga_file.write(json.dumps(data) + ('' if row_counter == len(rows) else '\n'))

            gcs_conn.upload(self.gcs_bucket,
                            self.gcs_objname,
                            ga_file.name)
