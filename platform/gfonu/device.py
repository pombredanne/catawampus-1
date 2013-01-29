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
# Modified based on Denton's device.py for Bruno Platform
#pylint: disable-msg=C6409

"""Data Model for GFiber ONU"""

__author__ = 'zixia@google.com (Ted Huang)'

# Modified based on gfmedia/device.py by Denton Gentry


import datetime
import fcntl
import os
import sys
import random
import subprocess
import traceback

import google3

import dm.device_info
import dm.igd_time
import dm.periodic_statistics
import dm.temperature
import platform_config
import pynetlinux
import tornado.ioloop
import tr.core
import tr.download
import tr.tr181_v2_4 as tr181

import optics


BASE98IGD = tr.tr098_v1_4.InternetGatewayDevice_v1_10.InternetGatewayDevice
PYNETIFCONF = pynetlinux.ifconfig.Interface

# tr-69 error codes
INTERNAL_ERROR = 9002

# Unit tests can override these with fake data
ACSCONNECTED = '/tmp/acsconnected'
ACSTIMEOUTMIN = 2*60*60
ACSTIMEOUTMAX = 4*60*60
CONFIGDIR = '/config/tr69'
DOWNLOADDIR = '/tmp'
GINSTALL = 'ginstall.py'
PROC_CPUINFO = '/proc/cpuinfo'
REBOOT = 'tr69_reboot'
REPOMANIFEST = '/etc/repo-buildroot-manifest'
SET_ACS = 'set-acs'
VERSIONFILE = '/etc/version'


class PlatformConfig(platform_config.PlatformConfigMeta):
  """PlatformConfig for GFONU devices."""

  def __init__(self, ioloop=None):
    platform_config.PlatformConfigMeta.__init__(self)
    self._ioloop = ioloop or tornado.ioloop.IOLoop.instance()
    self.acs_timeout = None
    self.acs_timeout_interval = random.randrange(ACSTIMEOUTMIN, ACSTIMEOUTMAX)
    self.acs_timeout_url = None

  def ConfigDir(self):
    return CONFIGDIR

  def DownloadDir(self):
    return DOWNLOADDIR

  def GetAcsUrl(self):
    setacs = subprocess.Popen([SET_ACS, 'print'], stdout=subprocess.PIPE)
    out, _ = setacs.communicate(None)
    return out if setacs.returncode == 0 else ''

  def SetAcsUrl(self, url):
    set_acs_url = url if url else 'clear'
    rc = subprocess.call(args=[SET_ACS, 'cwmp', set_acs_url.strip()])
    if rc != 0:
      raise AttributeError('set-acs failed')

  def InvalidateAcsUrl(self, url):
    try:
      subprocess.check_call(args=[SET_ACS, 'timeout', url.strip()])
    except subprocess.CalledProcessError:
      return False
    return True

  def _AcsAccessClearTimeout(self):
    if self.acs_timeout:
      self._ioloop.remove_timeout(self.acs_timeout)
      self.acs_timeout = None

  def _AcsAccessTimeout(self):
    """Timeout for AcsAccess.

    There has been no successful connection to ACS in
    self.acs_timeout_interval seconds.
    """
    try:
      os.remove(ACSCONNECTED)
    except OSError as e:
      if e.errno != errno.ENOENT:
        raise
      # No such file == harmless

    try:
      rc = subprocess.call(args=[SET_ACS, 'timeout',
                                 self.acs_timeout_url.strip()])
    except OSError:
      rc = -1

    if rc != 0:
      # Log the failure
      print '%s timeout %s failed %d' % (SET_ACS, self.acs_timeout_url, rc)

  def AcsAccessAttempt(self, url):
    """Called when a connection to the ACS is attempted."""
    if url != self.acs_timeout_url:
      self._AcsAccessClearTimeout()  # new ACS, restart timer
      self.acs_timeout_url = url
    if not self.acs_timeout:
      self.acs_timeout = self._ioloop.add_timeout(
          datetime.timedelta(seconds=self.acs_timeout_interval),
          self._AcsAccessTimeout)

  def AcsAccessSuccess(self, url):
    """Called when a session with the ACS successfully concludes."""
    self._AcsAccessClearTimeout()
    # We only *need* to create a 0 byte file, but write URL for debugging
    with open(ACSCONNECTED, 'w') as f:
      f.write(url)


# TODO: (zixia) based on real hardware chipset
class DeviceId(dm.device_info.DeviceIdMeta):

  @property
  def Manufacturer(self):
    return 'Google Fiber'

  @property
  def ManufacturerOUI(self):
    return 'F88FCA'

  @property
  def ModelName(self):
    return 'GFONU'

  @property
  def Description(self):
    return 'Optical Network Unit for Google Fiber network'

  @property
  def SerialNumber(self):
    return '666666666666'

  @property
  def HardwareVersion(self):
    return '1.0'

  @property
  def AdditionalHardwareVersion(self):
    return '1.0'

  @property
  def SoftwareVersion(self):
    return '1.0'

  @property
  def AdditionalSoftwareVersion(self):
    return '1.0'

  @property
  def ProductClass(self):
    return 'GFLT200'

  @property
  def ModemFirmwareVersion(self):
    return '1.0'


class Installer(tr.download.Installer):

  def __init__(self, filename, ioloop=None):
    tr.download.Installer.__init__(self)
    self.filename = filename
    self._install_cb = None
    self._ioloop = ioloop or tornado.ioloop.IOLoop.instance()

  def _call_callback(self, faultcode, faultstring):
    if self._install_cb:
      self._install_cb(faultcode, faultstring, must_reboot=True)

  def install(self, file_type, target_filename, callback):
    """Install self.filename to disk, then call callback."""
    print 'Installing: %r %r' % (file_type, target_filename)
    ftype = file_type.split()
    if ftype and ftype[0] != '1':
      self._call_callback(INTERNAL_ERROR,
                          'Unsupported file_type {0}'.format(ftype[0]))
      return False
    self._install_cb = callback

    if not os.path.exists(self.filename):
      self._call_callback(INTERNAL_ERROR,
                          'Installer: file %r does not exist.' % self.filename)
      return False

    #TODO(zixia): leave for GINSTALL

    cmd = [GINSTALL]
    try:
      self._ginstall = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    except OSError:
      self._call_callback(INTERNAL_ERROR, 'Unable to start installer process')
      return False

    self._call_callback(0, '')
    return True

  def reboot(self):
    sys.exit(32)


class Device(tr181.Device_v2_4.Device):
  """Device implementation for ONU device."""

  def __init__(self, device_id, periodic_stats):
    super(Device, self).__init__()
    self.Unexport(objects='ATM')
    self.Unexport(objects='Bridging')
    self.Unexport(objects='CaptivePortal')
    self.Unexport(objects='DHCPv4')
    self.Unexport(objects='DHCPv6')
    self.Unexport(objects='DNS')
    self.Unexport(objects='DSL')
    self.Unexport(objects='DSLite')
    self.Unexport(objects='Ethernet')
    self.Unexport(objects='Firewall')
    self.Unexport(objects='GatewayInfo')
    self.Unexport(objects='Ghn')
    self.Unexport(objects='HPNA')
    self.Unexport(objects='HomePlug')
    self.Unexport(objects='Hosts')
    self.Unexport(objects='IEEE8021x')
    self.Unexport(lists='InterfaceStack')
    self.Unexport('InterfaceStackNumberOfEntries')
    self.Unexport(objects='IP')
    self.Unexport(objects='IPv6rd')
    self.Unexport(objects='LANConfigSecurity')
    self.Unexport(objects='ManagementServer')
    self.Unexport(objects='MoCA')
    self.Unexport(objects='NAT')
    self.Unexport(objects='NeighborDiscovery')
    self.Unexport(objects='PPP')
    self.Unexport(objects='PTM')
    self.Unexport(objects='QoS')
    self.Unexport('RootDataModelVersion')
    self.Unexport(objects='RouterAdvertisement')
    self.Unexport(objects='Routing')
    self.Unexport(objects='Services')
    self.Unexport(objects='SmartCardReaders')
    self.Unexport(objects='UPA')
    self.Unexport(objects='USB')
    self.Unexport(objects='Users')
    self.Unexport(objects='WiFi')

    # DeficeInfo is defined under tr181.Device_v2_4,
    # not tr181.Device_v2_4.Device, so still need to Export here
    self.Export(objects=['DeviceInfo'])
    self.DeviceInfo = dm.device_info.DeviceInfo181Linux26(device_id)
    self.DeviceInfo.Unexport('X_CATAWAMPUS-ORG_LedStatusNumberOfEntries')
    self.DeviceInfo.Unexport(lists='X_CATAWAMPUS-ORG_LedStatus')
    self.DeviceInfo.Unexport('LocationNumberOfEntries')
    self.DeviceInfo.Unexport(lists='Processor')
    self.DeviceInfo.Unexport(lists='SupportedDataModel')
    self.DeviceInfo.Unexport(lists='VendorConfigFile')
    self.DeviceInfo.Unexport(lists='VendorLogFile')
    self.DeviceInfo.Unexport('ProcessorNumberOfEntries')
    self.DeviceInfo.Unexport('VendorLogFileNumberOfEntries')
    self.DeviceInfo.Unexport('VendorConfigFileNumberOfEntries')
    self.DeviceInfo.Unexport('SupportedDataModelNumberOfEntries')

    self.ManagementServer = tr.core.TODO()

    self.Optical = optics.Optical()

    ts = self.DeviceInfo.TemperatureStatus

    for IfIndex, IfModule in self.Optical.InterfaceList.iteritems():
      ts.AddSensor(name=IfModule.ifname,
                   sensor=optics.SensorReadFromI2C(IfModule))

    self.Export(objects=['PeriodicStatistics'])
    self.PeriodicStatistics = periodic_stats


class InternetGatewayDevice(BASE98IGD):
  """Implements tr-98 InternetGatewayDevice."""

  def __init__(self, device_id, periodic_stats):
    super(InternetGatewayDevice, self).__init__()
    self.Unexport(objects='CaptivePortal')
    self.Unexport(objects='DeviceConfig')
    self.Unexport(params='DeviceSummary')
    self.Unexport(objects='DownloadDiagnostics')
    self.Unexport(objects='IPPingDiagnostics')
    self.Unexport(objects='LANConfigSecurity')
    self.Unexport(objects='LANInterfaces')
    self.Unexport(objects='Layer2Bridging')
    self.Unexport(objects='Layer3Forwarding')
    self.ManagementServer = tr.core.TODO()
    self.Unexport(objects='QueueManagement')
    self.Unexport(objects='Services')
    self.Unexport(objects='TraceRouteDiagnostics')
    self.Unexport(objects='UploadDiagnostics')
    self.Unexport(objects='UserInterface')
    self.Unexport(lists='LANDevice')
    self.Unexport(lists='WANDevice')
    self.Unexport(params='LANDeviceNumberOfEntries')
    self.Unexport(params='WANDeviceNumberOfEntries')

    self.DeviceInfo = dm.device_info.DeviceInfo98Linux26(device_id)
    self.DeviceInfo.Unexport(params='VendorConfigFileNumberOfEntries')

    self.Time = dm.igd_time.TimeTZ()

    self.Export(objects=['PeriodicStatistics'])
    self.PeriodicStatistics = periodic_stats


def PlatformInit(name, device_model_root):
  """Create platform-specific device models and initialize platform."""
  tr.download.INSTALLER = Installer
  params = []
  objects = []
  dev_id = DeviceId()
  periodic_stats = dm.periodic_statistics.PeriodicStatistics()

  device_model_root.Device = Device(dev_id, periodic_stats)
  objects.append('Device')
  device_model_root.InternetGatewayDevice = InternetGatewayDevice(
      dev_id, periodic_stats)
  objects.append('InternetGatewayDevice')

  return (params, objects)


def main():
  periodic_stats = dm.periodic_statistics.PeriodicStatistics()
  dev_id = DeviceId()
  device = Device(dev_id, periodic_stats)
  tr.core.Dump(device)
  device.ValidateExports()
  print 'done'

if __name__ == '__main__':
  main()
