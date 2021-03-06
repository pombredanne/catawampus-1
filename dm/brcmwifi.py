#!/usr/bin/python
# Copyright 2012 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# TR-069 has mandatory attribute names that don't comply with policy
# pylint:disable=invalid-name

"""Implementation of tr-98/181 WLAN objects for Broadcom Wifi chipsets.

The platform code is expected to set the BSSID (which is really a MAC address).
The Wifi module should be populated with a MAC address. For example if it
appears as eth2, then "ifconfig eth2" will show the MAC address from the Wifi
card. The platform should execute:
  wl bssid xx:xx:xx:xx:xx:xx
To set the bssid to the desired MAC address, either the one from the wifi
card or your own.
"""

__author__ = 'dgentry@google.com (Denton Gentry)'

import collections
import copy
import re
import subprocess
import time
import tr.basemodel
import tr.core
import tr.cwmpbool
import tr.handle
import tr.helpers
import tr.session
import netdev
import wifi

BASE98WIFI = tr.basemodel.InternetGatewayDevice.LANDevice.WLANConfiguration
CATA98WIFI = tr.basemodel.InternetGatewayDevice.LANDevice.WLANConfiguration


# Supported Encryption Modes
EM_NONE = 0
EM_WEP = 1
EM_TKIP = 2
EM_AES = 4
EM_WSEC = 8  # Not enumerated in tr-98
EM_FIPS = 0x80  # Not enumerated in tr-98
EM_WAPI = 0x100  # Not enumerated in tr-98

# Unit tests can override these.
WL_EXE = 'wl'
# Broadcom recommendation for delay while scanning for a channel
WL_AUTOCHAN_SLEEP = 3
WL_RADIO_STATE_MARKER_FILE = '/tmp/wl_radio_on'

# Parameter enumerations
BEACONS = frozenset(['None', 'Basic', 'WPA', '11i', 'BasicandWPA',
                     'Basicand11i', 'WPAand11i', 'BasicandWPAand11i'])
BASICENCRYPTIONS = frozenset(['None', 'WEPEncryption'])
# We do not support EAPAuthentication
BASICAUTHMODES = frozenset(['None', 'SharedAuthentication'])
WPAAUTHMODES = frozenset(['PSKAuthentication'])

# regular expressions
TX_RE = re.compile(r'rate of last tx pkt: (\d+) kbps')
RX_RE = re.compile(r'rate of last rx pkt: (\d+) kbps')
IDLE_RE = re.compile(r'idle (\d+) seconds')


def IntOrZero(value):
  try:
    return int(value)
  except ValueError:
    return 0


class WifiConfig(object):
  """A dumb data object to store config settings."""
  pass


class Wl(object):
  """Class wrapping Broadcom's wl utility.

  This class implements low-level wifi handling, the stuff which tr-98
  and tr-181 can both take advantage of.

  This object cannot retain any state about the Wifi configuration, as
  both tr-98 and tr-181 can have instances of this object. It has to
  consult the wl utility for all state information.
  """

  def __init__(self, interface):
    self._if = interface

  def _SubprocessCall(self, cmd):
    print 'running: %s %s' % (WL_EXE, cmd)
    subprocess.check_call([WL_EXE, '-i', self._if] + cmd)

  def _SubprocessWithOutput(self, cmd):
    print 'running: %s %s' % (WL_EXE, cmd)
    wl = subprocess.Popen([WL_EXE, '-i', self._if] + cmd,
                          stdout=subprocess.PIPE)
    out, _ = wl.communicate(None)
    return out

  @tr.session.cache
  def GetWlCounters(self):
    """Returns a dict() with the value of every 'wl counters' stat."""
    out = self._SubprocessWithOutput(['counters'])

    # match three different types of stat output:
    # rxuflo: 1 2 3 4 5 6
    # rxfilter 1
    # d11_txretrie
    st = re.compile(r'(\w+:?(?: \d+)*)')

    stats = st.findall(out)
    r1 = re.compile(r'(\w+): (.+)')
    r2 = re.compile(r'(\w+) (\d+)')
    r3 = re.compile(r'(\w+)')
    sdict = dict()
    for stat in stats:
      p1 = r1.match(stat)
      p2 = r2.match(stat)
      p3 = r3.match(stat)
      if p1 is not None:
        sdict[p1.group(1).lower()] = p1.group(2).split()
      elif p2 is not None:
        sdict[p2.group(1).lower()] = p2.group(2)
      elif p3 is not None:
        sdict[p3.group(1).lower()] = '0'
    return sdict

  def DoAutoChannelSelect(self):
    """Run the AP through an auto channel selection."""
    # Make sure the interface is up, and ssid is the empty string.
    self._SubprocessCall(['down'])
    self._SubprocessCall(['ssid', ''])
    self._SubprocessCall(['ap', '0'])
    self._SubprocessCall(['spect', '0'])
    self._SubprocessCall(['mpc', '0'])
    self._SubprocessCall(['up'])
    # This starts a scan, and we give it some time to complete.
    # TODO(jnewlin): Chat with broadcom about how long we need/should
    # wait before setting the autoscanned channel.
    self._SubprocessCall(['autochannel', '1'])
    time.sleep(WL_AUTOCHAN_SLEEP)
    # You're supposed to use 'autochannel 2' at this point to set the
    # chanspec based on the autochannel results. Sadly, the driver is buggy
    # about this, and loses the autochannel setting most of the time.
    # So instead, just get the channel it chose, and force it explicitly
    # later.
    chanstr = self._SubprocessWithOutput(['autochannel'])
    print 'autochannel selection: %r' % chanstr
    chanspec = chanstr.split()[0]
    # Bring the interface back down and reset spect and mpc settings.
    # spect can't be changed for 0 -> 1 unless the interface is down.
    self._SubprocessCall(['down'])
    self._SubprocessCall(['spect', '1'])
    self._SubprocessCall(['mpc', '1'])
    return chanspec

  def SetApMode(self):
    """Put device into AP mode."""
    self._SubprocessCall(['ap', '1'])

  @tr.session.cache
  def GetAssociatedDevices(self):
    """Return a list of MAC addresses of associated STAs."""
    out = self._SubprocessWithOutput(['assoclist'])
    stamac_re = re.compile(r'((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})')
    stations = list()
    for line in out.splitlines():
      sta = stamac_re.search(line)
      if sta is not None:
        stations.append(sta.group(1))
    return stations

  @tr.session.cache
  def GetAssociatedDevice(self, mac):
    """Return information about as associated STA.

    Args:
      mac: MAC address of the requested STA as a string, xx:xx:xx:xx:xx:xx

    Returns:
      An AssociatedDevice namedtuple.
    """
    ad = collections.namedtuple(
        'AssociatedDevice', ('MACAddress'
                             'AuthenticationState'
                             'LastTransmitKbps',
                             'LastReceiveKbps',
                             'IdleSeconds'))
    ad.MACAddress = mac
    ad.AuthenticationState = False
    ad.LastTransmitKbps = 0
    ad.LastReceiveKbps = 0
    ad.IdleSeconds = 0
    out = self._SubprocessWithOutput(['sta_info', mac.upper()])
    for line in out.splitlines():
      if 'AUTHENTICATED' in line:
        ad.AuthenticationState = True
      tx_rate = TX_RE.search(line)
      if tx_rate is not None:
        ad.LastTransmitKbps = IntOrZero(tx_rate.group(1))
      rx_rate = RX_RE.search(line)
      if rx_rate is not None:
        ad.LastReceiveKbps = IntOrZero(rx_rate.group(1))
      idle = IDLE_RE.search(line)
      if idle is not None:
        ad.IdleSeconds = IntOrZero(idle.group(1))
    return ad

  @tr.session.cache
  def GetAutoRateFallBackEnabled(self):
    """Return WLANConfiguration.AutoRateFallBackEnabled as a boolean."""
    out = self._SubprocessWithOutput(['interference'])
    mode_re = re.compile(r'\(mode (\d)\)')
    result = mode_re.search(out)
    mode = -1
    if result is not None:
      mode = int(result.group(1))
    return True if mode == 3 or mode == 4 else False

  @tr.session.cache
  def SetAutoRateFallBackEnabled(self, value):
    """Set WLANConfiguration.AutoRateFallBackEnabled, expects a boolean."""
    interference = 4 if value else 3
    self._SubprocessCall(['interference', str(interference)])

  @tr.session.cache
  def GetBand(self):
    channel = self.GetChannel()
    return '2.4GHz' if channel < 20 else '5GHz'

  def SetBand(self, band):
    x = 'b' if band == '2.4GHz' else 'a'
    self._SubprocessCall(['band', str(x)])

  def ValidateBand(self, band):
    return band in ('2.4GHz', '5GHz')

  @tr.session.cache
  def GetBasicDataTransmitRates(self):
    out = self._SubprocessWithOutput(['rateset'])
    basic_re = re.compile(r'([0123456789]+(?:\.[0123456789]+)?)\(b\)')
    return ','.join(basic_re.findall(out))

  @tr.session.cache
  def GetBSSID(self):
    out = self._SubprocessWithOutput(['bssid'])
    bssid_re = re.compile(r'((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})')
    for line in out.splitlines():
      bssid = bssid_re.match(line)
      if bssid is not None:
        return bssid.group(1)
    return '00:00:00:00:00:00'

  def SetBSSID(self, value):
    self._SubprocessCall(['bssid', value])

  def ValidateBSSID(self, value):
    lower = value.lower()
    if lower == '00:00:00:00:00:00' or lower == 'ff:ff:ff:ff:ff:ff':
      return False
    bssid_re = re.compile(r'((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})')
    if bssid_re.search(value) is None:
      return False
    return True

  @tr.session.cache
  def GetBssStatus(self):
    out = self._SubprocessWithOutput(['bss'])
    lower = out.strip().lower()
    if lower == 'up':
      return 'Up'
    elif lower == 'down':
      return 'Disabled'
    else:
      return 'Error'

  def SetBssStatus(self, enable):
    status = 'up' if enable else 'down'
    self._SubprocessCall(['bss', status])

  @tr.session.cache
  def GetChannel(self):
    out = self._SubprocessWithOutput(['channel'])
    chan_re = re.compile(r'current mac channel(?:\s+)(\d+)')
    for line in out.splitlines():
      mr = chan_re.match(line)
      if mr is not None:
        return int(mr.group(1))
    return 0

  def SetChannel(self, value):
    try:
      int(value)
    except ValueError:
      # a string like '64l' is a chanspec
      self._SubprocessCall(['chanspec', str(value)])
    else:
      # if it can be converted to int, it's a normal channel number
      self._SubprocessCall(['channel', str(value)])

  def ValidateChannel(self, value):
    """Check for a valid Wifi channel number."""
    if value in range(1, 14):
      return True  # 2.4 GHz. US allows 1-11, Japan allows 1-13.
    if value in range(36, 144, 4):
      return True  # 5 GHz lower bands
    if value in range(149, 169, 4):
      return True  # 5 GHz upper bands
    return False

  def EM_StringToBitmap(self, enum):
    """Return bitmap suitable for 'wl crypto' for EncryptionModes."""
    wsec = {'X_CATAWAMPUS-ORG_None': EM_NONE,
            'None': EM_NONE,
            'WEPEncryption': EM_WEP,
            'TKIPEncryption': EM_TKIP,
            'WEPandTKIPEncryption': EM_WEP | EM_TKIP,
            'AESEncryption': EM_AES,
            'WEPandAESEncryption': EM_WEP | EM_AES,
            'TKIPandAESEncryption': EM_TKIP | EM_AES,
            'WEPandTKIPandAESEncryption': EM_WEP | EM_TKIP | EM_AES}
    return wsec.get(enum, EM_NONE)

  def EM_BitmapToString(self, bitmap):
    """Return string description of a 'wl crypto' bitmap."""
    bmap = {EM_NONE: 'X_CATAWAMPUS-ORG_None',
            EM_WEP: 'WEPEncryption',
            EM_TKIP: 'TKIPEncryption',
            EM_WEP | EM_TKIP: 'WEPandTKIPEncryption',
            EM_AES: 'AESEncryption',
            EM_WEP | EM_AES: 'WEPandAESEncryption',
            EM_TKIP | EM_AES: 'TKIPandAESEncryption',
            EM_WEP | EM_TKIP | EM_AES: 'WEPandTKIPandAESEncryption'}
    return bmap.get(bitmap)

  @tr.session.cache
  def GetEncryptionModes(self):
    out = self._SubprocessWithOutput(['wsec'])
    try:
      w = int(out.strip()) & 0x7
      return self.EM_BitmapToString(w)
    except ValueError:
      return 'X_CATAWAMPUS-ORG_None'

  def SetEncryptionModes(self, value):
    self._SubprocessCall(['wsec', str(value)])

  def ValidateEncryptionModes(self, value):
    ENCRYPTTYPES = frozenset(['X_CATAWAMPUS-ORG_None', 'None', 'WEPEncryption',
                              'TKIPEncryption', 'WEPandTKIPEncryption',
                              'AESEncryption', 'WEPandAESEncryption',
                              'TKIPandAESEncryption',
                              'WEPandTKIPandAESEncryption'])
    return True if value in ENCRYPTTYPES else False

  def SetJoin(self, ssid, amode):
    self._SubprocessCall(['join', str(ssid), 'imode', 'bss',
                          'amode', str(amode)])

  @tr.session.cache
  def GetOperationalDataTransmitRates(self):
    out = self._SubprocessWithOutput(['rateset'])
    oper_re = re.compile(r'([0123456789]+(?:\.[0123456789]+)?)')
    if out:
      line1 = out.splitlines()[0]
    else:
      line1 = ''
    return ','.join(oper_re.findall(line1))

  def SetPMK(self, value):
    self._SubprocessCall(['set_pmk', value])

  @tr.session.cache
  def GetPossibleChannels(self):
    out = self._SubprocessWithOutput(['channels'])
    if out:
      channels = [int(x) for x in out.split()]
      return wifi.ContiguousRanges(channels)
    else:
      return ''

  @tr.session.cache
  def GetRadioEnabled(self):
    out = self._SubprocessWithOutput(['radio'])
    # This may look backwards, but I assure you it is correct. If the
    # radio is off, 'wl radio' returns 0x0001.
    try:
      return False if int(out.strip(), 0) == 1 else True
    except ValueError:
      return False

  def SetRadioEnabled(self, value):
    radio = 'on' if value else 'off'
    self._SubprocessCall(['radio', radio])

    # Only modify marker file if command is successful
    if value:
      try:
        open(WL_RADIO_STATE_MARKER_FILE, 'a').close()
      except IOError:
        print 'Unable to write ' + WL_RADIO_STATE_MARKER_FILE
    else:
      tr.helpers.Unlink(WL_RADIO_STATE_MARKER_FILE)

  @tr.session.cache
  def GetRegulatoryDomain(self):
    out = self._SubprocessWithOutput(['country'])
    fields = out.split()
    if fields:
      return fields[0]
    else:
      return ''

  def SetRegulatoryDomain(self, value):
    self._SubprocessCall(['country', value])

  def ValidateRegulatoryDomain(self, value):
    out = self._SubprocessWithOutput(['country', 'list'])
    countries = set()
    for line in out.splitlines():
      fields = line.split(' ')
      if len(fields) and len(fields[0]) == 2:
        countries.add(fields[0])
    return True if value in countries else False

  def SetReset(self, do_reset):
    status = 'down' if do_reset else 'up'
    self._SubprocessCall([status])

  @tr.session.cache
  def GetSSID(self):
    """Return current Wifi SSID."""
    out = self._SubprocessWithOutput(['ssid'])
    ssid_re = re.compile(r'Current SSID: "(.*)"')
    for line in out.splitlines():
      ssid = ssid_re.match(line)
      if ssid is not None:
        return ssid.group(1)
    return ''

  def SetSSID(self, value, cfgnum=None):
    self._SubprocessCall(['up'])
    if cfgnum is not None:
      self._SubprocessCall(['ssid', '-C', str(cfgnum), value])
    else:
      self._SubprocessCall(['ssid', value])

  def ValidateSSID(self, value):
    if len(value) > 32:
      return False
    return True

  @tr.session.cache
  def GetSSIDAdvertisementEnabled(self):
    out = self._SubprocessWithOutput(['closed'])
    return True if out.strip() == '0' else False

  def SetSSIDAdvertisementEnabled(self, value):
    closed = '0' if value else '1'
    self._SubprocessCall(['closed', closed])

  def SetSupWpa(self, value):
    sup_wpa = '1' if value else '0'
    self._SubprocessCall(['sup_wpa', sup_wpa])

  @tr.session.cache
  def GetTransmitPower(self):
    out = self._SubprocessWithOutput(['pwr_percent'])
    return int(out.strip())

  def SetTransmitPower(self, value):
    self._SubprocessCall(['pwr_percent', str(value)])

  def ValidateTransmitPower(self, percent):
    if percent < 0 or percent > 100:
      return False
    return True

  def GetTransmitPowerSupported(self):
    # tr-98 describes this as a comma separated list, limited to string(64)
    # clearly it is expected to be a small number of discrete steps.
    # This chipset appears to have no such restriction. Hope a range is ok.
    return '1-100'

  def SetWepKey(self, index, key, mac=None):
    wl_cmd = ['addwep', str(index), key]
    if mac is not None:
      wl_cmd.append(str(mac))
    self._SubprocessCall(wl_cmd)

  def ClrWepKey(self, index):
    self._SubprocessCall(['rmwep', str(index)])

  def SetWepKeyIndex(self, index):
    # We do not use check_call here because primary_key fails if no WEP
    # keys have been configured, but we keep the code simple to always set it.
    subprocess.call([WL_EXE, '-i', self._if, 'primary_key', str(index)])

  def SetWepStatus(self, enable):
    status = 'on' if enable else 'off'
    self._SubprocessCall(['wepstatus', status])

  @tr.session.cache
  def GetWpaAuth(self):
    return self._SubprocessWithOutput(['wpa_auth'])

  def SetWpaAuth(self, value):
    self._SubprocessCall(['wpa_auth', str(value)])


class BrcmWifiWlanConfiguration(CATA98WIFI):
  """An implementation of tr98 WLANConfiguration for Broadcom Wifi chipsets."""

  DeviceOperationMode = tr.cwmptypes.ReadOnlyString('InfrastructureAccessPoint')
  Standard = tr.cwmptypes.ReadOnlyString('n')
  SupportedFrequencyBands = tr.cwmptypes.ReadOnlyString('2.4GHz,5GHz')
  UAPSDSupported = tr.cwmptypes.ReadOnlyBool(False)
  WEPEncryptionLevel = tr.cwmptypes.ReadOnlyString('Disabled,40-bit,104-bit')
  WMMSupported = tr.cwmptypes.ReadOnlyBool(False)

  def __init__(self, ifname):
    super(BrcmWifiWlanConfiguration, self).__init__()
    self._ifname = ifname
    self.wl = Wl(ifname)

    # Unimplemented, but not yet evaluated
    self.Unexport(['Alias', 'BeaconAdvertisementEnabled', 'ChannelsInUse',
                   'MaxBitRate', 'PossibleDataTransmitRates',
                   'TotalIntegrityFailures', 'TotalPSKFailures',
                   'OperatingStandards', 'SupportedStandards',
                   'RekeyingInterval', 'GuardInterval',
                   'X_CATAWAMPUS-ORG_Width24G',
                   'X_CATAWAMPUS-ORG_Width5G',
                   'X_CATAWAMPUS-ORG_AutoChannelAlgorithm',
                   'X_CATAWAMPUS-ORG_RecommendedChannel',
                   'X_CATAWAMPUS-ORG_InitiallyRecommendedChannel',
                   'X_CATAWAMPUS-ORG_AutoChanType',
                   'X_CATAWAMPUS-ORG_AllowAutoDisable',
                   'X_CATAWAMPUS-ORG_AutoDisableRecommended',
                   'X_CATAWAMPUS-ORG_ClientIsolation',
                   'X_CATAWAMPUS-ORG_OverrideSSID',
                   'X_CATAWAMPUS-ORG_Suffix24G'])

    self.PreSharedKeyList = {}
    for i in range(1, 2):
      # tr-98 spec deviation: spec says 10 PreSharedKeys objects,
      # BRCM only supports one.
      self.PreSharedKeyList[i] = wifi.PreSharedKey98()

    self._Stats = BrcmWlanConfigurationStats(ifname)

    self.WEPKeyList = {}
    for i in range(1, 5):
      self.WEPKeyList[i] = wifi.WEPKey98()

    self.LocationDescription = ''

    # No support for acting as a client, could be added later.
    self.Unexport(['ClientEnable'])

    # No RADIUS support, could be added later.
    self.Unexport(['AuthenticationServiceMode'])

    # Local settings, currently unimplemented. Will require more
    # coordination with the underlying platform support.
    self.Unexport(['InsecureOOBAccessEnabled'])

    # MAC Access controls, currently unimplemented but could be supported.
    self.Unexport(['MACAddressControlEnabled'])

    # Wifi Protected Setup, currently unimplemented and not recommended.
    self.Unexport(objects=['WPS'])

    # Wifi MultiMedia, currently unimplemented but could be supported.
    # "wl wme_*" commands
    self.Unexport(lists=['APWMMParameter', 'STAWMMParameter'])
    self.Unexport(['UAPSDEnable', 'WMMEnable'])

    # WDS, currently unimplemented but could be supported at some point.
    self.Unexport(['PeerBSSID', 'DistanceFromRoot'])

    self.config = self._GetDefaultSettings()
    self.old_config = None

  def _GetDefaultSettings(self):
    obj = WifiConfig()
    obj.p_auto_channel_enable = True
    obj.p_auto_rate_fallback_enabled = None
    obj.p_band = '2.4GHz'
    obj.p_basic_authentication_mode = 'None'
    obj.p_basic_encryption_modes = 'WEPEncryption'
    obj.p_beacon_type = 'WPAand11i'
    obj.p_bssid = None
    obj.p_channel = None
    obj.p_enable = False
    obj.p_ieee11i_authentication_mode = 'PSKAuthentication'
    obj.p_ieee11i_encryption_modes = 'X_CATAWAMPUS-ORG_None'
    obj.p_radio_enabled = True
    obj.p_regulatory_domain = None
    obj.p_ssid = None
    obj.p_ssid_advertisement_enabled = None
    obj.p_transmit_power = None
    obj.p_wepkeyindex = 1
    obj.p_wpa_authentication_mode = 'PSKAuthentication'
    obj.p_wpa_encryption_modes = 'X_CATAWAMPUS-ORG_None'
    return obj

  def StartTransaction(self):
    config = self.config
    self.config = copy.copy(config)
    self.old_config = config

  def AbandonTransaction(self):
    self.config = self.old_config
    self.old_config = None

  def CommitTransaction(self):
    self.old_config = None
    self._ConfigureBrcmWifi()

  @property
  def Name(self):
    return self._ifname

  @property
  def Stats(self):
    return self._Stats

  @property
  def TotalAssociations(self):
    return len(self.AssociatedDeviceList)

  def GetAutoRateFallBackEnabled(self):
    return self.wl.GetAutoRateFallBackEnabled()

  def SetAutoRateFallBackEnabled(self, value):
    self.config.p_auto_rate_fallback_enabled = tr.cwmpbool.parse(value)

  AutoRateFallBackEnabled = property(
      GetAutoRateFallBackEnabled, SetAutoRateFallBackEnabled, None,
      'WLANConfiguration.AutoRateFallBackEnabled')

  def GetBasicAuthenticationMode(self):
    return self.config.p_basic_authentication_mode

  def SetBasicAuthenticationMode(self, value):
    if value not in BASICAUTHMODES:
      raise ValueError('Unsupported BasicAuthenticationMode %s' % value)
    self.config.p_basic_authentication_mode = value

  BasicAuthenticationMode = property(
      GetBasicAuthenticationMode, SetBasicAuthenticationMode, None,
      'WLANConfiguration.BasicAuthenticationMode')

  def GetBasicDataTransmitRates(self):
    return self.wl.GetBasicDataTransmitRates()

  # TODO(dgentry) implement SetBasicDataTransmitRates

  BasicDataTransmitRates = property(
      GetBasicDataTransmitRates, None, None,
      'WLANConfiguration.BasicDataTransmitRates')

  def GetBasicEncryptionModes(self):
    return self.config.p_basic_encryption_modes

  def SetBasicEncryptionModes(self, value):
    if value not in BASICENCRYPTIONS:
      raise ValueError('Unsupported BasicEncryptionMode: %s' % value)
    self.config.p_basic_encryption_modes = value

  BasicEncryptionModes = property(GetBasicEncryptionModes,
                                  SetBasicEncryptionModes, None,
                                  'WLANConfiguration.BasicEncryptionModes')

  def GetBeaconType(self):
    return self.config.p_beacon_type

  def SetBeaconType(self, value):
    if value not in BEACONS:
      raise ValueError('Unsupported BeaconType: %s' % value)
    self.config.p_beacon_type = value

  BeaconType = property(GetBeaconType, SetBeaconType, None,
                        'WLANConfiguration.BeaconType')

  def GetBSSID(self):
    return self.wl.GetBSSID()

  def SetBSSID(self, value):
    if not self.wl.ValidateBSSID(value):
      raise ValueError('Invalid BSSID: %s' % value)
    self.config.p_bssid = value

  BSSID = property(GetBSSID, SetBSSID, None, 'WLANConfiguration.BSSID')

  def GetChannel(self):
    return self.wl.GetChannel()

  def SetChannel(self, value):
    ivalue = int(value)
    if not self.wl.ValidateChannel(ivalue):
      raise ValueError('Invalid Channel: %d' % ivalue)
    self.config.p_band = '2.4GHz' if ivalue < 20 else '5GHz'
    self.config.p_channel = ivalue
    self.config.p_auto_channel_enable = False

  Channel = property(GetChannel, SetChannel, None, 'WLANConfiguration.Channel')

  def GetEnable(self):
    return self.config.p_enable

  def SetEnable(self, value):
    self.config.p_enable = tr.cwmpbool.parse(value)

  Enable = property(GetEnable, SetEnable, None, 'WLANConfiguration.Enable')

  def GetIEEE11iAuthenticationMode(self):
    auth = self.wl.GetWpaAuth().split()
    eap = True if 'WPA2-802.1x' in auth else False
    psk = True if 'WPA2-PSK' in auth else False
    if eap and psk:
      return 'EAPandPSKAuthentication'
    elif eap:
      return 'EAPAuthentication'
    else:
      return 'PSKAuthentication'

  def SetIEEE11iAuthenticationMode(self, value):
    if value not in WPAAUTHMODES:
      raise ValueError('Unsupported IEEE11iAuthenticationMode %s' % value)
    self.config.p_ieee11i_authentication_mode = value

  IEEE11iAuthenticationMode = property(
      GetIEEE11iAuthenticationMode, SetIEEE11iAuthenticationMode,
      None, 'WLANConfiguration.IEEE11iAuthenticationMode')

  def GetIEEE11iEncryptionModes(self):
    return self.wl.GetEncryptionModes()

  def SetIEEE11iEncryptionModes(self, value):
    if not self.wl.ValidateEncryptionModes(value):
      raise ValueError('Invalid IEEE11iEncryptionMode: %s' % value)
    self.config.p_ieee11i_encryption_modes = value

  IEEE11iEncryptionModes = property(
      GetIEEE11iEncryptionModes, SetIEEE11iEncryptionModes, None,
      'WLANConfiguration.IEEE11iEncryptionModes')

  def GetKeyPassphrase(self):
    psk = self.PreSharedKeyList[1]
    return psk.KeyPassphrase

  def SetKeyPassphrase(self, value):
    psk = self.PreSharedKeyList[1]
    psk.KeyPassphrase = value
    # TODO(dgentry) need to set WEPKeys, but this is fraught with peril.
    # If KeyPassphrase is not exactly 5 or 13 bytes it must be padded.
    # Apple uses different padding than Windows (and others).
    # http://support.apple.com/kb/HT1344

  KeyPassphrase = property(GetKeyPassphrase, SetKeyPassphrase, None,
                           'WLANConfiguration.KeyPassphrase')

  def GetOperationalDataTransmitRates(self):
    return self.wl.GetOperationalDataTransmitRates()

  # TODO(dgentry) - need to implement SetOperationalDataTransmitRates

  OperationalDataTransmitRates = property(
      GetOperationalDataTransmitRates, None,
      None, 'WLANConfiguration.OperationalDataTransmitRates')

  def GetPossibleChannels(self):
    return self.wl.GetPossibleChannels()

  PossibleChannels = property(GetPossibleChannels, None, None,
                              'WLANConfiguration.PossibleChannels')

  def GetRadioEnabled(self):
    return self.wl.GetRadioEnabled()

  def SetRadioEnabled(self, value):
    self.config.p_radio_enabled = tr.cwmpbool.parse(value)

  RadioEnabled = property(GetRadioEnabled, SetRadioEnabled, None,
                          'WLANConfiguration.RadioEnabled')

  def GetRegulatoryDomain(self):
    return self.wl.GetRegulatoryDomain()

  def SetRegulatoryDomain(self, value):
    if not self.wl.ValidateRegulatoryDomain(value):
      raise ValueError('Unknown RegulatoryDomain: %s' % value)
    self.config.p_regulatory_domain = value

  RegulatoryDomain = property(GetRegulatoryDomain, SetRegulatoryDomain, None,
                              'WLANConfiguration.RegulatoryDomain')

  def GetAutoChannelEnable(self):
    return self.config.p_auto_channel_enable

  def SetAutoChannelEnable(self, value):
    self.config.p_auto_channel_enable = tr.cwmpbool.parse(value)

  AutoChannelEnable = property(GetAutoChannelEnable, SetAutoChannelEnable,
                               None, 'WLANConfiguration.AutoChannelEnable')

  def GetSSID(self):
    return self.wl.GetSSID()

  def SetSSID(self, value):
    if not self.wl.ValidateSSID(value):
      raise ValueError('Invalid SSID: %s' % value)
    self.config.p_ssid = value

  SSID = property(GetSSID, SetSSID, None, 'WLANConfiguration.SSID')

  def GetSSIDAdvertisementEnabled(self):
    return self.wl.GetSSIDAdvertisementEnabled()

  def SetSSIDAdvertisementEnabled(self, value):
    self.config.p_ssid_advertisement_enabled = tr.cwmpbool.parse(value)

  SSIDAdvertisementEnabled = property(
      GetSSIDAdvertisementEnabled, SetSSIDAdvertisementEnabled, None,
      'WLANConfiguration.SSIDAdvertisementEnabled')

  def GetBssStatus(self):
    return self.wl.GetBssStatus()

  Status = property(GetBssStatus, None, None, 'WLANConfiguration.Status')

  def GetTransmitPower(self):
    return self.wl.GetTransmitPower()

  def SetTransmitPower(self, value):
    percent = int(value)
    if not self.wl.ValidateTransmitPower(percent):
      raise ValueError('Invalid TransmitPower: %d' % percent)
    self.config.p_transmit_power = percent

  TransmitPower = property(GetTransmitPower, SetTransmitPower, None,
                           'WLANConfiguration.TransmitPower')

  def GetTransmitPowerSupported(self):
    return self.wl.GetTransmitPowerSupported()

  TransmitPowerSupported = property(GetTransmitPowerSupported, None, None,
                                    'WLANConfiguration.TransmitPowerSupported')

  def GetWEPKeyIndex(self):
    return self.config.p_wepkeyindex

  def SetWEPKeyIndex(self, value):
    self.config.p_wepkeyindex = int(value)

  WEPKeyIndex = property(GetWEPKeyIndex, SetWEPKeyIndex, None,
                         'WLANConfiguration.WEPKeyIndex')

  def GetWPAAuthenticationMode(self):
    auth = self.wl.GetWpaAuth().split()
    eap = True if 'WPA-802.1x' in auth else False
    if eap:
      return 'EAPAuthentication'
    else:
      return 'PSKAuthentication'

  def SetWPAAuthenticationMode(self, value):
    if value not in WPAAUTHMODES:
      raise ValueError('Unsupported WPAAuthenticationMode %s' % value)
    self.config.p_wpa_authentication_mode = value

  WPAAuthenticationMode = property(
      GetWPAAuthenticationMode, SetWPAAuthenticationMode,
      None, 'WLANConfiguration.WPAAuthenticationMode')

  def GetEncryptionModes(self):
    return self.wl.GetEncryptionModes()

  def SetWPAEncryptionModes(self, value):
    if not self.wl.ValidateEncryptionModes(value):
      raise ValueError('Invalid WPAEncryptionMode: %s' % value)
    self.config.p_wpa_encryption_modes = value

  WPAEncryptionModes = property(GetEncryptionModes, SetWPAEncryptionModes, None,
                                'WLANConfiguration.WPAEncryptionModes')

  def GetOperatingFrequencyBand(self):
    return self.wl.GetBand()

  def SetOperatingFrequencyBand(self, value):
    if not self.wl.ValidateBand(value):
      raise ValueError('Invalid OperatingFrequencyBand: %s' % value)
    self.config.p_band = value

    if ((value == '2.4GHz' and self.config.p_channel >= 20) or
        (value == '5GHz' and self.config.p_channel < 20)):
      # Old channel is invalid. We could either hardcode a valid default
      # or default to autochannel. They *might* be about to set either
      # autochannel=True (harmless since that's what we just did) or
      # an explicit channel (which will then set autochannel back to false).
      # In both cases, plus the case there they don't do anything, setting
      # autochannel to true by default here is the best we can do.
      self.config.p_auto_channel_enable = True

  OperatingFrequencyBand = property(GetOperatingFrequencyBand,
                                    SetOperatingFrequencyBand, None,
                                    'WLANConfiguration.OperatingFrequencyBand')

  def _ConfigureBrcmWifi(self):
    """Issue commands to the wifi device to configure it.

    The Wifi driver is somewhat picky about the order of the commands.
    For example, some settings can only be changed while the radio is on.
    Make sure any changes made in this routine work in a real system, unit
    tests do not (and realistically, cannot) model all behaviors of the
    real wl utility.
    """

    if not self.config.p_enable or not self.config.p_radio_enabled:
      self.wl.SetRadioEnabled(False)
      return

    self.wl.SetRadioEnabled(True)
    self.wl.SetReset(True)
    if self.config.p_band:
      self.wl.SetBand(self.config.p_band)
    if self.config.p_auto_channel_enable:
      chanspec = self.wl.DoAutoChannelSelect()
    else:
      chanspec = self.config.p_channel
    self.wl.SetApMode()
    self.wl.SetBssStatus(False)
    if self.config.p_auto_rate_fallback_enabled is not None:
      self.wl.SetAutoRateFallBackEnabled(
          self.config.p_auto_rate_fallback_enabled)
    if self.config.p_bssid is not None:
      self.wl.SetBSSID(self.config.p_bssid)
    self.wl.SetChannel(chanspec)
    if self.config.p_regulatory_domain is not None:
      self.wl.SetRegulatoryDomain(self.config.p_regulatory_domain)
    if self.config.p_ssid_advertisement_enabled is not None:
      self.wl.SetSSIDAdvertisementEnabled(
          self.config.p_ssid_advertisement_enabled)
    if self.config.p_transmit_power is not None:
      self.wl.SetTransmitPower(self.config.p_transmit_power)

    # sup_wpa should only be set WPA/WPA2 modes, not for Basic.
    sup_wpa = False
    amode = 0
    if self.config.p_beacon_type.find('11i') >= 0:
      crypto = self.wl.EM_StringToBitmap(self.config.p_ieee11i_encryption_modes)
      if crypto != EM_NONE:
        amode = 128
      sup_wpa = True
    elif self.config.p_beacon_type.find('WPA') >= 0:
      crypto = self.wl.EM_StringToBitmap(self.config.p_wpa_encryption_modes)
      if crypto != EM_NONE:
        amode = 4
      sup_wpa = True
    elif self.config.p_beacon_type.find('Basic') >= 0:
      crypto = self.wl.EM_StringToBitmap(self.config.p_basic_encryption_modes)
    else:
      crypto = EM_NONE
    self.wl.SetEncryptionModes(crypto)
    self.wl.SetSupWpa(sup_wpa)
    self.wl.SetWpaAuth(amode)

    for idx, psk in self.PreSharedKeyList.items():
      key = psk.GetKey(self.config.p_ssid)
      if key:
        self.wl.SetPMK(key)

    if self.config.p_ssid is not None:
      self.wl.SetSSID(self.config.p_ssid)

    # Setting WEP key has to come after setting SSID. (Doesn't make sense
    # to me, it just doesn't work if you do it before setting SSID.)
    for idx, wep in self.WEPKeyList.items():
      key = wep.WEPKey
      if key:
        self.wl.SetWepKey(idx - 1, key)
      else:
        self.wl.ClrWepKey(idx - 1)
    self.wl.SetWepKeyIndex(self.config.p_wepkeyindex)

  @tr.session.cache
  def _GetWlCounters(self):
    return self.wl.GetWlCounters()

  def GetTotalBytesReceived(self):
    counters = self._GetWlCounters()
    return int(counters.get('rxbyte', 0))

  TotalBytesReceived = property(GetTotalBytesReceived, None, None,
                                'WLANConfiguration.TotalBytesReceived')

  def GetTotalBytesSent(self):
    counters = self._GetWlCounters()
    return int(counters.get('txbyte', 0))

  TotalBytesSent = property(GetTotalBytesSent, None, None,
                            'WLANConfiguration.TotalBytesSent')

  def GetTotalPacketsReceived(self):
    counters = self._GetWlCounters()
    return int(counters.get('rxframe', 0))

  TotalPacketsReceived = property(GetTotalPacketsReceived, None, None,
                                  'WLANConfiguration.TotalPacketsReceived')

  def GetTotalPacketsSent(self):
    counters = self._GetWlCounters()
    return int(counters.get('txframe', 0))

  TotalPacketsSent = property(GetTotalPacketsSent, None, None,
                              'WLANConfiguration.TotalPacketsSent')

  def GetAssociation(self, mac):
    """Get an AssociatedDevice object for the given STA."""
    ad = BrcmWlanAssociatedDevice(self.wl.GetAssociatedDevice(mac))
    if ad:
      tr.handle.ValidateExports(ad)
    return ad

  @property
  def AssociatedDeviceList(self):
    """Retrieves a list of all associated STAs."""
    stations = self.wl.GetAssociatedDevices()
    associated_device_list = {}
    for idx, mac in enumerate(stations, start=1):
      associated_device_list[str(idx)] = self.GetAssociation(mac)
    return associated_device_list


class BrcmWlanConfigurationStats(netdev.NetdevStatsLinux26, BASE98WIFI.Stats):
  """tr98 InternetGatewayDevice.LANDevice.WLANConfiguration.Stats."""

  def __init__(self, ifname):
    netdev.NetdevStatsLinux26.__init__(self, ifname)
    BASE98WIFI.Stats.__init__(self)


class BrcmWlanAssociatedDevice(CATA98WIFI.AssociatedDevice):
  """Implementation of tr98 AssociatedDevice for Broadcom Wifi chipsets."""

  def __init__(self, assoc):
    super(BrcmWlanAssociatedDevice, self).__init__()
    self._assoc = assoc
    self.Unexport(['AssociatedDeviceIPAddress', 'LastPMKId',
                   'LastRequestedUnicastCipher',
                   'LastRequestedMulticastCipher',
                   'X_CATAWAMPUS-ORG_SignalStrength',
                   'X_CATAWAMPUS-ORG_SignalStrengthAverage',
                   'X_CATAWAMPUS-ORG_StationInfo'])

  @property
  def AssociatedDeviceMACAddress(self):
    return self._assoc.MACAddress

  @property
  def AssociatedDeviceAuthenticationState(self):
    return self._assoc.AuthenticationState

  @property
  def LastDataTransmitRate(self):
    mbps = self._assoc.LastTransmitKbps / 1000
    # tr-098-1-6 defines this as a string(4). Bizarre.
    return str(mbps)

  @property
  def X_CATAWAMPUS_ORG_LastDataDownlinkRate(self):
    return self._assoc.LastTransmitKbps

  @property
  def X_CATAWAMPUS_ORG_LastDataUplinkRate(self):
    return self._assoc.LastReceiveKbps

  @property
  def X_CATAWAMPUS_ORG_Active(self):
    return True if self._assoc.IdleSeconds < 120 else False
