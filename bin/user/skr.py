import Queue
import re
import sys
import syslog
import time
import urllib
import urllib2

import weewx
import weewx.restx
import weewx.units
import weewx.wxformulas
from weeutil.weeutil import to_bool, accumulateLeaves

VERSION = '0.3'

if weewx.__version__ < '3':
    raise weewx.UnsupportedFeature('weewx 3 is the minimum required, found %s' % weewx.__version__)


def log_msg(level, msg):
    """
    Shows a log message

    :param level:
    :param msg:
    """
    syslog.syslog(level, 'restx: Skiron: %s' % msg)


def log_dbg(msg):
    """
    Shows a log debug message

    :param msg:
    """
    log_msg(syslog.LOG_DEBUG, msg)


def log_inf(msg):
    """
    Shows a log info message

    :param msg:
    """
    log_msg(syslog.LOG_INFO, msg)


def log_err(msg):
    """
    Shows a log error message

    :param msg:
    """
    log_msg(syslog.LOG_ERR, msg)


def _invert(x):
    if x is None:
        return None
    elif x == 0:
        return 1
    return 0


def _convert_wind_speed(v, from_unit_system):
    """
    Utility to convert to METRICWX wind speed

    :param v:
    :param from_unit_system:
    :return:
    """
    if from_unit_system is None:
        return None

    if from_unit_system != weewx.METRICWX:
        (from_unit, _) = weewx.units.getStandardUnitType(from_unit_system, 'windSpeed')
        from_t = (v, from_unit, 'group_speed')
        v = weewx.units.convert(from_t, 'meter_per_second')[0]

    return v


def _calc_thw(heat_index_c, wind_speed_mps):
    """
    :param heat_index_c:
    :param wind_speed_mps:
    :return:
    """
    if heat_index_c is None or wind_speed_mps is None:
        return None

    wind_speed_mph = 2.25 * wind_speed_mps
    heat_index_f = 32 + heat_index_c * 9 / 5
    thw_f = heat_index_f - (1.072 * wind_speed_mph)
    thw_c = (thw_f - 32) * 5 / 9

    return thw_c


def _get_wind_avg(dbm, ts, interval=600):
    """
    Utility to convert to METRICWX wind average

    :param dbm:
    :param ts:
    :param interval:
    :return:
    """
    sts = ts - interval

    val = dbm.getSql(
        "SELECT AVG(windSpeed) FROM %s "
        "WHERE dateTime>? AND dateTime<=?" % dbm.table_name,
        (sts, ts)
    )

    return val[0] if val is not None else None


def _get_wind_hi(dbm, ts, interval=600):
    """
    Utility to convert to METRICWX wind high intensity

    :param dbm:
    :param ts:
    :param interval:
    :return:
    """
    sts = ts - interval

    val = dbm.getSql(
        """SELECT
 MAX(CASE WHEN windSpeed >= windGust THEN windSpeed ELSE windGust END)
 FROM %s
 WHERE dateTime>? AND dateTime<=?""" % dbm.table_name, (sts, ts)
    )

    return val[0] if val is not None else None


def _get_wind_dir_avg(dbm, ts, interval=600):
    """
    Utility to convert to METRICWX wind direction average

    :param dbm:
    :param ts:
    :param interval:
    :return:
    """
    sts = ts - interval

    val = dbm.getSql(
        "SELECT AVG(windDir) FROM %s "
        "WHERE dateTime>? AND dateTime<=?" %
        dbm.table_name, (sts, ts)
    )

    return val[0] if val is not None else None


def _get_site_dict(config_dict, service, *args):
    """Obtain the site options, with defaults from the StdRESTful section.
    If the service is not enabled, or if one or more required parameters is
    not specified, then return None."""

    try:
        site_dict = accumulateLeaves(config_dict['StdRESTful'][service], max_level=1)
    except KeyError:
        log_err("restx: %s: no config info. Skipped." % service)

        return None

    try:
        if not to_bool(site_dict['enabled']):
            log_inf("restx: %s: service not enabled." % service)
    except KeyError:
        pass

    try:
        for option in args:
            if site_dict[option] == 'replace_me':
                raise KeyError(option)
    except KeyError, e:
        log_dbg("restx: %s. Data will not be posted: missing option %s" % (service, e))

        return None

    # Get logging preferences from the root level
    if config_dict.get('log_success') is not None:
        site_dict.setdefault('log_success', config_dict.get('log_success'))

    if config_dict.get('log_failure') is not None:
        site_dict.setdefault('log_failure', config_dict.get('log_failure'))

    # Get rid of the no longer needed key 'enabled':
    site_dict.pop('enabled', None)

    return site_dict


class Skiron(weewx.restx.StdRESTbase):
    """ Upload data to Skiron - http://www.skiron.io

    To enable this module, add the following lines to weewx.conf:

    [StdRESTful]
        [[Skiron]]
            enabled    = True
            cloud_id   = CLOUD_ID
            cloud_key  = CLOUD_KEY

    The skiron server expects a single string of values. The first value
    position is the cloud id and the second is the cloud key.

    """

    def __init__(self, engine, config_dict):
        super(Skiron, self).__init__(engine, config_dict)

        log_inf("Service version is %s" % VERSION)

        site_dict = _get_site_dict(config_dict, 'Skiron', 'cloud_id', 'cloud_key')

        if site_dict is None:
            return

        site_dict['manager_dict'] = weewx.manager.get_manager_dict(
            config_dict['DataBindings'],
            config_dict['Databases'],
            'wx_binding'
        )

        self.archive_queue = Queue.Queue()
        self.archive_thread = SkironThread(self.archive_queue, **site_dict)
        self.archive_thread.start()
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)
        log_inf("Data will be uploaded for cloud_id = %s" % site_dict['cloud_id'])

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)


class SkironThread(weewx.restx.RESTThread):
    _SERVER_URL = 'http://skiron.io/api/weather-station/store'

    _DATA_MAP = {
        'temp':        ('outTemp', '%.0f', 10.0),
        'hum':         ('outHumidity', '%.0f', 1.0),
        'wdir':        ('windDir', '%.0f', 1.0),
        'wspd':        ('windSpeed', '%.0f', 10.0),
        'bar':         ('barometer', '%.0f', 10.0),
        'rain':        ('dayRain', '%.0f', 10.0),
        'rainrate':    ('rainRate', '%.0f', 10.0),
        'tempin':      ('inTemp', '%.0f', 10.0),
        'humin':       ('inHumidity', '%.0f', 1.0),
        'uvi':         ('UV', '%.0f', 10.0),
        'solarrad':    ('radiation', '%.0f', 10.0),
        'et':          ('ET', '%.0f', 10.0),
        'chill':       ('windchill', '%.0f', 10.0),
        'heat':        ('heatindex', '%.0f', 10.0),
        'dew':         ('dewpoint', '%.0f', 10.0),
        'battery':     ('consBatteryVoltage', '%.0f', 100.0),
        'temp01':      ('extraTemp1', '%.0f', 10.0),
        'temp02':      ('extraTemp2', '%.0f', 10.0),
        'temp03':      ('extraTemp3', '%.0f', 10.0),
        'temp04':      ('leafTemp1', '%.0f', 10.0),
        'temp05':      ('leafTemp2', '%.0f', 10.0),
        'temp06':      ('soilTemp1', '%.0f', 10.0),
        'temp07':      ('soilTemp2', '%.0f', 10.0),
        'temp08':      ('soilTemp3', '%.0f', 10.0),
        'temp09':      ('soilTemp4', '%.0f', 10.0),
        'temp10':      ('heatingTemp4', '%.0f', 10.0),
        'leafwet01':   ('leafWet1', '%.0f', 1.0),
        'leafwet02':   ('leafWet2', '%.0f', 1.0),
        'hum01':       ('extraHumid1', '%.0f', 1.0),
        'hum02':       ('extraHumid2', '%.0f', 1.0),
        'soilmoist01': ('soilMoist1', '%.0f', 1.0),
        'soilmoist02': ('soilMoist2', '%.0f', 1.0),
        'soilmoist03': ('soilMoist3', '%.0f', 1.0),
        'soilmoist04': ('soilMoist4', '%.0f', 1.0),
        'wspdhi':      ('windhi', '%.0f', 10.0),
        'wspdavg':     ('windavg', '%.0f', 10.0),
        'wdiravg':     ('winddiravg', '%.0f', 1.0),
        'heatin':      ('inheatindex', '%.0f', 10.0),
        'dewin':       ('indewpoint', '%.0f', 10.0),
        'battery01':   ('bat01', '%.0f', 1.0),
        'battery02':   ('bat02', '%.0f', 1.0),
        'battery03':   ('bat03', '%.0f', 1.0),
        'battery04':   ('bat04', '%.0f', 1.0),
        'battery05':   ('bat05', '%.0f', 1.0),
    }

    def __init__(self, queue, cloud_id, cloud_key, manager_dict,
                 server_url=_SERVER_URL, post_interval=300,
                 max_backlog=sys.maxint, stale=None,
                 log_success=True, log_failure=True,
                 timeout=60, max_tries=3, retry_wait=5,
                 skip_upload=False):

        super(SkironThread, self).__init__(
            queue,
            protocol_name='Skiron',
            manager_dict=manager_dict,
            post_interval=post_interval,
            max_backlog=max_backlog,
            stale=stale,
            log_success=log_success,
            log_failure=log_failure,
            max_tries=max_tries,
            timeout=timeout,
            retry_wait=retry_wait,
            skip_upload=skip_upload
        )

        self.cloud_id = cloud_id
        self.cloud_key = cloud_key
        self.server_url = server_url

    def process_record(self, record, dbm):
        """
        Process the records for server

        :param record:
        :param dbm:
        """
        r = self.get_record(record, dbm)
        url = self.get_url(r)

        headers = {
            "User-Agent": "weewx",
            "version":    weewx.__version__,
            "cloud_id":   self.cloud_id,
            "cloud_key":  self.cloud_key
        }

        req = urllib2.Request(url, None, headers)
        self.post_with_retries(req)

    def get_record(self, record, dbm):
        """
        Get all records for server

        :param record:
        :param dbm:
        :return:
        """
        rec = super(SkironThread, self).get_record(record, dbm)

        # units required by skiron
        rec = weewx.units.to_METRICWX(rec)

        rec['windavg'] = _get_wind_avg(dbm, record['dateTime'])
        rec['windhi'] = _get_wind_hi(dbm, record['dateTime'])
        rec['winddiravg'] = _get_wind_dir_avg(dbm, record['dateTime'])

        # this checks that wind direction is always between 0 and 359
        if 'windDir' in rec and rec['windDir'] > 359:
            rec['windDir'] -= 360

        if rec['winddiravg'] > 359:
            rec['winddiravg'] -= 360

        rec['windavg'] = _convert_wind_speed(rec['windavg'], record['usUnits'])
        rec['windhi'] = _convert_wind_speed(rec['windhi'], record['usUnits'])

        if 'inTemp' in rec and 'inHumidity' in rec:
            rec['inheatindex'] = weewx.wxformulas.heatindexC(rec['inTemp'], rec['inHumidity'])
            rec['indewpoint'] = weewx.wxformulas.dewpointC(rec['inTemp'], rec['inHumidity'])

        if 'heatindex' in rec and 'windSpeed' in rec:
            rec['thw'] = _calc_thw(rec['heatindex'], rec['windSpeed'])

        if 'txBatteryStatus' in record:
            rec['bat01'] = _invert(record['txBatteryStatus'])

        if 'windBatteryStatus' in record:
            rec['bat02'] = _invert(record['windBatteryStatus'])

        if 'rainBatteryStatus' in record:
            rec['bat03'] = _invert(record['rainBatteryStatus'])

        if 'outTempBatteryStatus' in record:
            rec['bat04'] = _invert(record['outTempBatteryStatus'])

        if 'inTempBatteryStatus' in record:
            rec['bat05'] = _invert(record['inTempBatteryStatus'])

        return rec

    def get_url(self, record):
        # put data into expected structure and format
        """
        Builds the url

        :param record:
        :return:
        """
        time_tt = time.gmtime(record['dateTime'])

        values = {
            'ver':   str(weewx.__version__),
            'c_id':  self.cloud_id,
            'c_key': self.cloud_key,
            'time':  time.strftime("%H%M", time_tt),
            'date':  time.strftime("%Y%m%d", time_tt)
        }

        for key in self._DATA_MAP:
            r_key = self._DATA_MAP[key][0]

            if r_key in record and record[r_key] is not None:
                v = record[r_key] * self._DATA_MAP[key][2]

                values[key] = self._DATA_MAP[key][1] % v

        url = self.server_url + '?' + urllib.urlencode(values)

        if weewx.debug >= 2:
            log_dbg('url: %s' % re.sub(r"key=[^\&]*", "key=XXX", url))

        return url
